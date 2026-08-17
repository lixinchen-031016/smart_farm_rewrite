"""备份与恢复页面（管理员专属）。

- 备份：全表 JSON 快照；可选 **Fernet 加密**（密钥单独下载，**修复旧库密钥与密文同包缺陷**）
- 恢复：上传 JSON 或密文 + 密钥，经 ORM（参数化写、显式 commit）回写；需勾选二次确认
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
from smart_farm.services import backup_service as bs

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


def _dump() -> dict:
    snapshot: dict = {}
    with get_session() as s:
        for name, model in TABLES.items():
            rows = s.execute(select(model)).scalars().all()
            snapshot[name] = [_row_to_dict(r) for r in rows]
    return snapshot


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


st.title("备份与恢复")
if not require_admin():
    st.stop()

st.subheader("导出备份")
encrypt_choice = st.toggle("加密备份（Fernet）", value=False,
                           help="开启后生成加密快照，密钥会单独提供下载——请妥善保存，解密恢复时需要它。")

if st.button("生成备份", type="primary", icon=":material/save:"):
    snapshot = _dump()
    if encrypt_choice:
        key = bs.generate_key()
        ciphertext = bs.encrypt_snapshot(snapshot, key)
        st.download_button(
            "下载加密备份 (.encrypted)",
            data=ciphertext,
            file_name=f"backup_{bs.filename_timestamp()}.encrypted",
            mime="application/octet-stream",
            icon=":material/download:",
        )
        st.download_button(
            "下载解密密钥 (.key) — 请单独保存！",
            data=bs.encode_key_b64(key).encode("utf-8"),
            file_name=f"backup_{bs.filename_timestamp()}.key",
            mime="text/plain",
            icon=":material/key:",
        )
        st.warning("**安全提示**：密钥与密文分开保存（修复旧版同包缺陷）。丢失密钥将无法解密。")
    else:
        st.download_button(
            "下载 JSON 快照",
            data=json.dumps(snapshot, default=str, ensure_ascii=False, indent=2).encode("utf-8"),
            file_name=f"backup_{bs.filename_timestamp()}.json",
            mime="application/json",
            icon=":material/download:",
        )

st.subheader("恢复备份")
st.warning("恢复将**覆盖**当前全部数据，请先导出备份。仅管理员可操作。")
restore_mode = st.segmented_control("恢复类型", ["JSON 快照", "加密备份"], default="JSON 快照")

if restore_mode == "加密备份":
    key_text = st.text_input("解密密钥", type="password", help="粘贴之前下载的 .key 内容（或 base64 密钥）")

uploaded = st.file_uploader("上传备份文件", type=["json", "encrypted"])
confirm = st.checkbox("我已了解风险，确认覆盖恢复")
if uploaded is not None and confirm:
    if st.button("执行恢复", type="primary", icon=":material/restore:"):
        try:
            raw = uploaded.read()
            if restore_mode == "加密备份":
                if not key_text:
                    st.error("请提供解密密钥。")
                    st.stop()
                key = key_text.strip()
                # 若是 base64 编码的 .key 内容则解码；否则视为原始 Fernet 密钥
                try:
                    key = bs.decode_key_b64(key)
                except Exception:  # noqa: BLE001 非 base64 编码，按原文处理
                    pass
                snapshot = bs.decrypt_snapshot(raw, key)
            else:
                snapshot = json.loads(raw.decode("utf-8"))
            with st.spinner("恢复中..."):
                n = _restore(snapshot)
            st.success(f"已恢复 {n} 条记录。")
        except Exception as e:  # noqa: BLE001
            st.error(f"恢复失败：{e}")
