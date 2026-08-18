"""综合监控仪表板页面。

对齐旧库 `integrated_dashboard`：
- 作物阶段推荐阈值 + 自定义阈值配置（偏好持久化 `dashboard_pref_{username}`）
- 快捷操作按钮（按角色过滤）
- 趋势图叠加：IQR 异常点 + 1 天预测线 + 阈值横线
"""

from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from smart_farm.app import cache
from smart_farm.app import greenhouse_context as gh_ctx
from smart_farm.data import repositories as repo
from smart_farm.data.database import get_session
from smart_farm.services import anomaly_service as an
from smart_farm.services import dashboard_service as ds
from smart_farm.services import prediction_service as ps

# 指标 -> (标签, 列名, 单位)
METRICS = {
    "temperature": ("温度", "temperature", "°C"),
    "humidity": ("湿度", "humidity", "%"),
    "soil_moisture": ("土壤湿度", "value", "%"),
    "soil_nutrient": ("土壤养分", "value", "ppm"),
    "light_intensity": ("光照强度", "value", "lux"),
}
# 指标 -> 数据库指标名
METRIC_DB = {
    "temperature": "air_temperature_humidity",
    "humidity": "air_temperature_humidity",
    "soil_moisture": "soil_moisture",
    "soil_nutrient": "soil_nutrient",
    "light_intensity": "light_intensity",
}
CROP_STAGES = ["growth", "flowering", "fruiting"]

# 快捷操作按钮（路由名须与 app_pages/ 文件名一致，修复旧版点击无动作）
ADMIN_ACTIONS = [
    ("data_analysis", "数据分析", ":material/analytics:"),
    ("prediction", "模型预测", ":material/timeline:"),
    ("system_monitoring", "系统监控", ":material/monitor_heart:"),
    ("user_management", "用户管理", ":material/group:"),
    ("backup_restore", "备份恢复", ":material/save:"),
    ("module_config", "模块配置", ":material/tune:"),
]
USER_ACTIONS = [
    ("data_analysis", "数据分析", ":material/analytics:"),
    ("prediction", "模型预测", ":material/timeline:"),
    ("data_cleaning", "数据清洗", ":material/cleaning_services:"),
    ("visualization", "可视化", ":material/bar_chart:"),
]


def _preferences() -> dict:
    """读取/初始化用户仪表板偏好（session_state 持久化，对齐旧库）。

    修复：用默认值补全缺失键（兼容旧版本偏好存档，避免 KeyError 页面崩溃）。
    """
    username = st.session_state.get("username", "guest")
    key = f"dashboard_pref_{username}"
    if key not in st.session_state:
        st.session_state[key] = ds.default_preferences(username)
    else:
        defaults = ds.default_preferences(username)
        stored = st.session_state[key]
        # 深度补全缺失的阈值键
        merged_th = dict(defaults["custom_thresholds"])
        merged_th.update(stored.get("custom_thresholds", {}))
        stored["custom_thresholds"] = merged_th
        for k, v in defaults.items():
            stored.setdefault(k, v)
        st.session_state[key] = stored
    return st.session_state[key]


def _get_prefs_ref() -> dict:
    username = st.session_state.get("username", "guest")
    return st.session_state[f"dashboard_pref_{username}"]


@st.cache_data(ttl=30, max_entries=16)
def _load_latest_cached(greenhouse_id: int | None) -> dict[str, float | None]:
    """各指标最新值（缓存 30 秒——修复每 rerun 5 次独立查库的性能问题）。

    按当前大棚过滤（多租户隔离）。
    """
    result: dict[str, float | None] = {}
    with get_session() as s:
        for metric, (_, col, _) in METRICS.items():
            db_metric = METRIC_DB[metric]
            row = repo.get_latest_sensor_reading(s, db_metric, greenhouse_id=greenhouse_id)
            result[metric] = round(getattr(row, col), 2) if row else None
    return result


def _load_latest() -> dict[str, float | None]:
    return _load_latest_cached(gh_ctx.current_greenhouse_id())


@st.cache_data(ttl=300, max_entries=8)
def _predict_trend_cached(
    metric: str, hours: int, values: tuple, timestamps: tuple
) -> dict:
    """趋势图短期预测线（缓存 5 分钟）：Prophet 短期预测，失败自动回退末值平推。

    升级：此前为 naive 兜底（对齐旧库 Prophet 叠加的降级实现），现对齐旧库体验。
    values/timestamps 以 tuple 传入以构成可靠缓存键。
    """
    ts = pd.to_datetime(list(timestamps))
    res = ps.short_term_forecast(list(values), ts, hours=min(hours, 24))
    return {
        "ds": [str(d) for d in res.forecast["ds"]],
        "yhat": res.forecast["yhat"].tolist(),
        "method": res.method,
    }


