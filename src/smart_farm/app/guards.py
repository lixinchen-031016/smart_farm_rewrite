"""UI 层公共守卫（角色校验等）。"""

import streamlit as st


def require_admin() -> bool:
    """管理员专属页守卫：非管理员显示提示并返回 False。"""
    if st.session_state.get("role") != "admin":
        st.warning(":material/lock: 该页面仅管理员可访问。")
        return False
    return True
