"""数据可视化页面。基于 Plotly 对传感器数据做多类图表。"""

from datetime import datetime, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from smart_farm.app import cache

METRICS = {
    "空气温湿度": ("air_temperature_humidity", ["temperature", "humidity"]),
    "土壤湿度": ("soil_moisture", ["value"]),
    "土壤养分": ("soil_nutrient", ["value"]),
    "光照强度": ("light_intensity", ["value"]),
}


def _fetch(metric: str, cols: list[str], since: datetime, limit: int = 4000) -> pd.DataFrame:
    iso = since.isoformat()
    frames = []
    for c in cols:
        d = cache.cached_sensor_df(metric, c, iso, limit=limit)
        frames.append(d.rename(columns={"value": c}))
    df = frames[0]
    for d in frames[1:]:
        df = df.merge(d, on="timestamp")
    return df


st.title("数据可视化")

metric_label = st.selectbox("选择指标", list(METRICS.keys()))
metric, default_cols = METRICS[metric_label]

if metric == "air_temperature_humidity":
    cols = st.multiselect("选择字段", ["temperature", "humidity"],
                          default=["temperature", "humidity"])
else:
    cols = default_cols

days = st.slider("时间范围（天）", 1, 90, 30)
since = datetime.now() - timedelta(days=days)
df = _fetch(metric, cols, since)
if df.empty:
    st.info("暂无数据，请先运行 `python -m smart_farm.data.seed` 生成演示数据。")
    st.stop()

chart = st.selectbox("图表类型", ["时序折线图", "直方图", "箱线图", "相关散点图"])

if chart == "时序折线图":
    fig = px.line(df, x="timestamp", y=cols, markers=True)
    fig.update_layout(height=400)
    st.plotly_chart(fig, width="stretch")

elif chart == "直方图":
    for c in cols:
        fig = px.histogram(df, x=c, nbins=30, title=c)
        fig.update_layout(height=320)
        st.plotly_chart(fig, width="stretch")

elif chart == "箱线图":
    for c in cols:
        fig = px.box(df, y=c, title=c)
        fig.update_layout(height=320)
        st.plotly_chart(fig, width="stretch")

elif chart == "相关散点图":
    if len(cols) < 2:
        st.info("相关散点图需要至少两个字段；请切换到「空气温湿度」并同时选择温度与湿度。")
    else:
        fig = px.scatter(df, x=cols[0], y=cols[1], trendline="ols",
                         title=f"{cols[0]} vs {cols[1]}")
        fig.update_layout(height=420)
        st.plotly_chart(fig, width="stretch")
