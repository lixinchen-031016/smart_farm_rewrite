"""预测服务。

重写要点（对照旧版）：
- 删除 `torch` / `gpu_accelerator` 依赖与 GPU 分支。
- 重模型（Prophet / SARIMA）**懒加载**，未安装时给出明确错误而非崩溃。
- 始终提供 `naive`（朴素）方法兜底，保证 UI 在无重依赖时也能演示。
- 设计为可被后台线程/任务队列调用（长任务异步化由 UI 层负责）。
"""

from dataclasses import dataclass
from typing import Callable, Optional, Sequence

import numpy as np
import pandas as pd

ProgressCallback = Callable[[float, str], None]


def _emit(callback: Optional[ProgressCallback], pct: float, stage: str) -> None:
    """安全触发进度回调（回调异常不影响预测本身）。"""
    if callback is None:
        return
    try:
        callback(pct, stage)
    except Exception:  # noqa: BLE001 进度回调失败不应中断预测
        pass


@dataclass
class ForecastResult:
    history: pd.DataFrame  # columns: ds, y
    forecast: pd.DataFrame  # columns: ds, yhat, yhat_lower, yhat_upper
    method: str
    rmse: Optional[float] = None
    explanation: str = ""


def _to_tsdf(values: Sequence[float], timestamps: Sequence[pd.Timestamp]) -> pd.DataFrame:
    return pd.DataFrame({"ds": pd.to_datetime(list(timestamps)), "y": list(values)})


def naive_forecast(
    values: Sequence[float],
    timestamps: Sequence[pd.Timestamp],
    prediction_days: int = 7,
    method: str = "last",
) -> ForecastResult:
    """朴素预测：method='last' 用末值，'mean' 用均值，'drift' 用末段斜率外推。

    修复：空输入时返回空结果而非 IndexError 崩溃（旧版 `values[-1]` 越界）。
    """
    clean_values = [v for v in values if v is not None]
    clean_ts = [t for t in timestamps if t is not None]
    if not clean_values:
        return ForecastResult(
            history=_to_tsdf([], []),
            forecast=pd.DataFrame(
                columns=["ds", "yhat", "yhat_lower", "yhat_upper"]
            ),
            method=f"naive({method})",
            explanation="无有效历史数据，无法预测。",
        )

    hist = _to_tsdf(clean_values, clean_ts)
    last_ts = hist["ds"].max()
    future_ts = [last_ts + pd.Timedelta(days=i + 1) for i in range(prediction_days)]

    if method == "mean":
        base = float(np.mean(clean_values))
    elif method == "drift":
        x = np.arange(len(clean_values))
        slope = float(np.polyfit(x, np.asarray(clean_values, dtype=float), 1)[0]) if len(clean_values) > 1 else 0.0
        base = float(clean_values[-1])
    else:  # last
        base = float(clean_values[-1])
        slope = 0.0

    if method == "drift":
        yhat = [base + slope * (i + 1) for i in range(prediction_days)]
    else:
        yhat = [base] * prediction_days

    band = float(np.std(clean_values)) if len(clean_values) > 1 else 0.0
    fc = pd.DataFrame(
        {
            "ds": future_ts,
            "yhat": yhat,
            "yhat_lower": [v - 1.96 * band for v in yhat],
            "yhat_upper": [v + 1.96 * band for v in yhat],
        }
    )
    return ForecastResult(
        history=hist,
        forecast=fc,
        method=f"naive({method})",
        explanation="朴素预测（无需重依赖）；Prophet/SARIMA 为内置必选依赖，可在模型选择中直接使用。",
    )


