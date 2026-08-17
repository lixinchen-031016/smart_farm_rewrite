from datetime import datetime, timedelta

from smart_farm.services import decision_service as ds
from smart_farm.services.decision_service import SensorPoint


def test_trend_per_hour_uses_real_time():
    base = datetime(2026, 1, 1, 0, 0)
    pts = [SensorPoint(base + timedelta(hours=i), 10.0 + i) for i in range(5)]
    slope = ds.calculate_trend_per_hour(pts)
    # 每小时 +1，斜率应约等于 1
    assert abs(slope - 1.0) < 1e-6


def test_decision_low_soil_moisture():
    engine = ds.DecisionEngine()
    recs = engine.evaluate({"soil_moisture": 20.0, "temperature": 22.0, "humidity": 55.0, "light_intensity": 5000.0},
                           {"soil_moisture": -0.5})
    types = [r.type for r in recs]
    assert "soil_moisture" in types
    soil = next(r for r in recs if r.type == "soil_moisture")
    assert soil.priority == "high"


def test_decision_all_normal():
    engine = ds.DecisionEngine()
    recs = engine.evaluate({"soil_moisture": 45.0, "temperature": 22.0, "humidity": 55.0, "light_intensity": 5000.0},
                           {})
    # 光照正常会被记录，其余无越界
    assert all(r.priority == "low" for r in recs)
