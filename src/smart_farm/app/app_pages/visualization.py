"""数据可视化页面。基于 Plotly 对传感器数据做多类图表（对齐旧库 data_visualization）。

- 时间戳列存在时提供起止日期筛选 + 数据质量指标
- 智能图表推荐（按数据特征）
- 基础图表：散点/线/柱/箱线/直方/饼/热力图
- 高级图表：双轴图、多子图
- 每图附中文解读
"""

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from smart_farm.app import cache
from smart_farm.services import visualization_service as vs

METRICS = {
    "空气温湿度": ("air_temperature_humidity", ["temperature", "humidity"]),
    "土壤湿度": ("soil_moisture", ["value"]),
    "土壤养分": ("soil_nutrient", ["value"]),
    "光照强度": ("light_intensity", ["value"]),
}


def _fetch(metric: str, cols: list[str], start: datetime, end: datetime, limit: int = 4000) -> pd.DataFrame:
    frames = []
    for c in cols:
        d = cache.cached_sensor_df(metric, c, start.isoformat(), limit=limit)
        frames.append(d.rename(columns={"value": c}))
    df = frames[0]
    for d in frames[1:]:
        df = df.merge(d, on="timestamp")
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df[(df["timestamp"] >= start) & (df["timestamp"] <= end)]
    return df


st.title("数据可视化")

metric_label = st.selectbox("选择指标", list(METRICS.keys()))
metric, default_cols = METRICS[metric_label]

if metric == "air_temperature_humidity":
    cols = st.multiselect("选择字段", ["temperature", "humidity"],
                          default=["temperature", "humidity"])
else:
    cols = default_cols

# 起止日期筛选（默认近 30 天）
c1, c2 = st.columns(2)
with c1:
    start_date = st.date_input("起始日期", value=(datetime.now() - timedelta(days=30)).date())
with c2:
    end_date = st.date_input("结束日期", value=datetime.now().date())
if start_date > end_date:
    st.error("起始日期不能晚于结束日期。")
    st.stop()
start = datetime.combine(start_date, datetime.min.time())
end = datetime.combine(end_date, datetime.max.time())

df = _fetch(metric, cols, start, end)
if df.empty:
    st.info("所选时间范围内无数据，请先运行 `python -m smart_farm.data.seed` 生成演示数据。")
    st.stop()

# 数据质量指标
c1, c2, c3 = st.columns(3)
c1.metric("行数", len(df))
c2.metric("缺失值", int(df.isnull().sum().sum()))
c3.metric("重复行", int(df.duplicated().sum()))

# 智能推荐
chart_type, params, reason = vs.create_smart_chart_recommendation(df)
st.caption(f"智能推荐：**{chart_type}** — {reason}")

tab1, tab2 = st.tabs(["基础图表", "高级图表"], on_change="rerun")

with tab1:
    chart = st.selectbox(
        "图表类型",
        ["散点图", "线图", "柱状图", "箱线图", "直方图", "饼图", "热力图"],
        index=["线图", "散点图", "柱状图", "箱线图", "直方图", "饼图", "热力图"].index(
            chart_type if chart_type in ("线图", "散点图", "柱状图", "箱线图", "直方图", "饼图", "热力图") else "线图"
        ),
    )
    all_cols = df.columns.tolist()
    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    cat_cols = df.select_dtypes(include=["object", "category"]).columns.tolist()

    x_column = y_column = color_column = column = None
    if chart in ("散点图", "线图", "柱状图"):
        x_column = st.selectbox("X 轴", all_cols)
        y_column = st.selectbox("Y 轴", numeric_cols)
        color_column = st.selectbox("颜色列", ["无"] + cat_cols)
        color_column = None if color_column == "无" else color_column
    elif chart in ("箱线图", "直方图"):
        column = st.selectbox("数值列", numeric_cols)
    elif chart == "饼图":
        column = st.selectbox("分类列", cat_cols if cat_cols else all_cols)

    try:
        fig = vs.build_chart(chart, df, x_column=x_column, y_column=y_column,
                             color_column=color_column, column=column, height=480)
        st.plotly_chart(fig, width="stretch")
        st.caption(vs.interpret_chart(chart, df, x_column=x_column, y_column=y_column, column=column))
    except ValueError as e:
        st.error(str(e))

with tab2:
    if not tab2.open:
        st.caption("切换到本标签页使用高级图表。")
    else:
        st.subheader("双轴图")
        x_axis = st.selectbox("X 轴", all_cols, key="dual_x")
        y1 = st.selectbox("左 Y 轴", numeric_cols, key="dual_y1")
        y2 = st.selectbox("右 Y 轴", numeric_cols, index=min(1, len(numeric_cols) - 1), key="dual_y2")
        if st.button("生成双轴图", icon=":material/bar_chart:"):
            if y1 == y2:
                st.error("左右 Y 轴需选择不同列。")
            else:
                fig = vs.create_dual_axis_chart(df, x_axis, y1, y2, y1_title=y1, y2_title=y2)
                st.plotly_chart(fig, width="stretch")
                r = df[[y1, y2]].dropna().corr().iloc[0, 1]
                level = "强" if abs(r) >= 0.7 else "中等" if abs(r) >= 0.3 else "弱"
                st.caption(f"{y1} 与 {y2} 相关系数 r={r:.3f}（{level}相关）")

        st.subheader("多子图")
        sub_cols = st.multiselect("选择变量（≥2 个）", numeric_cols, default=numeric_cols[:3])
        if len(sub_cols) >= 2:
            fig = vs.create_multi_subplot_chart(df, "timestamp" if "timestamp" in df.columns else all_cols[0], sub_cols)
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("请至少选择两个变量生成多子图。")
