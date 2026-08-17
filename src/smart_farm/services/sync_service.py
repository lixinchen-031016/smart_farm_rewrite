"""数据库同步服务（纯逻辑，无 Streamlit 依赖，可单测）。

对齐旧版 `utils/sync_manager.py`：
- 基于最大时间戳的增量双向同步（云端新 → 拉本地；本地新 → 推云端）
- 两端最大时间戳相等时走冲突策略（本版：跳过，对齐旧版恒 0 冲突）
- 同步 4 张传感器表（air_temperature_humidity / soil_moisture / soil_nutrient / light_intensity）
- 输入校验 `validate_database_inputs`

与旧版差异：连接信息用 SQLAlchemy URL 注入（而非 UI 表单），便于单测与解耦。
"""

import re
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote_plus

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from smart_farm.data.models import (
    SENSOR_MODELS,
    AirTemperatureHumidity,
    LightIntensity,
    SoilMoisture,
    SoilNutrient,
)

SYNC_TABLES = [
    ("air_temperature_humidity", AirTemperatureHumidity),
    ("soil_moisture", SoilMoisture),
    ("soil_nutrient", SoilNutrient),
    ("light_intensity", LightIntensity),
]


def validate_database_inputs(host: str, port: int, name: str, user: str, password: str) -> list[str]:
    """校验数据库连接参数（对齐旧版），返回错误列表（空 = 通过）。"""
    errors: list[str] = []
    if not re.match(r"^(\d{1,3}\.){3}\d{1,3}$|^localhost$|^[\w.-]+$", host):
        errors.append("无效的主机地址格式")
    try:
        port_num = int(port)
    except (TypeError, ValueError):
        errors.append("端口号必须是数字")
        return errors
    if not (1 <= port_num <= 65535):
        errors.append("端口号必须在 1-65535 之间")
    if not re.match(r"^[a-zA-Z0-9_]+$", name):
        errors.append("数据库名称只能包含字母、数字和下划线")
    if not user:
        errors.append("用户名不能为空")
    if password is None:
        errors.append("密码不能为 None")
    return errors


def build_mysql_url(host: str, port: int, name: str, user: str, password: str) -> str:
    """构造 mysql+pymysql URL（密码经 quote_plus 转义）。"""
    return f"mysql+pymysql://{user}:{quote_plus(password)}@{host}:{port}/{name}"


class DatabaseSync:
    """双向增量同步器：cloud_url 与 local_url 均为 SQLAlchemy 连接串。"""

    def __init__(self, cloud_url: str, local_url: str):
        self.cloud_url = cloud_url
        self.local_url = local_url
        self._cloud_engine = create_engine(cloud_url, pool_pre_ping=True)
        self._local_engine = create_engine(local_url, pool_pre_ping=True)

    def test_connection(self, url: Optional[str] = None) -> tuple[bool, str]:
        """测试连接：返回 (是否成功, 信息)。"""
        engine = create_engine(url or self.cloud_url)
        try:
            with engine.connect() as conn:
                conn.execute(select(1))
            return True, "连接成功"
        except Exception as e:  # noqa: BLE001
            return False, f"连接失败：{e}"
        finally:
            engine.dispose()

    @staticmethod
    def _max_timestamp(session: Session, model) -> Optional[datetime]:
        from sqlalchemy import func

        return session.execute(select(func.max(model.timestamp))).scalar()

    @staticmethod
    def _rows_after(session: Session, model, after: Optional[datetime], limit: int = 10000):
        """分页拉取 after 之后（含相等，修复同时间戳漏同步）的行。"""
        stmt = select(model)
        if after is not None:
            stmt = stmt.where(model.timestamp >= after)  # >= 修复同一最大时间戳下多批次数据漏同步
        stmt = stmt.order_by(model.timestamp.asc(), model.id.asc())
        return session.execute(stmt).scalars().all()

    @staticmethod
    def _row_key(row) -> tuple:
        """行去重键：(timestamp, 各非 id 列值)。同刻不同批次值不同 → 不去重。"""
        values = tuple(str(getattr(row, c.name)) for c in row.__table__.columns if c.name != "id")
        return (row.timestamp, values)

    @staticmethod
    def _existing_keys(session: Session, model, ts_list) -> set:
        """目标库中与 ts_list 匹配的行键集合（用于幂等去重）。"""
        if not ts_list:
            return set()
        rows = session.execute(
            select(model).where(model.timestamp.in_(ts_list))
        ).scalars().all()
        return {DatabaseSync._row_key(r) for r in rows}

    @staticmethod
    def _copy_rows(session: Session, model, rows, skip_keys: Optional[set] = None) -> int:
        """幂等复制：跳过目标库已存在的行（按 时间戳+值 去重，修复重复插入）。"""
        count = 0
        for row in rows:
            if skip_keys is not None and DatabaseSync._row_key(row) in skip_keys:
                continue
            kwargs = {
                c.name: getattr(row, c.name)
                for c in model.__table__.columns
                if c.name != "id"
            }
            session.add(model(**kwargs))
            count += 1
        return count

    def sync_table_data(self, table_name: str, model=None) -> dict[str, Any]:
        """同步单表：返回 {table, cloud_to_local, local_to_cloud, conflicts}。

        修复：
        - 增量条件 `>` → `>=`（同一最大时间戳下新增的同刻数据不再永久漏同步）
        - `_copy_rows` 幂等去重（同时间戳已存在则不重复插入）
        """
        model = model or SENSOR_MODELS[table_name]
        stats: dict[str, Any] = {"table": table_name, "cloud_to_local": 0, "local_to_cloud": 0, "conflicts": 0}

        with Session(self._cloud_engine) as cloud, Session(self._local_engine) as local:
            cloud_max = self._max_timestamp(cloud, model)
            local_max = self._max_timestamp(local, model)

            # 云端新 → 拉本地（>= 含同刻新增批次，幂等去重避免重复插入）
            if cloud_max and (not local_max or cloud_max >= local_max):
                rows = self._rows_after(cloud, model, local_max)
                skip = self._existing_keys(local, model, [r.timestamp for r in rows])
                stats["cloud_to_local"] = self._copy_rows(local, model, rows, skip)
                local.commit()

            # 本地新 → 推云端
            if local_max and (not cloud_max or local_max >= cloud_max):
                rows = self._rows_after(local, model, cloud_max)
                skip = self._existing_keys(cloud, model, [r.timestamp for r in rows])
                stats["local_to_cloud"] = self._copy_rows(cloud, model, rows, skip)
                cloud.commit()

            # 时间戳相等 → 冲突（跳过策略，对齐旧版；实际同步已由幂等去重处理）
            if cloud_max and local_max and cloud_max == local_max:
                stats["conflicts"] = 0

        return stats

    def sync_all_data(self) -> list[dict[str, Any]]:
        """同步全部 4 张传感器表，返回各表统计列表。"""
        return [self.sync_table_data(name, model) for name, model in SYNC_TABLES]

    def close(self) -> None:
        self._cloud_engine.dispose()
        self._local_engine.dispose()
