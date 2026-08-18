"""数据库连接与会话管理（全应用唯一创建 engine 的地方）。

双数据库支持（SQLite / MySQL 无感切换 + 故障自动转移）：

- 主库 `DATABASE_URL`（默认 SQLite），备库 `DATABASE_FALLBACK_URL`（可选，建议另一种数据库）。
- **启动时**：探测主库，不可达 → 自动切换备库（空库自动建表），日志与系统监控页明示。
- **运行时**：主库失联（拒绝连接/断开）→ `get_session()` 自动故障转移到备库
  （带冷却时间防抖动；SQLite 锁等待等临时错误不触发切换）。
- **切回主库**：主库恢复后需手动触发（系统监控页按钮或重启），避免数据在两库间来回跳；
  故障期间写入备库的数据可用「数据库同步」页合并回主库。
- 方言差异（连接参数 / 线程安全 / 内存库池策略）统一在 `_create_engine_for` 处理，业务代码无感。

其余约束不变：
- 仅此处创建 `engine` 与 `SessionLocal`；`get_session()` 统一 commit / rollback / close。
- 生产迁移以 Alembic 为准（`run_migrations()` 自动跟随**活动库**）。
"""

import logging
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from smart_farm.config import get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()

# 哨兵：区分"未传参"（回退配置）与"显式 None"（无备库）
_UNSET: object = object()

# 模块级活动引擎（全应用经 get_session() 使用；故障转移时整体换绑）
_lock = threading.RLock()
engine = None
SessionLocal = None

_state: dict = {
    "primary_url": None,
    "fallback_url": None,
    "active_url": None,
    "dialect": None,
    "role": "primary",  # primary / fallback
    "primary_error": None,  # 切换到备库时的主库错误
    "switched_at": None,
    "last_failover_ts": 0.0,  # monotonic，用于冷却判断
}

# 运行时判定「数据库失联」的错误特征（连接类错误；不含 SQLite 锁等待等临时错误）
_DOWN_PATTERNS = (
    "can't connect", "connection refused", "lost connection", "gone away",
    "connection reset", "timed out", "timeout", "unreachable",
    "name or service not known", "no route to host", "server has gone",
    "unable to open database file", "connection to server lost",
)


# ----------------------------- 引擎创建（方言感知） -----------------------------

def _dialect_of(url: str) -> str:
    """从 URL 提取方言名（mysql+pymysql → mysql；sqlite:/// → sqlite）。"""
    scheme = (urlsplit(url).scheme or "").lower()
    return scheme.split("+")[0]


def _create_engine_for(url: str):
    """按方言创建引擎：SQLite 线程安全 + 内存库共享连接；MySQL 连接超时与字符集。"""
    dialect = _dialect_of(url)
    kwargs: dict = {"echo": _settings.db_echo, "future": True, "pool_pre_ping": True}
    if dialect == "sqlite":
        # 网关线程（UDP/uvicorn）与会话可能跨线程 → 关闭同线程校验；timeout=忙等待上限
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if ":memory:" in url or url in ("sqlite://", "sqlite"):
            # 内存库：StaticPool 单连接共享，多线程共用同一份数据
            kwargs["poolclass"] = StaticPool
    else:
        kwargs["pool_size"] = _settings.db_pool_size
        kwargs["max_overflow"] = _settings.db_max_overflow
        if dialect == "mysql":
            kwargs["connect_args"] = {
                "connect_timeout": _settings.db_connect_timeout,
                "charset": "utf8mb4",
            }
    return create_engine(url, **kwargs)


def _probe(eng) -> tuple[bool, str]:
    """连通性探测：SELECT 1。返回 (是否可达, 错误描述)。"""
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, ""
    except Exception as e:  # noqa: BLE001 任意连接层异常都视为不可达
        return False, f"{type(e).__name__}: {e}"


def _mask_url(url: str) -> str:
    """日志/展示用 URL 脱敏（隐藏密码）。"""
    if not url or "://" not in url or "@" not in url:
        return url or ""
    head, rest = url.split("://", 1)
    userinfo, hostpart = rest.rsplit("@", 1)
    user = userinfo.split(":", 1)[0]
    return f"{head}://{user}:***@{hostpart}"


