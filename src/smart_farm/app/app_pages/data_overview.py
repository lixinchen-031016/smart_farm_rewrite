"""数据概览页面：数据库浏览 + 上传 CSV/Excel/JSON，预览与导出。"""

from datetime import datetime, timedelta
from io import BytesIO

import pandas as pd
import streamlit as st

from smart_farm.app import cache

METRIC_COLS = {
    "air_temperature_humidity": [("温度", "temperature"), ("湿度", "humidity")],
    "soil_moisture": [("土壤湿度", "value")],
    "soil_nutrient": [("土壤养分", "value")],
    "light_intensity": [("光照强度", "value")],
}


def _read_file(uploaded) -> pd.DataFrame | None:
    if uploaded.type == "application/json":
        return pd.read_json(uploaded)
    if uploaded.type in ("text/csv", "application/vnd.ms-excel"):
        return pd.read_csv(uploaded)
    if uploaded.type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        return pd.read_excel(uploaded)
    st.error("不支持的文件类型。")
    return None


def _download_bytes(data: pd.DataFrame, fmt: str) -> tuple[bytes, str, str]:
    if fmt == "CSV":
        return data.to_csv(index=False).encode(), "text/csv", "csv"
    if fmt == "Excel":
        buf = BytesIO()
        data.to_excel(buf, index=False, engine="openpyxl")
        return buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    return data.to_json(orient="records", force_ascii=False).encode(), "application/json", "json"


st.title("数据概览")
tab1, tab2 = st.tabs(["数据库数据", "上传与导出"])

with tab1:
    st.subheader("数据库传感器数据")
    metric = st.selectbox("选择指标", list(METRIC_COLS.keys()), key="db_metric")
    sub = st.selectbox("选择字段", METRIC_COLS[metric], format_func=lambda x: x[0], key="db_sub")
    _, col = sub
    days = st.slider("时间范围（天）", 1, 90, 30, key="db_days")
    since = datetime.now() - timedelta(days=days)
    df = cache.cached_sensor_df(metric, col, since.isoformat(), limit=5000)
    if df.empty:
        st.info("暂无数据，请先运行 `python -m smart_farm.data.seed` 生成演示数据。")
    else:
        st.caption(f"共 {len(df)} 行（最近 {days} 天）")
        st.dataframe(df, width="stretch")
        data, mime, ext = _download_bytes(df, "CSV")
        st.download_button(
            "下载 CSV",
            data=data,
            file_name=f"exported_data.{ext}",
            mime=mime,
            icon=":material/download:",
        )

with tab2:
    st.subheader("上传与导出")
    uploaded = st.file_uploader("上传文件", type=["csv", "xlsx", "xls", "json"])
    if uploaded is not None:
        data = _read_file(uploaded)
        if data is not None:
            if "timestamp" in data.columns:
                data["timestamp"] = pd.to_datetime(data["timestamp"], errors="coerce")
            st.session_state["data"] = data

    if "data" in st.session_state:
        data = st.session_state["data"]
        c1, c2, c3 = st.columns(3)
        c1.metric("行数", data.shape[0])
        c2.metric("列数", data.shape[1])
        c3.metric("缺失值", int(data.isnull().sum().sum()))

        st.subheader("数据预览")
        st.dataframe(data.head(), width="stretch")

        st.subheader("数据导出")
        fmt = st.radio("导出格式", ["CSV", "Excel", "JSON"])
        if st.button("导出", type="primary", icon=":material/file_upload:"):
            data_bytes, mime, ext = _download_bytes(data, fmt)
            st.download_button(
                f"下载 {fmt} 文件",
                data=data_bytes,
                file_name=f"exported_data.{ext}",
                mime=mime,
                icon=":material/download:",
            )
