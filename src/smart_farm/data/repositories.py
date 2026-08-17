"""数据访问层（仓库）。

重写要点：
- 所有传感器取数支持时间窗 + 分页，杜绝旧版 `fetch_data_in_bulk` 全量拉取。
- 纯 SQLAlchemy，不依赖 Streamlit；可被 services / API / 测试复用。
"""

from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from smart_farm.data.models import (
    SENSOR_MODELS,
    Greenhouse,
    OperationLog,
    User,
)

# ----------------------------- 传感器数据 -----------------------------


def get_sensor_readings(
    session: Session,
    metric: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    greenhouse_id: Optional[int] = None,
    limit: int = 1000,
    offset: int = 0,
) -> Sequence:
    """按时间窗 + 分页查询某类传感器数据（默认按时间倒序）。"""
    model = SENSOR_MODELS[metric]
    stmt = select(model)
    if greenhouse_id is not None:
        stmt = stmt.where(model.greenhouse_id == greenhouse_id)
    if start is not None:
        stmt = stmt.where(model.timestamp >= start)
    if end is not None:
        stmt = stmt.where(model.timestamp <= end)
    stmt = stmt.order_by(model.timestamp.desc()).limit(limit).offset(offset)
    return session.execute(stmt).scalars().all()


def get_latest_sensor_reading(session: Session, metric: str, greenhouse_id: Optional[int] = None):
    """获取某类传感器最新一条记录。"""
    model = SENSOR_MODELS[metric]
    stmt = select(model)
    if greenhouse_id is not None:
        stmt = stmt.where(model.greenhouse_id == greenhouse_id)
    stmt = stmt.order_by(model.timestamp.desc()).limit(1)
    return session.execute(stmt).scalars().first()


def count_sensor_readings(
    session: Session,
    metric: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    greenhouse_id: Optional[int] = None,
) -> int:
    model = SENSOR_MODELS[metric]
    stmt = select(func.count()).select_from(model)
    if greenhouse_id is not None:
        stmt = stmt.where(model.greenhouse_id == greenhouse_id)
    if start is not None:
        stmt = stmt.where(model.timestamp >= start)
    if end is not None:
        stmt = stmt.where(model.timestamp <= end)
    return session.execute(stmt).scalar() or 0


def add_sensor_reading(session: Session, metric: str, value=None, greenhouse_id=None, timestamp=None, **fields):
    """写入一条传感器数据。对 multi-column 模型（空气温湿度）用 fields 传额外列。"""
    model = SENSOR_MODELS[metric]
    row = model(greenhouse_id=greenhouse_id, timestamp=timestamp, **fields)
    # 单值类传感器
    if value is not None and hasattr(row, "value"):
        row.value = value
    session.add(row)
    session.flush()
    return row


# ----------------------------- 用户 -----------------------------


def get_user_by_username(session: Session, username: str) -> Optional[User]:
    return session.execute(select(User).where(User.username == username)).scalars().first()


def create_user(
    session: Session,
    username: str,
    password_hash: str,
    role: str = "user",
    greenhouse_id: Optional[int] = None,
) -> User:
    user = User(
        username=username,
        password=password_hash,
        last_login_time=datetime.now(),
        role=role,
        greenhouse_id=greenhouse_id,
    )
    session.add(user)
    session.flush()
    return user


def list_users(session: Session) -> Sequence[User]:
    return session.execute(select(User).order_by(User.id)).scalars().all()


def update_user_role(session: Session, user_id: int, role: str) -> None:
    user = session.get(User, user_id)
    if user:
        user.role = role


# ----------------------------- 大棚 -----------------------------


def list_greenhouses(session: Session) -> Sequence[Greenhouse]:
    return session.execute(select(Greenhouse).order_by(Greenhouse.id)).scalars().all()


# ----------------------------- 操作日志 -----------------------------


def add_log(
    session: Session,
    level: str,
    username: str,
    action_type: str,
    details: str,
    details_json=None,
) -> OperationLog:
    log = OperationLog(
        log_time=datetime.now(),
        log_level=level,
        username=username,
        action_type=action_type,
        action_details=details,
        details_json=details_json,
    )
    session.add(log)
    session.flush()
    return log


def get_logs(
    session: Session,
    username: Optional[str] = None,
    action_type: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> Sequence[OperationLog]:
    stmt = select(OperationLog)
    if username:
        stmt = stmt.where(OperationLog.username == username)
    if action_type:
        stmt = stmt.where(OperationLog.action_type == action_type)
    stmt = stmt.order_by(OperationLog.log_time.desc()).limit(limit).offset(offset)
    return session.execute(stmt).scalars().all()
