"""数据分析页面。调用 `analysis_service`（描述统计 / 相关性 / 分组聚合）。"""

from datetime import datetime, timedelta

import pandas as pd
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


def _load_analysis_df(metric: str) -> pd.DataFrame:
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


st.title("数据分析")

metric_label = st.selectbox("选择指标", list(METRICS.keys()))
metric = METRICS[metric_label]
df = _load_analysis_df(metric)
if df.empty:
    st.info("暂无数据，请先运行 `python -m smart_farm.data.seed` 生成演示数据。")
    st.stop()

tab1, tab2, tab3 = st.tabs(["描述统计", "相关性", "分组聚合"])

with tab1:
    st.dataframe(az.describe_data(df), width="stretch")

with tab2:
    corr = az.calculate_correlation(df)
    if corr is None:
        st.info("数值列不足 2 列，无法计算相关性矩阵。")
    else:
        fig = px.imshow(corr, text_auto=True, aspect="equal",
                        color_continuous_scale="RdBu_r", zmin=-1, zmax=1)
        fig.update_layout(height=420)
        st.plotly_chart(fig, width="stretch")
        st.caption("取值范围 [-1, 1]：越接近 ±1 相关性越强。")

with tab3:
    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    work = df.copy()
    work["星期"] = work["timestamp"].dt.dayofweek
    work["小时"] = work["timestamp"].dt.hour
    group_col = st.selectbox("分组维度", ["星期", "小时"])
    agg_col = st.selectbox("聚合数值列", numeric_cols)
    agg_func = st.selectbox("聚合方式", ["平均值", "总和", "最大值", "最小值"])
    try:
        result = az.group_and_aggregate(work, group_col, agg_col, agg_func)
        st.dataframe(result, width="stretch")
        fig = px.bar(result, x=group_col, y=result.columns[1], title=f"{agg_col} 按{group_col}{agg_func}")
        fig.update_layout(height=380)
        st.plotly_chart(fig, width="stretch")
    except ValueError as e:
        st.error(str(e))
