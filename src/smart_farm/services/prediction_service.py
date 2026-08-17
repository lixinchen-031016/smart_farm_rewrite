"""预测服务。

重写要点（对照旧版）：
- 删除 `torch` / `gpu_accelerator` 依赖与 GPU 分支。
- 重模型（Prophet / SARIMA）**懒加载**，未安装时给出明确错误而非崩溃。
- 始终提供 `naive`（朴素）方法兜底，保证 UI 在无重依赖时也能演示。
- 设计为可被后台线程/任务队列调用（长任务异步化由 UI 层负责）。
"""

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
import pandas as pd


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
    """朴素预测：method='last' 用末值，'mean' 用均值，'drift' 用末段斜率外推。"""
    hist = _to_tsdf(values, timestamps)
    last_ts = hist["ds"].max()
    future_ts = [last_ts + pd.Timedelta(days=i + 1) for i in range(prediction_days)]

    if method == "mean":
        base = float(np.mean(values))
    elif method == "drift":
        x = np.arange(len(values))
        slope = float(np.polyfit(x, np.asarray(values, dtype=float), 1)[0]) if len(values) > 1 else 0.0
        base = float(values[-1])
    else:  # last
        base = float(values[-1])
        slope = 0.0

    if method == "drift":
        yhat = [base + slope * (i + 1) for i in range(prediction_days)]
    else:
        yhat = [base] * prediction_days

    band = float(np.std(values)) if len(values) > 1 else 0.0
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
        explanation="朴素预测（无需重依赖），仅供演示；安装 ml 依赖后可用 Prophet/SARIMA。",
    )


def prophet_forecast(
    values: Sequence[float], timestamps: Sequence[pd.Timestamp], prediction_days: int = 7
) -> ForecastResult:
    """Prophet 预测（懒加载，未安装 prophet 时抛出明确异常）。"""
    try:
        from prophet import Prophet  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "未安装 prophet。请运行 `uv pip install -e '.[ml]'` 后重试。"
        ) from exc

    hist = _to_tsdf(values, timestamps)
    m = Prophet()
    m.fit(hist)
    future = m.make_future_dataframe(periods=prediction_days, freq="D")
    pred = m.predict(future)
    fc = pred[["ds", "yhat", "yhat_lower", "yhat_upper"]].tail(prediction_days).reset_index(drop=True)
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
            "未安装 statsmodels。请运行 `uv pip install -e '.[ml]'` 后重试。"
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
    """数据预处理（对齐旧版 perform_prediction）：3H 重采样 + 线性插值 + 99 分位修剪。

    Returns:
        DataFrame(index=ds, columns=[y])，时间索引为 3H 对齐。
    """
    df = pd.DataFrame({"ds": pd.to_datetime(list(timestamps)), "y": list(values)})
    df = df.set_index("ds").sort_index()
    df = df.asfreq(SAMPLING_FREQ)  # 对齐到 3H 网格，产生 NaN
    df["y"] = df["y"].interpolate(limit_direction="both")  # 线性插值补缺
    if df["y"].notna().sum() > 0:
        cap = df["y"].quantile(0.99)  # >99 分位替换为中位数（异常值处理）
        median = df["y"].median()
        df["y"] = df["y"].where(df["y"] <= cap, median)
    df = df.dropna()
    return df


def hybrid_forecast(
    values: Sequence[float],
    timestamps: Sequence[pd.Timestamp],
    prediction_days: int = 7,
    manual_prophet_weight: float = 0.6,
    manual_sarima_weight: float = 0.4,
    use_grid_search: bool = True,
) -> ForecastResult:
    """Prophet + SARIMA 权重融合预测（对齐旧版 sarima_validation_prediction）。

    策略：手动权重之和为 1.0 时用指定权重；否则按 1/(1+RMSE) 归一化自动分配。
    SARIMA 或 Prophet 不可用时回退到可用的单模型 / naive。
    """
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
    try:
        from statsmodels.tsa.statespace.sarimax import SARIMAX  # type: ignore

        hist = prepared["y"].astype(float)
        model = SARIMAX(
            hist,
            order=(1, 1, 1),
            seasonal_order=(1, 1, 1, 24),
            enforce_stationarity=False,
            enforce_invertibility=False,
        )
        fit = model.fit(disp=False, maxiter=200)
        sarima_pred = pd.Series(fit.forecast(steps=total_points).values, index=forecast_dates)
        fitted = fit.fittedvalues
        sarima_rmse = float(np.sqrt(np.mean((hist - fitted) ** 2)))
        sarima_ok = True
    except Exception:  # noqa: BLE001 任一模型失败不影响整体
        sarima_ok = False

    # ---- Prophet 拟合（懒加载） ----
    try:
        from prophet import Prophet  # type: ignore

        fit_df = prepared.reset_index().rename(columns={"index": "ds", "y": "y"})
        m = Prophet(yearly_seasonality=False, weekly_seasonality=True,
                    daily_seasonality=True, seasonality_mode="additive")
        m.fit(fit_df)
        future = pd.DataFrame({"ds": forecast_dates})
        pred = m.predict(future)
        prophet_pred = pred["yhat"].reset_index(drop=True)
        prophet_pred.index = forecast_dates
        # Prophet 历史拟合 RMSE
        fitted_hist = m.predict(fit_df[["ds"]])["yhat"].reset_index(drop=True)
        prophet_rmse = float(np.sqrt(np.mean((prepared["y"].values - fitted_hist.values) ** 2)))
        prophet_ok = True
    except Exception:  # noqa: BLE001
        prophet_ok = False

    # ---- 权重融合 ----
    if sarima_ok and prophet_ok:
        if manual_prophet_weight + manual_sarima_weight == 1.0:
            w_p, w_s = manual_prophet_weight, manual_sarima_weight
        else:
            wp_raw = 1 / (1 + prophet_rmse)
            ws_raw = 1 / (1 + sarima_rmse)
            w_p = wp_raw / (wp_raw + ws_raw)
            w_s = ws_raw / (wp_raw + ws_raw)
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
    return ForecastResult(
        history=prepared.reset_index().rename(columns={"index": "ds", "y": "y"}),
        forecast=fc,
        method=method_name,
        rmse=rmse if np.isfinite(rmse) else None,
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
        raise RuntimeError("未安装 scikit-learn。请运行 `uv pip install -e '.[ml]'` 后重试。") from exc

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

    feature_cols = [c for c in ("temperature", "humidity", "light", "temp_lag1", "humid_lag1", "temp_humid_interaction")
                    if c in work.columns]
    X = work[feature_cols]
    y_temp = work["temperature"]
    y_humid = work["humidity"]

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
    volatility = std / mean if abs(mean) > 1e-9 else 0.0

    score = 0
    # RMSE（相对均值）占 2 分
    rmse_ratio = rmse / mean if abs(mean) > 1e-9 else 1.0
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
