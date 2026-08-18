"""类型化配置。

所有配置项从环境变量 / `.env` 读取，敏感值（SECRET_KEY、DB 密码）
一律外置，禁止硬编码。默认值仅用于本地开发（SQLite）。
"""

import logging
from functools import lru_cache
from typing import Optional

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

    # 数据库（主库；备库可选——两者可分别为 SQLite / MySQL，实现无感切换与故障转移）
    database_url: str = "sqlite:///./smart_farm.db"
    database_fallback_url: Optional[str] = None  # 例：mysql+pymysql://user:pass@host:3306/smart_farm
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False
    db_connect_timeout: int = 5  # 主/备库连通性探测超时（秒）
    db_failover_cooldown_seconds: int = 60  # 运行时故障转移冷却，防主库抖动来回切换
    # 每次程序启动前自动同步主备库（传感器/日志增量双向 + 管理表按唯一键补齐）
    auto_sync_on_startup: bool = True

    # 认证
    secret_key: str = _INSECURE_DEFAULT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440
    bcrypt_rounds: int = 12

    # 登录限流（memory：进程内单 worker；redis：多 worker 共享，需另装 redis 包）
    login_rate_limit_backend: str = "memory"
    login_rate_limit_redis_url: str = "redis://localhost:6379/0"
    login_rate_limit_max_attempts: int = 10
    login_rate_limit_window_seconds: int = 30

    # IoT 网关（随应用自动启动；也可独立运行 python -m smart_farm.iot_gateway）
    auto_start_gateway: bool = True
    gateway_channels: str = "http,udp"  # 逗号分隔：http / mqtt / udp（mqtt 需可达的 Broker）
    iot_http_host: str = "0.0.0.0"
    iot_http_port: int = 8600
    iot_udp_host: str = "0.0.0.0"
    iot_udp_port: int = 8601
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: Optional[str] = None
    mqtt_password: Optional[str] = None
    mqtt_topic_prefix: str = "smart_farm/"


@lru_cache
def get_settings() -> Settings:
    """返回缓存的全局配置实例。"""
    settings = Settings()
    if settings.secret_key == _INSECURE_DEFAULT_SECRET and not settings.debug:
        logger.warning(
            "SECRET_KEY 仍为默认弱值且未处于 DEBUG 模式——生产环境请务必在 .env 中配置强随机密钥。"
        )
    return settings