def _ensure_schema(eng) -> bool:
    """目标库缺表时自动建表（幂等）。全新空库同时打上 alembic head 标记，保持迁移一致。

    Returns:
        是否执行了建表。
    """
    from smart_farm.data.models import Base

    targets = set(Base.metadata.tables)
    existing = set(inspect(eng).get_table_names())
    if targets <= existing:
        return False

    fresh = not (existing & targets)  # 完全空库（无任何业务表）
    Base.metadata.create_all(eng)
    if fresh:
        _stamp_alembic_head(eng.url.render_as_string(hide_password=False))
    logger.warning("目标库缺表，已自动建表（fresh=%s）", fresh)
    return True


def _stamp_alembic_head(db_url: str) -> None:
    """给全新建表的库打 alembic head 标记，避免后续 upgrade 重复建表（尽力而为）。"""
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(_PROJECT_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(_PROJECT_ROOT / "migrations"))
        cfg.attributes["db_url"] = db_url
        command.stamp(cfg, "head")
    except Exception:  # noqa: BLE001 标记失败不影响运行
        logger.warning("alembic stamp 失败（不影响使用，可稍后手动处理）", exc_info=True)


_PROJECT_ROOT = Path(__file__).resolve().parents[3]


# ----------------------------- 引擎换绑与初始化 -----------------------------

def _apply_engine(eng, url: str, role: str, primary_error: str | None = None) -> None:
    """线程安全地换绑活动引擎（旧引擎 dispose 释放连接）。"""
    global engine, SessionLocal
    with _lock:
        old = engine
        engine = eng
        SessionLocal = sessionmaker(bind=eng, expire_on_commit=False, future=True)
        _state.update(
            active_url=url,
            dialect=_dialect_of(url),
            role=role,
            primary_error=primary_error,
            switched_at=datetime.now(),
        )
        if old is not None and old is not eng:
            try:
                old.dispose()
            except Exception:  # noqa: BLE001 释放尽力而为
                logger.warning("旧引擎 dispose 失败", exc_info=True)


def _init_database(primary_url: str | None = None, fallback_url: str | object | None = _UNSET) -> dict:
    """初始化活动数据库：探测主库 → 不可达则切换备库 → 均不可达抛 RuntimeError。

    主库可达时同样确保表结构（全新部署免迁移即可用）。
    参数：primary_url 缺省取配置；fallback_url 缺省（_UNSET）取配置，
    显式传 None 表示"无备库"（供测试注入）。
    Returns:
        当前状态快照。
    """
    global engine_init_error
    with _lock:
        primary = primary_url or _settings.database_url
        fallback = (
            _settings.database_fallback_url if fallback_url is _UNSET else fallback_url  # type: ignore[comparison-overlap]
        )
        _state["primary_url"], _state["fallback_url"] = primary, fallback or None

    eng = _create_engine_for(primary)
    ok, err = _probe(eng)
    if ok:
        _ensure_schema(eng)  # 全新主库（如新装 SQLite 文件）免迁移直接可用
        _apply_engine(eng, primary, "primary")
        engine_init_error = None
        logger.info("数据库已连接（主库 %s）：%s", _dialect_of(primary), _mask_url(primary))
        return dict(_state)

    logger.warning("主库不可达：%s → %s", _mask_url(primary), err)
    if fallback and fallback != primary:
        eng2 = _create_engine_for(fallback)
        ok2, err2 = _probe(eng2)
        if ok2:
            _ensure_schema(eng2)
            _apply_engine(eng2, fallback, "fallback", primary_error=err)
            engine_init_error = None
            logger.warning(
                "已自动切换到备用数据库（%s）：%s；主库恢复后请手动切回",
                _dialect_of(fallback), _mask_url(fallback),
            )
            return dict(_state)
        raise RuntimeError(f"主备数据库均不可连接。主库错误：{err}；备库错误：{err2}")
    raise RuntimeError(f"数据库不可连接且未配置备库（DATABASE_FALLBACK_URL）：{err}")


engine_init_error: Exception | None = None
try:
    _init_database()
except Exception as e:  # noqa: BLE001 模块导入失败不能让所有页面白屏——延后到首次使用时报错
    engine_init_error = e
    logger.error("数据库初始化失败：%s", e)


# ----------------------------- 会话（含运行时故障转移） -----------------------------

def _is_connection_down_error(e: OperationalError) -> bool:
    """判定是否为「数据库失联」类错误（SQLite 锁等待等临时错误不切换）。"""
    msg = str(e).lower()
    return any(p in msg for p in _DOWN_PATTERNS)


