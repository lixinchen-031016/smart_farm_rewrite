"""自动化决策服务（规则驱动，无 Streamlit 依赖）。

重写要点（对照旧版）：
- 阈值抽成可配置 `rules`（未来可入库/配置表），不再硬编码散落各处。
- 趋势计算改用**真实时间差**（每小时变化率），修复旧版用索引位导致跨不均间隔失效的问题。
- 删除 `get_latest_sensor_data` 与 `_optimized` 的复制函数；取数交给仓库层。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np


@dataclass
class SensorPoint:
    timestamp: datetime
    value: float


@dataclass
class Recommendation:
    type: str
    message: str
    reason: str
    priority: str  # high | medium | low
    current_value: float
    threshold: float
    timestamp: datetime = field(default_factory=datetime.now)


# 默认规则（可整体替换/入库）
DEFAULT_RULES: dict[str, dict] = {
    "soil_moisture": {
        "low_threshold": 30,
        "high_threshold": 60,
        "low": "土壤湿度低于30%，建议立即灌溉",
        "high": "土壤湿度高于60%，无需灌溉",
    },
    "temperature": {
        "low_threshold": 15,
        "high_threshold": 30,
        "low": "温度低于15℃，建议检查大棚保温",
        "high": "温度高于30℃，建议通风降温",
    },
    "humidity": {
        "low_threshold": 40,
        "high_threshold": 70,
        "low": "空气湿度低于40%，建议增湿",
        "high": "空气湿度高于70%，建议通风除湿",
    },
    "light_intensity": {
        "low_threshold": 1000,
        "low": "光照强度低于1000lux，建议补光",
        "normal": "光照强度正常",
    },
}


def calculate_trend_per_hour(points: list[SensorPoint]) -> float:
    """用真实时间差计算每小时变化率（斜率）。不足 2 点返回 0。"""
    if len(points) < 2:
        return 0.0
    t0 = points[0].timestamp
    hours = np.array([(p.timestamp - t0).total_seconds() / 3600.0 for p in points], dtype=float)
    vals = np.array([p.value for p in points], dtype=float)
    if np.allclose(hours, hours[0]):
        return 0.0
    return float(np.polyfit(hours, vals, 1)[0])


class DecisionEngine:
    """基于阈值与趋势生成农业环境调控建议。"""

    def __init__(self, rules: Optional[dict] = None):
        self.rules = rules or DEFAULT_RULES

    def evaluate(
        self, latest: dict[str, float], trends: dict[str, float]
    ) -> list[Recommendation]:
        recs: list[Recommendation] = []
        for metric, rule in self.rules.items():
            if metric not in latest:
                continue
            value = latest[metric]
            low = rule.get("low_threshold")
            high = rule.get("high_threshold")
            trend = trends.get(metric, 0.0)
            trend_str = f"过去24小时呈{'上升' if trend > 0 else '下降'}趋势(斜率:{trend:.4f}/h)"

            if low is not None and value < low:
                reason = f"当前{metric}为{value:.1f}，低于阈值{low}；{trend_str}" if trend != 0 else f"当前{metric}为{value:.1f}，低于阈值{low}"
                recs.append(
                    Recommendation(
                        type=metric,
                        message=rule["low"],
                        reason=reason,
                        priority="high" if metric == "soil_moisture" else "medium",
                        current_value=value,
                        threshold=low,
                    )
                )
            elif high is not None and value > high:
                reason = f"当前{metric}为{value:.1f}，高于阈值{high}；{trend_str}" if trend != 0 else f"当前{metric}为{value:.1f}，高于阈值{high}"
                recs.append(
                    Recommendation(
                        type=metric,
                        message=rule["high"],
                        reason=reason,
                        priority="low",
                        current_value=value,
                        threshold=high,
                    )
                )
            else:
                if metric == "light_intensity":
                    recs.append(
                        Recommendation(
                            type=metric,
                            message=rule.get("normal", "光照强度正常"),
                            reason=f"当前光照强度为{value:.1f}lux，满足作物生长需求",
                            priority="low",
                            current_value=value,
                            threshold=low,
                        )
                    )
        return recs
