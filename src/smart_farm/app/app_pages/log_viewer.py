"""操作日志查看页面（管理员专属）。

对齐旧库 log_viewer + log_analyzer：
- 时间快捷按钮（1h/6h/24h/7d/自定义）+ 日期范围
- 关键词搜索（普通/正则）+ 级别/用户/操作类型过滤（修复旧库 AND 恒真死代码：
  搜索模式 AND/OR/EXACT 全部可用）
- 4 tab：日志详情 / 统计分析 / 操作链追踪 / 异常告警
- 日志下载
"""

import re
from datetime import datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import plotly.express as px
import streamlit as st

from smart_farm.app.guards import require_admin
from smart_farm.data import repositories as repo
from smart_farm.data.database import get_session
from smart_farm.services import log_analysis_service as las

PAGE_SIZE = 100
TIME_RANGES = {"最近 1 小时": 1, "最近 6 小时": 6, "最近 24 小时": 24, "最近 7 天": 24 * 7}

st.title("操作日志")
if not require_admin():
    st.stop()

# ---------------- 取数（修复：按时间窗 DB 层过滤 + 缓存，避免每 rerun 全量拉 10 万条） ----------------
@st.cache_data(ttl=60, max_entries=16)
def _load_logs(start_iso: str, end_iso: str) -> list[dict]:
    """缓存指定时间窗内的日志（DTO，可 pickle）。"""
    start_dt = datetime.fromisoformat(start_iso)
    end_dt = datetime.fromisoformat(end_iso)
    with get_session() as s:
        rows = repo.get_logs(s, start=start_dt, end=end_dt, limit=50000)
    return [
        {
            "log_time": r.log_time,
            "log_level": r.log_level,
            "username": r.username,
            "action_type": r.action_type,
            "action_details": r.action_details,
        }
        for r in rows
    ]


@st.cache_data(ttl=300, max_entries=8)
def _load_users_and_actions() -> tuple[list[str], list[str]]:
    with get_session() as s:
        users = sorted({log.username for log in repo.get_logs(s, limit=1000)})
        actions = sorted({log.action_type for log in repo.get_logs(s, limit=1000)})
    return users, actions


all_users, all_actions = _load_users_and_actions()

with st.container(horizontal=True):
    time_choice = st.selectbox("时间范围", list(TIME_RANGES.keys()) + ["自定义"], index=2)
    keyword = st.text_input("关键词", placeholder="输入关键词或多个关键词（空格分隔）")
    use_regex = st.checkbox("使用正则表达式")

start_dt = datetime.now() - timedelta(hours=TIME_RANGES.get(time_choice, 24))
end_dt = datetime.now()
if time_choice == "自定义":
    c1, c2 = st.columns(2)
    with c1:
        start_date = st.date_input("起始日期", value=start_dt.date())
    with c2:
        end_date = st.date_input("结束日期", value=end_dt.date())
    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date, datetime.max.time())

with st.expander("高级过滤"):
    level = st.selectbox("日志级别", ["ALL", "INFO", "WARNING", "ERROR", "DEBUG"])
    user = st.selectbox("用户名", ["ALL"] + all_users)
    action = st.selectbox("操作类型", ["ALL"] + all_actions)
    search_mode = st.radio("搜索模式", ["AND (且)", "OR (或)", "EXACT (精确)"], horizontal=True)

logs = [SimpleNamespace(**d) for d in _load_logs(start_dt.isoformat(), end_dt.isoformat())]
if level != "ALL":
    logs = [log for log in logs if log["log_level"] == level]
if user != "ALL":
    logs = [log for log in logs if log["username"] == user]
if action != "ALL":
    logs = [log for log in logs if log["action_type"] == action]

# 关键词过滤（AND/OR/EXACT 全可用，修复旧库恒 AND 死代码）
if keyword.strip():
    terms = [t for t in keyword.split() if t]
    if use_regex:
        try:
            pattern = re.compile(keyword)
            logs = [log for log in logs if pattern.search(log.action_details or "")]
        except re.error as e:
            st.error(f"正则表达式错误：{e}")
            logs = []
    elif search_mode == "EXACT (精确)":
        logs = [log for log in logs if keyword.strip() in (log.action_details or "")]
    elif search_mode == "AND (且)":
        logs = [log for log in logs if all(t in (log.action_details or "") for t in terms)]
    else:  # OR (或)
        logs = [log for log in logs if any(t in (log.action_details or "") for t in terms)]