def prophet_forecast(
    values: Sequence[float],
    timestamps: Sequence[pd.Timestamp],
    prediction_days: int = 7,
    changepoint_prior_scale: float = 0.05,
    seasonality_prior_scale: float = 10.0,
    progress_callback: Optional[ProgressCallback] = None,
) -> ForecastResult:
    """Prophet 预测（懒加载，未安装 prophet 时抛出明确异常）。

    修复：支持 changepoint/seasonality 参数（UI 滑块此前无效）。
    """
    try:
        from prophet import Prophet  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "未安装 prophet。请先安装项目完整依赖：`uv pip install -e .`。"
        ) from exc

    _emit(progress_callback, 0.2, "Prophet 拟合中")
    hist = _to_tsdf(values, timestamps)
    m = Prophet(
        yearly_seasonality=False,
        weekly_seasonality=True,
        daily_seasonality=True,
        seasonality_mode="additive",
        changepoint_prior_scale=changepoint_prior_scale,
        seasonality_prior_scale=seasonality_prior_scale,
    )
    m.fit(hist)
    future = m.make_future_dataframe(periods=prediction_days, freq="D")
    pred = m.predict(future)
    fc = pred[["ds", "yhat", "yhat_lower", "yhat_upper"]].tail(prediction_days).reset_index(drop=True)
    _emit(progress_callback, 1.0, "完成")
    return ForecastResult(
        history=hist,
        forecast=fc,
        method="prophet",
        explanation="Prophet 加法时序模型，自动刻画趋势与季节性。",
    )


def sarima_forecast(
    values: Sequence[float], timestamps: Sequence[pd.Timestamp], prediction_days: int = 7
) -> ForecastResult:
    """SARIMA 预测（懒加载，未安装 statsmodels 时抛出明确异常）。"""
    try:
        from statsmodels.tsa.statespace.sarimax import SARIMAX  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "未安装 statsmodels。请先安装项目完整依赖：`uv pip install -e .`。"
        ) from exc

    hist = _to_tsdf(values, timestamps).set_index("ds")["y"]
    model = SARIMAX(hist, order=(1, 1, 1), seasonal_order=(1, 1, 1, 7), enforce_stationarity=False)
    fit = model.fit(disp=False)
    pred = fit.get_forecast(steps=prediction_days)
    mean = pred.predicted_mean
    conf = pred.conf_int()
    fc = pd.DataFrame(
        {
            "ds": [hist.index.max() + pd.Timedelta(days=i + 1) for i in range(prediction_days)],
            "yhat": mean.values,
            "yhat_lower": conf.iloc[:, 0].values,
            "yhat_upper": conf.iloc[:, 1].values,
        }
    )
    return ForecastResult(
        history=_to_tsdf(values, timestamps),
        forecast=fc,
        method="sarima",
        explanation="SARIMA(1,1,1)(1,1,1,7) 差分时序模型。",
    )


def forecast(
    values: Sequence[float],
    timestamps: Sequence[pd.Timestamp],
    method: str = "naive",
    prediction_days: int = 7,
) -> ForecastResult:
    """统一入口：method ∈ {naive, prophet, sarima}。"""
    method = (method or "naive").lower()
    if method == "prophet":
        return prophet_forecast(values, timestamps, prediction_days)
    if method == "sarima":
        return sarima_forecast(values, timestamps, prediction_days)
    return naive_forecast(values, timestamps, prediction_days)


# =========================================================================
# 阶段 D 复刻：3H 采样 / 混合模型 / 多变量 / 综合评分（对齐旧版 predictions.py）
# =========================================================================

SAMPLING_FREQ = "3h"  # 每天 8 个点（每 3 小时一个），对齐旧版 freq='3H'
POINTS_PER_DAY = 8


def prepare_series(values: Sequence[float], timestamps: Sequence[pd.Timestamp]) -> pd.DataFrame:
    """数据预处理：3H 重采样聚合 + 线性插值 + 99 分位修剪。

    修复：旧版用 `asfreq("3h")` 把非整点时间戳全部丢弃（分钟级采样只剩 2/20 条），
    导致混合模型静默回退 naive。改为 `resample("3h").mean()` 按 3H 桶聚合，
    保留全部数据信息。

    Returns:
        DataFrame(index=ds, columns=[y])，时间索引为 3H 对齐（00/03/06/09... 点）。
    """
    df = pd.DataFrame({"ds": pd.to_datetime(list(timestamps)), "y": list(values)})
    df = df.dropna(subset=["y"]).set_index("ds").sort_index()
    if df.empty:
        return df
    # 3H 桶聚合（每个桶取均值；空桶为 NaN 待插值）
    df = df.resample(SAMPLING_FREQ).mean()
    df["y"] = df["y"].interpolate(limit_direction="both")  # 线性插值补缺
    if df["y"].notna().sum() > 0:
        cap = df["y"].quantile(0.99)  # >99 分位替换为中位数（异常值处理）
        median = df["y"].median()
        df["y"] = df["y"].where(df["y"] <= cap, median)
    df = df.dropna()
    return df


