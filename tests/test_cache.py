"""app/cache.py 缓存封装测试：空结果列结构保证（回归：sort_values KeyError）。"""

from contextlib import contextmanager
from datetime import datetime

from smart_farm.app import cache as app_cache


def test_cached_sensor_df_empty_has_columns(monkeypatch):
    """查询无结果时也必须返回带 timestamp/value 列的空表。

    回归：旧实现 `pd.DataFrame([])` 生成无列空表，
    dashboard `sort_values("timestamp")` 抛 KeyError。
    """

    @contextmanager
    def fake_session():
        yield None

    monkeypatch.setattr(app_cache, "get_session", fake_session)
    monkeypatch.setattr(
        app_cache.repo, "get_sensor_readings", lambda *a, **k: []
    )
    df = app_cache.cached_sensor_df(
        "soil_moisture", "value", datetime.now().isoformat(), greenhouse_id=1
    )
    assert df.empty
    assert list(df.columns) == ["timestamp", "value"]
    df.sort_values("timestamp")  # 不再抛 KeyError
