"""认证服务（纯逻辑，无 Streamlit 依赖）。

重写要点（对照旧版安全缺陷）：
- 密钥从配置读取，绝不在代码/compose 中硬编码弱口令。
- 登录态由调用方（UI）存入 session_state / Cookie；JWT **不放入 URL query_params**。
- 登录失败限流：当前为进程内计数（便于演示），结构上可平滑替换为 Redis/DB 实现。
"""

import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt

from smart_farm.config import get_settings

_settings = get_settings()


# ----------------------------- 密码 -----------------------------


def hash_password(password: str) -> str:
    """使用 bcrypt 对明文密码加盐哈希。"""
    pw = password.encode("utf-8")
    hashed = bcrypt.hashpw(pw, bcrypt.gensalt(rounds=_settings.bcrypt_rounds))
    return hashed.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """校验明文密码与 bcrypt 哈希是否匹配。"""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def check_password_complexity(password: str) -> bool:
    """密码复杂度：至少 8 位，含大小写字母、数字、特殊字符。"""
    if len(password) < 8:
        return False
    has_lower = any(c.islower() for c in password)
    has_upper = any(c.isupper() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_special = any(not c.isalnum() for c in password)
    return has_lower and has_upper and has_digit and has_special


# ----------------------------- JWT -----------------------------


def create_access_token(username: str, role: str) -> str:
    """签发短期访问令牌（默认 1440 分钟）。"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=_settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, _settings.secret_key, algorithm=_settings.jwt_algorithm)


def decode_access_token(token: str) -> Optional[dict]:
    """解码并校验令牌，失败返回 None。"""
    try:
        return jwt.decode(token, _settings.secret_key, algorithms=[_settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None


# ----------------------------- 登录限流 -----------------------------


class LoginLimiter:
    """登录失败限流。

    当前为进程内内存实现（演示用）。生产环境应替换为 Redis / DB 后端，
    以保证多 worker、重启后仍生效（旧版内存字典在重启后失效）。
    """

    def __init__(self, max_attempts: int = 5, window_minutes: int = 15):
        self.max_attempts = max_attempts
        self.window = timedelta(minutes=window_minutes)
        self._store: dict[str, list[datetime]] = {}
        self._lock = threading.Lock()

    def is_blocked(self, key: str) -> bool:
        with self._lock:
            attempts = self._store.get(key, [])
            attempts = [t for t in attempts if datetime.now() - t < self.window]
            self._store[key] = attempts
            return len(attempts) >= self.max_attempts

    def register_failure(self, key: str) -> None:
        with self._lock:
            self._store.setdefault(key, []).append(datetime.now())

    def reset(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)


limiter = LoginLimiter()
