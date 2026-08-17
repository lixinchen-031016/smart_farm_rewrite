"""操作日志查看页面（管理员专属）。按用户名 / 操作类型筛选，分页展示。"""

import pandas as pd
import streamlit as st

from smart_farm.app.guards import require_admin
from smart_farm.data import repositories as repo
from smart_farm.data.database import get_session

PAGE_SIZE = 50


def show() -> None:
    st.title("📜 操作日志")
    if not require_admin():
        return

    with get_session() as s:
        users = sorted({log.username for log in repo.get_logs(s, limit=500)})
        actions = sorted({log.action_type for log in repo.get_logs(s, limit=500)})

    c1, c2 = st.columns(2)
    with c1:
        username = st.selectbox("用户名", ["全部"] + users)
    with c2:
        action = st.selectbox("操作类型", ["全部"] + actions)

    page = st.number_input("页码", min_value=1, value=1, step=1)
    uname = None if username == "全部" else username
    atype = None if action == "全部" else action

    with get_session() as s:
        logs = repo.get_logs(s, username=uname, action_type=atype,
                             limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)

    if not logs:
        st.info("没有符合条件的日志。")
        return

    df = pd.DataFrame(
        [{
            "时间": log.log_time,
            "级别": log.log_level,
            "用户": log.username,
            "操作": log.action_type,
            "详情": log.action_details,
        } for log in logs]
    )
    st.dataframe(df, width="stretch")
    st.caption(f"每页 {PAGE_SIZE} 条；当前第 {page} 页。")
