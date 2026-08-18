"""系统监控页面（管理员专属）。

- tab1 数据量与质量（原有：用户/日志/大棚/数据量/质量抽样）
- tab2 psutil 实时监控（CPU/内存/磁盘 metric + 进程；未装 psutil 降级提示）
- tab3 性能分析（指标 + 优化建议）
- tab4 系统信息（CPU/内存详情）
"""

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from smart_farm.app import cache
from smart_farm.app.guards import require_admin
from smart_farm.data import repositories as repo
from smart_farm.data.database import get_session
from smart_farm.services import cleaning_service as cs
from smart_farm.services import system_service as sys_svc

METRICS = {
    "air_temperature_humidity": ("温度", "temperature"),
    "soil_moisture": ("土壤湿度", "value"),
    "soil_nutrient": ("土壤养分", "value"),
    "light_intensity": ("光照强度", "value"),
}

st.title("系统监控")
if not require_admin():
    st.stop()

tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["数据量与质量", "实时监控", "性能分析", "系统信息", "网络与磁盘 IO"],
    on_change="rerun",
)

with tab1:
    if not tab1.open:
        st.caption("切换到本标签页查看数据量与质量。")
    else:
        with get_session() as s:
            total_users = len(repo.list_users(s))
            total_logs = repo.count_logs(s)  # 修复：聚合计数替代全量拉取
            greenhouses = repo.list_greenhouses(s)
            counts = {name: repo.count_sensor_readings(s, m) for m, (name, _) in METRICS.items()}

        c1, c2, c3 = st.columns(3)
        c1.metric("注册用户", total_users)
        c2.metric("操作日志", total_logs)
        c3.metric("大棚数量", len(greenhouses))

        st.subheader("传感器数据量")
        cnt_df = pd.DataFrame([{"指标": name, "记录数": n} for name, n in counts.items()])
        st.bar_chart(cnt_df.set_index("指标")["记录数"])

        st.subheader("大棚列表")
        if greenhouses:
            gh_df = pd.DataFrame([{"ID": g.id, "名称": g.name, "位置": g.location} for g in greenhouses])
            st.dataframe(gh_df, width="stretch")
        else:
            st.info("尚未登记大棚。")

        st.subheader("近 7 天数据质量抽样")
        since = datetime.now() - timedelta(days=7)
        for metric, (label, col) in METRICS.items():
            df = cache.cached_sensor_df(metric, col, since.isoformat(), limit=2000)
            if df.empty:
                continue
            q = cs.assess_quality(df.rename(columns={"value": col}))
            st.markdown(f"- **{label}**：{q['total_rows']} 行，完整率 {q['completeness']}%")

with tab2:
    st.subheader("实时监控")
    if not tab2.open:
        st.caption("切换到本标签页查看实时监控。")
    elif not sys_svc.is_psutil_available():
        st.warning("未安装 psutil，实时监控不可用。请先安装项目完整依赖：`uv pip install -e .`。")
    else:
        metrics = sys_svc.collect_system_metrics()
        if not metrics:
            st.error("系统指标采集失败。")
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("CPU 使用率", f"{metrics['cpu_percent']}%",
                      delta="过高" if metrics["cpu_percent"] > 80 else None,
                      delta_color="inverse" if metrics["cpu_percent"] > 80 else "normal",
                      help="建议阈值 80%")
            c2.metric("内存使用率", f"{metrics['memory_percent']}%",
                      delta="过高" if metrics["memory_percent"] > 80 else None,
                      delta_color="inverse" if metrics["memory_percent"] > 80 else "normal",
                      help="建议阈值 80%")
            c3.metric("磁盘使用率", f"{metrics['disk_percent']}%",
                      delta="过高" if metrics["disk_percent"] > 85 else None,
                      delta_color="inverse" if metrics["disk_percent"] > 85 else "normal",
                      help="建议阈值 85%")
            st.caption(
                f"内存 {metrics['memory_used_gb']} / {metrics['memory_total_gb']} GB；"
                f"磁盘 {metrics['disk_used_gb']} / {metrics['disk_total_gb']} GB"
            )

            st.subheader("当前进程")
            proc = sys_svc.collect_process_info()
            if proc:
                st.dataframe(pd.DataFrame(proc), width="stretch")

with tab3:
    st.subheader("性能分析")
    if not tab3.open:
        st.caption("切换到本标签页查看性能分析。")
    elif not sys_svc.is_psutil_available():
        st.warning("未安装 psutil，性能分析不可用。请先安装项目完整依赖：`uv pip install -e .`。")
    else:
        metrics = sys_svc.collect_system_metrics()
        if metrics:
            c1, c2, c3 = st.columns(3)
            c1.metric("CPU", f"{metrics['cpu_percent']}%")
            c2.metric("内存", f"{metrics['memory_percent']}%")
            c3.metric("磁盘", f"{metrics['disk_percent']}%")
            recs = sys_svc.get_performance_recommendations(
                metrics["cpu_percent"], metrics["memory_percent"], metrics["disk_percent"]
            )
            for i, rec in enumerate(recs, 1):
                st.markdown(f"{i}. {rec}")

with tab4:
    st.subheader("系统信息")
    if not tab4.open:
        st.caption("切换到本标签页查看系统信息。")
    elif not sys_svc.is_psutil_available():
        st.warning("未安装 psutil，系统信息不可用。请先安装项目完整依赖：`uv pip install -e .`。")
    else:
        info = sys_svc.collect_system_info()
        if info:
            st.json(info)

with tab5:
    st.subheader("网络与磁盘 IO")
    if not tab5.open:
        st.caption("切换到本标签页查看网络与磁盘 IO。")
    elif not sys_svc.is_psutil_available():
        st.warning("未安装 psutil，网络与磁盘 IO 不可用。请先安装项目完整依赖：`uv pip install -e .`。")
    else:
        st.markdown("**网络 IO**（采样 0.5 秒计算速率）")
        net = sys_svc.collect_network_io(interval=0.5)
        if net:
            c1, c2 = st.columns(2)
            c1.metric("发送速率", f"{net['send_rate_mb_s']} MB/s")
            c2.metric("接收速率", f"{net['recv_rate_mb_s']} MB/s")
            st.caption(
                f"累计发送 {net['bytes_sent'] / 1024 / 1024:.1f} MB"
                f"（{net['packets_sent']:,} 包）；"
                f"累计接收 {net['bytes_recv'] / 1024 / 1024:.1f} MB"
                f"（{net['packets_recv']:,} 包）"
            )
        else:
            st.info("网络 IO 采集不可用。")

        st.markdown("**磁盘 IO**（自系统启动累计）")
        disk = sys_svc.collect_disk_io()
        if disk:
            c3, c4, c5, c6 = st.columns(4)
            c3.metric("读次数", f"{disk['read_count']:,}")
            c4.metric("写次数", f"{disk['write_count']:,}")
            c5.metric("读取量", f"{disk['read_mb']:,} MB")
            c6.metric("写入量", f"{disk['write_mb']:,} MB")
            st.caption(
                f"读耗时 {disk['read_time_s']} s；写耗时 {disk['write_time_s']} s。"
                "（部分平台/虚拟环境不支持磁盘 IO 统计）"
            )
        else:
            st.info("当前平台不支持磁盘 IO 统计（psutil.disk_io_counters 不可用）。")

        st.markdown("**磁盘分区**")
        parts = sys_svc.collect_disk_partitions()
        if parts:
            st.dataframe(pd.DataFrame(parts), width="stretch")
        else:
            st.info("未获取到分区信息。")
