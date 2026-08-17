"""认证 UI（登录 / 注册）。仅渲染与调用 auth_service / captcha_ui。"""

from datetime import datetime

import streamlit as st

from smart_farm.app import captcha_ui
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


def _register_user(username: str, password: str, role_choice: str) -> None:
    with get_session() as s:
        if repo.get_user_by_username(s, username):
            st.error("该用户名已存在。")
            return
        # 选「管理员」→ 走审批流程：存为普通用户 + admin_request 待审批（与原版一致）
        is_admin_apply = role_choice == "管理员"
        repo.create_user(
            s,
            username,
            auth.hash_password(password),
            role="user",
            admin_request=is_admin_apply,
        )
        if is_admin_apply:
            repo.add_log(s, "INFO", username, "注册", "新用户注册（管理员申请，待审批）")
            st.success("注册成功！管理员申请已提交，待管理员审批后生效，请先以普通用户身份登录。")
        else:
            repo.add_log(s, "INFO", username, "注册", "新用户注册")
            st.success("注册成功，请登录。")


def _render_password_strength(password: str) -> None:
    """注册页密码强度条（原生 st.progress，替代旧库 CSS 条）。"""
    if not password:
        st.progress(0.0, text="请输入密码")
        return
    strength, score, feedback = auth.evaluate_password_strength(password)
    ratio = score / 100.0
    if strength == "high":
        label = f"密码强度：高（{score} 分）"
    elif strength == "medium":
        label = f"密码强度：中（{score} 分）"
    else:
        label = f"密码强度：低（{score} 分）"
    st.progress(ratio, text=label)
    with st.expander("强度提示", expanded=strength == "low"):
        for item in feedback:
            st.markdown(f"- {item}")


def show_auth() -> None:
    st.title("智慧大棚数据管理平台")
    st.caption("智慧农业数据接入、清洗、分析、预测与运维一体化平台。")
    mode = st.segmented_control("选择操作", ["登录", "注册"], default="登录")

    if mode == "登录":
        captcha_ui.create_captcha_widget("login")
        with st.form("login_form"):
            username = st.text_input("用户名")
            password = st.text_input("密码", type="password")
            captcha = st.text_input("验证码", max_chars=4)
            submitted = st.form_submit_button("登录", type="primary", icon=":material/login:")
        if submitted:
            if not captcha_ui.validate_captcha_input(captcha, "login"):
                st.rerun()
            elif _do_login(username, password):
                st.success("登录成功！")
                st.rerun()
            else:
                captcha_ui.refresh_captcha("login")
    else:
        captcha_ui.create_captcha_widget("register")
        with st.form("register_form"):
            username = st.text_input("用户名", key="reg_username")
            password = st.text_input("密码", type="password", key="reg_password")
            confirm = st.text_input("确认密码", type="password", key="reg_confirm")
            role_choice = st.segmented_control("身份", ["普通用户", "管理员"], default="普通用户")
            captcha = st.text_input("验证码", max_chars=4)
            submitted = st.form_submit_button("注册", type="primary", icon=":material/person_add:")
        if submitted:
            if not captcha_ui.validate_captcha_input(captcha, "register"):
                st.rerun()
            elif not username:
                st.error("用户名不能为空。")
            elif password != confirm:
                st.error("两次密码不一致。")
            elif not auth.check_password_complexity(password):
                st.warning("密码至少 8 位，且含大小写字母、数字、特殊字符。")
            else:
                _register_user(username, password, role_choice)
                captcha_ui.refresh_captcha("register")

        # 强度条渲染在表单外（避免 form 提交时被吞），实时跟随密码输入
        typed = st.session_state.get("reg_password", "")
        _render_password_strength(typed)