total = len(logs)
logs = sorted(logs, key=lambda log: log.log_time or datetime.min, reverse=True)

tab1, tab2, tab3, tab4 = st.tabs(["日志详情", "统计分析", "操作链追踪", "异常告警"])

with tab1:
    st.caption(f"共 {total} 条匹配日志，展示前 {PAGE_SIZE} 条。")
    page_logs = logs[:PAGE_SIZE]
    if not page_logs:
        st.info("没有符合条件的日志。")
    else:
        df = pd.DataFrame([{
            "时间": log.log_time,
            "级别": log.log_level,
            "用户": log.username,
            "操作": log.action_type,
            "详情": log.action_details,
        } for log in page_logs])
        st.dataframe(df, width="stretch")
        # 日志下载（修复：用原生 download_button，导出全部匹配日志）
        log_text = "\n".join(
            f"[{log.log_time}] [{log.log_level}] {log.username} | {log.action_type} | {log.action_details}"
            for log in logs
        )
        st.download_button(
            "下载日志",
            data=log_text.encode("utf-8"),
            file_name=f"logs_{datetime.now():%Y%m%d_%H%M%S}.log",
            mime="text/plain",
            icon=":material/download:",
        )

with tab2:
    st.subheader("统计分析")
    if not logs:
        st.info("无数据。")
    else:
        c1, c2 = st.columns(2)
        with c1:
            level_counts = pd.Series([log.log_level for log in logs]).value_counts()
            st.metric("级别分布", f"{len(level_counts)} 种")
            st.plotly_chart(px.pie(level_counts, values=level_counts.values, names=level_counts.index),
                            width="stretch")
        with c2:
            top_actions = las.analyze_top_actions(logs, limit=10, hours=24 * 365)
            st.caption("操作类型 Top10")
            st.bar_chart(pd.Series(dict(top_actions)).sort_values(ascending=False))
        st.subheader("错误统计（按操作）")
        err_stats = las.analyze_error_stats(logs, hours=24 * 365)
        if err_stats:
            st.dataframe(pd.DataFrame(
                [{"操作类型": k, "错误数": v} for k, v in sorted(err_stats.items(), key=lambda x: -x[1])]
            ), width="stretch")
        else:
            st.info("无 ERROR 日志。")

with tab3:
    st.subheader("操作链追踪")
    if not logs:
        st.info("无数据。")
    else:
        chain_user = st.selectbox("选择用户", ["全部"] + sorted({log.username for log in logs}))
        chain_logs = [log for log in logs if chain_user == "全部" or log.username == chain_user]
        if chain_logs:
            timeline = pd.DataFrame([{
                "时间": log.log_time,
                "用户": log.username,
                "操作": log.action_type,
                "详情": log.action_details,
            } for log in chain_logs])
            st.dataframe(timeline, width="stretch")
            risk_keywords = ["删除", "修改", "恢复", "备份"]
            risk = [log for log in chain_logs if any(k in (log.action_details or "") for k in risk_keywords)]
            st.caption(f"风险操作（删除/修改/恢复/备份）：最近 {min(5, len(risk))} 条")
            for log in risk[:5]:
                st.warning(f"[{log.log_time}] {log.username} | {log.action_type} | {log.action_details}")
        else:
            st.info("该用户无操作记录。")

with tab4:
    st.subheader("异常告警")
    if not logs:
        st.info("无数据。")
    else:
        error_count = sum(1 for log in logs if log.log_level == "ERROR")
        warning_count = sum(1 for log in logs if log.log_level == "WARNING")
        error_rate = error_count / total * 100 if total else 0
        c1, c2, c3 = st.columns(3)
        c1.metric("错误数", error_count)
        c2.metric("警告数", warning_count)
        c3.metric("错误率", f"{error_rate:.1f}%")
        threshold = st.slider("错误率告警阈值 (%)", 0, 100, 10)
        if error_rate > threshold:
            st.error(f"错误率 {error_rate:.1f}% 超过阈值 {threshold}%，请关注。")
        else:
            st.success(f"错误率 {error_rate:.1f}% 在阈值 {threshold}% 内。")
        peaks = las.analyze_error_peaks(logs, hours=24 * 365)
        if peaks:
            st.caption("错误时段分布（按小时）")
            st.bar_chart(pd.Series(peaks))
