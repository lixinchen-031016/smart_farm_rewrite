"""类型化配置。

所有配置项从环境变量 / `.env` 读取，敏感值（SECRET_KEY、DB 密码）
一律外置，禁止硬编码。默认值仅用于本地开发（SQLite）。
"""

import logging
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# 生产必须替换的弱默认值（安全修复：检测并告警，防止漏配密钥）
_INSECURE_DEFAULT_SECRET = "change-me-in-production-use-a-32-plus-char-random-string"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # 应用
    app_name: str = "智慧大棚数据管理平台"
    debug: bool = False

    # 数据库
    database_url: str = "sqlite:///./smart_farm.db"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False

    # 认证
    secret_key: str = _INSECURE_DEFAULT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440
    bcrypt_rounds: int = 12


@lru_cache
def get_settings() -> Settings:
    """返回缓存的全局配置实例。"""
    settings = Settings()
    if settings.secret_key == _INSECURE_DEFAULT_SECRET and not settings.debug:
        logger.warning(
            "SECRET_KEY 仍为默认弱值且未处于 DEBUG 模式——生产环境请务必在 .env 中配置强随机密钥。"
        )
    return settings
