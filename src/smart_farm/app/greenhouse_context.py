"""多棚会话上下文（UI 层）：当前用户可见大棚 + 会话级选中大棚。

规则：
- admin：可见全部大棚。
- 普通用户：关联表授权 ∪ User.greenhouse_id（兼容旧数据）。
- 选中棚存 `st.session_state["greenhouse_id"]`，切换后缓存键随之变化
  （`cache.cached_sensor_df` 已含 greenhouse_id）。
- 无任何授权大棚的普通用户：返回 None（视为未隔离，看全局数据——与旧库行为一致）。
"""

import streamlit as st

from smart_farm.data import repositories as repo
from smart_farm.data.database import get_session
from smart_farm.data.models import Greenhouse


def get_user_greenhouses() -> list[Greenhouse]:
    """当前登录用户可见的大棚列表（admin 全量）。"""
    username = st.session_state.get("username", "")
    with get_session() as s:
        user = repo.get_user_by_username(s, username)
        if user is None:
            return []
        return list(repo.list_greenhouses_for_user(s, user))


def current_greenhouse_id() -> int | None:
    """会话当前选中的大棚 id（未初始化时默认第一个；无可选项返回 None）。"""
    if "greenhouse_id" not in st.session_state:
        ghs = get_user_greenhouses()
        st.session_state["greenhouse_id"] = ghs[0].id if ghs else None
    gid = st.session_state["greenhouse_id"]
    # 会话期间授权被收回 → 回退到第一个仍可见的大棚
    if gid is not None:
        ghs = get_user_greenhouses()
        if gid not in {g.id for g in ghs}:
            st.session_state["greenhouse_id"] = ghs[0].id if ghs else None
    return st.session_state["greenhouse_id"]


def current_greenhouse_name() -> str:
    gid = current_greenhouse_id()
    if gid is None:
        return "全部大棚"
    with get_session() as s:
        gh = repo.get_greenhouse(s, gid)
        return gh.name if gh else "全部大棚"


def render_greenhouse_selector() -> None:
    """侧边栏大棚切换器（单棚或无棚时显示只读信息，不打扰）。"""
    ghs = get_user_greenhouses()
    if len(ghs) <= 1:
        label = ghs[0].name if ghs else "未绑定大棚"
        st.caption(f"当前大棚：**{label}**")
        st.session_state["greenhouse_id"] = ghs[0].id if ghs else None
        return

    names = [g.name for g in ghs]
    cur = current_greenhouse_id()
    idx = next((i for i, g in enumerate(ghs) if g.id == cur), 0)
    choice = st.selectbox(
        "当前大棚",
        names,
        index=idx,
        key="greenhouse_selector",
    )
    selected = ghs[names.index(choice)]
    if selected.id != st.session_state.get("greenhouse_id"):
        st.session_state["greenhouse_id"] = selected.id
        st.rerun()
