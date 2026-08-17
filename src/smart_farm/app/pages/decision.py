"""自动化决策页面。调用 decision_service（规则驱动 + 真实时间趋势）。"""

from datetime import datetime, timedelta

import streamlit as st

from smart_farm.data import repositories as repo
from smart_farm.data.database import get_session
from smart_farm.services import decision_service as ds
from smart_farm.services.decision_service import SensorPoint

METRIC_MAP = {
    "soil_moisture": ("soil_moisture", "value"),
    "temperature": ("air_temperature_humidity", "temperature"),
    "humidity": ("air_temperature_humidity", "humidity"),
    "light_intensity": ("light_intensity", "value"),
}


def show() -> None:
    st.title("🤖 自动化决策")

    if st.button("评估当前环境条件", type="primary"):
        engine = ds.DecisionEngine()
        with get_session() as s:
            latest = {}
            trends = {}
            for metric, (orm_metric, col) in METRIC_MAP.items():
                row = repo.get_latest_sensor_reading(s, orm_metric)
                if row:
                    latest[metric] = getattr(row, col)
                rows = repo.get_sensor_readings(s, orm_metric, start=datetime.now() - timedelta(hours=24), limit=500)
                pts = [SensorPoint(r.timestamp, getattr(r, col)) for r in rows]
                trends[metric] = ds.calculate_trend_per_hour(pts)

        if not latest:
            st.success("所有环境参数都在理想范围内！")
            return

        recs = engine.evaluate(latest, trends)
        if not recs:
            st.success("所有环境参数都在理想范围内！")
            return

        for r in recs:
            st.markdown(
                f"""
                <div style="border-left:5px solid {'#F44336' if r.priority=='high' else '#FFC107' if r.priority=='medium' else '#4CAF50'};
                            padding:1rem;margin:0.6rem 0;background:rgba(0,0,0,0.03);border-radius:8px;">
                    <h4>{r.type} 建议</h4>
                    <p><strong>{r.message}</strong></p>
                    <p style="color:#555;">决策依据: {r.reason}</p>
                    <p style="color:#555;">当前值: {r.current_value} ｜ 阈值: {r.threshold}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
