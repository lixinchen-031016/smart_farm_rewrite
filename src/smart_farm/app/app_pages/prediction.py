"""本地数据预测页面（对齐旧库 data_prediction）。

- 模式：单变量（Prophet+SARIMA 推荐 / 纯 Prophet）/ 多变量（随机森林）
- 3H 采样（每天 8 点）、参数滑块、置信区间、综合评分
- **长任务异步化**：预测在后台线程执行，`@st.fragment(run_every=1)` 轮询进度条，
  页面不再阻塞；完成后自动切换到结果渲染
- 预测自动保存（CSV+MD+SQLite）＋「预测历史」tab
"""

from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from smart_farm.data import repositories as repo
from smart_farm.data.database import get_session
from smart_farm.services import prediction_archive as pa
from smart_farm.services import prediction_runner as runner
from smart_farm.services import prediction_service as ps

METRIC_MAP = {
    "空气温度": ("air_temperature_humidity", "temperature"),
    "空气湿度": ("air_temperature_humidity", "humidity"),
    "土壤湿度": ("soil_moisture", "value"),
    "土壤养分": ("soil_nutrient", "value"),
    "光照强度": ("light_intensity", "value"),
}

MODEL_OPTIONS = ["Prophet+SARIMA(推荐)", "纯 Prophet", "残差分解混合(进阶)"]

TASK_KEY = "pred_task"  # session_state 中后台任务句柄的 key


def _fetch_series(metric: str, col: str, hours: int = 24 * 60) -> tuple[list, list]:
    """拉取历史序列（对齐旧版 get_historical_data：取最近数据按时间升序）。"""
    since = datetime.now() - timedelta(hours=hours)
    with get_session() as s:
        rows = repo.get_sensor_readings(s, metric, start=since, limit=5000)
    values = [getattr(r, col) for r in rows]
    timestamps = [r.timestamp for r in rows]
    return values, timestamps


def _fetch_multivariate() -> tuple[list, list, list, list]:
    """拉取温度/湿度/光照最近 200 条（对齐旧版多变量数据源）。"""
    with get_session() as s:
        temp = repo.get_sensor_readings(s, "air_temperature_humidity", limit=200)
        temp = sorted(temp, key=lambda r: r.timestamp)
        ts = [r.timestamp for r in temp]
        temp_vals = [r.temperature for r in temp]
        humid_vals = [r.humidity for r in temp]
        light_rows = repo.get_sensor_readings(s, "light_intensity", limit=200)
        light_map = {r.timestamp: r.value for r in light_rows}
        light_vals = [light_map.get(t) for t in ts]
    return temp_vals, humid_vals, light_vals, ts


def _render_forecast_chart(result: ps.ForecastResult, title: str) -> None:
    hist = result.history.rename(columns={"y": "实际值"})
    fc = result.forecast.rename(columns={"yhat": "预测值"})
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=list(fc["ds"]) + list(fc["ds"][::-1]),
        y=list(fc["yhat_upper"]) + list(fc["yhat_lower"][::-1]),
        fill="toself", fillcolor="rgba(255,193,7,0.20)",
        line=dict(color="rgba(255,193,7,0)"), name="置信区间", hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(x=hist["ds"], y=hist["实际值"], mode="lines", name="实际值"))
    fig.add_trace(go.Scatter(x=fc["ds"], y=fc["预测值"], mode="lines+markers", name="预测值"))
    fig.update_layout(height=420, xaxis_title="日期", yaxis_title="数值", title=title)
    st.plotly_chart(fig, width="stretch")


def _render_score(result: ps.ForecastResult, values: list) -> None:
    score = ps.score_prediction(result.rmse, values)
    st.caption(f"方法：{result.method} ｜ {result.explanation}")
    c1, c2, c3 = st.columns(3)
    c1.metric("综合评分", f"{score['score']} / 6")
    c2.metric("可信度", score["level"])
    c3.metric("RMSE", f"{result.rmse:.4f}" if result.rmse else "—")


# =========================================================================
# 后台计算（在后台线程执行，禁止调用任何 st.*）
# =========================================================================


