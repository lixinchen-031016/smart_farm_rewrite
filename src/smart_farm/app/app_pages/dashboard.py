"""综合监控仪表板页面。"""

from datetime import datetime, timedelta

import plotly.express as px
import streamlit as st

from smart_farm.app import cache
from smart_farm.data import repositories as repo
from smart_farm.data.database import get_session

METRICS = {
    "air_temperature_humidity": ("温度(℃)", "temperature"),
    "soil_moisture": ("土壤湿度(%)", "value"),
    "soil_nutrient": ("土壤养分", "value"),
    "light_intensity": ("光照强度(lux)", "value"),
}

st.title("综合监控仪表板")

# 指标卡：最新值（轻量查询，直接取，不走缓存）
cols = st.columns(len(METRICS))
with get_session() as s:
    latest_map = {m: repo.get_latest_sensor_reading(s, m) for m in METRICS}
for i, (metric, (label, col)) in enumerate(METRICS.items()):
    latest = latest_map[metric]
    with cols[i]:
        if latest:
            st.metric(
                label,
                round(getattr(latest, col), 2),
                help=f"采集时间 {latest.timestamp:%Y-%m-%d %H:%M}",
            )
        else:
            st.metric(label, "—")

# 趋势图（热点查询，走 cache_data）
st.subheader("近期趋势")
metric = st.selectbox("选择指标", list(METRICS.keys()), format_func=lambda m: METRICS[m][0])
label, col = METRICS[metric]
since = datetime.now() - timedelta(days=7)
df = cache.cached_sensor_df(metric, col, since.isoformat(), limit=2000)
if df.empty:
    st.info("暂无数据。可运行 `python -m smart_farm.data.seed` 生成演示数据。")
    st.stop()
df = df.sort_values("timestamp")
fig = px.line(df, x="timestamp", y="value", title=label, markers=True)
fig.update_layout(height=380)
st.plotly_chart(fig, width="stretch")