def _load_trend(metric: str, hours: int) -> pd.DataFrame:
    db_metric = METRIC_DB[metric]
    _, col, _ = METRICS[metric]
    since = datetime.now() - timedelta(hours=hours)
    df = cache.cached_sensor_df(
        db_metric, col, since.isoformat(), limit=5000,
        greenhouse_id=gh_ctx.current_greenhouse_id(),
    )
    return df.sort_values("timestamp")


def _render_threshold_config(prefs: dict) -> None:
    """阈值配置展开区（对齐旧库 render_threshold_config_ui）。"""
    with st.expander("自定义告警阈值配置", expanded=False):
        crop_stage = st.selectbox(
            "选择当前作物生长阶段",
            CROP_STAGES,
            format_func=lambda x: ds.get_crop_stage_recommendations(x)["name"],
            index=CROP_STAGES.index(prefs.get("crop_stage", "growth")),
        )
        rec = ds.get_crop_stage_recommendations(crop_stage)

        if st.button("应用推荐阈值", icon=":material/check:"):
            prefs["custom_thresholds"] = ds.recommendations_to_thresholds(rec)
            prefs["custom_thresholds"]["soil_nutrient"] = prefs["custom_thresholds"].get(
                "soil_nutrient", {"min": 10, "max": 20}
            )
            prefs["crop_stage"] = crop_stage
            st.success(f"已应用{rec['name']}推荐阈值！")
            st.rerun()

        st.markdown("**手动调整阈值**")
        th = prefs["custom_thresholds"]
        c1, c2 = st.columns(2)
        with c1:
            t_min = st.number_input("温度最小值 (°C)", value=float(th["temperature"]["min"]), min_value=-10.0, max_value=50.0, key="th_tmin")
            t_max = st.number_input("温度最大值 (°C)", value=float(th["temperature"]["max"]), min_value=-10.0, max_value=50.0, key="th_tmax")
            h_min = st.number_input("湿度最小值 (%)", value=float(th["humidity"]["min"]), min_value=0.0, max_value=100.0, key="th_hmin")
            h_max = st.number_input("湿度最大值 (%)", value=float(th["humidity"]["max"]), min_value=0.0, max_value=100.0, key="th_hmax")
        with c2:
            s_min = st.number_input("土壤湿度最小值 (%)", value=float(th["soil_moisture"]["min"]), min_value=0.0, max_value=100.0, key="th_smin")
            s_max = st.number_input("土壤湿度最大值 (%)", value=float(th["soil_moisture"]["max"]), min_value=0.0, max_value=100.0, key="th_smax")
            l_min = st.number_input("光照最小值 (lux)", value=float(th["light_intensity"]["min"]), min_value=0.0, max_value=100000.0, key="th_lmin")
            l_max = st.number_input("光照最大值 (lux)", value=float(th["light_intensity"]["max"]), min_value=0.0, max_value=100000.0, key="th_lmax")

        if st.button("保存自定义阈值", icon=":material/save:"):
            prefs["custom_thresholds"] = {
                "temperature": {"min": t_min, "max": t_max},
                "humidity": {"min": h_min, "max": h_max},
                "soil_moisture": {"min": s_min, "max": s_max},
                "light_intensity": {"min": l_min, "max": l_max},
                "soil_nutrient": th.get("soil_nutrient", {"min": 10, "max": 20}),
            }
            prefs["crop_stage"] = crop_stage
            st.success("阈值已保存！")
            st.rerun()

        # 当前阶段推荐参考
        st.markdown("**当前阶段推荐值参考**")
        rc = st.columns(4)
        rc[0].info(f"温度：{rec['temperature']['min']}-{rec['temperature']['max']}°C\n\n最优：{rec['temperature']['optimal']}°C")
        rc[1].info(f"湿度：{rec['humidity']['min']}-{rec['humidity']['max']}%\n\n最优：{rec['humidity']['optimal']}%")
        rc[2].info(f"土壤湿度：{rec['soil_moisture']['min']}-{rec['soil_moisture']['max']}%\n\n最优：{rec['soil_moisture']['optimal']}%")
        rc[3].info(f"光照：{rec['light_intensity']['min']}-{rec['light_intensity']['max']} lux\n\n最优：{rec['light_intensity']['optimal']} lux")


