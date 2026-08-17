"""Streamlit 应用入口（UI 层，仅渲染 + 调用 services）。

遵循 developing-with-streamlit 技能：
- `st.navigation` + `st.Page` + `app_pages/` 目录（替代 legacy pages/ 自动发现与手动 MENU 路由）。
- 页面文件为直接脚本，不包裹 `show()` 函数。
- 登录态存于 `st.session_state`，**JWT 不写入 URL query_params**（修复旧版泄露风险）。
- 业务逻辑全部委托给 `services/*`，本文件不含算法。
"""

import streamlit as st

from smart_farm.app import auth_ui

st.set_page_config(
    page_title="智慧大棚数据管理平台",
    page_icon="🌱",
    layout="wide",
    menu_items={"Get Help": None, "Report a bug": None, "About": None},
)

st.session_state.setdefault("logged_in", False)
st.session_state.setdefault("role", "user")

# 未登录：只显示认证页，不构建导航
if not st.session_state["logged_in"]:
    auth_ui.show_auth()
    st.stop()

# 侧边栏用户信息 + 退出（原生元素，无自定义 HTML）
with st.sidebar:
    role_label = "管理员" if st.session_state["role"] == "admin" else "普通用户"
    st.caption(f"当前用户：**{st.session_state['username']}**（{role_label}）")
    if st.button("退出登录", icon=":material/logout:"):
        st.session_state.clear()
        st.rerun()

# 页面清单（普通用户不注入管理页，且管理页内部仍有 require_admin 守卫）
_pages = [
    st.Page("app_pages/dashboard.py", title="综合监控仪表板", icon=":material/dashboard:", default=True),
    st.Page("app_pages/data_overview.py", title="数据概览", icon=":material/table_view:"),
    st.Page("app_pages/data_cleaning.py", title="数据清洗与异常", icon=":material/cleaning_services:"),
    st.Page("app_pages/data_analysis.py", title="数据分析", icon=":material/analytics:"),
    st.Page("app_pages/advanced_analysis.py", title="高级分析", icon=":material/insights:"),
    st.Page("app_pages/visualization.py", title="可视化", icon=":material/bar_chart:"),
    st.Page("app_pages/prediction.py", title="本地数据预测", icon=":material/timeline:"),
    st.Page("app_pages/decision.py", title="自动化决策", icon=":material/psychology:"),
]
if st.session_state["role"] == "admin":
    _pages += [
        st.Page("app_pages/history_reports.py", title="历史报告", icon=":material/history:"),
        st.Page("app_pages/user_management.py", title="用户管理", icon=":material/group:"),
        st.Page("app_pages/log_viewer.py", title="操作日志", icon=":material/receipt_long:"),
        st.Page("app_pages/system_monitoring.py", title="系统监控", icon=":material/monitor_heart:"),
        st.Page("app_pages/module_config.py", title="模块配置", icon=":material/tune:"),
        st.Page("app_pages/backup_restore.py", title="备份与恢复", icon=":material/save:"),
    ]

nav = st.navigation(_pages)
nav.run()
