"""备份服务（纯逻辑，无 Streamlit 依赖）。

对齐旧版 backup/restore 的 Fernet 加密能力，并**修复旧库安全缺陷**：
- 旧库把加密密钥与密文同 zip 存放（等于没加密）→ 新版密钥**单独下载/保存**，绝不与密文同包
- 恢复流程由 UI 二次确认
- 数据源：全表 JSON 快照（与 backup_restore 现有逻辑一致）
"""

import base64
import json
from datetime import datetime
from typing import Any

from cryptography.fernet import Fernet


def generate_key() -> str:
    """生成 Fernet 密钥（base64 URL-safe 字符串）。"""
    return Fernet.generate_key().decode("utf-8")


def encrypt_snapshot(snapshot: dict[str, Any], key: str) -> bytes:
    """加密 JSON 快照为密文 bytes。"""
    payload = json.dumps(snapshot, default=str, ensure_ascii=False).encode("utf-8")
    return Fernet(key.encode("utf-8")).encrypt(payload)


def decrypt_snapshot(ciphertext: bytes, key: str) -> dict[str, Any]:
    """解密密文为 JSON 快照 dict。密钥错误/格式错误抛 ValueError。"""
    try:
        payload = Fernet(key.encode("utf-8")).decrypt(ciphertext)
        return json.loads(payload.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("解密失败：密钥错误或备份文件损坏。") from exc


def encode_key_b64(key: str) -> str:
    """密钥转可下载文本（base64，双保险）。"""
    return base64.urlsafe_b64encode(key.encode("utf-8")).decode("utf-8")


def decode_key_b64(encoded: str) -> str:
    """从下载文本还原密钥。"""
    return base64.urlsafe_b64decode(encoded.encode("utf-8")).decode("utf-8")


def filename_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")