def _compute_univariate(
    metric_label: str,
    model_type: str,
    days: int,
    cp: float,
    season: float,
    use_grid_search: bool = True,
    progress_callback=None,
) -> dict:
    """单变量预测计算（后台线程执行）。返回结果字典供主线程渲染。"""
    metric, col = METRIC_MAP[metric_label]
    values, timestamps = _fetch_series(metric, col, hours=60 * 24)
    if len(values) < 10:
        raise ValueError("历史数据不足（至少 10 条），请先运行 seed 生成演示数据。")

    if model_type == "纯 Prophet":
        result = ps.prophet_forecast(
            values, timestamps, days,
            changepoint_prior_scale=cp, seasonality_prior_scale=season,
            progress_callback=progress_callback,
        )
    elif model_type == "残差分解混合(进阶)":
        # 两阶段：SARIMA 捕获线性 → Prophet 学习残差非线性 → 叠加（对齐旧库 HybridPredictor）
        result = ps.residual_hybrid_forecast(
            values, timestamps, days,
            changepoint_prior_scale=cp, seasonality_prior_scale=season,
            progress_callback=progress_callback,
        )
    else:
        result = ps.hybrid_forecast(
            values, timestamps, days,
            use_grid_search=use_grid_search,
            changepoint_prior_scale=cp, seasonality_prior_scale=season,
            progress_callback=progress_callback,
        )
    return {
        "kind": "univariate", "result": result, "values": values,
        "days": days, "metric_label": metric_label, "model_type": model_type,
    }


def _compute_multivariate(
    days: int,
    n_estimators: int,
    use_lag: bool,
    use_inter: bool,
    progress_callback=None,
) -> dict:
    """多变量随机森林预测（后台线程执行）。返回结果字典供主线程渲染。"""
    temp, humid, light, ts = _fetch_multivariate()
    if not temp or len([t for t in temp if t is not None]) < 20:
        raise ValueError("多变量数据不足（至少 20 条），请先运行 seed 生成演示数据。")
    merged, fc, fi, explanation = ps.multivariate_forecast(
        temp, humid, light, ts, prediction_days=days,
        n_estimators=n_estimators, use_lag_features=use_lag, use_interaction=use_inter,
        progress_callback=progress_callback,
    )
    return {"kind": "multivariate", "merged": merged, "fc": fc, "fi": fi,
            "explanation": explanation, "days": days}


# =========================================================================
# 结果渲染（主线程；仅在任务完成后调用一次保存，避免 rerun 重复写库）
# =========================================================================


def _render_done(task: runner.PredictionTask) -> None:
    """任务完成后渲染图表/评分，并自动保存归档（幂等：每个任务只保存一次）。"""
    payload = task.result
    if payload["kind"] == "univariate":
        result, values = payload["result"], payload["values"]
        _render_forecast_chart(result, f"{payload['metric_label']} 预测（{payload['days']} 天）")
        _render_score(result, values)
        st.caption("阴影区为预测置信区间（±1.96σ）。")
    else:
        merged, fc, fi = payload["merged"], payload["fc"], payload["fi"]
        st.caption(payload["explanation"])
        st.subheader("特征重要性")
        st.dataframe(fi[["feature", "temp_importance_pct", "humid_importance_pct"]], width="stretch")

        fig = go.Figure()
        for col, color in (("temperature", "#185FA5"), ("humidity", "#D85A30")):
            fig.add_trace(go.Scatter(x=merged["ds"], y=merged[col], mode="lines", name=f"历史{col}",
                                     line={"color": color}))
            fig.add_trace(go.Scatter(x=fc["ds"], y=fc[col], mode="lines", name=f"预测{col}",
                                     line={"color": color, "dash": "dash"}))
        fig.update_layout(height=420, xaxis_title="日期", yaxis_title="数值")
        st.plotly_chart(fig, width="stretch")

        st.dataframe(fc, width="stretch")
        st.download_button(
            "下载多变量预测 CSV",
            data=fc.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"multivariate_forecast_{datetime.now():%Y%m%d_%H%M%S}.csv",
            mime="text/csv",
            icon=":material/download:",
        )

    # 自动保存（每个任务仅保存一次；主脚本 rerun 不会重复写库）
    if st.session_state.get(f"{TASK_KEY}_saved"):
        return
    st.session_state[f"{TASK_KEY}_saved"] = True
    try:
        if payload["kind"] == "univariate":
            result = payload["result"]
            res = pa.archive.save_prediction_result(
                result.history, result.forecast, prediction_type=payload["metric_label"],
                model_type=payload["model_type"], prediction_days=payload["days"],
                model_explanation=result.explanation, rmse=result.rmse or 0.0,
                username=st.session_state.get("username", "system"),
            )
        else:
            res = pa.archive.save_prediction_result(
                payload["merged"], payload["fc"], prediction_type="多变量耦合预测",
                model_type="RandomForest-Multivariate", prediction_days=payload["days"],
                model_explanation=payload["explanation"], rmse=0.0,
                username=st.session_state.get("username", "system"),
            )
        st.success(f"预测结果已自动保存（ID：{res['prediction_id']}）。")
    except Exception as e:  # noqa: BLE001 归档失败不阻断结果展示
        st.warning(f"预测结果保存失败：{e}")


