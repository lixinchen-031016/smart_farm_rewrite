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
