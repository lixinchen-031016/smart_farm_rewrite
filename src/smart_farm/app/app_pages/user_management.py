"""用户管理页面（管理员专属）。审批、改角色、建用户、改密、编辑/删除。"""

import pandas as pd
import streamlit as st

from smart_farm.app.guards import require_admin
from smart_farm.data import repositories as repo
from smart_farm.data.database import get_session
from smart_farm.services import auth_service as auth


def _user_id(s, username: str) -> int:
    user = repo.get_user_by_username(s, username)
    if user is None:  # 修复：解引用保护（并发删除场景）
        return -1
    return user.id


def _admin_count(s, exclude_user_id: int | None = None) -> int:
    """统计 admin 数量（可排除指定用户，用于最后管理员保护）。"""
    admins = [u for u in repo.list_users(s) if u.role == "admin" and u.id != exclude_user_id]
    return len(admins)


st.title("用户管理")
if not require_admin():
    st.stop()

current_user = st.session_state.get("username")

# ---------------- 管理员申请审批 ----------------
with get_session() as s:
    pending = repo.list_admin_requests(s)

if pending:
    st.subheader("管理员申请审批")
    for u in pending:
        c1, c2, c3 = st.columns([3, 1, 1])
        c1.markdown(f"**{u.username}** 申请于 {u.admin_request_time:%Y-%m-%d %H:%M}")
        with c2:
            if st.button("批准", key=f"approve_{u.id}", icon=":material/check:"):
                with get_session() as s2:
                    if repo.approve_admin_request(s2, u.id):
                        repo.add_log(s2, "INFO", current_user, "用户管理", f"批准 {u.username} 的管理员申请")
                        st.success(f"已批准 {u.username} 为管理员。")
                st.rerun()
        with c3:
            if st.button("拒绝", key=f"reject_{u.id}", icon=":material/close:"):
                with get_session() as s2:
                    if repo.reject_admin_request(s2, u.id):
                        repo.add_log(s2, "INFO", current_user, "用户管理", f"拒绝 {u.username} 的管理员申请")
                        st.info(f"已拒绝 {u.username} 的管理员申请。")
                st.rerun()

# ---------------- 用户列表 ----------------
with get_session() as s:
    users = repo.list_users(s)
if not users:
    st.info("暂无用户。")
    st.stop()

df = pd.DataFrame(
    [{
        "ID": u.id,
        "用户名": u.username,
        "角色": u.role,
        "管理员申请中": "是" if u.admin_request else "",
        "最后登录": u.last_login_time,
    } for u in users]
)
st.subheader("用户列表")
st.dataframe(df, width="stretch")

# ---------------- 大棚授权（多租户） ----------------
with get_session() as s:
    ghs = repo.list_greenhouses(s)

if ghs:
    st.subheader("大棚授权（多棚多用户）")
    st.caption("普通用户仅能看到并操作被授权的大棚；管理员默认可见全部。")
    with st.form("grant_gh_form"):
        grant_target = st.selectbox("选择用户", [u.username for u in users], key="grant_target")
        current_ids: list[int] = []
        for u in users:
            if u.username == grant_target:
                current_ids = repo.list_greenhouse_ids_for_user(s, u.id)
                break
        # 兼容旧字段 User.greenhouse_id：作为初始勾选的一部分展示
        for u in users:
            if u.username == grant_target and u.greenhouse_id is not None:
                current_ids = list(set(current_ids + [u.greenhouse_id]))
        picked = st.multiselect(
            "授权大棚",
            [g.id for g in ghs],
            default=sorted(current_ids),
            format_func=lambda i: next(g.name for g in ghs if g.id == i),
            key="grant_picked",
        )
        if st.form_submit_button("保存授权", type="primary", icon=":material/key:"):
            with get_session() as s2:
                target_id = _user_id(s2, grant_target)
                if target_id < 0:
                    st.error("目标用户不存在。")
                else:
                    repo.set_user_greenhouses(s2, target_id, picked)
                    repo.add_log(
                        s2, "INFO", current_user, "用户管理",
                        f"更新 {grant_target} 的大棚授权：{[next(g.name for g in ghs if g.id == i) for i in picked]}",
                    )
            st.success(f"已保存 {grant_target} 的授权大棚（{len(picked)} 个）。")
            st.rerun()

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
                target_id = _user_id(s, target)
                if target_id < 0:
                    st.error("目标用户不存在。")
                elif new_role != "admin" and _admin_count(s, exclude_user_id=target_id) <= 0:
                    # 修复：最后一名管理员保护，防止系统锁死
                    st.error("不能移除最后一名管理员。")
                else:
                    repo.update_user_role(s, target_id, new_role)
                    repo.add_log(s, "INFO", current_user, "用户管理", f"将 {target} 角色改为 {new_role}")
                    st.success(f"已将 {target} 的角色更新为 {new_role}。")
                    st.rerun()

