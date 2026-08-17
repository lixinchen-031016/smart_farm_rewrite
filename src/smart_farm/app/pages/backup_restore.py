"""备份与恢复页面（管理员专属）。

- 备份：将全部表（大棚 / 用户 / 传感器 / 操作日志）导出为 JSON 快照下载。
- 恢复：上传 JSON 快照，经 ORM（参数化写、显式 commit）回写；需勾选确认。
所有读写均走 ORM / 参数化，无拼接 SQL。
"""

import json
from datetime import datetime

import streamlit as st
from sqlalchemy import DateTime, select

from smart_farm.app.guards import require_admin
from smart_farm.data.database import get_session
from smart_farm.data.models import (
    SENSOR_MODELS,
    Greenhouse,
    OperationLog,
    User,
)

TABLES = {
    "greenhouse": Greenhouse,
    "user": User,
    "operation_logs": OperationLog,
    **SENSOR_MODELS,
}


def _row_to_dict(obj) -> dict:
    return {
        c.name: getattr(obj, c.name)
        for c in obj.__table__.columns
        if c.name != "id"
    }


def _dump() -> str:
    snapshot: dict = {}
    with get_session() as s:
        for name, model in TABLES.items():
            rows = s.execute(select(model)).scalars().all()
            snapshot[name] = [_row_to_dict(r) for r in rows]
    return json.dumps(snapshot, default=str, ensure_ascii=False, indent=2)


def _restore(snapshot: dict) -> int:
    count = 0
    with get_session() as s:
        for name, model in TABLES.items():
            data = snapshot.get(name, [])
            # 清空该表（恢复为快照内容）
            for existing in s.execute(select(model)).scalars().all():
                s.delete(existing)
            # 重建
            dt_cols = {c.name for c in model.__table__.columns if isinstance(c.type, DateTime)}
            for entry in data:
                kwargs = {}
                for k, v in entry.items():
                    if k == "id":
                        continue
                    if k in dt_cols and isinstance(v, str):
                        kwargs[k] = datetime.fromisoformat(v)
                    else:
                        kwargs[k] = v
                s.add(model(**kwargs))
                count += 1
        # get_session 上下文结束处显式 commit
    return count


def show() -> None:
    st.title("💾 备份与恢复")
    if not require_admin():
        return

    st.subheader("导出备份")
    if st.button("生成并下载 JSON 快照", type="primary"):
        payload = _dump()
        st.download_button("📥 下载 backup.json", payload, file_name="smart_farm_backup.json")

    st.subheader("恢复备份")
    st.warning("恢复将**覆盖**当前全部数据，请先导出备份。仅管理员可操作。")
    uploaded = st.file_uploader("上传 backup.json", type=["json"])
    confirm = st.checkbox("我已了解风险，确认覆盖恢复")
    if uploaded is not None and confirm:
        if st.button("执行恢复"):
            try:
                snapshot = json.loads(uploaded.read())
                with st.spinner("恢复中..."):
                    n = _restore(snapshot)
                st.success(f"已恢复 {n} 条记录。")
            except Exception as e:  # noqa: BLE001
                st.error(f"恢复失败：{e}")
