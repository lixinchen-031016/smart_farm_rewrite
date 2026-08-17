"""认证服务（纯逻辑，无 Streamlit 依赖）。

重写要点（对照旧版安全缺陷）：
- 密钥从配置读取，绝不在代码/compose 中硬编码弱口令。
- 登录态由调用方（UI）存入 session_state / Cookie；JWT **不放入 URL query_params**。
- 登录失败限流：当前为进程内计数（便于演示），结构上可平滑替换为 Redis/DB 实现。
"""

import re
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


# ----------------------------- 登录限流 -----------------------------


class LoginLimiter:
    """登录失败限流（对齐旧库：10 次失败 / 30 秒锁定）。

    当前为进程内内存实现（演示用）。生产环境应替换为 Redis / DB 后端，
    以保证多 worker、重启后仍生效（旧版内存字典在重启后失效）。
    """

    def __init__(self, max_attempts: int = 10, window_seconds: int = 30):
        self.max_attempts = max_attempts
        self.window = timedelta(seconds=window_seconds)
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
