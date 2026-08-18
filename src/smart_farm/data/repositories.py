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
    Device,
    Greenhouse,
    LightIntensity,
    OperationLog,
    SoilMoisture,
    SoilNutrient,
    User,
    UserGreenhouse,
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
    """写入一条传感器数据。对 multi-column 模型（空气温湿度）用 fields 传额外列。

    修复：校验必填列，避免漏传 temperature/humidity 到 flush 才抛 IntegrityError。
    """
    model = SENSOR_MODELS[metric]
    row = model(greenhouse_id=greenhouse_id, timestamp=timestamp, **fields)
    # 单值类传感器
    if value is not None and hasattr(row, "value"):
        row.value = value
    # 必填列校验（除 id/greenhouse_id/timestamp 外的非空列）
    required = {
        c.name for c in model.__table__.columns
        if not c.nullable and c.name not in ("id", "greenhouse_id", "timestamp") and c.default is None
    }
    missing = {c for c in required if getattr(row, c, None) is None}
    if missing:
        raise ValueError(f"{metric} 缺少必填字段：{', '.join(sorted(missing))}")
    session.add(row)
    session.flush()
    return row


def fetch_data_in_bulk(
    session: Session,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    limit: int = 100000,
) -> pd.DataFrame:
    """按时间窗批量拉取全部传感器（多表窗口匹配，对齐旧库输出列）。

    以 `air_temperature_humidity` 为左表，按「±2 分钟时间窗」关联其余 3 张表
    （修复：等值 JOIN 在真实设备采样时刻有秒级偏差时整行丢失）。
    输出列：timestamp, temperature, humidity, soil_moisture, soil_nutrient, light_intensity。
    """
    from sqlalchemy import outerjoin, select

    at = AirTemperatureHumidity
    sm = SoilMoisture
    sn = SoilNutrient
    li = LightIntensity

    def _window(right_table):
        """构造时间窗匹配条件：|at.ts - right.ts| <= 120 秒。"""
        from sqlalchemy import func as sa_func

        return sa_func.abs(sa_func.extract("epoch", at.timestamp) - sa_func.extract("epoch", right_table.timestamp)) <= 120

    stmt = select(
        at.timestamp.label("timestamp"),
        at.temperature.label("temperature"),
        at.humidity.label("humidity"),
        sm.value.label("soil_moisture"),
        sn.value.label("soil_nutrient"),
        li.value.label("light_intensity"),
    ).select_from(
        outerjoin(
            outerjoin(outerjoin(at, sm, _window(sm)), sn, _window(sn)),
            li,
            _window(li),
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


def get_greenhouse(session: Session, greenhouse_id: int) -> Optional[Greenhouse]:
    return session.get(Greenhouse, greenhouse_id)


def create_greenhouse(session: Session, name: str, location: Optional[str] = None) -> Greenhouse:
    """新建大棚（同名不重复创建，幂等返回既有）。"""
    existing = session.execute(select(Greenhouse).where(Greenhouse.name == name)).scalars().first()
    if existing:
        return existing
    gh = Greenhouse(name=name, location=location)
    session.add(gh)
    session.flush()
    return gh


def update_greenhouse(session: Session, greenhouse_id: int, name: str, location: Optional[str]) -> bool:
    gh = session.get(Greenhouse, greenhouse_id)
    if not gh:
        return False
    gh.name = name
    gh.location = location
    return True


def delete_greenhouse(session: Session, greenhouse_id: int) -> bool:
    """删除大棚（连带清理用户关联与设备归属）。"""
    gh = session.get(Greenhouse, greenhouse_id)
    if not gh:
        return False
    session.query(UserGreenhouse).filter(UserGreenhouse.greenhouse_id == greenhouse_id).delete()
    session.query(Device).filter(Device.greenhouse_id == greenhouse_id).update(
        {"greenhouse_id": None}
    )
    session.delete(gh)
    session.flush()
    return True


# ----------------------------- 用户-大棚授权（多租户） -----------------------------


def list_greenhouse_ids_for_user(session: Session, user_id: int) -> list[int]:
    """用户经关联表授权的大棚 id 列表（不含 User.greenhouse_id 兼容字段）。"""
    rows = session.execute(
        select(UserGreenhouse.greenhouse_id).where(UserGreenhouse.user_id == user_id)
    ).scalars().all()
    return sorted(rows)


def list_greenhouses_for_user(session: Session, user: User) -> Sequence[Greenhouse]:
    """用户可见大棚：关联表 ∪ 自身 greenhouse_id 字段（向后兼容旧数据）。

    admin 可见全部大棚。
    """
    if user.role == "admin":
        return list_greenhouses(session)
    linked_ids = set(
        session.execute(
            select(UserGreenhouse.greenhouse_id).where(UserGreenhouse.user_id == user.id)
        ).scalars().all()
    )
    if user.greenhouse_id is not None:
        linked_ids.add(user.greenhouse_id)
    if not linked_ids:
        return []
    stmt = select(Greenhouse).where(Greenhouse.id.in_(linked_ids)).order_by(Greenhouse.id)
    return session.execute(stmt).scalars().all()


def set_user_greenhouses(session: Session, user_id: int, greenhouse_ids: Sequence[int]) -> None:
    """整体替换用户的授权大棚集合（多选保存语义）。"""
    session.query(UserGreenhouse).filter(UserGreenhouse.user_id == user_id).delete()
    for gid in greenhouse_ids:
        session.add(UserGreenhouse(user_id=user_id, greenhouse_id=gid, granted_at=datetime.now()))
    session.flush()


def user_can_access_greenhouse(session: Session, user: User, greenhouse_id: Optional[int]) -> bool:
    """鉴权：admin 全量可见；普通用户按授权集合判断（greenhouse_id 为 None 表示未隔离）。"""
    if user.role == "admin":
        return True
    if greenhouse_id is None:
        return True
    if user.greenhouse_id == greenhouse_id:
        return True
    link = session.execute(
        select(UserGreenhouse).where(
            UserGreenhouse.user_id == user.id, UserGreenhouse.greenhouse_id == greenhouse_id
        )
    ).scalars().first()
    return link is not None


# ----------------------------- IoT 设备 -----------------------------


def get_device_by_key(session: Session, device_key: str) -> Optional[Device]:
    return session.execute(select(Device).where(Device.device_key == device_key)).scalars().first()


def list_devices(session: Session, greenhouse_id: Optional[int] = None) -> Sequence[Device]:
    stmt = select(Device).order_by(Device.id)
    if greenhouse_id is not None:
        stmt = stmt.where(Device.greenhouse_id == greenhouse_id)
    return session.execute(stmt).scalars().all()


def create_device(
    session: Session,
    name: str,
    protocol: str,
    device_key: str,
    greenhouse_id: Optional[int] = None,
    note: Optional[str] = None,
) -> Device:
    device = Device(
        device_key=device_key,
        name=name,
        protocol=protocol,
        greenhouse_id=greenhouse_id,
        enabled=True,
        note=note,
        created_at=datetime.now(),
    )
    session.add(device)
    session.flush()
    return device


def set_device_enabled(session: Session, device_id: int, enabled: bool) -> bool:
    device = session.get(Device, device_id)
    if not device:
        return False
    device.enabled = enabled
    return True


def touch_device(session: Session, device_id: int) -> None:
    """更新设备最近上报时间（数据接入时调用）。"""
    device = session.get(Device, device_id)
    if device:
        device.last_seen_at = datetime.now()


def delete_device(session: Session, device_id: int) -> bool:
    device = session.get(Device, device_id)
    if not device:
        return False
    session.delete(device)
    return True


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
        username=(username or "")[:50],  # 修复：对齐 OperationLog.username 列宽，防 MySQL Data too long
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
    log_level: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    limit: int = 200,
    offset: int = 0,
) -> Sequence[OperationLog]:
    stmt = select(OperationLog)
    if username:
        stmt = stmt.where(OperationLog.username == username)
    if action_type:
        stmt = stmt.where(OperationLog.action_type == action_type)
    if log_level:
        stmt = stmt.where(OperationLog.log_level == log_level)
    if start is not None:
        stmt = stmt.where(OperationLog.log_time >= start)
    if end is not None:
        stmt = stmt.where(OperationLog.log_time <= end)
    stmt = stmt.order_by(OperationLog.log_time.desc()).limit(limit).offset(offset)
    return session.execute(stmt).scalars().all()


def count_logs(
    session: Session,
    log_level: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> int:
    """日志计数（聚合查询，避免全量拉取——修复 len(get_logs(limit=100000)) 性能问题）。"""
    stmt = select(func.count()).select_from(OperationLog)
    if log_level:
        stmt = stmt.where(OperationLog.log_level == log_level)
    if start is not None:
        stmt = stmt.where(OperationLog.log_time >= start)
    if end is not None:
        stmt = stmt.where(OperationLog.log_time <= end)
    return session.execute(stmt).scalar() or 0
