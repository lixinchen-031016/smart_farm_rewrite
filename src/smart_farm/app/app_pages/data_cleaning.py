"""数据清洗与异常检测页面（对齐旧库 5-tab 布局）。

tab1 规则模板（农业/ML/自定义 + 规则 JSON 导出/加载）
tab2 基础清洗（去重、按类型/手动删列）
tab3 缺失值（逐列 保持不变/删除/填充，带 `{col}_filled` 标识列）
tab4 异常值（IQR/Z-Score/孤立森林，参数与旧库对齐，可清除）
tab5 数据导出（CSV/Excel/JSON）

数据源：数据库传感器指标 或 上传文件。清洗只作用于内存 DataFrame，不回写数据库。
"""

import json
from datetime import datetime, timedelta
from io import BytesIO

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from smart_farm.app import cache
from smart_farm.services import anomaly_service as an
from smart_farm.services import cleaning_service as cs

METRIC_COLS = {
    "air_temperature_humidity": [("温度", "temperature"), ("湿度", "humidity")],
    "soil_moisture": [("土壤湿度", "value")],
    "soil_nutrient": [("土壤养分", "value")],
    "light_intensity": [("光照强度", "value")],
}

# 会话内共享数据
DATA_KEY = "data"
RULE_KEY = "current_rule"


def _detect_mask(df: pd.DataFrame, col: str, method: str, **kw) -> pd.Series:
    if method == "iqr":
        return an.detect_outliers_iqr(df, col, kw.get("factor", 1.5))
    if method == "zscore":
        return an.detect_outliers_zscore(df, col, kw.get("threshold", 3.0))
    return an.detect_outliers_isolation_forest(df, [col], **kw)


def _load_source() -> tuple[pd.DataFrame | None, str | None]:
    """返回 (工作 df, 目标数值列名)。df 至少含一列数值列。"""
    source = st.segmented_control("数据源", ["数据库传感器数据", "上传文件"], default="数据库传感器数据")

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
        col = st.selectbox("选择数值列", numeric, key="up_col")
        return df, col

    metric = st.selectbox("选择指标", list(METRIC_COLS.keys()))
    sub = st.selectbox("选择字段", METRIC_COLS[metric], format_func=lambda x: x[0])
    _, col = sub
    since = datetime.now() - timedelta(days=90)
    df = cache.cached_sensor_df(metric, col, since.isoformat(), limit=5000)
    if df.empty:
        st.warning("暂无数据，请先运行 `python -m smart_farm.data.seed` 生成演示数据。")
        return None, None
    df = df.rename(columns={"value": col, "timestamp": "timestamp"})
    return df, col


def _ensure_data() -> tuple[pd.DataFrame | None, str | None]:
    """加载数据源并写入 session_state。返回 (df, 目标列)。"""
    df, col = _load_source()
    if df is None or col is None:
        return None, None
    st.session_state[DATA_KEY] = df
    st.success(f"已加载 **{len(df)}** 行，目标列：`{col}`（缺失 {int(df[col].isnull().sum())} 个）")
    return df, col


def _get_data() -> pd.DataFrame | None:
    return st.session_state.get(DATA_KEY)


