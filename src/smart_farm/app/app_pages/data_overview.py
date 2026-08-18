"""数据概览页面：数据库浏览（含多表 JOIN 起止日期取数）+ 上传 CSV/Excel/JSON，预览与导出。"""

from datetime import datetime, timedelta
from io import BytesIO

import pandas as pd
import streamlit as st

from smart_farm.app import cache
from smart_farm.app import greenhouse_context as gh_ctx
from smart_farm.data import repositories as repo
from smart_farm.data.database import get_session

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


def _render_download(data: pd.DataFrame, fmt: str) -> None:
    data_bytes, mime, ext = _download_bytes(data, fmt)
    st.download_button(
        f"下载 {fmt} 文件",
        data=data_bytes,
        file_name=f"exported_data.{ext}",
        mime=mime,
        icon=":material/download:",
    )


st.title("数据概览")
tab1, tab2 = st.tabs(["数据库数据", "上传与导出"])

with tab1:
    mode = st.segmented_control("数据来源", ["按指标浏览", "全量多表查询"], default="按指标浏览")

    if mode == "按指标浏览":
        st.subheader("数据库传感器数据")
        metric = st.selectbox("选择指标", list(METRIC_COLS.keys()), key="db_metric")
        sub = st.selectbox("选择字段", METRIC_COLS[metric], format_func=lambda x: x[0], key="db_sub")
        _, col = sub
        days = st.slider("时间范围（天）", 1, 90, 30, key="db_days")
        since = datetime.now() - timedelta(days=days)
        df = cache.cached_sensor_df(
            metric, col, since.isoformat(), limit=5000,
            greenhouse_id=gh_ctx.current_greenhouse_id(),
        )
        if df.empty:
            st.info("暂无数据，请先运行 `python -m smart_farm.data.seed` 生成演示数据。")
        else:
            st.caption(f"共 {len(df)} 行（最近 {days} 天）")
            st.dataframe(df, width="stretch")
            _render_download(df, "CSV")
    else:
        st.subheader("全量多表查询（多表 JOIN）")
        c1, c2 = st.columns(2)
        with c1:
            start_date = st.date_input("起始日期", value=(datetime.now() - timedelta(days=7)).date())
        with c2:
            end_date = st.date_input("结束日期", value=datetime.now().date())
        if st.button("查询数据", type="primary", icon=":material/search:"):
            if start_date > end_date:
                st.error("起始日期不能晚于结束日期。")
            else:
                start = datetime.combine(start_date, datetime.min.time())
                end = datetime.combine(end_date, datetime.max.time())
                with st.spinner("查询中..."):
                    with get_session() as s:
                        df = repo.fetch_data_in_bulk(s, start=start, end=end)
                if df.empty:
                    st.info("所选时间范围内无数据。")
                else:
                    st.caption(f"共 {len(df)} 行（{start_date} ~ {end_date}）")
                    c1, c2, c3 = st.columns(3)
                    c1.metric("行数", df.shape[0])
                    c2.metric("列数", df.shape[1])
                    c3.metric("缺失值", int(df.isnull().sum().sum()))
                    st.dataframe(df, width="stretch")
                    st.subheader("字段类型")
                    dtype_df = pd.DataFrame(
                        {"字段": df.columns, "类型": [str(t) for t in df.dtypes]}
                    )
                    st.dataframe(dtype_df, width="stretch")
                    fmt = st.selectbox("导出格式", ["CSV", "Excel", "JSON"], key="bulk_fmt")
                    _render_download(df, fmt)

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
        fmt = st.radio("导出格式", ["CSV", "Excel", "JSON"], key="upload_fmt")  # 修复：补 key 防跨页串扰
        # 修复：直接渲染下载按钮（旧版嵌套在"导出"按钮内，交互后消失）
        _render_download(data, fmt)
