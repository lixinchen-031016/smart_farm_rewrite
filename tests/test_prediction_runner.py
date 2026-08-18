"""prediction_runner 后台任务封装测试（纯逻辑，不依赖 Streamlit）。"""

import time

import pandas as pd

from smart_farm.services import prediction_runner as pr
from smart_farm.services import prediction_service as ps


def _slow_success(pct_step: float = 0.2, progress_callback=None) -> str:
    """慢速成功任务：逐步上报进度后返回结果。"""
    for i in range(1, 6):
        if progress_callback:
            progress_callback(pct_step * i, f"阶段{i}")
        time.sleep(0.02)
    return "ok"


def _boom(progress_callback=None) -> None:
    """必然失败的任务。"""
    raise ValueError("模拟预测失败")


def _series3h(n: int = 80, start: str = "2026-01-01") -> tuple[list, list]:
    """3H 对齐数据（与 test_prediction 一致，80 点足以让 hybrid 双模型成功）。"""
    ts = [pd.Timestamp(start) + pd.Timedelta(hours=3 * i) for i in range(n)]
    vals = [10.0 + i * 0.05 for i in range(n)]
    return vals, ts


def test_task_lifecycle_success():
    task = pr.start_prediction_task(_slow_success)
    assert task.status in ("pending", "running")
    pr.wait_prediction(task, timeout=10)
    assert task.status == "done"
    assert task.result == "ok"
    assert task.progress == 1.0
    assert task.error == ""
    assert task.elapsed >= 0.0
    assert task.started_at is not None and task.finished_at is not None


def test_task_reports_progress_stages():
    task = pr.start_prediction_task(_slow_success)
    pr.wait_prediction(task, timeout=10)
    assert task.status == "done"
    # 进度推进到 1.0，且阶段名被写入
    assert task.progress == 1.0
    assert task.stage == "阶段5"


def test_task_captures_exception():
    task = pr.start_prediction_task(_boom)
    pr.wait_prediction(task, timeout=10)
    assert task.status == "error"
    assert "ValueError" in task.error
    assert "模拟预测失败" in task.error
    assert task.result is None


def test_wait_timeout_returns_running():
    task = pr.start_prediction_task(lambda progress_callback=None: time.sleep(5))
    pr.wait_prediction(task, timeout=0.1)
    assert task.status == "running"  # 超时后不阻塞、不误报完成


def test_task_without_progress_param():
    """目标函数无 progress_callback 参数时也能正常运行（不注入）。"""
    task = pr.start_prediction_task(lambda: "plain")
    pr.wait_prediction(task, timeout=5)
    assert task.status == "done"
    assert task.result == "plain"


def test_kwargs_progress_callback_conflict():
    """调用方 kwargs 中误传 progress_callback 时由 runner 覆盖，不报重复参数。"""

    def fn(x: int, progress_callback=None):
        if progress_callback:
            progress_callback(1.0, "end")
        return x * 2

    task = pr.start_prediction_task(fn, x=3, progress_callback=lambda p, s: None)
    pr.wait_prediction(task, timeout=5)
    assert task.status == "done"
    assert task.result == 6


def test_multiple_tasks_independent():
    t1 = pr.start_prediction_task(_slow_success)
    t2 = pr.start_prediction_task(_slow_success)
    pr.wait_prediction(t1, timeout=10)
    pr.wait_prediction(t2, timeout=10)
    assert t1.status == t2.status == "done"
    assert t1.task_id != t2.task_id


def test_hybrid_progress_callback_integration():
    """真实 hybrid_forecast 进度回调：阶段序列完整、末进度 1.0、结果正常。"""
    vals, ts = _series3h(n=80)
    events: list[tuple[float, str]] = []

    result = ps.hybrid_forecast(vals, ts, prediction_days=2, progress_callback=lambda p, s: events.append((p, s)))

    assert result.forecast is not None
    assert events, "进度回调应至少触发一次"
    # 阶段序列完整（含"数据预处理"与"权重融合"）
    stages = [s for _, s in events]
    assert any("预处理" in s for s in stages)
    assert any("融合" in s for s in stages)
    # 最后一个进度必须为 1.0
    assert events[-1][0] == 1.0


def test_prediction_runner_integration_with_hybrid():
    """runner 与预测服务组合：真实 hybrid 在后台线程完成，返回 8 点预测。"""
    vals, ts = _series3h(n=80)
    task = pr.start_prediction_task(ps.hybrid_forecast, vals, ts, prediction_days=1)
    pr.wait_prediction(task, timeout=120)
    assert task.status == "done"
    assert len(task.result.forecast) == ps.POINTS_PER_DAY
