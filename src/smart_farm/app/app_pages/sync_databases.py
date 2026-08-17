"""数据库同步页面（管理员专属）。

手动双向增量同步（默认不自动运行）：
- 连接测试 / 验证权限 / 开始同步 三个按钮
- 连接信息从环境变量读取默认值（CLOUD_DATABASE_* / LOCAL_DATABASE_*）
"""

import streamlit as st

from smart_farm.app.guards import require_admin
from smart_farm.config import get_settings
from smart_farm.services import sync_service as ss

st.title("数据库同步")
if not require_admin():
    st.stop()

_settings = get_settings()


def _conn_default(prefix: str) -> tuple[str, int, str, str, str]:
    """从环境变量读取连接默认值（对齐旧版 env 默认）。"""
    host = _settings.database_url.split("//")[-1].split(":")[0] if "sqlite" not in _settings.database_url else "localhost"
    return host, 3306, "intelligent_farm", "root", ""


st.caption("基于最大时间戳的增量双向同步（云端 ↔ 本地，4 张传感器表）。默认不自动运行，手动触发。")

# 本地数据库信息（只读展示）
st.subheader("本地数据库")
st.code(_settings.database_url, language="text")

# 云端数据库信息（可编辑）
st.subheader("云端数据库连接")
with st.form("cloud_form"):
    c_host = st.text_input("主机", value="localhost")
    c_port = st.number_input("端口", min_value=1, max_value=65535, value=3306)
    c_name = st.text_input("数据库名", value="intelligent_farm")
    c_user = st.text_input("用户名", value="root")
    c_password = st.text_input("密码", type="password")
    submitted = st.form_submit_button("保存连接信息", icon=":material/save:")

if submitted:
    errors = ss.validate_database_inputs(c_host, int(c_port), c_name, c_user, c_password)
    if errors:
        for e in errors:
            st.error(e)
    else:
        st.session_state["cloud_conn"] = {
            "host": c_host, "port": int(c_port), "name": c_name,
            "user": c_user, "password": c_password,
        }
        st.success("连接信息已保存，可进行连接测试。")

conn = st.session_state.get("cloud_conn")
if conn is None:
    st.info("请先填写并保存云端连接信息。")
    st.stop()

cloud_url = ss.build_mysql_url(conn["host"], conn["port"], conn["name"], conn["user"], conn["password"])

c1, c2, c3 = st.columns(3)
with c1:
    if st.button("测试连接", icon=":material/wifi_tethering:"):
        ok, msg = ss.DatabaseSync(cloud_url, _settings.database_url).test_connection(cloud_url)
        st.success(msg) if ok else st.error(msg)
with c2:
    if st.button("验证权限", icon=":material/verified:"):
        st.info("权限验证需在目标库执行写入测试；当前为连接校验。")
        ok, msg = ss.DatabaseSync(cloud_url, _settings.database_url).test_connection(cloud_url)
        st.success("连接正常，可执行同步。") if ok else st.error(msg)
with c3:
    if st.button("开始同步", type="primary", icon=":material/sync:"):
        st.warning("生产环境请先备份！")
        with st.spinner("同步中..."):
            try:
                sync = ss.DatabaseSync(cloud_url, _settings.database_url)
                stats = sync.sync_all_data()
                sync.close()
            except Exception as e:  # noqa: BLE001
                st.error(f"同步失败：{e}")
                st.stop()
        for s in stats:
            st.success(f"表 {s['table']}：云端→本地 {s['cloud_to_local']} 条，"
                       f"本地→云端 {s['local_to_cloud']} 条，冲突 {s['conflicts']} 条")