def _try_failover(reason: str) -> bool:
    """运行时故障转移：探测备库可达才切换；带冷却防抖动。返回是否切换成功。"""
    with _lock:
        fallback = _state["fallback_url"]
        if not fallback or fallback == _state["active_url"]:
            return False
        if time.monotonic() - _state["last_failover_ts"] < _settings.db_failover_cooldown_seconds:
            return False
        _state["last_failover_ts"] = time.monotonic()

    eng = _create_engine_for(fallback)
    ok, err = _probe(eng)
    if not ok:
        logger.error("故障转移失败（备库不可达）：%s", err)
        return False
    _ensure_schema(eng)
    _apply_engine(eng, fallback, "fallback", primary_error=reason)
    logger.warning("主库失联，已自动切换备用库：%s（原因：%s）", _mask_url(fallback), reason)
    return True


def _new_session_with_failover():
    """创建会话并立即建立连接：主库失联在此暴露并自动切换，业务语句不会中途断掉。"""
    for attempt in (1, 2):
        session = SessionLocal()
        try:
            session.connection()
            return session
        except OperationalError as e:
            session.close()
            if attempt == 2 or not _is_connection_down_error(e) or not _try_failover(f"{e}"):
                raise


@contextmanager
def get_session():
    """数据库会话上下文管理器：自动提交/回滚/关闭；主库失联自动故障转移。"""
    if engine_init_error is not None:
        raise RuntimeError(f"数据库初始化失败：{engine_init_error}") from engine_init_error
    session = _new_session_with_failover()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ----------------------------- 状态查询与管理 -----------------------------

def get_active_url() -> str:
    """当前活动库连接串（未初始化前回退配置值）。"""
    return _state["active_url"] or _settings.database_url


def get_database_urls() -> tuple[str, str | None]:
    """(主库 URL, 备库 URL) 原始值——供启动同步等需要直连两库的场景。

    展示用途请用 `active_database_info()`（已脱敏）。
    """
    return (
        _state["primary_url"] or _settings.database_url,
        _state["fallback_url"] if _state["fallback_url"] is not None else _settings.database_fallback_url,
    )


def active_database_info() -> dict:
    """数据库状态快照（系统监控页展示用；URL 已脱敏）。"""
    return {
        "role": _state["role"],
        "dialect": _state["dialect"],
        "active_url": _mask_url(_state["active_url"] or _settings.database_url),
        "primary_url": _mask_url(_state["primary_url"] or _settings.database_url),
        "fallback_url": _mask_url(_state["fallback_url"] or ""),
        "fallback_configured": bool(_state["fallback_url"]),
        "primary_error": _state["primary_error"],
        "switched_at": _state["switched_at"].strftime("%Y-%m-%d %H:%M:%S") if _state["switched_at"] else None,
    }


def reconnect_primary() -> tuple[bool, str]:
    """手动切回主库：探测可达才切换（不可达不动，避免半途丢数据）。"""
    with _lock:
        primary = _state["primary_url"] or _settings.database_url
    eng = _create_engine_for(primary)
    ok, err = _probe(eng)
    if not ok:
        return False, f"主库仍不可达：{err}"
    _apply_engine(eng, primary, "primary")
    logger.info("已切回主库：%s", _mask_url(primary))
    return True, "已切回主库（故障期间写入备库的数据可用「数据库同步」页合并）"


def probe_database(url: str) -> tuple[bool, str]:
    """探测任意连接串（连接测试用），返回 (是否可达, 错误描述)。"""
    try:
        eng = _create_engine_for(url)
    except Exception as e:  # noqa: BLE001 URL 非法等
        return False, f"{type(e).__name__}: {e}"
    try:
        return _probe(eng)
    finally:
        eng.dispose()


# ----------------------------- 迁移 -----------------------------

def run_migrations() -> None:
    """执行 Alembic 迁移至最新版本（自动跟随**活动库**，故障转移后仍作用于当前库）。"""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_PROJECT_ROOT / "migrations"))
    cfg.attributes["db_url"] = get_active_url()
    command.upgrade(cfg, "head")
    logger.info("数据库迁移已执行至 head: %s", _mask_url(get_active_url()))


def init_db() -> None:
    """兼容别名：执行 Alembic 迁移（建议直接用 `alembic upgrade head`）。"""
    run_migrations()
