"""anomaly_service 测试。"""

import numpy as np
import pandas as pd
import pytest

from smart_farm.services import anomaly_service as an


def _sample() -> pd.DataFrame:
    rng = np.random.default_rng(0)
    n = 200
    vals = rng.normal(20, 2, n)
    vals[5] = 200.0  # 高异常
    vals[50] = -100.0  # 低异常
    return pd.DataFrame({"temp": vals, "hum": rng.normal(50, 5, n)})


def test_iqr_flags_extremes():
    df = _sample()
    mask = an.detect_outliers_iqr(df, "temp")
    assert mask.iloc[5] and mask.iloc[50]
    assert mask.sum() <= 10  # 不应误报太多


def test_zscore_no_index_misalignment():
    """回归测试：旧版会错误地用 isin 匹配 dropna 后的索引导致漏报/误报。"""
    df = _sample()
    # 人为在前部制造 NaN，确保 dropna 后索引与原始错位
    df.loc[0:3, "temp"] = np.nan
    mask = an.detect_outliers_zscore(df, "temp", threshold=3.0)
    # 即便前部有 NaN，异常点仍应被正确标记
    assert mask.iloc[5] and mask.iloc[50]
    # NaN 位置不应被标记为异常
    assert not mask.iloc[0:4].any()


def test_zscore_threshold_control():
    df = _sample()
    mask = an.detect_outliers_zscore(df, "temp", threshold=0.1)  # 极严阈值 -> 大量异常
    assert mask.sum() > 5
    wide = an.detect_outliers_zscore(df, "temp", threshold=100.0)  # 极宽阈值 -> 无异常
    assert wide.sum() == 0


def test_isolation_forest_marks_anomalies():
    pytest.importorskip("sklearn")
    df = _sample()
    mask = an.detect_outliers_isolation_forest(df, ["temp", "hum"])
    assert mask.iloc[5] or mask.iloc[50]  # 至少一处极端点被判异常
    assert mask.dtype == bool


def test_detect_anomalies_per_column():
    df = _sample()
    res = an.detect_anomalies(df, method="iqr")
    assert "temp" in res and "hum" in res
    assert 5 in res["temp"] and 50 in res["temp"]


def test_remove_anomalies_drops_rows():
    df = _sample()
    anomalies = {"temp": [5, 50]}
    out = an.remove_anomalies(df, anomalies)
    assert len(out) == len(df) - 2
    # 两个极端值（200 与 -100）应已被移除
    assert 200.0 not in out["temp"].values
    assert -100.0 not in out["temp"].values


def test_anomaly_summary():
    anomalies = {"temp": [5, 50], "hum": [5]}
    summary = an.get_anomaly_summary(anomalies)
    assert summary["temp"]["count"] == 2
    assert summary["hum"]["count"] == 1
    assert 0 <= summary["temp"]["percentage"] <= 100
