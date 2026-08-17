"""Streamlit 缓存接入（热点查询 / 预测）。

遵循 `developing-with-streamlit` 技能要点：
- `st.cache_data`：带 `ttl` + `max_entries`，防止缓存无限增长；用于传感器时序查询与预测结果。
- 不包含任何 `st.*` 业务逻辑的纯函数；本模块仅做缓存封装。
- 缓存返回值为可 pickle 的结构（DataFrame / dataclass），避免直接缓存 ORM 对象。
"""

from datetime import datetime

import pandas as pd
import streamlit as st

from smart_farm.data import repositories as repo
from smart_farm.data.database import get_session


@st.cache_data(ttl=300, max_entries=128)
def cached_sensor_df(metric: str, value_col: str, start_iso: str, limit: int = 2000) -> pd.DataFrame:
    """缓存传感器时序查询，按 (指标, 列, 起始时间, limit) 作为缓存键。

    返回可直接用于绘图的 DataFrame（timestamp, value），不含 ORM 对象。
    """
    start = datetime.fromisoformat(start_iso)
    with get_session() as s:
        rows = repo.get_sensor_readings(s, metric, start=start, limit=limit)
    return pd.DataFrame(
        [{"timestamp": r.timestamp, "value": getattr(r, value_col)} for r in rows]
    )


@st.cache_data(ttl=600, max_entries=32)
def cached_forecast(
    metric: str,
    value_col: str,
    method: str,
    days: int,
    start_iso: str,
) -> "object":
    """缓存预测结果（ForecastResult）。键含方法/天数/起始时间。"""
    from smart_farm.services import prediction_service as ps

    start = datetime.fromisoformat(start_iso)
    with get_session() as s:
        rows = repo.get_sensor_readings(s, metric, start=start, limit=5000)
    values = [getattr(r, value_col) for r in rows]
    timestamps = [r.timestamp for r in rows]
    return ps.forecast(values, timestamps, method=method, prediction_days=days)
