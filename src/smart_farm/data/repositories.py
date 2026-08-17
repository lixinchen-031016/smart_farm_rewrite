"""数据访问层（仓库）。

重写要点：
- 所有传感器取数支持时间窗 + 分页，杜绝旧版 `fetch_data_in_bulk` 全量拉取。
- 纯 SQLAlchemy，不依赖 Streamlit；可被 services / API / 测试复用。
"""

from datetime import datetime
from typing import Optional, Sequence

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from smart_farm.data.models import (
    SENSOR_MODELS,
    AirTemperatureHumidity,
    Greenhouse,
    LightIntensity,
    OperationLog,
    SoilMoisture,
    SoilNutrient,
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


def fetch_data_in_bulk(
    session: Session,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    limit: int = 100000,
) -> pd.DataFrame:
    """按时间窗批量拉取全部传感器（多表 LEFT JOIN，对齐旧库输出列）。

    以 `air_temperature_humidity` 为左表 JOIN 其余 3 张传感器表，
    输出列：timestamp, temperature, humidity, soil_moisture, soil_nutrient, light_intensity。
    """
    from sqlalchemy import outerjoin, select

    at = AirTemperatureHumidity
    sm = SoilMoisture
    sn = SoilNutrient
    li = LightIntensity
    stmt = select(
        at.timestamp.label("timestamp"),
        at.temperature.label("temperature"),
        at.humidity.label("humidity"),
        sm.value.label("soil_moisture"),
        sn.value.label("soil_nutrient"),
        li.value.label("light_intensity"),
    ).select_from(
        outerjoin(
            outerjoin(outerjoin(at, sm, at.timestamp == sm.timestamp), sn, at.timestamp == sn.timestamp),
            li,
            at.timestamp == li.timestamp,
        )
    )
    if start is not None:
        stmt = stmt.where(at.timestamp >= start)
    if end is not None:
        stmt = stmt.where(at.timestamp <= end)
    stmt = stmt.order_by(at.timestamp.asc()).limit(limit)
    rows = session.execute(stmt).all()
    return pd.DataFrame(rows, columns=list(stmt.selected_columns.keys()))


# ----------------------------- 用户 -----------------------------


def get_user_by_username(session: Session, username: str) -> Optional[User]:
    return session.execute(select(User).where(User.username == username)).scalars().first()


def create_user(
    session: Session,
    username: str,
    password_hash: str,
    role: str = "user",
    greenhouse_id: Optional[int] = None,
    admin_request: bool = False,
) -> User:
    user = User(
        username=username,
        password=password_hash,
        last_login_time=datetime.now(),
        role=role,
        greenhouse_id=greenhouse_id,
        admin_request=admin_request,
        admin_request_time=datetime.now() if admin_request else None,
    )
    session.add(user)
    session.flush()
    return user


def list_users(session: Session) -> Sequence[User]:
    return session.execute(select(User).order_by(User.id)).scalars().all()


def list_admin_requests(session: Session) -> Sequence[User]:
    """列出待审批的管理员申请（admin_request=True）。"""
    return (
        session.execute(
            select(User)
            .where(User.admin_request.is_(True))
            .order_by(User.admin_request_time.asc())
        )
        .scalars()
        .all()
    )


def approve_admin_request(session: Session, user_id: int) -> bool:
    """批准管理员申请：角色升为 admin，清除申请标志。"""
    user = session.get(User, user_id)
    if not user or not user.admin_request:
        return False
    user.role = "admin"
    user.admin_request = False
    user.admin_request_time = None
    return True


def reject_admin_request(session: Session, user_id: int) -> bool:
    """拒绝管理员申请：清除申请标志（保持普通用户）。"""
    user = session.get(User, user_id)
    if not user or not user.admin_request:
        return False
    user.admin_request = False
    user.admin_request_time = None
    return True


def update_user_role(session: Session, user_id: int, role: str) -> None:
    user = session.get(User, user_id)
    if user:
        user.role = role


def update_user_password(session: Session, user_id: int, password_hash: str) -> bool:
    """重置用户密码（bcrypt 哈希）。用户不存在返回 False。"""
    user = session.get(User, user_id)
    if not user:
        return False
    user.password = password_hash
    return True


def delete_user(session: Session, user_id: int) -> bool:
    """删除用户（调用方负责禁止删除自身）。用户不存在返回 False。"""
    user = session.get(User, user_id)
    if not user:
        return False
    session.delete(user)
    return True


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
