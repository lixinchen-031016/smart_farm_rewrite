"""数据库连接与会话管理（全应用唯一创建 engine 的地方）。

重写要点：
- 仅此处创建 `engine` 与 `SessionLocal`，杜绝旧版 `auth.py` 与 `database.py` 双会话工厂。
- `get_session()` 上下文管理器统一处理 commit / rollback / close。
- `init_db()` 用于本地开发快速建表；生产迁移以 Alembic 为准（见 migrations/）。
"""

import logging
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from smart_farm.config import get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()


def _create_engine():
    return create_engine(
        _settings.database_url,
        pool_size=_settings.db_pool_size,
        max_overflow=_settings.db_max_overflow,
        echo=_settings.db_echo,
        future=True,
    )


engine = _create_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def get_session():
    """数据库会话上下文管理器：自动提交/回滚/关闭。"""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def run_migrations() -> None:
    """执行 Alembic 迁移至最新版本（建表/升级的规范方式，单一事实来源）。"""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_PROJECT_ROOT / "migrations"))
    command.upgrade(cfg, "head")
    logger.info("数据库迁移已执行至 head: %s", _settings.database_url)


def init_db() -> None:
    """兼容别名：执行 Alembic 迁移（建议直接用 `alembic upgrade head`）。"""
    run_migrations()
