"""数据库 ORM 模型（SQLAlchemy 2.0 风格）。

重写要点：
- `OperationLog` 改为 `id` 单字段自增主键，`log_time` 仅加索引（修复旧版双主键）。
- 表名使用简洁、可移植的命名（不再带 `intelligent_farm_` 前缀）。
- 预留 `greenhouse` / `greenhouse_id` 以支持未来多棚多租户。
- `details_json` 由应用层写入（不再依赖 MySQL 的 GENERATED 列，保证 SQLite 可用）。
"""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Greenhouse(Base):
    """大棚（多租户预留）。"""

    __tablename__ = "greenhouse"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    location = Column(String(255))


class AirTemperatureHumidity(Base):
    __tablename__ = "air_temperature_humidity"
    __table_args__ = (
        # 修复：复合索引覆盖最热查询 WHERE greenhouse_id=? AND timestamp>=? ORDER BY timestamp
        Index("ix_ath_gh_ts", "greenhouse_id", "timestamp"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    greenhouse_id = Column(Integer, ForeignKey("greenhouse.id"), nullable=True, index=True)
    temperature = Column(Float, nullable=False)
    humidity = Column(Float, nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)


class SoilMoisture(Base):
    __tablename__ = "soil_moisture"
    __table_args__ = (
        Index("ix_sm_gh_ts", "greenhouse_id", "timestamp"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    greenhouse_id = Column(Integer, ForeignKey("greenhouse.id"), nullable=True, index=True)
    value = Column(Float, nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)


class SoilNutrient(Base):
    __tablename__ = "soil_nutrient"
    __table_args__ = (
        Index("ix_sn_gh_ts", "greenhouse_id", "timestamp"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    greenhouse_id = Column(Integer, ForeignKey("greenhouse.id"), nullable=True, index=True)
    value = Column(Float, nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)


class LightIntensity(Base):
    __tablename__ = "light_intensity"
    __table_args__ = (
        Index("ix_li_gh_ts", "greenhouse_id", "timestamp"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    greenhouse_id = Column(Integer, ForeignKey("greenhouse.id"), nullable=True, index=True)
    value = Column(Float, nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)


class User(Base):
    __tablename__ = "user"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(255), unique=True, nullable=False, index=True)
    password = Column(String(255), nullable=False)  # bcrypt 哈希
    last_login_time = Column(DateTime, nullable=False)
    role = Column(String(50), nullable=False, default="user")
    admin_request = Column(Boolean, nullable=False, default=False)
    admin_request_time = Column(DateTime, nullable=True)
    greenhouse_id = Column(Integer, ForeignKey("greenhouse.id"), nullable=True)


class OperationLog(Base):
    """操作审计日志。"""

    __tablename__ = "operation_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)  # 单字段自增主键
    log_time = Column(DateTime, nullable=False, index=True)
    log_level = Column(String(10), nullable=False)
    username = Column(String(50), index=True)
    action_type = Column(String(50), nullable=False, index=True)
    action_details = Column(Text)
    details_json = Column(JSON, nullable=True)  # 由应用层写入，跨数据库可移植


# 指标名 -> ORM 模型 的映射，供仓库层通用查询使用
SENSOR_MODELS = {
    "air_temperature_humidity": AirTemperatureHumidity,
    "soil_moisture": SoilMoisture,
    "soil_nutrient": SoilNutrient,
    "light_intensity": LightIntensity,
}
