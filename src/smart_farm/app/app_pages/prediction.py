"""本地数据预测页面（对齐旧库 data_prediction）。

- 模式：单变量（Prophet+SARIMA 推荐 / 纯 Prophet）/ 多变量（随机森林）
- 3H 采样（每天 8 点）、参数滑块、置信区间、综合评分
- 预测自动保存（CSV+MD+SQLite）＋「预测历史」tab
"""

from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from smart_farm.data import repositories as repo
from smart_farm.data.database import get_session
from smart_farm.services import prediction_archive as pa
from smart_farm.services import prediction_service as ps

METRIC_MAP = {
    "空气温度": ("air_temperature_humidity", "temperature"),
    "空气湿度": ("air_temperature_humidity", "humidity"),
    "土壤湿度": ("soil_moisture", "value"),
    "土壤养分": ("soil_nutrient", "value"),
    "光照强度": ("light_intensity", "value"),
}

MODEL_OPTIONS = ["Prophet+SARIMA(推荐)", "纯 Prophet"]


def _fetch_series(metric: str, col: str, hours: int = 24 * 60) -> tuple[list, list]:
    """拉取历史序列（对齐旧版 get_historical_data：取最近数据按时间升序）。"""
    since = datetime.now() - timedelta(hours=hours)
    with get_session() as s:
        rows = repo.get_sensor_readings(s, metric, start=since, limit=5000)
    values = [getattr(r, col) for r in rows]
    timestamps = [r.timestamp for r in rows]
    return values, timestamps


def _fetch_multivariate() -> tuple[list, list, list]:
    """拉取温度/湿度/光照最近 200 条（对齐旧版多变量数据源）。"""
    with get_session() as s:
        temp = repo.get_sensor_readings(s, "air_temperature_humidity", limit=200)
        temp = sorted(temp, key=lambda r: r.timestamp)
        ts = [r.timestamp for r in temp]
        temp_vals = [r.temperature for r in temp]
        humid_vals = [r.humidity for r in temp]
        light_rows = repo.get_sensor_readings(s, "light_intensity", limit=200)
        light_map = {r.timestamp: r.value for r in light_rows}
        light_vals = [light_map.get(t) for t in ts]
    return temp_vals, humid_vals, light_vals, ts


def _render_forecast_chart(result: ps.ForecastResult, title: str) -> None:
    hist = result.history.rename(columns={"y": "实际值"})
    fc = result.forecast.rename(columns={"yhat": "预测值"})
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=list(fc["ds"]) + list(fc["ds"][::-1]),
        y=list(fc["yhat_upper"]) + list(fc["yhat_lower"][::-1]),
        fill="toself", fillcolor="rgba(255,193,7,0.20)",
        line=dict(color="rgba(255,193,7,0)"), name="置信区间", hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(x=hist["ds"], y=hist["实际值"], mode="lines", name="实际值"))
    fig.add_trace(go.Scatter(x=fc["ds"], y=fc["预测值"], mode="lines+markers", name="预测值"))
    fig.update_layout(height=420, xaxis_title="日期", yaxis_title="数值", title=title)
    st.plotly_chart(fig, width="stretch")


def _render_score(result: ps.ForecastResult, values: list) -> None:
    score = ps.score_prediction(result.rmse, values)
    st.caption(f"方法：{result.method} ｜ {result.explanation}")
    c1, c2, c3 = st.columns(3)
    c1.metric("综合评分", f"{score['score']} / 6")
    c2.metric("可信度", score["level"])
    c3.metric("RMSE", f"{result.rmse:.4f}" if result.rmse else "—")


def _run_univariate(metric_label: str, model_type: str, days: int, cp: float, season: float) -> None:
    metric, col = METRIC_MAP[metric_label]
    values, timestamps = _fetch_series(metric, col, hours=60 * 24)
    if len(values) < 10:
        st.warning("历史数据不足（至少 10 条），请先运行 seed 生成演示数据。")
        return

    if model_type == "纯 Prophet":
        try:
            result = ps.prophet_forecast(
                values, timestamps, days,
                changepoint_prior_scale=cp, seasonality_prior_scale=season,
            )
        except RuntimeError as e:
            st.error(str(e))
            return
    else:
        result = ps.hybrid_forecast(
            values, timestamps, days,
            changepoint_prior_scale=cp, seasonality_prior_scale=season,
        )

    _render_forecast_chart(result, f"{metric_label} 预测（{days} 天）")
    _render_score(result, values)
    st.caption("阴影区为预测置信区间（±1.96σ）。")

    # 自动保存
    try:
        res = pa.archive.save_prediction_result(
            result.history, result.forecast, prediction_type=metric_label,
            model_type=model_type, prediction_days=days,
            model_explanation=result.explanation, rmse=result.rmse or 0.0,
            username=st.session_state.get("username", "system"),
        )
        st.success(f"预测结果已自动保存（ID：{res['prediction_id']}）。")
    except Exception as e:  # noqa: BLE001
        st.warning(f"预测结果保存失败：{e}")


