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


# ----------------------------- 阶段 D：3H 采样 / 混合 / 多变量 / 评分 -----------------------------


def _series3h(n: int = 80, start="2026-01-01"):
    """3H 对齐的时序数据（对齐旧版采样频率）。"""
    timestamps = pd.date_range(start, periods=n, freq="3h").to_list()
    values = [10.0 + i * 0.05 + np.sin(i / 6.0) for i in range(n)]
    return values, timestamps


def test_prepare_series_resamples_and_interpolates():
    values, ts = _series3h(n=50)
    df = ps.prepare_series(values, ts)
    assert isinstance(df.index, pd.DatetimeIndex)
    # 插值后无缺失
    assert df["y"].isnull().sum() == 0
    assert len(df) >= 40


def test_prepare_series_caps_extreme():
    # 极端值占比 <1% 时，>99 分位修剪生效（对齐旧版异常值处理）
    values = [10.0] * 100 + [99999.0]
    ts = pd.date_range("2026-01-01", periods=101, freq="3h").to_list()
    df = ps.prepare_series(values, ts)
    assert df["y"].max() < 1000  # 99 分位修剪生效


def test_hybrid_falls_back_to_single_or_naive():
    values, ts = _series3h()
    res = ps.hybrid_forecast(values, ts, prediction_days=2)
    assert len(res.forecast) == 2 * ps.POINTS_PER_DAY  # 每天 8 点
    assert res.method in ("hybrid(prophet+sarima)", "sarima", "prophet", "naive(last)")
    assert set(res.forecast.columns) >= {"ds", "yhat", "yhat_lower", "yhat_upper"}


def test_multivariate_forecast():
    pytest.importorskip("sklearn")
    ts = pd.date_range("2026-01-01", periods=50, freq="3h").to_list()
    temp = [20.0 + i * 0.1 for i in range(50)]
    humid = [60.0 - i * 0.05 for i in range(50)]
    light = [3000.0 + i * 10 for i in range(50)]
    merged, fc, fi, explanation = ps.multivariate_forecast(temp, humid, light, ts, prediction_days=2)
    assert len(fc) == 2 * ps.POINTS_PER_DAY
    assert set(fc.columns) >= {"ds", "temperature", "humidity"}
    assert "temp_importance" in fi.columns
    assert "温度" in explanation or "特征" in explanation
    assert len(merged) >= 20


def test_multivariate_insufficient_data():
    pytest.importorskip("sklearn")
    ts = pd.date_range("2026-01-01", periods=10, freq="3h").to_list()
    try:
        ps.multivariate_forecast([1.0] * 10, [2.0] * 10, [3.0] * 10, ts, prediction_days=1)
        assert False, "应抛出 ValueError"
    except ValueError:
        pass


def test_score_prediction():
    values = [10.0 + np.sin(i / 5.0) for i in range(50)]
    result = ps.score_prediction(0.2, values)
    assert result["score"] <= 6
    assert result["level"] in ("高", "中", "低")
    assert "RMSE" in result["rmse_note"]
    none_result = ps.score_prediction(None, [])
    assert none_result["score"] == 0
