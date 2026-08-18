"""启动同步（startup_sync / sync_management_tables）测试。

核心场景：故障转移期间备库新增大棚/设备/用户/传感器数据，
重启程序后启动同步把它们完整合并回主库——包括跨库自增 id 不同时的
外键映射正确性。
"""

import struct
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from smart_farm.data import repositories as repo
from smart_farm.data.models import Base, Device, Greenhouse, SoilMoisture, User, UserGreenhouse
from smart_farm.services import sync_service as ss

BASE = datetime(2026, 1, 1, 12, 0)


@pytest.fixture()
def two_dbs(tmp_path):
    """主备两个文件型 SQLite 库（文件库保证多 engine 共享数据）。"""
    p_url, f_url = f"sqlite:///{tmp_path}/primary.db", f"sqlite:///{tmp_path}/fallback.db"
    p_eng, f_eng = create_engine(p_url), create_engine(f_url)
    Base.metadata.create_all(p_eng)
    Base.metadata.create_all(f_eng)
    p_sess, f_sess = sessionmaker(bind=p_eng, expire_on_commit=False), sessionmaker(bind=f_eng, expire_on_commit=False)
    yield p_sess, f_sess, p_url, f_url
    p_eng.dispose()
    f_eng.dispose()


# ----------------------------- 管理表按唯一键补齐 -----------------------------

def test_management_tables_merge_with_id_mapping(two_dbs):
    """备库新增的大棚/设备/用户/授权完整合并到主库，外键 id 正确映射。"""
    p_sess, f_sess, _, _ = two_dbs
    # 主库：一号棚(id=1) + 其设备
    with p_sess() as s:
        gh = repo.create_greenhouse(s, "一号棚", "东侧")
        repo.create_device(s, "主库节点", "http", "sf-p-1", greenhouse_id=gh.id)
        s.commit()

    # 备库：同样的一号棚(id=1) + 故障期间新建的四号棚(id=2) + 设备 + 用户 + 授权
    with f_sess() as s:
        repo.create_greenhouse(s, "一号棚", "东侧")
        gh4 = repo.create_greenhouse(s, "四号棚", "西侧")
        repo.create_device(s, "故障期节点", "udp", "sf-f-9", greenhouse_id=gh4.id)
        repo.create_device(s, "一号棚节点", "http", "sf-p-1", greenhouse_id=1)
        u = repo.create_user(s, "gardener2", "hash", role="user")
        s.add(UserGreenhouse(user_id=u.id, greenhouse_id=gh4.id, granted_at=datetime.now()))
        s.commit()

    stats, f2p, p2f = ss.sync_management_tables(_eng(two_dbs, 0), _eng(two_dbs, 1))
    # 备→主：四号棚 + 故障期节点 + gardener2 + 授权各 1；主→备：0
    assert stats["greenhouse"] == 1 and stats["device"] == 1
    assert stats["user"] == 1 and stats["user_greenhouse"] == 1

    with p_sess() as s:
        # 一号棚两边 id 相同（都是 1）；四号棚在主库获得新 id
        gh4_p = s.execute(select(Greenhouse).where(Greenhouse.name == "四号棚")).scalars().first()
        assert gh4_p.id == 2 and f2p[2] == 2
        # 设备外键经映射指向主库的四号棚 id
        dev = s.execute(select(Device).where(Device.device_key == "sf-f-9")).scalars().first()
        assert dev.greenhouse_id == gh4_p.id
        # 用户授权映射到主库的 (gardener2.id, 四号棚.id)
        u2 = s.execute(select(User).where(User.username == "gardener2")).scalars().first()
        links = s.execute(select(UserGreenhouse)).scalars().all()
        assert (u2.id, gh4_p.id) in [(lk.user_id, lk.greenhouse_id) for lk in links]


def _eng(two_dbs, idx):
    """从 fixture 取底层 engine（通过 session factory 的 bind）。"""
    return two_dbs[idx].kw["bind"]


