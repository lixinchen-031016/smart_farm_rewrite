"""数据清洗与异常检测页面。

数据源：数据库传感器指标 或 上传文件。调用 `cleaning_service` 与 `anomaly_service`
（纯函数、可单测），本页只负责取数、渲染与导出。清洗结果可下载为 CSV（不回写数据库）。
"""

import base64
from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from smart_farm.app import cache
from smart_farm.services import anomaly_service as an
from smart_farm.services import cleaning_service as cs
from smart_farm.services.cleaning_service import CleaningConfig

METRIC_COLS = {
    "air_temperature_humidity": [("温度", "temperature"), ("湿度", "humidity")],
    "soil_moisture": [("土壤湿度", "value")],
    "soil_nutrient": [("土壤养分", "value")],
    "light_intensity": [("光照强度", "value")],
}


def _download_csv(df: pd.DataFrame, filename: str) -> None:
    b64 = base64.b64encode(df.to_csv(index=False).encode()).decode()
    st.markdown(
        f'<a href="data:text/csv;base64,{b64}" download="{filename}">📥 下载 {filename}</a>',
        unsafe_allow_html=True,
    )


def _detect_mask(df: pd.DataFrame, col: str, method: str, **kw) -> pd.Series:
    if method == "iqr":
        return an.detect_outliers_iqr(df, col, kw.get("factor", 1.5))
    if method == "zscore":
        return an.detect_outliers_zscore(df, col, kw.get("threshold", 3.0))
    return an.detect_outliers_isolation_forest(df, [col], **kw)


def _load_source() -> tuple[pd.DataFrame | None, str | None]:
    """返回 (工作 df, 目标数值列名)。df 至少含一列数值列。"""
    source = st.radio("数据源", ["数据库传感器数据", "上传文件"], horizontal=True)

    if source == "上传文件":
        uploaded = st.file_uploader("上传文件", type=["csv", "xlsx", "xls", "json"])
        if uploaded is None:
            return None, None
        if uploaded.type == "application/json":
            df = pd.read_json(uploaded)
        elif uploaded.type in ("text/csv", "application/vnd.ms-excel"):
            df = pd.read_csv(uploaded)
        else:
            df = pd.read_excel(uploaded)
        numeric = df.select_dtypes(include="number").columns.tolist()
        if not numeric:
            st.error("文件中无数值列，无法做异常检测/清洗。")
            return None, None
        col = st.selectbox("选择数值列", numeric)
        return df, col

    metric = st.selectbox("选择指标", list(METRIC_COLS.keys()))
    sub = st.selectbox("选择字段", METRIC_COLS[metric], format_func=lambda x: x[0])
    label, col = sub
    since = datetime.now() - timedelta(days=90)
    df = cache.cached_sensor_df(metric, col, since.isoformat(), limit=5000)
    if df.empty:
        st.warning("暂无数据，请先运行 `python -m smart_farm.data.seed` 生成演示数据。")
        return None, None
    df = df.rename(columns={"value": col, "timestamp": "timestamp"})
    return df, col


def show() -> None:
    st.title("🧹 数据清洗与异常检测")

    df, col = _load_source()
    if df is None or col is None:
        return

    st.success(f"已加载 **{len(df)}** 行，目标列：`{col}`（缺失 {int(df[col].isnull().sum())} 个）")

    # ---------------- 异常检测 ----------------
    st.subheader("🔍 异常检测")
    method = st.selectbox("检测方法", ["iqr", "zscore", "isolation_forest"],
                          help="IQR/Z-Score 适合单变量；孤立森林适合多维联合异常。")
    params: dict = {}
    if method == "iqr":
        params["factor"] = st.slider("IQR 倍数", 1.0, 5.0, 1.5, 0.5)
    elif method == "zscore":
        params["threshold"] = st.slider("Z-Score 阈值", 1.0, 10.0, 3.0, 0.5)
    else:
        params["contamination"] = st.slider("异常比例估计", 0.01, 0.5, 0.1, 0.01)

    if st.button("运行异常检测", type="primary"):
        with st.spinner("检测中..."):
            try:
                mask = _detect_mask(df, col, method, **params)
            except RuntimeError as e:
                st.error(str(e))
                return
            idx = df.index[mask]
            summary = an.get_anomaly_summary({col: idx.tolist()})
            st.metric("检出异常点", f"{len(idx)} 个（{summary[col]['percentage']}%）")

            fig = go.Figure()
            fig.add_trace(go.Scatter(x=df.index, y=df[col], mode="lines", name="数据", line={"color": "#4CAF50"}))
            if len(idx):
                fig.add_trace(go.Scatter(
                    x=idx, y=df.loc[idx, col], mode="markers", name="异常",
                    marker={"color": "#F44336", "size": 8},
                ))
            fig.update_layout(height=380, xaxis_title="序号", yaxis_title=col)
            st.plotly_chart(fig, width="stretch")

    # ---------------- 数据清洗 ----------------
    st.subheader("🛠 数据清洗")
    with st.form("clean_form"):
        drop_dup = st.checkbox("删除完全重复的行")
        fill_method = st.selectbox("缺失值处理", ["不处理", "mean", "median", "mode", "constant", "drop"])
        fill_value = None
        if fill_method == "constant":
            fill_value = st.number_input("填充常数", value=0.0)
        submitted = st.form_submit_button("执行清洗")
    if submitted:
        missing_cfg: dict = {}
        if fill_method != "不处理":
            missing_cfg[col] = (fill_method, fill_value)
        cfg = CleaningConfig(drop_duplicates=drop_dup, missing=missing_cfg)
        with st.spinner("清洗中..."):
            out, report = cs.clean_dataframe(df, cfg)

        q_before = cs.assess_quality(df)
        q_after = cs.assess_quality(out)
        c1, c2, c3 = st.columns(3)
        c1.metric("行数", q_after["total_rows"], q_before["total_rows"] - q_after["total_rows"])
        c2.metric("完整率", f"{q_after['completeness']}%",
                  round(q_after["completeness"] - q_before["completeness"], 2))
        c3.metric("缺失值", q_after["missing_cells"], q_before["missing_cells"] - q_after["missing_cells"])

        if report["operations"]:
            st.json(report["operations"], expanded=False)
        _download_csv(out, "cleaned_data.csv")