SARIMA_PARAM_GRID: tuple[tuple[tuple[int, int, int], tuple[int, int, int, int]], ...] = (
    ((1, 1, 1), (1, 1, 1, 24)),
    ((2, 1, 1), (1, 1, 1, 24)),
    ((1, 1, 2), (1, 1, 1, 24)),
    ((1, 1, 1), (1, 1, 2, 24)),
    ((2, 1, 2), (1, 1, 1, 24)),
)


def grid_search_sarima_params(
    series: pd.Series,
    param_grid: Optional[
        Sequence[tuple[tuple[int, int, int], tuple[int, int, int, int]]]
    ] = None,
    maxiter: int = 100,
) -> tuple[tuple[int, int, int], tuple[int, int, int, int], float]:
    """SARIMA 参数网格搜索（按 AIC 选优，对齐旧版 grid_search_sarima_params）。

    修复：此前 `hybrid_forecast` 的 `use_grid_search` 参数存在但从未生效（死参数），
    SARIMA 固定 (1,1,1)(1,1,1,24)。现在搜索真正执行。

    Returns:
        (best_order, best_seasonal_order, best_aic)
    Raises:
        RuntimeError: statsmodels 未安装或全部候选拟合失败。
    """
    from statsmodels.tsa.statespace.sarimax import SARIMAX  # type: ignore

    best: Optional[tuple[tuple[int, int, int], tuple[int, int, int, int], float]] = None
    for order, seasonal in (param_grid if param_grid is not None else SARIMA_PARAM_GRID):
        try:
            fit = SARIMAX(
                series,
                order=order,
                seasonal_order=seasonal,
                enforce_stationarity=False,
                enforce_invertibility=False,
            ).fit(disp=False, maxiter=maxiter)
            aic = float(fit.aic)
            if np.isfinite(aic) and (best is None or aic < best[2]):
                best = (order, seasonal, aic)
        except Exception:  # noqa: BLE001 单个候选失败跳过，不影响其余
            continue
    if best is None:
        raise RuntimeError("SARIMA 网格搜索失败：全部候选参数均无法拟合。")
    return best


def _rmse(actual: np.ndarray, pred: np.ndarray) -> float:
    return float(np.sqrt(np.mean((actual - pred) ** 2)))


def _mae(actual: np.ndarray, pred: np.ndarray) -> float:
    return float(np.mean(np.abs(actual - pred)))


def _mape(actual: np.ndarray, pred: np.ndarray) -> float:
    denom = np.where(np.abs(actual) < 1e-9, 1e-9, actual)
    return float(np.mean(np.abs((actual - pred) / denom)) * 100)


