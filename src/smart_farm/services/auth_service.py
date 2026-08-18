"""认证服务（纯逻辑，无 Streamlit 依赖）。

重写要点（对照旧版安全缺陷）：
- 密钥从配置读取，绝不在代码/compose 中硬编码弱口令。
- 登录态由调用方（UI）存入 session_state / Cookie；JWT **不放入 URL query_params**。
- 登录失败限流：可插拔后端（默认进程内存，可配置 Redis 以支持多 worker）。
"""

import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Protocol

import bcrypt
import jwt

from smart_farm.config import get_settings

logger = logging.getLogger(__name__)
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


_USERNAME_RE = re.compile(r"^[\w\u4e00-\u9fa5-]{2,50}$")


def validate_username(username: str) -> tuple[bool, str]:
    """用户名合法性（安全修复：限制字符集与长度，防存储型注入/超长列）。

    Returns:
        (是否合法, 错误信息或空串)
    """
    if not username:
        return False, "用户名不能为空"
    if not _USERNAME_RE.match(username):
        return False, "用户名限 2-50 位，仅含字母、数字、下划线、中文或连字符"
    return True, ""


_SPECIAL_CHARS = r'[!@#$%^&*(),.?"{}|<>\[\]\\/_+=~-]'


def evaluate_password_strength(password: str) -> tuple[str, int, list[str]]:
    """评估密码强度（对齐旧库评分规则）。

    Returns:
        (强度等级, 分数 0-100, 反馈列表)
        - 等级: high(≥70) / medium(≥40) / low
        - 分数由长度、字符类型、复杂性加分与常见模式扣分累加得到
    """
    if not password:
        return "low", 0, []

    score = 0
    feedback: list[str] = []

    # 长度检查
    if len(password) >= 12:
        score += 25
        feedback.append("密码长度充足 (≥12位)")
    elif len(password) >= 8:
        score += 15
        feedback.append("密码长度一般 (8-11位)")
    else:
        feedback.append("密码长度不足 (<8位)")

    # 字符类型检查
    has_lower = bool(re.search(r"[a-z]", password))
    has_upper = bool(re.search(r"[A-Z]", password))
    has_digit = bool(re.search(r"[0-9]", password))
    has_special = bool(re.search(_SPECIAL_CHARS, password))
    char_types = sum([has_lower, has_upper, has_digit, has_special])

    if char_types >= 3:
        score += 30
        feedback.append("包含多种字符类型")
    elif char_types == 2:
        score += 15
        feedback.append("字符类型较少")
    else:
        feedback.append("字符类型单一")

    # 复杂性加分
    if has_lower and has_upper:
        score += 15
    if has_digit:
        score += 10
    if has_special:
        score += 20

    # 常见模式扣分
    if re.search(r"(.)\1{2,}", password):  # 连续重复字符
        score -= 10
        feedback.append("存在连续重复字符")
    if re.search(r"(012|123|234|345|456|567|678|789|890)", password):  # 连续数字
        score -= 10
        feedback.append("存在连续数字序列")
    if re.search(r"(abc|bcd|cde|def|efg|fgh|ghi|hij|ijk)", password.lower()):  # 连续字母
        score -= 10
        feedback.append("存在连续字母序列")

    # 确定强度等级
    if score >= 70:
        strength = "high"
    elif score >= 40:
        strength = "medium"
    else:
        strength = "low"

    return strength, max(0, min(100, score)), feedback


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


# ----------------------------- 登录限流（可插拔后端） -----------------------------


class RateLimitBackend(Protocol):
    """限流后端协议：实现本协议即可替换（内存 / Redis / DB ...）。"""

    def recent_failure_count(self, key: str) -> int:
        """清理过期记录并返回窗口内的失败次数。"""
        ...

    def register_failure(self, key: str) -> None: ...

    def reset(self, key: str) -> None: ...


class InMemoryRateLimitBackend:
    """进程内内存后端（单 worker 演示用；重启失效）。"""

    def __init__(self, window_seconds: int = 30):
        self.window = timedelta(seconds=window_seconds)
        self._store: dict[str, list[datetime]] = {}
        self._lock = threading.Lock()

    def recent_failure_count(self, key: str) -> int:
        with self._lock:
            attempts = self._store.get(key, [])
            attempts = [t for t in attempts if datetime.now() - t < self.window]
            self._store[key] = attempts
            return len(attempts)

    def register_failure(self, key: str) -> None:
        with self._lock:
            self._store.setdefault(key, []).append(datetime.now())

    def reset(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)


class RedisRateLimitBackend:
    """Redis 后端（多 worker / 多进程共享，生产推荐；需 `uv pip install redis`）。

    以 sorted set 记录窗口内每次失败的时间戳，score 即时间。
    """

    def __init__(self, client: Any, window_seconds: int = 30, prefix: str = "login_rate:"):
        self.client = client
        self.window = window_seconds
        self.prefix = prefix

    @classmethod
    def from_url(cls, url: str, window_seconds: int = 30) -> "RedisRateLimitBackend":
        import redis  # 可选依赖，懒加载

        return cls(redis.Redis.from_url(url), window_seconds=window_seconds)

    def _key(self, key: str) -> str:
        return f"{self.prefix}{key}"

    def _prune(self, key: str) -> None:
        now = time.time()
        self.client.zremrangebyscore(self._key(key), 0, now - self.window)
        self.client.expire(self._key(key), self.window * 2)

    def recent_failure_count(self, key: str) -> int:
        self._prune(key)
        return int(self.client.zcard(self._key(key)))

    def register_failure(self, key: str) -> None:
        now = time.time()
        self.client.zadd(self._key(key), {f"{now:.6f}": now})
        self._prune(key)

    def reset(self, key: str) -> None:
        self.client.delete(self._key(key))


class LoginLimiter:
    """登录失败限流门面（对齐旧库：10 次失败 / 30 秒锁定）。

    委托可插拔后端（`RateLimitBackend` 协议）：
    - memory（默认）：进程内，单 worker 演示。
    - redis：多 worker / 重启后仍生效，经 `LOGIN_RATE_LIMIT_BACKEND=redis` 启用。
    """

    def __init__(
        self,
        max_attempts: int = 10,
        window_seconds: int = 30,
        backend: Optional[RateLimitBackend] = None,
    ):
        self.max_attempts = max_attempts
        self.window = timedelta(seconds=window_seconds)
        self.backend = backend or InMemoryRateLimitBackend(window_seconds)

    def is_blocked(self, key: str) -> bool:
        return self.backend.recent_failure_count(key) >= self.max_attempts

    def register_failure(self, key: str) -> None:
        self.backend.register_failure(key)

    def reset(self, key: str) -> None:
        self.backend.reset(key)


def _build_default_limiter() -> LoginLimiter:
    """按配置构建限流器：memory（默认）/ redis；redis 不可用时回退内存并告警。"""
    backend_name = _settings.login_rate_limit_backend.lower()
    window = _settings.login_rate_limit_window_seconds
    attempts = _settings.login_rate_limit_max_attempts
    if backend_name == "redis":
        try:
            backend: RateLimitBackend = RedisRateLimitBackend.from_url(
                _settings.login_rate_limit_redis_url, window_seconds=window
            )
            return LoginLimiter(attempts, window, backend=backend)
        except ImportError:
            logger.warning("redis 未安装，登录限流回退进程内内存后端（单 worker）。")
    return LoginLimiter(attempts, window)


limiter = _build_default_limiter()