def test_management_tables_bidirectional_and_idempotent(two_dbs):
    """主备互有新增 → 双向补齐；重复执行零新增（幂等）。"""
    p_sess, f_sess, _, _ = two_dbs
    with p_sess() as s:
        repo.create_greenhouse(s, "主库棚", None)
        s.commit()
    with f_sess() as s:
        repo.create_greenhouse(s, "备库棚", None)
        s.commit()

    p_eng, f_eng = _eng(two_dbs, 0), _eng(two_dbs, 1)
    stats, _, _ = ss.sync_management_tables(p_eng, f_eng)
    assert stats["greenhouse"] == 2  # 双向各 1

    stats2, _, _ = ss.sync_management_tables(p_eng, f_eng)
    assert stats2["greenhouse"] == 0 and stats2["device"] == 0  # 幂等

    for sess in (p_sess, f_sess):
        with sess() as s:
            assert s.execute(select(func.count(Greenhouse.id))).scalar() == 2


# ----------------------------- 传感器数据 + 外键映射 -----------------------------

def test_startup_sync_merges_failover_data(two_dbs):
    """端到端：故障期间备库的数据（含新大棚）经 startup_sync 完整合并回主库。"""
    p_sess, f_sess, p_url, f_url = two_dbs
    # 主库：一号棚 + 常规数据
    with p_sess() as s:
        gh = repo.create_greenhouse(s, "一号棚", None)
        repo.add_sensor_reading(s, "soil_moisture", value=30.0, timestamp=BASE, greenhouse_id=gh.id)
        s.commit()

    # 备库：一号棚数据 + 故障期间新建四号棚并写入数据（备库 id=2）
    with f_sess() as s:
        repo.create_greenhouse(s, "一号棚", None)
        repo.add_sensor_reading(s, "soil_moisture", value=31.0, timestamp=BASE, greenhouse_id=1)
        gh4 = repo.create_greenhouse(s, "四号棚", None)
        repo.add_sensor_reading(s, "soil_moisture", value=44.0, timestamp=BASE + timedelta(hours=1), greenhouse_id=gh4.id)
        s.commit()

    result = ss.startup_sync(primary_url=p_url, fallback_url=f_url)
    assert result["status"] == "ok"
    assert result["total"]["fallback_to_primary"] == 2  # 备库 2 行 → 主库
    assert result["total"]["primary_to_fallback"] == 1  # 主库 30.0 行 → 备库
    assert result["management"]["greenhouse"] == 1

    with p_sess() as s:
        gh4_p = s.execute(select(Greenhouse).where(Greenhouse.name == "四号棚")).scalars().first()
        rows = s.execute(select(SoilMoisture).where(SoilMoisture.greenhouse_id == gh4_p.id)).scalars().all()
        assert [r.value for r in rows] == [44.0]  # 外键已映射为主库的 gh4 id

    # 两边数据集一致（一号棚 30.0/31.0 + 四号棚 44.0，共 3 行）
    for sess in (p_sess, f_sess):
        with sess() as s:
            assert s.execute(select(func.count(SoilMoisture.id))).scalar() == 3


def test_startup_sync_idempotent_second_run(two_dbs):
    """第二次启动同步零迁移（数据已一致）。"""
    p_sess, f_sess, p_url, f_url = two_dbs
    with p_sess() as s:
        gh = repo.create_greenhouse(s, "一号棚", None)
        repo.add_sensor_reading(s, "soil_moisture", value=30.0, timestamp=BASE, greenhouse_id=gh.id)
        s.commit()

    first = ss.startup_sync(primary_url=p_url, fallback_url=f_url)
    assert first["total"]["primary_to_fallback"] == 1
    second = ss.startup_sync(primary_url=p_url, fallback_url=f_url)
    assert second["status"] == "ok"
    assert second["total"]["primary_to_fallback"] == 0
    assert second["total"]["fallback_to_primary"] == 0
    assert sum(second["management"].values()) == 0