def short_term_forecast(
    values: Sequence[float],
    timestamps: Sequence[pd.Timestamp],
    hours: int = 24,
    progress_callback: Optional[ProgressCallback] = None,
) -> ForecastResult:
    """短期预测线（仪表板趋势叠加，对齐旧库 Prophet 短期预测叠加）。

    3H 采样 + Prophet 外推 hours/3 个点；Prophet 不可用/拟合失败时回退
    末值平推（naive-short），保证叠加线总能渲染。
    """
    points = max(1, int(hours) // 3)
    prepared = prepare_series(values, timestamps)
    if len(prepared) < 2:
        return naive_forecast(values, timestamps, prediction_days=1)

    hist = prepared["y"].astype(float)
    last_date = prepared.index[-1]
    dates = pd.date_range(
        start=last_date + pd.Timedelta(hours=3), periods=points, freq=SAMPLING_FREQ
    )
    _emit(progress_callback, 0.5, "Prophet 短期拟合中")
    try:
        from prophet import Prophet  # type: ignore

        fit_df = pd.DataFrame({"ds": prepared.index, "y": hist.values})
        m = Prophet(
            yearly_seasonality=False,
            weekly_seasonality=False,
            daily_seasonality=True,
            seasonality_mode="additive",
        )
        m.fit(fit_df)
        yhat = m.predict(pd.DataFrame({"ds": dates}))["yhat"].to_numpy()
        method = "prophet-short"
        explanation = "Prophet 短期预测（3H 步长，日季节性）。"
    except Exception:  # noqa: BLE001 回退末值平推
        yhat = np.full(points, float(hist.iloc[-1]))
        method = "naive-short"
        explanation = "Prophet 短期预测不可用，回退末值平推。"

    band = float(hist.std()) if len(hist) > 1 else 0.0
    fc = pd.DataFrame(
        {
            "ds": dates,
            "yhat": yhat,
            "yhat_lower": yhat - 1.96 * band,
            "yhat_upper": yhat + 1.96 * band,
        }
    )
    _emit(progress_callback, 1.0, "完成")
    hist_df = prepared.reset_index()
    if "ds" not in hist_df.columns:
        hist_df = hist_df.rename(columns={"index": "ds"})
    return ForecastResult(
        history=hist_df,
        forecast=fc,
        method=method,
        explanation=explanation,
    )


def hybrid_forecast(
    values: Sequence[float],
    timestamps: Sequence[pd.Timestamp],
    prediction_days: int = 7,
    manual_prophet_weight: float = 0.6,
    manual_sarima_weight: float = 0.4,
    use_grid_search: bool = True,
    changepoint_prior_scale: float = 0.05,
    seasonality_prior_scale: float = 10.0,
    progress_callback: Optional[ProgressCallback] = None,
) -> ForecastResult:
    """Prophet + SARIMA 权重融合预测（对齐旧版 sarima_validation_prediction）。

    策略：手动权重之和为 1.0 时用指定权重；否则按 1/(1+RMSE) 归一化自动分配。
    SARIMA 或 Prophet 不可用时回退到可用的单模型 / naive。
    修复：透传 changepoint/seasonality 参数（UI 滑块此前无效）。
    """
    _emit(progress_callback, 0.05, "数据预处理")
    prepared = prepare_series(values, timestamps)
    if len(prepared) < 2:
        return naive_forecast(values, timestamps, prediction_days)

    total_points = prediction_days * POINTS_PER_DAY
    last_date = prepared.index[-1]
    forecast_dates = pd.date_range(
        start=last_date + pd.Timedelta(hours=3), periods=total_points, freq=SAMPLING_FREQ
    )

    sarima_ok = prophet_ok = False
    sarima_pred: Optional[pd.Series] = None
    prophet_pred: Optional[pd.Series] = None
    sarima_rmse = prophet_rmse = float("inf")

    # ---- SARIMA 拟合 ----
    _emit(progress_callback, 0.15, "SARIMA 参数网格搜索中" if use_grid_search else "SARIMA 拟合中")
    try:
        from statsmodels.tsa.statespace.sarimax import SARIMAX  # type: ignore

        hist = prepared["y"].astype(float)
        order, seasonal_order = (1, 1, 1), (1, 1, 1, 24)
        if use_grid_search:
            # 激活网格搜索（此前 use_grid_search 为死参数，从未生效）
            try:
                gs_order, gs_seasonal, _aic = grid_search_sarima_params(hist)
                order, seasonal_order = gs_order, gs_seasonal
            except Exception:  # noqa: BLE001 搜索失败回退默认参数
                pass
            _emit(progress_callback, 0.2, f"SARIMA 拟合中（最优参数 {order}×{seasonal_order}）")
        model = SARIMAX(
            hist,
            order=order,
            seasonal_order=seasonal_order,
            enforce_stationarity=False,
            enforce_invertibility=False,
        )
        fit = model.fit(disp=False, maxiter=200)
        sarima_pred = pd.Series(fit.forecast(steps=total_points).values, index=forecast_dates)
        fitted = fit.fittedvalues
        # 修复：差分模型 fittedvalues 头部可能含 NaN，去 NaN 后计算 RMSE
        mask = fitted.notna() & hist.notna()
        if mask.sum() > 0:
            sarima_rmse = float(np.sqrt(np.mean((hist[mask] - fitted[mask]) ** 2)))
        else:
            sarima_rmse = float("inf")
        if np.isfinite(sarima_rmse) and not np.isnan(sarima_pred.values).all():
            sarima_ok = True
    except Exception:  # noqa: BLE001 任一模型失败不影响整体
        sarima_ok = False

    # ---- Prophet 拟合（懒加载） ----
    _emit(progress_callback, 0.5, "Prophet 拟合中")
    try:
        from prophet import Prophet  # type: ignore

        fit_df = prepared.reset_index().rename(columns={"index": "ds", "y": "y"})
        m = Prophet(yearly_seasonality=False, weekly_seasonality=True,
                    daily_seasonality=True, seasonality_mode="additive",
                    changepoint_prior_scale=changepoint_prior_scale,
                    seasonality_prior_scale=seasonality_prior_scale)
        m.fit(fit_df)
        future = pd.DataFrame({"ds": forecast_dates})
        pred = m.predict(future)
        prophet_pred = pred["yhat"].reset_index(drop=True)
        prophet_pred.index = forecast_dates
        # Prophet 历史拟合 RMSE（修复：reset_index 后索引与 prepared 不一致，
        # Series 布尔索引对齐会产生长度翻倍的 mask → IndexError，改用 numpy 数组）
        fitted_hist = m.predict(fit_df[["ds"]])["yhat"].to_numpy()
        actual = prepared["y"].to_numpy()
        mask = ~np.isnan(fitted_hist) & ~np.isnan(actual)
        if mask.sum() > 0:
            prophet_rmse = float(np.sqrt(np.mean((actual[mask] - fitted_hist[mask]) ** 2)))
        else:
            prophet_rmse = float("inf")
        if np.isfinite(prophet_rmse) and not np.isnan(prophet_pred.values).all():
            prophet_ok = True
    except Exception:  # noqa: BLE001
        prophet_ok = False

    # ---- 权重融合 ----
    _emit(progress_callback, 0.85, "权重融合中")
    if sarima_ok and prophet_ok:
        if manual_prophet_weight + manual_sarima_weight == 1.0:
            w_p, w_s = manual_prophet_weight, manual_sarima_weight
        else:
            # 修复：任一 RMSE 非有限时用另一模型权重（避免 1/(1+NaN) 产生 NaN 融合结果）
            wp_raw = 1 / (1 + prophet_rmse) if np.isfinite(prophet_rmse) else 0.0
            ws_raw = 1 / (1 + sarima_rmse) if np.isfinite(sarima_rmse) else 0.0
            total = wp_raw + ws_raw
            if total <= 0:
                w_p, w_s = manual_prophet_weight, manual_sarima_weight
            else:
                w_p, w_s = wp_raw / total, ws_raw / total
        combined = w_p * prophet_pred + w_s * sarima_pred
        method_name = "hybrid(prophet+sarima)"
        explanation = (
            f"Prophet 与 SARIMA 权重融合（手动 {w_p:.2f}/{w_s:.2f}）。"
            "SARIMA 捕获线性趋势与季节性，Prophet 补充非线性模式。"
        )
        rmse = min(prophet_rmse, sarima_rmse) if np.isfinite(prophet_rmse) else sarima_rmse
    elif sarima_ok:
        combined = sarima_pred
        method_name = "sarima"
        explanation = "Prophet 不可用，回退 SARIMA 单模型。"
        rmse = sarima_rmse
    elif prophet_ok:
        combined = prophet_pred
        method_name = "prophet"
        explanation = "SARIMA 不可用，回退 Prophet 单模型。"
        rmse = prophet_rmse
    else:
        return naive_forecast(values, timestamps, prediction_days)

    band = float(np.std(prepared["y"])) if len(prepared) > 1 else 0.0
    fc = pd.DataFrame(
        {
            "ds": forecast_dates,
            "yhat": combined.values,
            "yhat_lower": (combined - 1.96 * band).values,
            "yhat_upper": (combined + 1.96 * band).values,
        }
    )
    _emit(progress_callback, 1.0, "完成")
    return ForecastResult(
        history=prepared.reset_index().rename(columns={"index": "ds", "y": "y"}),
        forecast=fc,
        method=method_name,
        rmse=rmse if np.isfinite(rmse) else None,
        explanation=explanation,
    )


def residual_hybrid_forecast(
    values: Sequence[float],
    timestamps: Sequence[pd.Timestamp],
    prediction_days: int = 7,
    changepoint_prior_scale: float = 0.05,
    seasonality_prior_scale: float = 10.0,
    progress_callback: Optional[ProgressCallback] = None,
) -> ForecastResult:
    """残差分解混合预测（对齐旧库 HybridPredictor 两阶段法，补齐复刻缺口）。

    阶段一：SARIMA 捕获线性趋势与季节性；
    阶段二：Prophet 在 SARIMA 残差上学习非线性模式；
    融合：最终预测 = SARIMA 外推 + Prophet 残差外推。
    评估：最后 20% 样本上对比混合 vs 纯 SARIMA（RMSE/MAE/MAPE + 改进率）。

    任一阶段失败时逐级回退：`hybrid_forecast`（权重融合）→ 单模型 → naive。
    """
    _emit(progress_callback, 0.05, "数据预处理")
    prepared = prepare_series(values, timestamps)
    if len(prepared) < 24:
        _emit(progress_callback, 0.5, "样本不足，回退权重融合")
        return hybrid_forecast(
            values, timestamps, prediction_days,
            changepoint_prior_scale=changepoint_prior_scale,
            seasonality_prior_scale=seasonality_prior_scale,
            progress_callback=progress_callback,
        )

    hist = prepared["y"].astype(float)
    total_points = prediction_days * POINTS_PER_DAY
    last_date = prepared.index[-1]
    forecast_dates = pd.date_range(
        start=last_date + pd.Timedelta(hours=3), periods=total_points, freq=SAMPLING_FREQ
    )

    def _fallback_weight_fusion(reason: str) -> ForecastResult:
        _emit(progress_callback, 0.6, f"{reason}，回退权重融合")
        res = hybrid_forecast(
            values, timestamps, prediction_days,
            changepoint_prior_scale=changepoint_prior_scale,
            seasonality_prior_scale=seasonality_prior_scale,
            progress_callback=progress_callback,
        )
        res.explanation = f"（{reason}，已回退权重融合）{res.explanation}"
        return res

    # ---- 阶段一：SARIMA 拟合 ----
    _emit(progress_callback, 0.2, "阶段一：SARIMA 拟合")
    try:
        from statsmodels.tsa.statespace.sarimax import SARIMAX  # type: ignore

        fit = SARIMAX(
            hist,
            order=(1, 1, 1),
            seasonal_order=(1, 1, 1, 24),
            enforce_stationarity=False,
            enforce_invertibility=False,
        ).fit(disp=False, maxiter=200)
        fitted = fit.fittedvalues
        sarima_future = np.asarray(fit.forecast(steps=total_points), dtype=float)
    except Exception:  # noqa: BLE001
        return _fallback_weight_fusion("SARIMA 拟合失败")

    mask = fitted.notna() & hist.notna()
    resid = (hist - fitted)[mask]
    actual_hist = hist[mask].to_numpy()
    fitted_hist = fitted[mask].to_numpy()

    if len(resid) < 10 or float(resid.std()) < 1e-9:
        # 残差无信息：SARIMA 已充分刻画序列，直接用纯 SARIMA 结果
        band = float(hist.std()) if len(hist) > 1 else 0.0
        fc = pd.DataFrame(
            {
                "ds": forecast_dates,
                "yhat": sarima_future,
                "yhat_lower": sarima_future - 1.96 * band,
                "yhat_upper": sarima_future + 1.96 * band,
            }
        )
        hist_df = prepared.reset_index()
        if "ds" not in hist_df.columns:
            hist_df = hist_df.rename(columns={"index": "ds"})
        _emit(progress_callback, 1.0, "完成")
        return ForecastResult(
            history=hist_df,
            forecast=fc,
            method="sarima(残差无信息)",
            rmse=_rmse(actual_hist, fitted_hist),
            explanation="SARIMA 残差近似白噪声，非线性增益有限，已回退纯 SARIMA。",
        )

    # ---- 阶段二：Prophet 学习残差 ----
    _emit(progress_callback, 0.55, "阶段二：Prophet 残差学习")
    try:
        from prophet import Prophet  # type: ignore

        rdf = pd.DataFrame({"ds": resid.index, "y": resid.values})
        m = Prophet(
            yearly_seasonality=False,
            weekly_seasonality=False,
            daily_seasonality=True,
            seasonality_mode="additive",
            changepoint_prior_scale=changepoint_prior_scale,
            seasonality_prior_scale=seasonality_prior_scale,
        )
        m.fit(rdf)
        resid_future = m.predict(pd.DataFrame({"ds": forecast_dates}))["yhat"].to_numpy()
        resid_fitted = m.predict(rdf[["ds"]])["yhat"].to_numpy()
    except Exception:  # noqa: BLE001
        return _fallback_weight_fusion("Prophet 残差学习失败")

    # ---- 融合与评估（最后 20% 样本：混合 vs 纯 SARIMA） ----
    _emit(progress_callback, 0.85, "融合与评估")
    combined_future = sarima_future + resid_future
    combined_fitted = fitted_hist + resid_fitted

    n_eval = max(1, len(actual_hist) // 5)
    a_eval, h_eval, s_eval = (
        actual_hist[-n_eval:],
        combined_fitted[-n_eval:],
        fitted_hist[-n_eval:],
    )
    rmse_h, rmse_s = _rmse(a_eval, h_eval), _rmse(a_eval, s_eval)
    mae_h, mae_s = _mae(a_eval, h_eval), _mae(a_eval, s_eval)
    mape_h, mape_s = _mape(a_eval, h_eval), _mape(a_eval, s_eval)
    improvement = (rmse_s - rmse_h) / rmse_s * 100 if rmse_s > 1e-9 else 0.0
    verdict = "混合模型优于纯 SARIMA" if improvement > 0 else "未优于纯 SARIMA（增益有限）"

    band = float(hist.std()) if len(hist) > 1 else 0.0
    fc = pd.DataFrame(
        {
            "ds": forecast_dates,
            "yhat": combined_future,
            "yhat_lower": combined_future - 1.96 * band,
            "yhat_upper": combined_future + 1.96 * band,
        }
    )
    hist_df = prepared.reset_index()
    if "ds" not in hist_df.columns:
        hist_df = hist_df.rename(columns={"index": "ds"})
    _emit(progress_callback, 1.0, "完成")
    explanation = (
        f"残差分解两阶段：SARIMA 捕获线性趋势/季节性，Prophet 学习残差非线性后叠加。"
        f"末 20% 样本评估：混合 RMSE={rmse_h:.4f} vs 纯 SARIMA {rmse_s:.4f}"
        f"（改进 {improvement:+.1f}%，{verdict}）；MAE {mae_h:.4f} vs {mae_s:.4f}，"
        f"MAPE {mape_h:.2f}% vs {mape_s:.2f}%。"
    )
    return ForecastResult(
        history=hist_df,
        forecast=fc,
        method="hybrid-residual(sarima+prophet)",
        rmse=rmse_h,
        explanation=explanation,
    )


def multivariate_forecast(
    temp_values: Sequence[float],
    humid_values: Sequence[float],
    light_values: Sequence[float],
    timestamps: Sequence[pd.Timestamp],
    prediction_days: int = 7,
    n_estimators: int = 100,
    use_lag_features: bool = True,
    use_interaction: bool = True,
    progress_callback: Optional[ProgressCallback] = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict, str]:
    """多变量随机森林预测（对齐旧版 multivariate_prediction）。

    特征：temperature/humidity/light + temp_lag1 + humid_lag1 + temp_humid_interaction。
    sklearn 未安装时抛出 RuntimeError。

    Returns:
        (merged_df, forecast_df, feature_importance, explanation)
    """
    try:
        from sklearn.ensemble import RandomForestRegressor  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("未安装 scikit-learn。请先安装项目完整依赖：`uv pip install -e .`。") from exc

    _emit(progress_callback, 0.1, "数据合并")
    merged = pd.DataFrame(
        {
            "ds": pd.to_datetime(list(timestamps)),
            "temperature": list(temp_values),
            "humidity": list(humid_values),
            "light": list(light_values),
        }
    ).drop_duplicates("ds").set_index("ds").sort_index()

    if len(merged) < 20:
        raise ValueError("多变量数据量不足（至少需 20 条）。")

    work = merged.copy()
    if use_lag_features:
        work["temp_lag1"] = work["temperature"].shift(1)
        work["humid_lag1"] = work["humidity"].shift(1)
    if use_interaction:
        work["temp_humid_interaction"] = work["temperature"] * work["humidity"]
    work = work.dropna()

    # 修复：dropna 后样本数可能骤减（如 light 全 NaN），需在拟合前重新校验
    if len(work) < 2:
        raise ValueError(
            "多变量数据在构造特征后有效样本不足（存在全空列或过多样本被剔除）。"
        )

    feature_cols = [c for c in ("temperature", "humidity", "light", "temp_lag1", "humid_lag1", "temp_humid_interaction")
                    if c in work.columns]
    X = work[feature_cols]
    y_temp = work["temperature"]
    y_humid = work["humidity"]

    _emit(progress_callback, 0.5, "随机森林训练中")
    rf_temp = RandomForestRegressor(n_estimators=n_estimators, random_state=42, n_jobs=-1)
    rf_humid = RandomForestRegressor(n_estimators=n_estimators, random_state=42, n_jobs=-1)
    rf_temp.fit(X, y_temp)
    rf_humid.fit(X, y_humid)

    feature_importance = pd.DataFrame(
        {
            "feature": X.columns,
            "temp_importance": rf_temp.feature_importances_,
            "humid_importance": rf_humid.feature_importances_,
        }
    ).sort_values("temp_importance", ascending=False)
    feature_importance["temp_importance_pct"] = (feature_importance["temp_importance"] * 100).round(2)
    feature_importance["humid_importance_pct"] = (feature_importance["humid_importance"] * 100).round(2)

    # 多步递归预测（逐点用前一轮预测值填充 lag）
    _emit(progress_callback, 0.85, "递归预测中")
    last = work.iloc[-1].copy()
    rows = []
    for step in range(prediction_days * POINTS_PER_DAY):
        feat = {
            "temperature": last["temperature"],
            "humidity": last["humidity"],
            "light": last.get("light", 0.0),
        }
        if use_lag_features:
            feat["temp_lag1"] = last.get("temp_lag1", last["temperature"])
            feat["humid_lag1"] = last.get("humid_lag1", last["humidity"])
        if use_interaction:
            feat["temp_humid_interaction"] = feat["temperature"] * feat["humidity"]
        X_next = pd.DataFrame([feat])[feature_cols]
        t_next = float(rf_temp.predict(X_next)[0])
        h_next = float(rf_humid.predict(X_next)[0])
        ts = work.index[-1] + pd.Timedelta(hours=3 * (step + 1))
        rows.append({"ds": ts, "temperature": t_next, "humidity": h_next})
        last = pd.Series(
            {"temperature": t_next, "humidity": h_next, "light": feat["light"]},
            name=ts,
        )
        if use_lag_features:
            last["temp_lag1"] = feat["temperature"]
            last["humid_lag1"] = feat["humidity"]
        if use_interaction:
            last["temp_humid_interaction"] = t_next * h_next

    forecast_df = pd.DataFrame(rows)

    _emit(progress_callback, 1.0, "完成")
    feature_name_map = {
        "temperature": "温度",
        "humidity": "湿度",
        "light": "光照强度",
        "temp_lag1": "温度滞后 (t-1)",
        "humid_lag1": "湿度滞后 (t-1)",
        "temp_humid_interaction": "温度×湿度交互项",
    }
    top = feature_importance.iloc[0]
    explanation = (
        "多变量耦合预测：随机森林同时建模温度与湿度，"
        f"特征含 {'滞后项 + 交互项' if use_lag_features and use_interaction else '基础项'}。"
        f"对温度预测最重要的特征是「{feature_name_map.get(top['feature'], top['feature'])}」"
        f"（贡献 {top['temp_importance_pct']}%）。"
    )
    return work.reset_index(), forecast_df, feature_importance, explanation


def score_prediction(rmse: Optional[float], values: Sequence[float]) -> dict:
    """综合评分（对齐旧版 show_prediction_results 的评分逻辑）。

    Returns:
        {"score": 0-6, "level": 高/中/低, "rmse_note": str}
    """
    if rmse is None or not values:
        return {"score": 0, "level": "低", "rmse_note": "RMSE 不可用"}

    series = pd.Series(list(values), dtype=float).dropna()
    std = float(series.std()) if len(series) > 1 else 0.0
    mean = float(series.mean()) if len(series) else 0.0
    volatility = std / abs(mean) if abs(mean) > 1e-9 else 0.0

    score = 0
    # 修复：RMSE 比值用均值绝对值，避免负均值数据（如冬季负温）恒得高分
    rmse_ratio = rmse / abs(mean) if abs(mean) > 1e-9 else 1.0
    if rmse_ratio < 0.05:
        score += 2
    elif rmse_ratio < 0.15:
        score += 1
    # 波动率占 2 分（越小越稳）
    if volatility < 0.05:
        score += 2
    elif volatility < 0.15:
        score += 1
    # 拟合优度占 2 分（由 RMSE 反推估算）
    if rmse_ratio < 0.05:
        score += 2
    elif rmse_ratio < 0.15:
        score += 1

    level = "高" if score >= 4 else "中" if score >= 2 else "低"
    return {"score": score, "level": level, "rmse_note": f"RMSE={rmse:.4f}"}
