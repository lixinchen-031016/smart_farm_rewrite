"""用户管理页面（管理员专属）。列出用户、修改角色、创建用户。"""

import pandas as pd
import streamlit as st

from smart_farm.app.guards import require_admin
from smart_farm.data import repositories as repo
from smart_farm.data.database import get_session
from smart_farm.services import auth_service as auth


def _user_id(s, username: str) -> int:
    return repo.get_user_by_username(s, username).id


st.title("用户管理")
if not require_admin():
    st.stop()

current_user = st.session_state.get("username")

# ---------------- 用户列表 ----------------
with get_session() as s:
    users = repo.list_users(s)
if not users:
    st.info("暂无用户。")
    st.stop()

df = pd.DataFrame(
    [{"用户名": u.username, "角色": u.role, "最后登录": u.last_login_time} for u in users]
)
st.dataframe(df, width="stretch")

# ---------------- 修改角色 ----------------
st.subheader("修改用户角色")
with st.form("role_form"):
    target = st.selectbox("选择用户", [u.username for u in users])
    new_role = st.selectbox("新角色", ["user", "admin"])
    if st.form_submit_button("保存角色", type="primary"):
        if target == current_user and new_role != "admin":
            st.error("不能取消自己的管理员权限。")
        else:
            with get_session() as s:
                repo.update_user_role(s, _user_id(s, target), new_role)
                repo.add_log(s, "INFO", current_user, "用户管理", f"将 {target} 角色改为 {new_role}")
            st.success(f"已将 {target} 的角色更新为 {new_role}。")
            st.rerun()

# ---------------- 创建用户 ----------------
st.subheader("新建用户")
with st.form("create_user_form"):
    nu = st.text_input("用户名")
    npw = st.text_input("初始密码", type="password")
    nrole = st.selectbox("角色", ["user", "admin"], key="new_role_sel")
    if st.form_submit_button("创建用户"):
        if not nu:
            st.error("用户名不能为空。")
        elif not auth.check_password_complexity(npw):
            st.warning("密码至少 8 位，且含大小写字母、数字、特殊字符。")
        else:
            with get_session() as s:
                if repo.get_user_by_username(s, nu):
                    st.error("该用户名已存在。")
                else:
                    repo.create_user(s, nu, auth.hash_password(npw), role=nrole)
                    repo.add_log(s, "INFO", current_user, "用户管理", f"创建用户 {nu}")
                    st.success(f"用户 {nu} 创建成功。")
                    st.rerun()
