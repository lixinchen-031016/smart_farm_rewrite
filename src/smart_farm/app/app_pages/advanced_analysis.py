"""高级分析页面（对齐旧库 advanced_analysis）。

按分组列做聚合，输出解读、结果表、最高/最低分组智能洞察、柱状图。
"""

from datetime import datetime, timedelta

import plotly.express as px
import streamlit as st

from smart_farm.app import cache
from smart_farm.services import analysis_service as az

METRICS = {
    "空气温湿度": "air_temperature_humidity",
    "土壤湿度": "soil_moisture",
    "土壤养分": "soil_nutrient",
    "光照强度": "light_intensity",
}


def _load_df(metric: str):
    since = datetime.now() - timedelta(days=30)
    iso = since.isoformat()
    if metric == "air_temperature_humidity":
        d1 = cache.cached_sensor_df(metric, "temperature", iso, limit=3000)
        d2 = cache.cached_sensor_df(metric, "humidity", iso, limit=3000)
        df = d1.rename(columns={"value": "temperature"}).merge(
            d2.rename(columns={"value": "humidity"}), on="timestamp"
        )
        return df
    df = cache.cached_sensor_df(metric, "value", iso, limit=3000)
    return df.rename(columns={"value": metric})


st.title("高级分析")

metric_label = st.selectbox("选择指标", list(METRICS.keys()))
metric = METRICS[metric_label]
df = _load_df(metric)
if df.empty:
    st.info("暂无数据，请先运行 `python -m smart_farm.data.seed` 生成演示数据。")
    st.stop()

work = df.copy()
work["星期"] = work["timestamp"].dt.dayofweek
work["小时"] = work["timestamp"].dt.hour

group_col = st.selectbox("分组列", ["星期", "小时"])
numeric_cols = df.select_dtypes(include="number").columns.tolist()
agg_col = st.selectbox("聚合列（数值列）", numeric_cols)
agg_func = st.selectbox("聚合函数", ["平均值", "总和", "最大值", "最小值"])

try:
    result = az.group_and_aggregate(work, group_col, agg_col, agg_func)
except ValueError as e:
    st.error(str(e))
    st.stop()

st.subheader("聚合结果")
st.dataframe(result, width="stretch")

st.subheader("智能洞察")
best = result.loc[result[result.columns[1]].idxmax()]
worst = result.loc[result[result.columns[1]].idxmin()]
group_name = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}.get(
    int(best[group_col]), best[group_col]
)
worst_name = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}.get(
    int(worst[group_col]), worst[group_col]
)
st.info(f"**{group_name}** 的 {agg_col} 最高（{best[result.columns[1]]:.2f}）；"
        f"**{worst_name}** 最低（{worst[result.columns[1]]:.2f}）。")

# 温湿度阈值建议（当聚合列是温/湿度时，对齐旧库）
lower = agg_col.lower()
if "temp" in lower or "温" in agg_col:
    st.caption("温度建议区间：15~30°C；低于 15 注意保温，高于 30 注意通风降温。")
elif "humid" in lower or ("湿" in agg_col and "土" not in agg_col):
    st.caption("湿度建议区间：40~70%；低于 40 增湿，高于 70 除湿。")
elif "moist" in lower or "土" in agg_col:
    st.caption("土壤湿度建议区间：30~60%；低于 30 灌溉，高于 60 排水。")

st.subheader("可视化")
fig = px.bar(result, x=group_col, y=result.columns[1],
             title=f"{agg_col} 按星期{agg_func}")
fig.update_layout(height=400)
st.plotly_chart(fig, width="stretch")

st.subheader("交叉热力图（星期 × 小时，本页独有）")
WEEKDAY_NAMES = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}
try:
    pivot = az.cross_pivot(work, "星期", "小时", agg_col, agg_func)
    show = pivot.rename(index=WEEKDAY_NAMES)
    fig_heat = px.imshow(
        show, text_auto=True, aspect="auto",
        color_continuous_scale="RdYlBu_r",
        title=f"{agg_col} {agg_func}：星期 × 小时",
        labels={"x": "小时", "y": "星期", "color": agg_col},
    )
    fig_heat.update_layout(height=460)
    st.plotly_chart(fig_heat, width="stretch")
    insight = az.cross_pivot_insight(pivot, agg_col, row_names=WEEKDAY_NAMES)
    if insight:
        st.caption(insight)
except ValueError as e:
    st.error(str(e))
