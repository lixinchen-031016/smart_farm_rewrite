"""本地数据预测页面。调用 prediction_service（朴素兜底 / Prophet / SARIMA）。"""

from datetime import datetime, timedelta

import plotly.graph_objects as go
import streamlit as st

from smart_farm.app import cache

METRIC_MAP = {
    "空气温度": ("air_temperature_humidity", "temperature"),
    "土壤湿度": ("soil_moisture", "value"),
    "土壤养分": ("soil_nutrient", "value"),
    "光照强度": ("light_intensity", "value"),
}

st.title("本地数据预测")

metric_label = st.selectbox("选择预测指标", list(METRIC_MAP.keys()))
metric, col = METRIC_MAP[metric_label]
method = st.selectbox("预测方法", ["naive", "prophet", "sarima"], index=0,
                      help="naive 无需额外依赖；prophet/sarima 需 `uv pip install -e '.[ml]'`")
days = st.slider("预测天数", 3, 30, 7)

if st.button("执行预测", type="primary"):
    since = datetime.now() - timedelta(days=60)
    with st.spinner("预测中..."):
        try:
            result = cache.cached_forecast(metric, col, method, days, since.isoformat())
        except RuntimeError as e:
            st.error(str(e))
            st.stop()

    st.caption(f"方法：{result.method} ｜ {result.explanation}")
    hist = result.history.rename(columns={"y": "实际值"})
    fc = result.forecast.rename(columns={"yhat": "预测值"})

    # 实际值 + 预测值 + 置信区间阴影带
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=list(fc["ds"]) + list(fc["ds"][::-1]),
        y=list(fc["yhat_upper"]) + list(fc["yhat_lower"][::-1]),
        fill="toself",
        fillcolor="rgba(255,193,7,0.20)",
        line=dict(color="rgba(255,193,7,0)"),
        name="置信区间",
        hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(x=hist["ds"], y=hist["实际值"], mode="lines", name="实际值"))
    fig.add_trace(go.Scatter(x=fc["ds"], y=fc["预测值"], mode="lines+markers", name="预测值"))
    fig.update_layout(height=420, xaxis_title="日期", yaxis_title="数值")
    st.plotly_chart(fig, width="stretch")
    st.caption("阴影区为预测置信区间（±1.96σ）。长模型（Prophet/SARIMA）的异步化与进度条为后续增强项。")
