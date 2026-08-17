"""Streamlit 应用入口（UI 层，仅渲染 + 调用 services）。

重写要点：
- 登录态存于 `st.session_state`，**JWT 不写入 URL query_params**（修复旧版泄露风险）。
- 路由基于 `st.session_state['page']`，不依赖 URL 中的令牌。
- 业务逻辑全部委托给 `services/*`，本文件不含算法。
"""

import streamlit as st

from smart_farm.app import auth_ui
from smart_farm.app.pages import dashboard, data_overview, decision, prediction

st.set_page_config(
    page_title="智慧大棚数据管理平台",
    page_icon="🌱",
    layout="wide",
    menu_items={"Get Help": None, "Report a bug": None, "About": None},
)

# 菜单 -> 页面 key
MENU = {
    "综合监控仪表板": "dashboard",
    "数据概览": "data_overview",
    "本地数据预测": "prediction",
    "自动化决策": "decision",
}


def _ensure_session_defaults() -> None:
    st.session_state.setdefault("logged_in", False)
    st.session_state.setdefault("page", "dashboard")
    st.session_state.setdefault("role", "user")


def _sidebar() -> None:
    with st.sidebar:
        st.markdown(
            f"**当前用户：** <span style='color:#4CAF50'>{st.session_state['username']}</span>  "
            f"({'管理员' if st.session_state['role']=='admin' else '普通用户'})",
            unsafe_allow_html=True,
        )
        if st.button("🚪 退出登录"):
            st.session_state.clear()
            st.rerun()

        choice = st.radio("功能菜单", list(MENU.keys()), key="menu")
        page = MENU[choice]
        if page != st.session_state["page"]:
            st.session_state["page"] = page
            st.rerun()


def main() -> None:
    _ensure_session_defaults()

    if not st.session_state["logged_in"]:
        auth_ui.show_auth()
        return

    _sidebar()

    page = st.session_state["page"]
    if page == "dashboard":
        dashboard.show()
    elif page == "data_overview":
        data_overview.show()
    elif page == "prediction":
        prediction.show()
    elif page == "decision":
        decision.show()
    else:
        st.session_state["page"] = "dashboard"
        st.rerun()


if __name__ == "__main__":
    main()
