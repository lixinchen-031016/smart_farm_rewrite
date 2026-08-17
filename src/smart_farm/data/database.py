"""数据库连接与会话管理（全应用唯一创建 engine 的地方）。

重写要点：
- 仅此处创建 `engine` 与 `SessionLocal`，杜绝旧版 `auth.py` 与 `database.py` 双会话工厂。
- `get_session()` 上下文管理器统一处理 commit / rollback / close。
- `init_db()` 用于本地开发快速建表；生产迁移以 Alembic 为准（见 migrations/）。
"""

import logging
from contextlib import contextmanager

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


def init_db() -> None:
    """开发环境快速建表（生产请用 Alembic 迁移）。"""
    from smart_farm.data.models import Base

    Base.metadata.create_all(engine)
    logger.info("数据库表已初始化: %s", _settings.database_url)
