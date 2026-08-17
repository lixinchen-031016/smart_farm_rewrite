"""仪表板服务（纯逻辑，无 Streamlit 依赖）。

对齐旧库 `integrated_dashboard.py` 的作物阶段推荐表与默认阈值；偏好/渲染由 UI 层负责。
"""

from typing import Any, Optional

# 作物阶段 -> 四指标 {min, max, optimal}
CROP_STAGE_RECOMMENDATIONS: dict[str, dict[str, Any]] = {
    "growth": {
        "name": "生长期",
        "temperature": {"min": 20, "max": 28, "optimal": 24},
        "humidity": {"min": 50, "max": 70, "optimal": 60},
        "soil_moisture": {"min": 35, "max": 65, "optimal": 50},
        "light_intensity": {"min": 1500, "max": 40000, "optimal": 20000},
    },
    "flowering": {
        "name": "开花期",
        "temperature": {"min": 18, "max": 26, "optimal": 22},
        "humidity": {"min": 40, "max": 60, "optimal": 50},
        "soil_moisture": {"min": 30, "max": 55, "optimal": 45},
        "light_intensity": {"min": 2000, "max": 45000, "optimal": 25000},
    },
    "fruiting": {
        "name": "结果期",
        "temperature": {"min": 22, "max": 30, "optimal": 26},
        "humidity": {"min": 45, "max": 65, "optimal": 55},
        "soil_moisture": {"min": 40, "max": 70, "optimal": 55},
        "light_intensity": {"min": 2500, "max": 50000, "optimal": 30000},
    },
}

# 默认自定义阈值（用户未设置时生效），对齐旧库
DEFAULT_THRESHOLDS: dict[str, dict[str, float]] = {
    "temperature": {"min": 20, "max": 30},
    "humidity": {"min": 40, "max": 70},
    "soil_moisture": {"min": 30, "max": 60},
    "soil_nutrient": {"min": 10, "max": 20},
    "light_intensity": {"min": 1000, "max": 50000},
}

# 各指标告警判定用的固定阈值（对齐旧库 data_preview 硬编码）
ALERT_THRESHOLDS: dict[str, Any] = {
    "temperature": {"min": 20, "max": 30},
    "humidity": {"min": 40, "max": 70},
    "soil_moisture": {"min": 30, "max": 60},
    "soil_nutrient": {"min": 10, "max": 20},
    "light_intensity": {"min": 1000, "max": None},  # 光照仅下限告警
}


def get_crop_stage_recommendations(crop_stage: Optional[str]) -> dict[str, Any]:
    """返回作物阶段推荐参数（未知名阶段回退生长期）。"""
    return CROP_STAGE_RECOMMENDATIONS.get(crop_stage or "growth", CROP_STAGE_RECOMMENDATIONS["growth"])


def recommendations_to_thresholds(recommendations: dict[str, Any]) -> dict[str, dict[str, float]]:
    """把阶段推荐转成 min/max 阈值字典（沿用用户已有的养分阈值或默认）。"""
    thresholds = {
        "temperature": {"min": recommendations["temperature"]["min"], "max": recommendations["temperature"]["max"]},
        "humidity": {"min": recommendations["humidity"]["min"], "max": recommendations["humidity"]["max"]},
        "soil_moisture": {"min": recommendations["soil_moisture"]["min"], "max": recommendations["soil_moisture"]["max"]},
        "light_intensity": {"min": recommendations["light_intensity"]["min"], "max": recommendations["light_intensity"]["max"]},
    }
    return thresholds


def default_preferences(username: str) -> dict[str, Any]:
    """构造默认仪表板偏好（对齐旧库 get_user_preferences 初值）。"""
    return {
        "layout": "grid",
        "metrics": ["temperature", "humidity", "soil_moisture", "light_intensity"],
        "time_range": "24h",
        "custom_thresholds": {k: dict(v) for k, v in DEFAULT_THRESHOLDS.items()},
        "crop_stage": "growth",
        "show_predictions": True,
        "show_anomalies": True,
    }


def is_value_alert(
    metric: str,
    value: Optional[float],
    thresholds: Optional[dict[str, dict[str, float]]] = None,
) -> bool:
    """按阈值判断当前值是否越界告警。无数据或阈值缺失返回 False。"""
    if value is None:
        return False
    rules = thresholds or DEFAULT_THRESHOLDS
    rule = rules.get(metric)
    if not rule:
        return False
    if rule.get("min") is not None and value < rule["min"]:
        return True
    if rule.get("max") is not None and value > rule["max"]:
        return True
    return False
