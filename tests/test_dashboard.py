"""dashboard_service 与 fetch_data_in_bulk 测试。"""
from datetime import datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from smart_farm.data import repositories as repo
from smart_farm.data.models import Base
from smart_farm.services import dashboard_service as ds
from smart_farm.services import system_service as ss


def _fill_sensor(session, metric, ts, value=None, **fields):
    repo.add_sensor_reading(session, metric, value=value, timestamp=ts, **fields)


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()
    engine.dispose()


# ---------------- dashboard_service ----------------

def test_crop_stage_recommendations():
    growth = ds.get_crop_stage_recommendations("growth")
    assert growth["name"] == "生长期"
    assert growth["temperature"] == {"min": 20, "max": 28, "optimal": 24}
    fruiting = ds.get_crop_stage_recommendations("fruiting")
    assert fruiting["light_intensity"]["optimal"] == 30000
    # 未知阶段回退生长期
    assert ds.get_crop_stage_recommendations("unknown")["name"] == "生长期"
    assert ds.get_crop_stage_recommendations(None)["name"] == "生长期"


def test_recommendations_to_thresholds():
    rec = ds.get_crop_stage_recommendations("flowering")
    th = ds.recommendations_to_thresholds(rec)
    assert th["temperature"] == {"min": 18, "max": 26}
    assert th["light_intensity"] == {"min": 2000, "max": 45000}


def test_default_preferences():
    prefs = ds.default_preferences("alice")
    assert prefs["layout"] == "grid"
    assert prefs["crop_stage"] == "growth"
    assert prefs["custom_thresholds"]["temperature"] == {"min": 20, "max": 30}
    assert prefs["custom_thresholds"]["light_intensity"] == {"min": 1000, "max": 50000}
    # 深拷贝：修改副本不影响默认
    prefs["custom_thresholds"]["temperature"]["min"] = 99
    assert ds.default_preferences("bob")["custom_thresholds"]["temperature"]["min"] == 20


def test_is_value_alert():
    assert ds.is_value_alert("temperature", 15.0) is True
    assert ds.is_value_alert("temperature", 35.0) is True
    assert ds.is_value_alert("temperature", 25.0) is False
    assert ds.is_value_alert("temperature", None) is False
    assert ds.is_value_alert("unknown_metric", 10.0) is False
    # 自定义阈值
    custom = {"temperature": {"min": 10, "max": 20}}
    assert ds.is_value_alert("temperature", 25.0, custom) is True
    assert ds.is_value_alert("temperature", 15.0, custom) is False


# ---------------- fetch_data_in_bulk ----------------

def test_fetch_data_in_bulk_joins_all_tables(session):
    base = datetime(2026, 1, 1, 0, 0)
    for i in range(3):
        ts = base + timedelta(hours=i)
        _fill_sensor(session, "air_temperature_humidity", ts, temperature=20.0 + i, humidity=50.0 + i)
        _fill_sensor(session, "soil_moisture", ts, value=30.0 + i)
        _fill_sensor(session, "soil_nutrient", ts, value=40.0 + i)
        _fill_sensor(session, "light_intensity", ts, value=1000.0 + i)
    session.commit()

    df = repo.fetch_data_in_bulk(session)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 3
    assert list(df.columns) == [
        "timestamp", "temperature", "humidity",
        "soil_moisture", "soil_nutrient", "light_intensity",
    ]
    assert df["temperature"].tolist() == [20.0, 21.0, 22.0]


def test_fetch_data_in_bulk_time_window(session):
    base = datetime(2026, 1, 1, 0, 0)
    for i in range(5):
        ts = base + timedelta(days=i)
        _fill_sensor(session, "air_temperature_humidity", ts, temperature=20.0 + i, humidity=50.0)
    session.commit()

    start = base + timedelta(days=1)
    end = base + timedelta(days=3)
    df = repo.fetch_data_in_bulk(session, start=start, end=end)
    assert len(df) == 3  # day1..day3 共 3 条


def test_fetch_data_in_bulk_missing_join_tables(session):
    # 只有空气温湿度表有数据，其余表为空 → 仍返回行，其余列 NaN
    ts = datetime(2026, 1, 1, 0, 0)
    _fill_sensor(session, "air_temperature_humidity", ts, temperature=25.0, humidity=60.0)
    session.commit()
    df = repo.fetch_data_in_bulk(session)
    assert len(df) == 1
    assert df["soil_moisture"].iloc[0] is None or pd.isna(df["soil_moisture"].iloc[0])


# ----------------------------- system_service -----------------------------


def test_performance_recommendations_thresholds():
    recs = ss.get_performance_recommendations(90, 50, 40)
    assert any("CPU" in r for r in recs)
    recs = ss.get_performance_recommendations(50, 90, 40)
    assert any("内存" in r for r in recs)
    recs = ss.get_performance_recommendations(50, 50, 90)
    assert any("磁盘" in r for r in recs)
    recs = ss.get_performance_recommendations(30, 30, 30)
    assert any("良好" in r for r in recs)


def test_system_metrics_collect():
    # psutil 已装或未装都应安全返回
    metrics = ss.collect_system_metrics()
    if ss.is_psutil_available():
        assert "cpu_percent" in metrics
        assert "memory_percent" in metrics
        assert "disk_percent" in metrics
    else:
        assert metrics == {}


def test_fetch_data_in_bulk_offsets_match(session):
    """修复：各传感器采样时刻有秒级偏差时仍能窗口匹配（旧版等值 JOIN 丢行）。"""
    base = datetime(2026, 1, 1, 12, 0, 0)
    _fill_sensor(session, "air_temperature_humidity", base, temperature=20.0, humidity=50.0)
    # 其他表时间偏移 60 秒
    _fill_sensor(session, "soil_moisture", base + timedelta(seconds=60), value=30.0)
    _fill_sensor(session, "soil_nutrient", base + timedelta(seconds=60), value=40.0)
    _fill_sensor(session, "light_intensity", base + timedelta(seconds=60), value=1000.0)
    session.commit()
    df = repo.fetch_data_in_bulk(session)
    assert len(df) == 1
    assert df["soil_moisture"].iloc[0] == 30.0
    assert df["light_intensity"].iloc[0] == 1000.0
