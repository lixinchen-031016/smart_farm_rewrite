"""认证 UI（登录 / 注册）。仅渲染与调用 auth_service。"""

from datetime import datetime

import streamlit as st

from smart_farm.data import repositories as repo
from smart_farm.data.database import get_session
from smart_farm.services import auth_service as auth


def _do_login(username: str, password: str) -> bool:
    if auth.limiter.is_blocked(username):
        st.error("尝试次数过多，请稍后再试。")
        return False
    with get_session() as s:
        user = repo.get_user_by_username(s, username)
        if not user or not auth.verify_password(password, user.password):
            auth.limiter.register_failure(username)
            st.error("用户名或密码错误。")
            return False
        auth.limiter.reset(username)
        user.last_login_time = datetime.now()
        token = auth.create_access_token(user.username, user.role)
        # 登录态仅存 session_state，令牌不写入 URL
        st.session_state["logged_in"] = True
        st.session_state["username"] = user.username
        st.session_state["role"] = user.role
        st.session_state["token"] = token  # 仅供后续服务端调用，不入 URL
        repo.add_log(s, "INFO", user.username, "登录", "用户登录成功")
        return True


def show_auth() -> None:
    st.title("智慧大棚数据管理平台")
    st.caption("智慧农业数据接入、清洗、分析、预测与运维一体化平台。")
    mode = st.segmented_control("选择操作", ["登录", "注册"], default="登录")

    if mode == "登录":
        with st.form("login_form"):
            username = st.text_input("用户名")
            password = st.text_input("密码", type="password")
            if st.form_submit_button("登录", type="primary", icon=":material/login:"):
                if _do_login(username, password):
                    st.success("登录成功！")
                    st.rerun()
    else:
        with st.form("register_form"):
            username = st.text_input("用户名")
            password = st.text_input("密码", type="password")
            confirm = st.text_input("确认密码", type="password")
            if st.form_submit_button("注册", type="primary", icon=":material/person_add:"):
                if not username:
                    st.error("用户名不能为空。")
                elif password != confirm:
                    st.error("两次密码不一致。")
                elif not auth.check_password_complexity(password):
                    st.warning("密码至少 8 位，且含大小写字母、数字、特殊字符。")
                else:
                    with get_session() as s:
                        if repo.get_user_by_username(s, username):
                            st.error("该用户名已存在。")
                        else:
                            repo.create_user(s, username, auth.hash_password(password), role="user")
                            repo.add_log(s, "INFO", username, "注册", "新用户注册")
                            st.success("注册成功，请登录。")
