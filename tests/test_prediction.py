"""prediction_service 测试。naive 路径无需重依赖；SARIMA 用 importorskip 守卫。"""

import numpy as np
import pandas as pd
import pytest

from smart_farm.services import prediction_service as ps


def _series(n: int = 20, start="2026-01-01") -> tuple[list[float], list[pd.Timestamp]]:
    values = [10.0 + i * 0.5 + (0.0 if i % 3 else 2.0) for i in range(n)]
    timestamps = pd.date_range(start, periods=n, freq="D").to_list()
    return values, timestamps


def test_naive_last():
    values, ts = _series()
    res = ps.naive_forecast(values, ts, prediction_days=5, method="last")
    assert res.method == "naive(last)"
    assert len(res.forecast) == 5
    assert res.forecast["yhat"].iloc[0] == pytest.approx(values[-1])
    assert (res.forecast["yhat_lower"] <= res.forecast["yhat"]).all()
    assert (res.forecast["yhat_upper"] >= res.forecast["yhat"]).all()
    assert set(res.forecast.columns) >= {"ds", "yhat", "yhat_lower", "yhat_upper"}
    assert res.history["y"].tolist() == values


def test_naive_mean():
    values, ts = _series()
    res = ps.naive_forecast(values, ts, prediction_days=3, method="mean")
    assert res.method == "naive(mean)"
    assert res.forecast["yhat"].iloc[0] == pytest.approx(np.mean(values))


def test_naive_drift():
    values, ts = _series()
    res = ps.naive_forecast(values, ts, prediction_days=4, method="drift")
    assert res.method == "naive(drift)"
    # 单调递增序列外推应大于末值
    assert res.forecast["yhat"].iloc[-1] > res.forecast["yhat"].iloc[0]


def test_naive_single_point_no_band():
    res = ps.naive_forecast([5.0], [pd.Timestamp("2026-01-01")], prediction_days=2)
    assert res.forecast["yhat_lower"].iloc[0] == pytest.approx(5.0)
    assert res.forecast["yhat_upper"].iloc[0] == pytest.approx(5.0)


def test_forecast_dispatch_default_naive():
    values, ts = _series()
    res = ps.forecast(values, ts, method="naive", prediction_days=7)
    assert res.method.startswith("naive")


def test_forecast_unknown_method_falls_back_to_naive():
    values, ts = _series()
    res = ps.forecast(values, ts, method="whatever", prediction_days=7)
    assert res.method.startswith("naive")


def test_sarima_forecast():
    pytest.importorskip("statsmodels")
    values, ts = _series(n=30)
    res = ps.sarima_forecast(values, ts, prediction_days=5)
    assert res.method == "sarima"
    assert len(res.forecast) == 5
    assert (res.forecast["yhat_lower"] <= res.forecast["yhat"]).all()


def test_prophet_not_installed_raises():
    try:
        pytest.importorskip("prophet")
    except Exception:
        # 未安装 prophet：应抛出带安装提示的 RuntimeError
        with pytest.raises(RuntimeError, match="prophet"):
            ps.prophet_forecast([1.0, 2.0], [pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-02")])
        return
    # 已安装 prophet：跑真实预测
    values, ts = _series(n=30)
    res = ps.prophet_forecast(values, ts, prediction_days=5)
    assert res.method == "prophet"
    assert len(res.forecast) == 5