def _render_quick_actions() -> None:
    """快捷操作按钮（按角色过滤，点击跳转对应页面——修复旧版点击无动作）。"""
    role = st.session_state.get("role", "user")
    actions = ADMIN_ACTIONS if role == "admin" else USER_ACTIONS
    with st.container(horizontal=True):
        for page, label, icon in actions:
            if st.button(label, icon=icon, key=f"quick_{page}"):
                try:
                    st.switch_page(f"app_pages/{page}.py")
                except Exception:  # noqa: BLE001 页面不可用时给出提示而非静默
                    st.info(f"页面「{label}」当前不可用（可能被模块配置禁用）。")


st.title("综合监控仪表板")

prefs = _preferences()
latest = _load_latest()

# 指标卡（含告警色，对齐旧库 render_data_metrics）
th = prefs["custom_thresholds"]
cols = st.columns(len(METRICS))
for i, (metric, (label, _, unit)) in enumerate(METRICS.items()):
    value = latest.get(metric)
    alert = ds.is_value_alert(metric, value, th)
    with cols[i]:
        if value is None:
            st.metric(label, "—")
        else:
            st.metric(
                label,
                f"{value} {unit}",
                delta="告警" if alert else None,
                delta_color="inverse" if alert else "normal",
                help="超出阈值范围" if alert else None,
            )

# 阈值配置
_render_threshold_config(prefs)
# 快捷操作
_render_quick_actions()

# 趋势图（可叠加异常点 + 预测线 + 阈值横线）
st.subheader("近期趋势")
metric = st.selectbox("选择指标", list(METRICS.keys()), format_func=lambda m: f"{METRICS[m][0]} ({METRICS[m][2]})")
TIME_RANGES = {"1小时": 1, "6小时": 6, "24小时": 24, "7天": 168}
hours = TIME_RANGES[st.selectbox("时间范围", list(TIME_RANGES.keys()), index=2)]
show_anomalies = st.toggle("显示异常点", value=prefs.get("show_anomalies", True))
show_prediction = st.toggle("显示 1 天预测", value=prefs.get("show_predictions", True))

df = _load_trend(metric, hours)
if df.empty:
    st.info("暂无数据。可运行 `python -m smart_farm.data.seed` 生成演示数据。")
    st.stop()

label, col, unit = METRICS[metric]
rule = th.get(metric)
fig = go.Figure()
fig.add_trace(go.Scatter(x=df["timestamp"], y=df["value"], mode="lines", name="实际值",
                         line={"color": "#185FA5"}))

# 异常点（IQR，对齐旧库）
if show_anomalies:
    mask = an.detect_outliers_iqr(df, "value")
    idx = df.index[mask]
    if len(idx):
        fig.add_trace(go.Scatter(x=df.loc[idx, "timestamp"], y=df.loc[idx, "value"],
                                 mode="markers", name="异常点",
                                 marker={"color": "#E24B4A", "symbol": "x", "size": 10}))

# 1 天预测（Prophet 短期预测，失败回退末值平推；对齐旧库 Prophet 叠加）
pred_method = ""
if show_prediction:
    try:
        pred = _predict_trend_cached(
            metric, hours,
            tuple(df["value"].tolist()), tuple(df["timestamp"].tolist()),
        )
        pred_method = "Prophet" if pred["method"].startswith("prophet") else "朴素"
        fig.add_trace(go.Scatter(x=pred["ds"], y=pred["yhat"], mode="lines",
                                 name=f"短期预测({pred_method})",
                                 line={"color": "#D85A30", "dash": "dash"}))
    except Exception:  # noqa: BLE001 预测失败不影响主图
        pass

# 阈值横线
if rule:
    if rule.get("min") is not None:
        fig.add_hline(y=rule["min"], line_dash="dot", line_color="#888780",
                      annotation_text=f"min {rule['min']}", annotation_position="top left")
    if rule.get("max") is not None:
        fig.add_hline(y=rule["max"], line_dash="dot", line_color="#888780",
                      annotation_text=f"max {rule['max']}", annotation_position="bottom left")

fig.update_layout(
    height=420,
    xaxis_title="时间",
    yaxis_title=f"{label} ({unit})",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
)
st.plotly_chart(fig, width="stretch")
st.caption("异常点基于 IQR 法；预测线为 Prophet 短期预测（3H 步长，不可用时自动回退朴素法）。可在「自定义告警阈值配置」调整阈值与作物阶段。")
