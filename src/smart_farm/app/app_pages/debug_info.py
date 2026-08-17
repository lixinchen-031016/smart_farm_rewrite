"""调试信息页面（管理员专属，DEBUG_MODE 门控）。

对齐旧版 debug_utils 的精简版：环境信息 / 数据库状态 / 缓存统计 / 异常模拟。
"""

import os

import pandas as pd
import streamlit as st

from smart_farm.app.guards import require_admin
from smart_farm.config import get_settings
from smart_farm.data import repositories as repo
from smart_farm.data.database import get_session
from smart_farm.services import errors

st.title("调试信息")
if not require_admin():
    st.stop()

_settings = get_settings()
if not _settings.debug:
    st.error("调试模式未启用。请在 .env 中设置 DEBUG=true 后重启。")
    st.stop()

tab1, tab2, tab3, tab4 = st.tabs(["环境信息", "数据库状态", "缓存统计", "异常模拟"])

with tab1:
    import re as _re

    _url = str(_settings.database_url)
    # 修复：完整掩码数据库口令（旧版仅替换首个 :// 导致密码泄露）
    _masked_url = _re.sub(r"(://[^:/]+):[^@/]+@", r"\1:***@", _url)
    st.json({
        "cwd": os.getcwd(),
        "python": __import__("sys").version.split()[0],
        "debug_mode": _settings.debug,
        "database_url": _masked_url,
        "env_keys": [k for k in os.environ if k.startswith(("DEBUG_", "DATABASE_", "SECRET", "LOG_"))],
    })

with tab2:
    with get_session() as s:
        user_count = len(repo.list_users(s))
        log_count = repo.count_logs(s)  # 修复：聚合计数替代全量拉取
        sensor_counts = {name: repo.count_sensor_readings(s, m) for m, name in
                         [("air_temperature_humidity", "空气温湿度"), ("soil_moisture", "土壤湿度"),
                          ("soil_nutrient", "土壤养分"), ("light_intensity", "光照强度")]}
    c1, c2 = st.columns(2)
    c1.metric("用户数", user_count)
    c2.metric("日志数", log_count)
    st.dataframe(pd.DataFrame(
        [{"指标": k, "记录数": v} for k, v in sensor_counts.items()]
    ), width="stretch")

with tab3:
    st.json({} if not hasattr(st, "_cache_stats") else {
        "data_caches": len(st.cache_data.get_all() if hasattr(st.cache_data, "get_all") else {}),
    })
    st.caption("Streamlit 缓存统计（DataFrame 缓存项数量）。")

with tab4:
    st.caption("触发一个异常以验证错误处理体系。")
    err_type = st.selectbox("异常类型", ["ValueError", "TypeError", "RuntimeError"])
    if st.button("模拟异常", icon=":material/bug_report:"):
        if err_type == "ValueError":
            e: Exception = ValueError("模拟的 ValueError")
        elif err_type == "TypeError":
            e = TypeError("模拟的 TypeError")
        else:
            e = RuntimeError("模拟的 RuntimeError")
        info = errors.handle_exception(e, username=st.session_state.get("username"), operation="debug_sim")
        st.json(info, expanded=False)
        st.error(f"已捕获 {info['error_type']}：{info['message']}")
