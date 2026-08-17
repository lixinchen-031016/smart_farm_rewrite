"""日志分析服务（纯函数，无 Streamlit 依赖）。

对齐旧版 `utils/log_analyzer.py`：
- `analyze_error_stats`：错误统计（按操作类型，仅 ERROR）
- `analyze_user_activity`：用户活动（总操作数 / 错误数 / 错误率）
- `analyze_log_trends`：日志趋势（按天 × 级别）
- `analyze_top_actions`：操作类型 Top N
输入为日志 ORM 对象列表（或带同名属性的对象），输出 dict/DataFrame，便于单测。
"""

from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Sequence


def _iter_attrs(logs: Sequence[Any]):
    for log in logs:
        yield {
            "log_time": getattr(log, "log_time", None),
            "log_level": getattr(log, "log_level", ""),
            "username": getattr(log, "username", ""),
            "action_type": getattr(log, "action_type", ""),
            "action_details": getattr(log, "action_details", ""),
        }


def analyze_error_stats(logs: Sequence[Any], hours: int = 24) -> dict[str, int]:
    """错误统计：{action_type: count}，仅统计 ERROR 级别、最近 N 小时。"""
    since = datetime.now() - timedelta(hours=hours)
    counter: Counter = Counter()
    for log in _iter_attrs(logs):
        if log["log_level"] != "ERROR":
            continue
        if log["log_time"] and log["log_time"] < since:
            continue
        counter[log["action_type"] or "未知"] += 1
    return dict(counter)


def analyze_user_activity(logs: Sequence[Any], hours: int = 24) -> dict[str, dict[str, Any]]:
    """用户活动：{username: {total_actions, error_actions, error_rate_pct}}。"""
    since = datetime.now() - timedelta(hours=hours)
    total: Counter = Counter()
    errors: Counter = Counter()
    for log in _iter_attrs(logs):
        if log["log_time"] and log["log_time"] < since:
            continue
        user = log["username"] or "匿名"
        total[user] += 1
        if log["log_level"] == "ERROR":
            errors[user] += 1
    result: dict[str, dict[str, Any]] = {}
    for user, count in total.items():
        err = errors.get(user, 0)
        result[user] = {
            "total_actions": count,
            "error_actions": err,
            "error_rate_pct": round(err / count * 100, 2) if count else 0.0,
        }
    return result


def analyze_log_trends(logs: Sequence[Any], days: int = 7) -> dict[str, dict[str, int]]:
    """日志趋势：{日期字符串: {level: count}}。"""
    since = datetime.now() - timedelta(days=days)
    result: dict[str, dict[str, int]] = {}
    for log in _iter_attrs(logs):
        t = log["log_time"]
        if not t or t < since:
            continue
        day = t.strftime("%Y-%m-%d")
        level = log["log_level"] or "UNKNOWN"
        result.setdefault(day, {}).setdefault(level, 0)
        result[day][level] += 1
    return result


def analyze_top_actions(logs: Sequence[Any], limit: int = 10, hours: int = 24) -> list[tuple[str, int]]:
    """操作类型 Top N（降序）。"""
    since = datetime.now() - timedelta(hours=hours)
    counter: Counter = Counter()
    for log in _iter_attrs(logs):
        if log["log_time"] and log["log_time"] < since:
            continue
        counter[log["action_type"] or "未知"] += 1
    return counter.most_common(limit)


def analyze_error_peaks(logs: Sequence[Any], hours: int = 24) -> dict[str, int]:
    """异常时段：按小时聚合 ERROR 数量（找峰值时段）。"""
    since = datetime.now() - timedelta(hours=hours)
    counter: Counter = Counter()
    for log in _iter_attrs(logs):
        if log["log_level"] != "ERROR":
            continue
        t = log["log_time"]
        if not t or t < since:
            continue
        counter[t.strftime("%H:00")] += 1
    return dict(sorted(counter.items()))