def test_startup_sync_operation_logs(two_dbs):
    """操作日志表纳入时间戳增量同步。"""
    from smart_farm.data.models import OperationLog

    p_sess, f_sess, p_url, f_url = two_dbs
    with p_sess() as s:
        s.add(OperationLog(log_time=BASE, log_level="INFO", username="admin",
                           action_type="login", action_details="ok"))
        s.commit()
    with f_sess() as s:
        s.add(OperationLog(log_time=BASE + timedelta(hours=1), log_level="INFO", username="gardener",
                           action_type="login", action_details="ok"))
        s.commit()

    result = ss.startup_sync(primary_url=p_url, fallback_url=f_url)
    assert result["status"] == "ok"
    assert result["tables"]["operation_logs"]["primary_to_fallback"] == 1
    assert result["tables"]["operation_logs"]["fallback_to_primary"] == 1


# ----------------------------- 跨方言归一化 -----------------------------

def test_norm_value_absorbs_cross_dialect_precision_loss():
    """MySQL FLOAT(32位)/DATETIME(秒级) 与 SQLite(64位/微秒) 精度不对称 → 归一化后键相等。"""
    v64 = 44.4378163512          # SQLite REAL 原值
    v32 = struct.unpack("f", struct.pack("f", v64))[0]  # MySQL FLOAT 往返值
    assert v64 != v32
    assert ss._norm_value(v64) == ss._norm_value(v32)   # 归一化后一致

    ts_us = datetime(2026, 1, 1, 12, 0, 0, 289144)      # SQLite 微秒
    ts_s = datetime(2026, 1, 1, 12, 0, 0)               # MySQL 截断值
    assert ss._norm_value(ts_us) == ss._norm_value(ts_s)
    assert ss._norm_value(3) == 3 and ss._norm_value("x") == "x"


def test_sync_roundtrip_no_duplicates_across_precision(two_dbs):
    """带微秒/高精度值的行同步后，第二次同步零迁移（往返不膨胀）。"""
    p_sess, f_sess, p_url, f_url = two_dbs
    with f_sess() as s:  # 备库：模拟 SQLite 高精度原值（微秒 + float64）
        gh = repo.create_greenhouse(s, "一号棚", None)
        v = 44.4378163512
        repo.add_sensor_reading(s, "soil_moisture", value=v,
                                timestamp=datetime(2026, 1, 1, 12, 0, 0, 289144),
                                greenhouse_id=gh.id)
        s.commit()

    r1 = ss.startup_sync(primary_url=p_url, fallback_url=f_url)
    assert r1["total"]["fallback_to_primary"] == 1
    r2 = ss.startup_sync(primary_url=p_url, fallback_url=f_url)
    assert r2["total"]["primary_to_fallback"] == 0
    assert r2["total"]["fallback_to_primary"] == 0
    for sess in (p_sess, f_sess):
        with sess() as s:
            assert s.execute(select(func.count(SoilMoisture.id))).scalar() == 1


# ----------------------------- 跳过条件 -----------------------------

def test_startup_sync_skips_no_fallback(tmp_path):
    result = ss.startup_sync(primary_url=f"sqlite:///{tmp_path}/p.db", fallback_url=None)
    assert result["status"] == "skipped" and "未配置备用" in result["reason"]


def test_startup_sync_skips_same_url(tmp_path):
    url = f"sqlite:///{tmp_path}/same.db"
    result = ss.startup_sync(primary_url=url, fallback_url=url)
    assert result["status"] == "skipped" and "相同" in result["reason"]


def test_startup_sync_skips_unreachable_fallback(tmp_path):
    result = ss.startup_sync(
        primary_url=f"sqlite:///{tmp_path}/p.db",
        fallback_url="sqlite:////nonexistent-dir/x.db",
    )
    assert result["status"] == "skipped" and "不可达" in result["reason"]


def test_last_sync_result_recorded(two_dbs):
    _, _, p_url, f_url = two_dbs
    ss.startup_sync(primary_url=p_url, fallback_url=f_url)
    last = ss.last_sync_result()
    assert last["status"] == "ok" and "finished_at" in last
