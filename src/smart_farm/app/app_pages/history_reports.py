"""历史报告查看页面（对齐旧库 history_report_viewer，仅预测报告部分）。

浏览 predictions_exports/ 下的预测归档（CSV + Markdown），支持搜索/排序/预览/下载/删除。
AI 洞察报告已随 AI 模块取消，不在此展示。
"""

import re
from pathlib import Path

import pandas as pd
import streamlit as st

from smart_farm.app.guards import require_admin
from smart_farm.services.prediction_archive import _default_exports_dir

# 展示用的文件后缀（md 预览 + csv 下载）
MD_SUFFIX = ".md"
CSV_SUFFIX = ".csv"


def _list_reports(base_dir: Path) -> list[dict]:
    """列出归档目录中的预测报告（按时间戳降序）。"""
    reports = []
    for f in sorted(base_dir.glob(f"*{MD_SUFFIX}")):
        ts_match = re.search(r"_(\d{8})_(\d{6})_", f.stem)
        ts = f"{ts_match.group(1)} {ts_match.group(2)}" if ts_match else "未知时间"
        size = f.stat().st_size if f.exists() else 0
        reports.append({
            "name": f.stem,
            "title": f.name,
            "md_path": f,
            "csv_path": f.with_suffix(CSV_SUFFIX),
            "timestamp": ts,
            "size": size,
            "size_text": f"{size / 1024:.1f} KB" if size >= 1024 else f"{size} B",
        })
    reports.sort(key=lambda r: r["timestamp"], reverse=True)
    return reports


def _search_reports(reports: list[dict], keyword: str) -> list[dict]:
    kw = keyword.strip().lower()
    if not kw:
        return reports
    return [r for r in reports if kw in r["name"].lower() or kw in r["title"].lower()]


def _render_preview(report: dict) -> str:
    """读取 md 前 60 行作为预览（对齐旧版前 50 行语义）。"""
    lines = report["md_path"].read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[:60])


st.title("历史报告")
if not require_admin():  # 修复：管理页二次守卫（纵深防御）
    st.stop()

base_dir = _default_exports_dir()
if not base_dir.exists():
    st.info("暂无预测报告。执行预测后自动保存至 predictions_exports/。")
    st.stop()

reports = _list_reports(base_dir)
if not reports:
    st.info("暂无预测报告。执行预测后自动保存至 predictions_exports/。")
    st.stop()

c1, c2, c3 = st.columns(3)
c1.metric("预测报告", len(reports))
c2.metric("总占用", f"{sum(r['size'] for r in reports) / 1024:.1f} KB")
c3.metric("可下载 CSV", sum(r["csv_path"].exists() for r in reports))

keyword = st.text_input("搜索报告", placeholder="输入关键词（文件名/标题）")
sort_label = st.selectbox("排序", ["最新优先", "最旧优先"])
reports = _search_reports(reports, keyword)
if sort_label == "最旧优先":
    reports = list(reversed(reports))

if not reports:
    st.info("没有匹配的报告。")
    st.stop()

# 展示前 10 个
visible = reports[:10]
st.caption(f"共 {len(reports)} 份报告，展示前 {len(visible)} 份。")

for i, report in enumerate(visible, 1):
    st.subheader(f"报告 {i}：{report['title']}")
    st.info(f"生成时间：{report['timestamp']} ｜ 大小：{report['size_text']}")
    with st.expander("预览内容", expanded=False):
        st.code(_render_preview(report), language="markdown")
    c1, c2 = st.columns(2)
    with c1:
        if report["csv_path"].exists():
            st.download_button(
                "下载 CSV",
                data=report["csv_path"].read_bytes(),
                file_name=report["csv_path"].name,
                mime="text/csv",
                icon=":material/download:",
                key=f"dl_{report['name']}",
            )
        else:
            st.download_button(
                "下载报告 (MD)",
                data=report["md_path"].read_bytes(),
                file_name=report["md_path"].name,
                mime="text/markdown",
                icon=":material/download:",
                key=f"dl_md_{report['name']}",
            )
    with c2:
        if st.button("删除", icon=":material/delete:", key=f"del_{report['name']}"):
            for p in (report["md_path"], report["csv_path"]):
                if p.exists():
                    p.unlink()
            st.success(f"已删除 {report['title']}。")
            st.rerun()

# 历史表格（供快速概览）
st.subheader("全部记录")
df = pd.DataFrame([{
    "名称": r["name"],
    "时间": r["timestamp"],
    "大小": r["size_text"],
} for r in reports])
st.dataframe(df, width="stretch")