# =========================================================================
# 异步任务控制
# =========================================================================


@st.fragment(run_every=1.0)
def _poll_progress() -> None:
    """进度轮询：任务运行中每秒刷新进度条；结束后触发主脚本 rerun 切换到结果渲染。"""
    task = st.session_state.get(TASK_KEY)
    if task is None:
        return
    if task.is_running():
        st.progress(task.progress, text=f"{task.stage}（已运行 {task.elapsed:.0f} 秒）")
    else:
        st.rerun(scope="app")  # 结束轮询，由主脚本渲染最终态


def _launch(target, params: dict) -> None:
    """启动后台预测任务并立即 rerun 展示进度。"""
    task = runner.start_prediction_task(target, **params)
    st.session_state[TASK_KEY] = task
    st.session_state[f"{TASK_KEY}_saved"] = False
    st.rerun()


def _show_history() -> None:
    st.subheader("预测历史")
    history = pa.archive.get_prediction_history(limit=50)
    if not history:
        st.info("暂无预测记录。执行预测后自动保存。")
        return
    stats = pa.archive.get_statistics()
    c1, c2, c3 = st.columns(3)
    c1.metric("总次数", stats["total_predictions"])
    c2.metric("近 7 天", stats["recent_predictions_7d"])
    c3.metric("平均 RMSE", stats["avg_rmse"])
    df = pd.DataFrame([{
        "ID": h["prediction_id"],
        "类型": h["prediction_type"],
        "模型": h["model_type"],
        "天数": h["prediction_days"],
        "RMSE": round(h["rmse"], 4),
        "时间": h["created_at"][:19],
    } for h in history])
    st.dataframe(df, width="stretch")
    if st.button("删除全部历史", icon=":material/delete:"):
        for h in history:
            pa.archive.delete_prediction(h["prediction_id"])
        st.rerun()


# =========================================================================
# 页面
# =========================================================================

st.title("本地数据预测")
tab1, tab2 = st.tabs(["执行预测", "预测历史"])

with tab1:
    mode = st.segmented_control(
        "预测模式", ["单变量时间序列预测", "多变量耦合预测"], default="单变量时间序列预测",
        key="pred_mode",
    )

    if mode == "单变量时间序列预测":
        metric_label = st.selectbox("选择预测的数据类型", list(METRIC_MAP.keys()))
        model_type = st.selectbox("选择预测模型", MODEL_OPTIONS)
        days = st.number_input("预测天数", min_value=1, max_value=30, value=7, step=1)
        cp = st.slider("变化点灵敏度 (Prophet)", 0.001, 0.5, 0.05, 0.01)
        season = st.slider("季节性强度 (Prophet)", 0.1, 20.0, 10.0, 0.1)
        # 网格搜索仅作用于权重融合模式（残差分解模式固定参数以保证两阶段可比性）
        use_grid_search = st.toggle(
            "SARIMA 参数网格搜索（按 AIC 选优，更准但更慢）",
            value=True, disabled=model_type != "Prophet+SARIMA(推荐)",
            help="对 5 组候选 (p,d,q)(P,D,Q,24) 按 AIC 选优；关闭时使用固定 (1,1,1)(1,1,1,24)。",
        )
        if st.button("执行预测", type="primary", icon=":material/timeline:"):
            _launch(_compute_univariate, {
                "metric_label": metric_label, "model_type": model_type,
                "days": int(days), "cp": cp, "season": season,
                "use_grid_search": use_grid_search,
            })
    else:
        days = st.number_input("预测天数", min_value=1, max_value=15, value=7, step=1)
        n_estimators = st.slider("随机森林树数量", 50, 200, 100, 10)
        use_lag = st.checkbox("启用滞后特征", value=True)
        use_inter = st.checkbox("启用交互项", value=True)
        if st.button("执行多变量预测", type="primary", icon=":material/timeline:"):
            _launch(_compute_multivariate, {
                "days": int(days), "n_estimators": n_estimators,
                "use_lag": use_lag, "use_inter": use_inter,
            })

    # 预测结果区：运行中 → 进度条轮询；完成 → 结果渲染
    st.divider()
    st.subheader("预测结果")
    task = st.session_state.get(TASK_KEY)
    if task is None:
        st.caption("配置参数后点击「执行预测」；长任务在后台运行，页面不会卡顿。")
    elif task.is_running():
        _poll_progress()
    elif task.status == "error":
        st.error(f"预测失败：{task.error}")
    else:
        _render_done(task)

with tab2:
    _show_history()