# ---------------- 修改密码 ----------------
st.subheader("重置密码")
with st.form("reset_pwd_form"):
    pwd_target = st.selectbox("选择用户", [u.username for u in users], key="pwd_target")
    new_pw = st.text_input("新密码", type="password")
    confirm_pw = st.text_input("确认新密码", type="password")
    if st.form_submit_button("重置密码"):
        if new_pw != confirm_pw:
            st.error("两次密码不一致。")
        elif not auth.check_password_complexity(new_pw):
            st.warning("密码至少 8 位，且含大小写字母、数字、特殊字符。")
        else:
            with get_session() as s:
                repo.update_user_password(s, _user_id(s, pwd_target), auth.hash_password(new_pw))
                repo.add_log(s, "INFO", current_user, "用户管理", f"重置 {pwd_target} 的密码")
            st.success(f"已重置 {pwd_target} 的密码。")

# ---------------- 编辑 / 删除 ----------------
st.subheader("编辑 / 删除用户")
with st.form("edit_delete_form"):
    op_target = st.selectbox("选择用户", [u.username for u in users], key="op_target")
    op = st.selectbox("操作", ["编辑用户名", "删除用户"])
    new_name = None
    if op == "编辑用户名":
        new_name = st.text_input("新用户名")
    if st.form_submit_button("执行"):
        with get_session() as s:
            op_id = _user_id(s, op_target)
            if op_id < 0:
                st.error("目标用户不存在。")
            elif op_target == current_user and op == "删除用户":
                st.error("不能删除当前登录的用户。")
            elif op == "删除用户":
                if _admin_count(s, exclude_user_id=op_id) <= 0:
                    # 修复：最后一名管理员保护
                    st.error("不能删除最后一名管理员。")
                else:
                    ok = repo.delete_user(s, op_id)
                    if ok:
                        repo.add_log(s, "INFO", current_user, "用户管理", f"删除用户 {op_target}")
                        st.success(f"已删除用户 {op_target}。")
                        st.rerun()
            else:
                if not new_name:
                    st.error("请输入新用户名。")
                else:
                    valid_name, name_err = auth.validate_username(new_name)
                    if not valid_name:
                        st.error(name_err)
                    elif repo.get_user_by_username(s, new_name):
                        st.error("该用户名已存在。")
                    else:
                        repo.get_user_by_username(s, op_target).username = new_name
                        repo.add_log(s, "INFO", current_user, "用户管理", f"将 {op_target} 重命名为 {new_name}")
                        st.success(f"已将 {op_target} 重命名为 {new_name}。")
                        st.rerun()

# ---------------- 创建用户 ----------------
st.subheader("新建用户")
with st.form("create_user_form"):
    nu = st.text_input("用户名")
    npw = st.text_input("初始密码", type="password")
    nrole = st.selectbox("角色", ["user", "admin"], key="new_role_sel")
    if st.form_submit_button("创建用户"):
        valid_name, name_err = auth.validate_username(nu)
        if not valid_name:
            st.error(name_err)
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
