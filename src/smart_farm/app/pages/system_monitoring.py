"""系统监控页面（管理员专属）。展示数据量、用户/日志规模、大棚与近期数据质量。"""

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from smart_farm.app import cache
from smart_farm.app.guards import require_admin
from smart_farm.data import repositories as repo
from smart_farm.data.database import get_session
from smart_farm.services import cleaning_service as cs

METRICS = {
    "air_temperature_humidity": ("温度", "temperature"),
    "soil_moisture": ("土壤湿度", "value"),
    "soil_nutrient": ("土壤养分", "value"),
    "light_intensity": ("光照强度", "value"),
}


def show() -> None:
    st.title("📡 系统监控")
    if not require_admin():
        return

    with get_session() as s:
        total_users = len(repo.list_users(s))
        total_logs = len(repo.get_logs(s, limit=100000))
        greenhouses = repo.list_greenhouses(s)
        counts = {name: repo.count_sensor_readings(s, m) for m, (name, _) in METRICS.items()}

    # 概览指标卡
    c1, c2, c3 = st.columns(3)
    c1.metric("注册用户", total_users)
    c2.metric("操作日志", total_logs)
    c3.metric("大棚数量", len(greenhouses))

    # 各指标数据量
    st.subheader("传感器数据量")
    cnt_df = pd.DataFrame(
        [{"指标": name, "记录数": n} for name, n in counts.items()]
    )
    st.bar_chart(cnt_df.set_index("指标")["记录数"])

    # 大棚列表
    st.subheader("大棚列表")
    if greenhouses:
        gh_df = pd.DataFrame(
            [{"ID": g.id, "名称": g.name, "位置": g.location} for g in greenhouses]
        )
        st.dataframe(gh_df, width="stretch")
    else:
        st.info("尚未登记大棚。")

    # 近 7 天数据质量抽样
    st.subheader("近 7 天数据质量抽样")
    since = datetime.now() - timedelta(days=7)
    for metric, (label, col) in METRICS.items():
        df = cache.cached_sensor_df(metric, col, since.isoformat(), limit=2000)
        if df.empty:
            continue
        q = cs.assess_quality(df.rename(columns={"value": col}))
        st.markdown(f"- **{label}**：{q['total_rows']} 行，完整率 {q['completeness']}%")