def _run_multivariate(days: int, n_estimators: int, use_lag: bool, use_inter: bool) -> None:
    temp, humid, light, ts = _fetch_multivariate()
    if not temp or len([t for t in temp if t is not None]) < 20:
        st.warning("多变量数据不足（至少 20 条）。")
        return
    try:
        merged, fc, fi, explanation = ps.multivariate_forecast(
            temp, humid, light, ts, prediction_days=days,
            n_estimators=n_estimators, use_lag_features=use_lag, use_interaction=use_inter,
        )
    except (RuntimeError, ValueError) as e:
        st.error(str(e))
        return

    st.caption(explanation)
    st.subheader("特征重要性")
    st.dataframe(fi[["feature", "temp_importance_pct", "humid_importance_pct"]], width="stretch")

    fig = go.Figure()
    for col, color in (("temperature", "#185FA5"), ("humidity", "#D85A30")):
        fig.add_trace(go.Scatter(x=merged["ds"], y=merged[col], mode="lines", name=f"历史{col}",
                                 line={"color": color}))
        fig.add_trace(go.Scatter(x=fc["ds"], y=fc[col], mode="lines", name=f"预测{col}",
                                 line={"color": color, "dash": "dash"}))
    fig.update_layout(height=420, xaxis_title="日期", yaxis_title="数值")
    st.plotly_chart(fig, width="stretch")

    st.dataframe(fc, width="stretch")
    st.download_button(
        "下载多变量预测 CSV",
        data=fc.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"multivariate_forecast_{datetime.now():%Y%m%d_%H%M%S}.csv",
        mime="text/csv",
        icon=":material/download:",
    )

    # 自动保存
    try:
        res = pa.archive.save_prediction_result(
            merged, fc, prediction_type="多变量耦合预测", model_type="RandomForest-Multivariate",
            prediction_days=days, model_explanation=explanation, rmse=0.0,
            username=st.session_state.get("username", "system"),
        )
        st.success(f"预测结果已自动保存（ID：{res['prediction_id']}）。")
    except Exception as e:  # noqa: BLE001
        st.warning(f"预测结果保存失败：{e}")


def _show_history() -> None:
    st.subheader("预测历史")
    history = pa.archive.get_prediction_history(limit=50)
    if not history:
        st.info("暂无预测记录。执行预测后自动保存。")
        return
    stats = pa.archive.get_statistics()
    c1, c2, c3 = st.columns(3)
    c1.metric("总次数", stats["total_predictions"])
    c2.metric("近 7 天", stats["recent_predictions_7d"])
    c3.metric("平均 RMSE", stats["avg_rmse"])
    df = pd.DataFrame([{
        "ID": h["prediction_id"],
        "类型": h["prediction_type"],
        "模型": h["model_type"],
        "天数": h["prediction_days"],
        "RMSE": round(h["rmse"], 4),
        "时间": h["created_at"][:19],
    } for h in history])
    st.dataframe(df, width="stretch")
    if st.button("删除全部历史", icon=":material/delete:"):
        for h in history:
            pa.archive.delete_prediction(h["prediction_id"])
        st.rerun()


st.title("本地数据预测")
tab1, tab2 = st.tabs(["执行预测", "预测历史"])

with tab1:
    mode = st.radio("预测模式", ["单变量时间序列预测", "多变量耦合预测"], horizontal=True)

    if mode == "单变量时间序列预测":
        metric_label = st.selectbox("选择预测的数据类型", list(METRIC_MAP.keys()))
        model_type = st.selectbox("选择预测模型", MODEL_OPTIONS)
        days = st.number_input("预测天数", min_value=1, max_value=30, value=7, step=1)
        cp = st.slider("变化点灵敏度 (Prophet)", 0.001, 0.5, 0.05, 0.01)
        season = st.slider("季节性强度 (Prophet)", 0.1, 20.0, 10.0, 0.1)
        if st.button("执行预测", type="primary", icon=":material/timeline:"):
            _run_univariate(metric_label, model_type, int(days), cp, season)
    else:
        days = st.number_input("预测天数", min_value=1, max_value=15, value=7, step=1)
        n_estimators = st.slider("随机森林树数量", 50, 200, 100, 10)
        use_lag = st.checkbox("启用滞后特征", value=True)
        use_inter = st.checkbox("启用交互项", value=True)
        if st.button("执行多变量预测", type="primary", icon=":material/timeline:"):
            _run_multivariate(int(days), n_estimators, use_lag, use_inter)

with tab2:
    _show_history()
