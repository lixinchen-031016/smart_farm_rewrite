"""数据概览页面：上传 CSV/Excel/JSON，预览与导出。"""

import base64
from io import BytesIO

import pandas as pd
import streamlit as st


def _read_file(uploaded) -> pd.DataFrame | None:
    if uploaded.type == "application/json":
        return pd.read_json(uploaded)
    if uploaded.type in ("text/csv", "application/vnd.ms-excel"):
        return pd.read_csv(uploaded)
    if uploaded.type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        return pd.read_excel(uploaded)
    st.error("不支持的文件类型。")
    return None


def show() -> None:
    st.title("📋 数据概览")
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
        st.dataframe(data.head())

        st.subheader("数据导出")
        fmt = st.radio("导出格式", ["CSV", "Excel", "JSON"])
        if st.button("📤 导出", type="primary"):
            if fmt == "CSV":
                b64 = base64.b64encode(data.to_csv(index=False).encode()).decode()
                mime, ext = "text/csv", "csv"
            elif fmt == "Excel":
                buf = BytesIO()
                data.to_excel(buf, index=False, engine="openpyxl")
                b64 = base64.b64encode(buf.getvalue()).decode()
                mime, ext = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
            else:
                b64 = base64.b64encode(data.to_json(orient="records", force_ascii=False).encode()).decode()
                mime, ext = "application/json", "json"
            st.markdown(
                f'<a href="data:{mime};base64,{b64}" download="exported_data.{ext}">下载 {ext.upper()} 文件</a>',
                unsafe_allow_html=True,
            )