def _tab_rules(df: pd.DataFrame) -> None:
    st.subheader("规则模板")
    template = st.selectbox("选择模板", ["农业数据标准清洗流程", "机器学习清洗模板", "自定义规则（自动分析）"])
    if st.button("应用模板并查看效果", type="primary", icon=":material/tune:"):
        if template == "农业数据标准清洗流程":
            rule = cs.create_agricultural_standard_template()
        elif template == "机器学习清洗模板":
            rule = cs.create_machine_learning_template(df)
        else:
            rule = cs.create_template_rule("自定义规则", df)
        st.session_state[RULE_KEY] = rule
        with st.spinner("清洗中..."):
            out, report = cs.DataCleaner().apply_rule(df, rule)
        qa = report["quality_after"]
        qb = report["quality_before"]
        c1, c2, c3 = st.columns(3)
        c1.metric("行数", qa["total_rows"], qb["total_rows"] - qa["total_rows"])
        c2.metric("完整率", f"{qa['completeness']}%", round(qa["completeness"] - qb["completeness"], 2))
        c3.metric("缺失值", qa["missing_cells"], qb["missing_cells"] - qa["missing_cells"])
        st.code(cs.DataCleaner().generate_report(report), language="text")
        st.session_state[DATA_KEY] = out

    st.markdown("**规则管理**")
    c1, c2 = st.columns(2)
    with c1:
        if st.download_button(
            "导出规则 JSON",
            data=(st.session_state.get(RULE_KEY) or cs.create_agricultural_standard_template()).to_json(),
            file_name="cleaning_rule.json",
            mime="application/json",
            icon=":material/download:",
        ):
            pass
    with c2:
        uploaded = st.file_uploader("加载规则 JSON", type=["json"], key="rule_upload")
        if uploaded is not None:
            try:
                rule = cs.DataCleaningRule.from_dict(json.loads(uploaded.read()))
                st.session_state[RULE_KEY] = rule
                st.success(f"已加载规则：{rule.name}")
            except Exception as e:  # noqa: BLE001
                st.error(f"规则加载失败：{e}")


def _tab_basic(df: pd.DataFrame) -> pd.DataFrame:
    st.subheader("基础清洗")
    if st.button("删除重复行", icon=":material/content_cut:"):
        before = len(df)
        df = df.drop_duplicates().reset_index(drop=True)
        st.success(f"已删除 {before - len(df)} 行重复数据。")
        st.session_state[DATA_KEY] = df
        return df

    st.markdown("**按类型批量删列**（自动保护 timestamp 列）")
    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    cat_cols = df.select_dtypes(include=["object", "category"]).columns.tolist()
    time_cols = df.select_dtypes(include=["datetime"]).columns.tolist()
    drop_numeric = st.checkbox(f"删除数值列（{len(numeric_cols)} 列）")
    drop_cat = st.checkbox(f"删除分类列（{len(cat_cols)} 列）")
    drop_time = st.checkbox(f"删除时间列（{len(time_cols)} 列）")

    to_drop: list[str] = []
    if drop_numeric:
        to_drop += numeric_cols
    if drop_cat:
        to_drop += cat_cols
    if drop_time:
        to_drop += [c for c in time_cols if c != "timestamp"]
    if "timestamp" in to_drop:
        to_drop.remove("timestamp")  # 保护时间列

    manual = st.multiselect("手动选择要删除的列", df.columns.tolist(), key="manual_drop")
    to_drop = list(dict.fromkeys(to_drop + manual))

    if st.button("执行删列", icon=":material/delete:"):
        if to_drop:
            df = df.drop(columns=to_drop)
            st.success(f"已删除列：{', '.join(to_drop)}")
            st.session_state[DATA_KEY] = df
        else:
            st.info("未选择任何列。")
    return df


def _tab_missing(df: pd.DataFrame) -> pd.DataFrame:
    st.subheader("缺失值处理")
    missing_cols = [c for c in df.columns if df[c].isnull().any()]
    if not missing_cols:
        st.info("当前数据没有缺失值。")
        return df

    chosen: dict[str, str] = {}
    for col in missing_cols:
        chosen[col] = st.selectbox(
            f"选择处理 {col} 缺失值的方法",
            ["保持不变", "删除", "填充平均值", "填充中位数", "填充众数"],
            key=f"miss_{col}",
        )
    if st.button("执行缺失值处理", type="primary", icon=":material/play_arrow:"):
        out = df.copy()
        for col, method in chosen.items():
            if method == "保持不变":
                continue
            if method == "删除":
                before = len(out)
                out = out.dropna(subset=[col]).reset_index(drop=True)
                st.info(f"{col}：删除 {before - len(out)} 行。")
            else:
                m = {"填充平均值": "mean", "填充中位数": "median", "填充众数": "mode"}[method]
                out, report = cs.fill_missing_with_flag(out, col, m)
                st.success(f"{col}：已填充 {report['filled']} 个缺失值，标识列 {report['flag_column']}。")
        st.session_state[DATA_KEY] = out
        st.rerun()
    return df


