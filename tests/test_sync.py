"""sync_service 测试（双内存 SQLite 库验证双向增量同步）。"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from smart_farm.data import repositories as repo
from smart_farm.data.models import Base
from smart_farm.services import sync_service as ss


@pytest.fixture()
def dbs():
    """返回 (cloud_session_factory, local_session_factory, sync)。"""
    cloud_engine = create_engine("sqlite:///:memory:")
    local_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(cloud_engine)
    Base.metadata.create_all(local_engine)
    cloud_sess = sessionmaker(bind=cloud_engine, expire_on_commit=False)
    local_sess = sessionmaker(bind=local_engine, expire_on_commit=False)
    sync = ss.DatabaseSync("sqlite:///:memory:cloud", "sqlite:///:memory:local")
    # 覆盖 engine（内存库 URL 需指向已建表的 engine）
    sync._cloud_engine = cloud_engine
    sync._local_engine = local_engine
    yield cloud_sess, local_sess, sync
    cloud_engine.dispose()
    local_engine.dispose()


def _add(sess_factory, metric, ts, value=None, **fields):
    with sess_factory() as s:
        repo.add_sensor_reading(s, metric, value=value, timestamp=ts, **fields)
        s.commit()


def test_validate_database_inputs():
    assert ss.validate_database_inputs("localhost", 3306, "farm", "root", "pw") == []
    assert ss.validate_database_inputs("bad host!", 70000, "farm", "root", "pw")
    assert ss.validate_database_inputs("localhost", 3306, "bad-name!", "root", "pw")


def test_build_mysql_url_escapes_password():
    url = ss.build_mysql_url("localhost", 3306, "farm", "root", "p@ss:word")
    assert "p%40ss%3Aword" in url
    assert url.startswith("mysql+pymysql://root:")


def test_sync_cloud_to_local(dbs):
    cloud_sess, local_sess, sync = dbs
    base = datetime(2026, 1, 1)
    _add(cloud_sess, "soil_moisture", base, 30.0)
    _add(cloud_sess, "soil_moisture", base + timedelta(hours=1), 32.0)
    _add(local_sess, "soil_moisture", base, 30.0)  # 本地已有相同最早数据

    stats = sync.sync_table_data("soil_moisture")
    assert stats["cloud_to_local"] == 1  # 只有 1 小时后那条新数据被拉取
    with local_sess() as s:
        rows = repo.get_sensor_readings(s, "soil_moisture", limit=10)
        assert len(rows) == 2


def test_sync_local_to_cloud(dbs):
    cloud_sess, local_sess, sync = dbs
    base = datetime(2026, 1, 1)
    _add(local_sess, "light_intensity", base, 1000.0)
    _add(local_sess, "light_intensity", base + timedelta(hours=2), 2000.0)
    _add(cloud_sess, "light_intensity", base, 1000.0)

    stats = sync.sync_table_data("light_intensity")
    assert stats["local_to_cloud"] == 1
    with cloud_sess() as s:
        rows = repo.get_sensor_readings(s, "light_intensity", limit=10)
        assert len(rows) == 2


def test_sync_equal_timestamp_no_conflict(dbs):
    cloud_sess, local_sess, sync = dbs
    base = datetime(2026, 1, 1)
    _add(cloud_sess, "soil_nutrient", base, 40.0)
    _add(local_sess, "soil_nutrient", base, 40.0)
    stats = sync.sync_table_data("soil_nutrient")
    assert stats["cloud_to_local"] == 0
    assert stats["local_to_cloud"] == 0
    assert stats["conflicts"] == 0  # 跳过策略


def test_sync_all_data(dbs):
    cloud_sess, local_sess, sync = dbs
    base = datetime(2026, 1, 1)
    _add(cloud_sess, "air_temperature_humidity", base, temperature=20.0, humidity=50.0)
    _add(cloud_sess, "soil_moisture", base, 30.0)
    _add(cloud_sess, "soil_nutrient", base, 40.0)
    _add(cloud_sess, "light_intensity", base, 1000.0)
    stats_list = sync.sync_all_data()
    assert len(stats_list) == 4
    total = sum(s["cloud_to_local"] for s in stats_list)
    assert total == 4


def test_sync_same_timestamp_batch(dbs):
    """修复：同一最大时间戳下的新增批次不再漏同步（>= 而非 >）。"""
    cloud_sess, local_sess, sync = dbs
    base = datetime(2026, 1, 1, 12, 0)
    # 本地已有 12:00 的数据
    _add(local_sess, "soil_moisture", base, 30.0)
    # 云端在相同 12:00 又有一条（同刻多批次）
    _add(cloud_sess, "soil_moisture", base, 33.0)
    stats = sync.sync_table_data("soil_moisture")
    assert stats["cloud_to_local"] == 1
    with local_sess() as s:
        rows = repo.get_sensor_readings(s, "soil_moisture", limit=10)
        assert len(rows) == 2


def test_sync_idempotent_no_duplicates(dbs):
    """修复：重复执行同步不产生重复数据（幂等去重）。"""
    cloud_sess, local_sess, sync = dbs
    base = datetime(2026, 1, 1)
    _add(cloud_sess, "soil_nutrient", base, 40.0)
    _add(cloud_sess, "soil_nutrient", base + timedelta(hours=1), 41.0)
    sync.sync_table_data("soil_nutrient")
    sync.sync_table_data("soil_nutrient")  # 二次同步
    with local_sess() as s:
        rows = repo.get_sensor_readings(s, "soil_nutrient", limit=10)
        assert len(rows) == 2  # 不重复


def test_validate_port_non_numeric():
    errors = ss.validate_database_inputs("localhost", "abc", "farm", "root", "pw")
    assert any("数字" in e for e in errors)
