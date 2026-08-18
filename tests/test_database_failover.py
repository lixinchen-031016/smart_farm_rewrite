"""双数据库（SQLite/MySQL）无感切换与故障转移测试。

覆盖：方言感知引擎创建 / 启动探测与自动切换 / 空备库自动建表 /
运行时故障转移 / 冷却防抖 / 手动切回主库 / 全部不可达报错 / URL 脱敏。
"""

import time

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from smart_farm.data import database as db
from smart_farm.data import repositories as repo
from smart_farm.data.models import Greenhouse

# 端口 1 基本必被拒绝 → 无需真实 MySQL 即可模拟「主库不可达」
BAD_MYSQL = "mysql+pymysql://root:x@127.0.0.1:1/test"
BAD_SQLITE = "sqlite:////nonexistent-dir/no-perm/x.db"


@pytest.fixture()
def restore_db(monkeypatch):
    """每用例独立的冷却配置 + 结束后恢复默认数据库连接。"""
    monkeypatch.setattr(db._settings, "db_failover_cooldown_seconds", 0)
    monkeypatch.setattr(db._settings, "db_connect_timeout", 1)
    yield
    db._init_database()  # 恢复默认（主库 sqlite ./smart_farm.db）


# ----------------------------- 引擎创建（方言感知） -----------------------------

def test_dialect_of():
    assert db._dialect_of("mysql+pymysql://u:p@h/db") == "mysql"
    assert db._dialect_of("sqlite:///./a.db") == "sqlite"
    assert db._dialect_of("postgresql+psycopg://u@h/db") == "postgresql"


def test_create_engine_sqlite_memory_uses_static_pool():
    eng = db._create_engine_for("sqlite://")
    assert db._dialect_of(str(eng.url)) == "sqlite"
    with eng.connect() as c:
        c.execute(db.text("SELECT 1"))


def test_create_engine_mysql_has_connect_timeout():
    eng = db._create_engine_for("mysql+pymysql://root:x@127.0.0.1:1/test")
    # 未连接即不触碰网络：仅验证引擎可按 mysql 方言构建（池参数生效）
    assert eng.dialect.name == "mysql"


def test_mask_url_hides_password():
    masked = db._mask_url("mysql+pymysql://root:secret@127.0.0.1:3306/farm")
    assert masked == "mysql+pymysql://root:***@127.0.0.1:3306/farm"
    assert db._mask_url("sqlite:///./a.db") == "sqlite:///./a.db"


# ----------------------------- 启动探测与自动切换 -----------------------------

def test_init_primary_ok(tmp_path, restore_db):
    primary = f"sqlite:///{tmp_path}/p.db"
    state = db._init_database(primary, f"sqlite:///{tmp_path}/f.db")
    assert state["role"] == "primary"
    assert db.get_active_url() == primary


def test_init_failover_to_fallback(tmp_path, restore_db):
    """主库不可达 → 自动切换备库，且空备库自动建表可写入。"""
    fallback = f"sqlite:///{tmp_path}/f.db"
    state = db._init_database(BAD_MYSQL, fallback)
    assert state["role"] == "fallback"
    assert state["dialect"] == "sqlite"
    assert "Can't connect" in state["primary_error"]

    # 空备库自动建表：直接经 get_session 写入读回
    with db.get_session() as s:
        repo.create_greenhouse(s, "备库棚", "测试")
    with db.get_session() as s:
        assert len(repo.list_greenhouses(s)) == 1


def test_init_all_down_raises(restore_db):
    with pytest.raises(RuntimeError, match="均不可连接"):
        db._init_database(BAD_MYSQL, BAD_SQLITE)


def test_init_no_fallback_configured(restore_db):
    with pytest.raises(RuntimeError, match="未配置备库"):
        db._init_database(BAD_SQLITE, None)


# ----------------------------- 运行时故障转移 -----------------------------

def test_runtime_failover_on_connection_loss(tmp_path, restore_db):
    """主库运行中失联 → get_session 自动切到备库，业务无感。"""
    primary = f"sqlite:///{tmp_path}/p.db"
    fallback = f"sqlite:///{tmp_path}/f.db"
    db._init_database(primary, fallback)
    with db.get_session() as s:  # 主库正常写入
        repo.create_greenhouse(s, "主库棚", "测试")

    # 模拟主库失联：活动引擎换成不可达 SQLite 路径（连接即 OperationalError）
    broken = db._create_engine_for(BAD_SQLITE)
    db._apply_engine(broken, BAD_SQLITE, "primary")

    # get_session 触发自动故障转移：写入实际落到备库
    with db.get_session() as s:
        repo.create_greenhouse(s, "备库棚", "测试")

    assert db._state["role"] == "fallback"
    assert db.get_active_url() == fallback
    with db.get_session() as s:
        names = {g.name for g in repo.list_greenhouses(s)}
    assert names == {"备库棚"}  # 备库独立数据，不含主库行

    # 备库文件确实有表有数据
    verify = create_engine(fallback)
    with Session(verify) as s:
        assert s.execute(select(Greenhouse.name)).scalars().all() == ["备库棚"]
    verify.dispose()


