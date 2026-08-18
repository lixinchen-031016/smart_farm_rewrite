"""物联网数据接入服务（协议无关，纯逻辑，无 Streamlit 依赖）。

被三种接入通道复用：
- HTTP 网关（`smart_farm.iot_gateway`，FastAPI）
- MQTT 订阅（paho-mqtt 后台线程）
- UDP 局域网直推（标准库 socket）

统一流程：设备认证 → payload 归一化 → 逐条写传感器表 → 更新设备在线状态。

payload 格式（UTF-8 JSON，字段大小写不敏感的别名见 `_FIELD_ALIASES`）：

单条：
    {"device_key": "...", "metric": "soil_moisture", "value": 42.1,
     "timestamp": "2026-08-18T10:00:00"}          # timestamp 可省略=服务器当前时间
    {"device_key": "...", "metric": "air", "temperature": 25.3, "humidity": 60.5}

批量：
    {"device_key": "...", "readings": [ {...}, {...} ]}
"""

import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from smart_farm.data import repositories as repo
from smart_farm.data.models import SENSOR_MODELS

# 传感器指标别名 → 数据库指标名
METRIC_ALIASES = {
    "air": "air_temperature_humidity",
    "air_temperature_humidity": "air_temperature_humidity",
    "temperature": "air_temperature_humidity",
    "humidity": "air_temperature_humidity",
    "soil_moisture": "soil_moisture",
    "soil": "soil_moisture",
    "soil_nutrient": "soil_nutrient",
    "nutrient": "soil_nutrient",
    "light_intensity": "light_intensity",
    "light": "light_intensity",
}

# 各指标除公共字段外的取值列（air 为双列，其余单列 value）
_VALUE_COLUMNS: dict[str, tuple[str, ...]] = {
    "air_temperature_humidity": ("temperature", "humidity"),
    "soil_moisture": ("value",),
    "soil_nutrient": ("value",),
    "light_intensity": ("value",),
}


class IngestError(Exception):
    """接入失败（认证 / 格式 / 校验），message 面向设备端展示。"""


@dataclass
class IngestResult:
    accepted: int = 0
    rejected: int = 0
    errors: list[str] = field(default_factory=list)


def generate_device_key() -> str:
    """生成设备凭证 `sf-` + 32 位随机 hex（不可猜测）。"""
    return f"sf-{secrets.token_hex(16)}"


def _parse_timestamp(raw: Any) -> datetime:
    """时间戳解析：ISO 字符串 / epoch 秒 / epoch 毫秒。"""
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, (int, float)):
        # 毫秒级 epoch 通常 > 1e12
        return datetime.fromtimestamp(raw / 1000.0 if raw > 1e12 else raw)
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError as e:
            raise IngestError(f"无法解析时间戳：{raw}") from e
    raise IngestError(f"时间戳类型不支持：{type(raw).__name__}")


def _normalize_reading(raw: dict) -> tuple[str, dict[str, Any], Optional[datetime]]:
    """单条读数归一化 → (metric, 取值字段, timestamp)。非法即抛 IngestError。"""
    if not isinstance(raw, dict):
        raise IngestError("读数必须是 JSON 对象")

    metric_raw = str(raw.get("metric", "")).strip().lower()
    if not metric_raw:
        # 无 metric 字段时按包含的取值列名推断（temperature/humidity → air）
        keys = {k.lower() for k in raw}
        if keys & {"temperature", "humidity"}:
            metric_raw = "air"
        elif "light" in keys:
            metric_raw = "light"
        elif keys & {"soil_moisture", "soil"}:
            metric_raw = "soil_moisture"
        elif keys & {"soil_nutrient", "nutrient"}:
            metric_raw = "soil_nutrient"
    metric = METRIC_ALIASES.get(metric_raw)
    if metric is None:
        raise IngestError(f"未知指标：{metric_raw}（可选 {sorted(set(SENSOR_MODELS))}）")

    fields: dict[str, Any] = {}
    for col in _VALUE_COLUMNS[metric]:
        v = raw.get(col)
        if v is None:
            raise IngestError(f"{metric} 缺少必填字段 {col}")
        try:
            fields[col] = float(v)
        except (TypeError, ValueError) as e:
            raise IngestError(f"{metric}.{col} 必须是数值：{v!r}") from e

    ts_raw = raw.get("timestamp")
    return metric, fields, _parse_timestamp(ts_raw) if ts_raw is not None else None


def authenticate_device(session: Session, device_key: Optional[str]):
    """按 device_key 认证设备；失败抛 IngestError。"""
    if not device_key:
        raise IngestError("缺少设备凭证 device_key")
    device = repo.get_device_by_key(session, device_key)
    if device is None:
        raise IngestError("设备未注册")
    if not device.enabled:
        raise IngestError("设备已停用")
    return device


def ingest_payload(session: Session, payload: dict, device_key: Optional[str] = None) -> IngestResult:
    """统一入口：认证（payload 或显式参数中的 key）→ 解析 → 入库。

    支持 `{"readings": [...]}` 批量与单条对象；`greenhouse_id` 取设备注册时
    绑定的大棚（防止设备伪造他人棚号）。
    """
    if not isinstance(payload, dict):
        raise IngestError("payload 必须是 JSON 对象")
    key = device_key or payload.get("device_key")
    device = authenticate_device(session, key)

    readings = payload.get("readings")
    items = readings if isinstance(readings, list) else [payload]
    if not items:
        raise IngestError("payload 为空")

    result = IngestResult()
    now = datetime.now()
    for i, raw in enumerate(items):
        try:
            metric, fields, ts = _normalize_reading(raw)
            repo.add_sensor_reading(
                session,
                metric,
                greenhouse_id=device.greenhouse_id,
                timestamp=ts or now,
                **fields,
            )
            result.accepted += 1
        except (IngestError, ValueError) as e:
            result.rejected += 1
            result.errors.append(f"第 {i + 1} 条：{e}")
    repo.touch_device(session, device.id)
    return result
