"""本地数据预测页面。调用 prediction_service（朴素兜底 / Prophet / SARIMA）。"""

from datetime import datetime, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from smart_farm.data import repositories as repo
from smart_farm.data.database import get_session
from smart_farm.services import prediction_service as ps

METRIC_MAP = {
    "空气温度": ("air_temperature_humidity", "temperature"),
    "土壤湿度": ("soil_moisture", "value"),
    "土壤养分": ("soil_nutrient", "value"),
    "光照强度": ("light_intensity", "value"),
}


def show() -> None:
    st.title("🔮 本地数据预测")

    metric_label = st.selectbox("选择预测指标", list(METRIC_MAP.keys()))
    metric, col = METRIC_MAP[metric_label]
    method = st.selectbox("预测方法", ["naive", "prophet", "sarima"], index=0,
                          help="naive 无需额外依赖；prophet/sarima 需 `uv pip install -e '.[ml]'`")
    days = st.slider("预测天数", 3, 30, 7)

    if st.button("执行预测", type="primary"):
        with get_session() as s:
            since = datetime.now() - timedelta(days=60)
            rows = repo.get_sensor_readings(s, metric, start=since, limit=5000)
        if not rows:
            st.warning("暂无足够历史数据，请先生成演示数据（`python -m smart_farm.data.seed`）。")
            return

        values = [getattr(r, col) for r in rows]
        timestamps = [r.timestamp for r in rows]

        with st.spinner("预测中..."):
            try:
                result = ps.forecast(values, timestamps, method=method, prediction_days=days)
            except RuntimeError as e:
                st.error(str(e))
                return

        st.caption(f"方法：{result.method} ｜ {result.explanation}")
        hist = result.history.rename(columns={"y": "实际值"})
        fc = result.forecast.rename(columns={"yhat": "预测值"})
        combined = pd.concat([hist, fc[["ds", "预测值", "yhat_lower", "yhat_upper"]]], ignore_index=True)
        fig = px.line(combined, x="ds", y=["实际值", "预测值"])
        fig.update_layout(height=400)
        st.plotly_chart(fig, use_container_width=True)