def _tab_anomaly(df: pd.DataFrame) -> pd.DataFrame:
    st.subheader("异常值检测与清除")
    method_label = st.radio(
        "选择异常值检测方法",
        ["四分位距法 (IQR)", "Z-Score 法", "孤立森林算法"],
        horizontal=True,
    )
    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    if not numeric_cols:
        st.info("没有数值列。")
        return df

    params: dict = {}
    if method_label == "四分位距法 (IQR)":
        method = "iqr"
        params["factor"] = st.slider("IQR 倍数", 1.0, 5.0, 1.5, 0.5)
    elif method_label == "Z-Score 法":
        method = "zscore"
        params["threshold"] = st.slider("Z-Score 阈值", 1.0, 10.0, 3.0, 0.5)
    else:
        method = "isolation_forest"
        params["contamination"] = st.slider("异常比例估计", 0.01, 0.5, 0.1, 0.01)
        params["n_estimators"] = st.slider("树数量", 50, 500, 100, 10)
        samples = st.slider("样本比例", 0.1, 1.0, 1.0, 0.05)
        params["max_samples"] = "auto" if samples >= 1.0 else samples

    cols = st.multiselect("选择检测列", numeric_cols, default=numeric_cols[:3])
    if not cols:
        st.info("请至少选择一列。")
        return df

    if st.button("检测异常值", type="primary", icon=":material/search:"):
        anomalies: dict[str, list] = {}
        with st.spinner("检测中..."):
            for col in cols:
                anomalies[col] = df.index[_detect_mask(df, col, method, **params)].tolist()
        st.session_state["anomalies"] = anomalies
        summary = an.get_anomaly_summary(anomalies)
        for col, s in summary.items():
            st.metric(f"{col} 异常点", f"{s['count']} 个（{s['percentage']}%）")
        st.json({k: v[:20] for k, v in anomalies.items()}, expanded=False)

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df.index, y=df[cols[0]], mode="lines", name=cols[0],
                                 line={"color": "#185FA5"}))
        if anomalies[cols[0]]:
            fig.add_trace(go.Scatter(
                x=anomalies[cols[0]], y=df.loc[anomalies[cols[0]], cols[0]],
                mode="markers", name="异常", marker={"color": "#E24B4A", "symbol": "x", "size": 10},
            ))
        fig.update_layout(height=360, xaxis_title="序号", yaxis_title=cols[0])
        st.plotly_chart(fig, width="stretch")

    if st.button("清除检测到的异常值", icon=":material/delete_sweep:"):
        anomalies = st.session_state.get("anomalies", {})
        if not anomalies:
            st.warning("请先执行异常值检测。")
        else:
            before = len(df)
            df = an.remove_anomalies(df, anomalies)
            st.success(f"已清除 {before - len(df)} 行异常数据。")
            st.session_state[DATA_KEY] = df
            st.session_state["anomalies"] = {}
            st.rerun()
    return df


def _tab_export(df: pd.DataFrame) -> None:
    st.subheader("数据导出")
    fmt = st.selectbox("导出格式", ["CSV", "Excel", "JSON"])
    if fmt == "CSV":
        data = df.to_csv(index=False).encode(), "text/csv", "csv"
    elif fmt == "Excel":
        buf = BytesIO()
        df.to_excel(buf, index=False, engine="openpyxl")
        data = buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    else:
        data = df.to_json(orient="records", force_ascii=False).encode(), "application/json", "json"
    st.download_button(
        f"下载 {fmt} 文件",
        data=data[0],
        file_name=f"cleaned_data.{data[2]}",
        mime=data[1],
        icon=":material/download:",
    )


st.title("数据清洗与异常检测")

df, col = _ensure_data()
if df is None or col is None:
    st.stop()

tab1, tab2, tab3, tab4, tab5 = st.tabs(["规则模板", "基础清洗", "缺失值", "异常值", "导出"])
with tab1:
    _tab_rules(_get_data() if _get_data() is not None else df)
with tab2:
    _tab_basic(_get_data() if _get_data() is not None else df)
with tab3:
    _tab_missing(_get_data() if _get_data() is not None else df)
with tab4:
    _tab_anomaly(_get_data() if _get_data() is not None else df)
with tab5:
    _tab_export(_get_data() if _get_data() is not None else df)