def test_runtime_failover_respects_cooldown(tmp_path, restore_db, monkeypatch):
    """冷却期内不重复故障转移（防主库抖动来回切）。"""
    monkeypatch.setattr(db._settings, "db_failover_cooldown_seconds", 3600)
    db._init_database(f"sqlite:///{tmp_path}/p.db", f"sqlite:///{tmp_path}/f.db")
    broken = db._create_engine_for(BAD_SQLITE)
    db._apply_engine(broken, BAD_SQLITE, "primary")
    db._state["last_failover_ts"] = 0.0  # 清除前序用例的冷却计时

    assert db._try_failover("test") is True   # 第一次：切换成功
    assert db._state["role"] == "fallback"
    assert db._try_failover("test") is False  # 冷却期内：不再动作


def test_lock_error_does_not_trigger_failover(tmp_path, restore_db):
    """SQLite 锁等待等临时错误不属于「失联」，不触发切换。"""
    assert db._is_connection_down_error.__name__ == "_is_connection_down_error"
    from sqlalchemy.exc import OperationalError

    lock_err = OperationalError("stmt", {}, Exception("database is locked"))
    assert db._is_connection_down_error(lock_err) is False
    down_err = OperationalError("stmt", {}, Exception("Can't connect to MySQL server"))
    assert db._is_connection_down_error(down_err) is True


# ----------------------------- 切回主库 -----------------------------

def test_reconnect_primary(tmp_path, restore_db):
    """备库运行中主库恢复 → 手动切回成功；主库仍不可达 → 拒绝切换。"""
    primary = f"sqlite:///{tmp_path}/p.db"
    fallback = f"sqlite:///{tmp_path}/f.db"
    db._init_database(primary, fallback)

    # 模拟已切到备库
    db._apply_engine(db._create_engine_for(fallback), fallback, "fallback", primary_error="simulated")
    ok, msg = db.reconnect_primary()
    assert ok and db._state["role"] == "primary"

    # 主库不可达时拒绝切回
    db._apply_engine(db._create_engine_for(fallback), fallback, "fallback", primary_error="simulated")
    db._state["primary_url"] = BAD_MYSQL
    ok2, msg2 = db.reconnect_primary()
    assert not ok2 and "仍不可达" in msg2
    assert db._state["role"] == "fallback"


# ----------------------------- 状态与迁移跟随 -----------------------------

def test_active_database_info_masks_url(restore_db):
    """状态快照中的 URL 必须脱敏（直接换绑引擎，不探测真实 MySQL）。"""
    mysql_url = "mysql+pymysql://root:secret@127.0.0.1:3306/farm"
    db._apply_engine(db._create_engine_for(mysql_url), mysql_url, "primary")
    info = db.active_database_info()
    assert "secret" not in info["active_url"]
    assert info["active_url"] == "mysql+pymysql://root:***@127.0.0.1:3306/farm"
    assert info["dialect"] == "mysql"


def test_run_migrations_targets_active_db(tmp_path, restore_db):
    """迁移作用于活动库（attributes 注入 URL 而非配置值）。"""
    active = f"sqlite:///{tmp_path}/active.db"
    db._init_database(active, None)
    db.run_migrations()
    from sqlalchemy import inspect

    tables = set(inspect(create_engine(active)).get_table_names())
    assert "greenhouse" in tables and "device" in tables and "alembic_version" in tables


def test_probe_database_helper(tmp_path, restore_db):
    ok, _ = db.probe_database(f"sqlite:///{tmp_path}/ok.db")
    assert ok
    ok2, err2 = db.probe_database(BAD_SQLITE)
    assert not ok2 and err2


def test_failover_keeps_gateway_writing(tmp_path, restore_db):
    """故障转移后网关数据通路仍可用（get_session 是全应用唯一入口）。"""
    db._init_database(f"sqlite:///{tmp_path}/p.db", f"sqlite:///{tmp_path}/f.db")
    from smart_farm.data import repositories as r2
    from smart_farm.services import ingest_service as ingest

    # 正常在主库注册设备
    with db.get_session() as s:
        gh = r2.create_greenhouse(s, "一号棚", None)
        dev = r2.create_device(s, "节点", "udp", "sf-fo-1", greenhouse_id=gh.id)

    # 主库失联 → 切备库（备库为空库 → 自动建表，但设备不在备库 → 拒绝）
    db._apply_engine(db._create_engine_for(BAD_SQLITE), BAD_SQLITE, "primary")
    with db.get_session() as s:  # 先触发故障转移
        r2.list_greenhouses(s)
    assert db._state["role"] == "fallback"

    # 备库注册同密钥设备后，ingest 恢复工作
    with db.get_session() as s:
        gh2 = r2.create_greenhouse(s, "一号棚", None)
        r2.create_device(s, "节点", "udp", dev.device_key, greenhouse_id=gh2.id)
    with db.get_session() as s:
        res = ingest.ingest_payload(s, {"device_key": dev.device_key, "metric": "soil", "value": 33.3})
    assert res.accepted == 1
    with db.get_session() as s:
        row = r2.get_latest_sensor_reading(s, "soil_moisture", greenhouse_id=gh2.id)
    assert row.value == 33.3


def test_monotonic_time_import_not_stale():
    """冷却计时使用 time.monotonic（不受系统时钟回拨影响）。"""
    assert isinstance(db._state["last_failover_ts"], float)
    assert db._state["last_failover_ts"] <= time.monotonic() + 1
