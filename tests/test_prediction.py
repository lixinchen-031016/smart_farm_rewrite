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


# ----------------------------- 优化修复回归 -----------------------------


def test_prepare_series_keeps_minute_sampling():
    """修复：非整点（分钟级）采样按 3H 桶聚合保留，而非被网格丢弃。"""
    ts = pd.date_range("2026-01-01 09:10", periods=48 * 20, freq="10min")  # 跨 8 天
    vals = [10 + i for i in range(len(ts))]
    df = ps.prepare_series(vals, ts)
    # 8 天 × 8 桶 ≈ 64 桶，绝大部分有值（修复前分钟级数据几乎全被丢弃）
    assert len(df) >= 50


def test_naive_empty_input_no_crash():
    """修复：空输入返回空结果而非 IndexError。"""
    res = ps.naive_forecast([], [])
    assert res.forecast.empty
    assert "无有效历史数据" in res.explanation


def test_score_prediction_negative_mean():
    """修复：负均值序列不再恒得高分。"""
    values = [-5.0 - i * 0.1 for i in range(50)]
    result = ps.score_prediction(10.0, values)
    assert result["score"] < 6  # 修复前恒 6 分


def test_multivariate_dropna_insufficient():
    """修复：dropna 后样本不足抛明确异常而非 sklearn 内部错误。"""
    pytest.importorskip("sklearn")
    ts = pd.date_range("2026-01-01", periods=30, freq="3h").to_list()
    try:
        ps.multivariate_forecast(
            [20.0] * 30, [60.0] * 30, [float("nan")] * 30, ts, prediction_days=1
        )
        assert False, "应抛出 ValueError"
    except ValueError as e:
        assert "有效样本不足" in str(e)


# ----------------------------- 网格搜索 / 短期 / 残差混合预测 -----------------------------


def test_grid_search_sarima_params():
    """网格搜索返回候选集中 AIC 最优参数。"""
    pytest.importorskip("statsmodels")
    values, ts = _series3h(n=80)
    series = pd.Series(values, index=pd.DatetimeIndex(ts))
    grid = [
        ((1, 0, 0), (1, 0, 0, 8)),
        ((0, 1, 1), (1, 0, 0, 8)),
    ]
    order, seasonal, aic = ps.grid_search_sarima_params(series, param_grid=grid, maxiter=50)
    assert (order, seasonal) in grid
    assert np.isfinite(aic)


def test_grid_search_sarima_all_fail_raises():
    pytest.importorskip("statsmodels")
    # 显式空网格（不回退默认候选）→ 全部候选失败路径
    series = pd.Series([1.0, 2.0, 3.0], index=pd.date_range("2026-01-01", periods=3, freq="h"))
    with pytest.raises(RuntimeError, match="网格搜索失败"):
        ps.grid_search_sarima_params(series, param_grid=[], maxiter=5)


def test_short_term_forecast_structure():
    """短期预测：输出 hours/3 个点，必有 yhat 与置信带。"""
    values, ts = _series3h(n=100)
    res = ps.short_term_forecast(values, ts, hours=12)
    assert len(res.forecast) == 4
    assert res.method in ("prophet-short", "naive-short")
    assert set(res.forecast.columns) >= {"ds", "yhat", "yhat_lower", "yhat_upper"}
    assert (res.forecast["yhat_lower"] <= res.forecast["yhat"]).all()


def test_short_term_forecast_fallback():
    """历史不足 2 桶时回退 naive，不崩溃。"""
    res = ps.short_term_forecast([5.0], [pd.Timestamp("2026-01-01")], hours=6)
    assert res.method.startswith("naive")


def test_residual_hybrid_forecast():
    """残差分解混合：两阶段 SARIMA→Prophet 残差修正。"""
    pytest.importorskip("statsmodels")
    pytest.importorskip("prophet")
    values, ts = _series3h(n=120)
    res = ps.residual_hybrid_forecast(values, ts, prediction_days=2)
    assert len(res.forecast) == 2 * ps.POINTS_PER_DAY
    assert set(res.forecast.columns) >= {"ds", "yhat", "yhat_lower", "yhat_upper"}
    assert res.method.startswith("residual") or "hybrid" in res.method


def test_hybrid_forecast_grid_search_toggle():
    """use_grid_search=True 时走网格搜索分支仍产出合法结果。"""
    pytest.importorskip("statsmodels")
    values, ts = _series3h(n=80)
    res = ps.hybrid_forecast(values, ts, prediction_days=1, use_grid_search=True)
    assert len(res.forecast) == ps.POINTS_PER_DAY
    assert set(res.forecast.columns) >= {"ds", "yhat"}
