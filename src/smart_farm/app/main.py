"""Streamlit 应用入口（UI 层，仅渲染 + 调用 services）。

遵循 developing-with-streamlit 技能：
- `st.navigation` + `st.Page` + `app_pages/` 目录（替代 legacy pages/ 自动发现与手动 MENU 路由）。
- 页面文件为直接脚本，不包裹 `show()` 函数。
- 登录态存于 `st.session_state`，**JWT 不写入 URL query_params**（修复旧版泄露风险）。
- 业务逻辑全部委托给 `services/*`，本文件不含算法。
- IoT 网关随应用自动启动（`st.cache_resource` 进程级单例，多会话/多次 rerun 仅启动一次）。
"""

import streamlit as st

from smart_farm.app import auth_ui, greenhouse_context
from smart_farm.config import get_settings
from smart_farm.services import module_manager as mm

st.set_page_config(
    page_title="智慧大棚数据管理平台",
    page_icon="🌱",
    layout="wide",
    menu_items={"Get Help": None, "Report a bug": None, "About": None},
)

st.session_state.setdefault("logged_in", False)
st.session_state.setdefault("role", "user")


@st.cache_resource(show_spinner=False)
def _autostart_iot_gateway() -> dict[str, str]:
    """进程级单例启动 IoT 接入网关（HTTP/UDP/MQTT 按配置）。

    cache_resource 保证：多个浏览器会话、无数次 rerun，网关只随进程启动一次；
    通道线程为 daemon，随应用进程退出自动清理。
    """
    from smart_farm import iot_gateway

    return iot_gateway.start_gateway_background(
        iot_gateway.parse_channels(get_settings().gateway_channels)
    )


if get_settings().auto_start_gateway:
    _autostart_iot_gateway()

# 未登录：只显示认证页，不构建导航
if not st.session_state["logged_in"]:
    auth_ui.show_auth()
    st.stop()

# 安全修复：角色以数据库为权威（防篡改 session_state 提权；被降级后会话即时回收）
if "role" in st.session_state:
    from smart_farm.data import repositories as _repo
    from smart_farm.data.database import get_session as _get_session

    with _get_session() as _s:
        _user = _repo.get_user_by_username(_s, st.session_state.get("username", ""))
    if _user is None:
        # 用户被删除 → 强制登出
        st.session_state.clear()
        st.rerun()
    if _user.role != st.session_state.get("role"):
        st.session_state["role"] = _user.role
        if st.session_state["role"] != "admin":
            st.warning("您的权限已变更，请刷新后继续操作。")

# 侧边栏用户信息 + 大棚切换 + 退出（原生元素，无自定义 HTML）
with st.sidebar:
    role_label = "管理员" if st.session_state["role"] == "admin" else "普通用户"
    st.caption(f"当前用户：**{st.session_state['username']}**（{role_label}）")
    greenhouse_context.render_greenhouse_selector()
    if st.button("退出登录", icon=":material/logout:"):
        st.session_state.clear()
        st.rerun()

# 页面路由表（路由键 -> st.Page 工厂），由模块配置真过滤（修复旧库"菜单隐藏但可直访"矛盾）
def _page(route: str, title: str, icon: str, default: bool = False):
    return st.Page(f"app_pages/{route}.py", title=title, icon=icon, default=default)


ALL_PAGES: dict[str, st.Page] = {
    "dashboard": _page("dashboard", "综合监控仪表板", ":material/dashboard:", default=True),
    "data_overview": _page("data_overview", "数据概览", ":material/table_view:"),
    "data_cleaning": _page("data_cleaning", "数据清洗与异常", ":material/cleaning_services:"),
    "data_analysis": _page("data_analysis", "数据分析", ":material/analytics:"),
    "advanced_analysis": _page("advanced_analysis", "高级分析", ":material/insights:"),
    "visualization": _page("visualization", "可视化", ":material/bar_chart:"),
    "prediction": _page("prediction", "本地数据预测", ":material/timeline:"),
    "decision": _page("decision", "自动化决策", ":material/psychology:"),
    "history_reports": _page("history_reports", "历史报告", ":material/history:"),
    "user_management": _page("user_management", "用户管理", ":material/group:"),
    "devices": _page("devices", "设备接入", ":material/sensors:"),
    "log_viewer": _page("log_viewer", "操作日志", ":material/receipt_long:"),
    "system_monitoring": _page("system_monitoring", "系统监控", ":material/monitor_heart:"),
    "sync_databases": _page("sync_databases", "数据库同步", ":material/sync:"),
    "backup_restore": _page("backup_restore", "备份与恢复", ":material/save:"),
    "use_instruction": _page("use_instruction", "使用说明", ":material/menu_book:"),
    "module_config": _page("module_config", "模块配置", ":material/tune:"),
    "debug_info": _page("debug_info", "调试信息", ":material/bug_report:"),
}

# 模块配置真过滤：仅注入「启用 + 当前角色可见」的页面
_is_admin = st.session_state["role"] == "admin"
_manager = mm.get_module_manager()
_visible_modules = {m.name for m in _manager.enabled_modules_for_user(is_admin=_is_admin)}
_route_to_module = {v: k for k, v in mm.MODULE_TO_PAGE.items()}
_pages = [page for route, page in ALL_PAGES.items() if _route_to_module.get(route) in _visible_modules]

nav = st.navigation(_pages)
nav.run()
