"""后台预测任务封装（纯逻辑，无 Streamlit 依赖）。

长任务（Prophet / 混合模型 / 多变量随机森林）在 UI 线程同步执行会阻塞整个页面。
本模块提供：

- ``PredictionTask``：任务状态对象（status / progress / stage / error / elapsed）
- ``start_prediction_task``：在后台线程执行目标函数，返回任务句柄（非阻塞）
- ``wait_prediction``：阻塞等待任务完成（测试与脚本场景使用）

线程安全说明：任务状态通过简单属性赋值更新（GIL 下原子），
``progress`` / ``stage`` 供 UI 轮询读取，无需额外锁。
"""

import inspect
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Optional

TaskStatus = Literal["pending", "running", "done", "error"]
ProgressCallback = Callable[[float, str], None]


@dataclass
class PredictionTask:
    """一次后台预测任务的状态句柄（由 UI 轮询读取）。"""

    task_id: str
    status: TaskStatus = "pending"
    progress: float = 0.0
    stage: str = "排队中"
    error: str = ""
    result: Any = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    _thread: threading.Thread = field(repr=False, default=None)

    @property
    def elapsed(self) -> float:
        """任务已运行时长（秒）。"""
        end = self.finished_at or datetime.now(timezone.utc)
        start = self.started_at or end
        return max(0.0, (end - start).total_seconds())

    def is_running(self) -> bool:
        return self.status in ("pending", "running")


def start_prediction_task(
    target: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> PredictionTask:
    """在后台线程执行 ``target(*args, **kwargs)``，立即返回任务句柄。

    若 ``target`` 接受 ``progress_callback`` 参数，则自动注入进度回调，
    将 (progress 0~1, stage) 写入任务对象；目标函数抛出的任何异常都会被
    捕获并写入 ``task.error``（status=error），不中断页面。

    Returns:
        新建的 PredictionTask（pending 状态）。
    """
    task = PredictionTask(task_id=uuid.uuid4().hex[:12])
    kwargs = {k: v for k, v in kwargs.items() if k != "progress_callback"}  # 由 runner 统一注入

    # 仅当目标函数声明了 progress_callback 参数时才注入（兼容无进度能力的函数）
    try:
        accepts_progress = "progress_callback" in inspect.signature(target).parameters
    except (TypeError, ValueError):  # 内置函数等无法内省签名
        accepts_progress = False

    def _progress(pct: float, stage: str) -> None:
        task.progress = max(0.0, min(1.0, float(pct)))
        task.stage = stage

    def _run() -> None:
        task.status = "running"
        task.started_at = datetime.now(timezone.utc)
        try:
            call_kwargs = dict(kwargs)
            if accepts_progress:
                call_kwargs["progress_callback"] = _progress
            task.result = target(*args, **call_kwargs)
            task.progress = 1.0
            task.status = "done"
        except Exception as exc:  # noqa: BLE001 后台任务异常统一转入 error 状态
            task.error = f"{type(exc).__name__}: {exc}"
            task.status = "error"
        finally:
            task.finished_at = datetime.now(timezone.utc)

    thread = threading.Thread(
        target=_run,
        daemon=True,
        name=f"pred-{task.task_id}",
    )
    task._thread = thread
    thread.start()
    return task


def wait_prediction(task: PredictionTask, timeout: Optional[float] = None) -> PredictionTask:
    """阻塞等待任务完成（测试 / 脚本场景）。

    Args:
        task: start_prediction_task 返回的任务句柄。
        timeout: 最大等待秒数；None 表示无限等待。超时后返回当前状态。

    Returns:
        任务句柄（status 可能仍为 running）。
    """
    deadline = None if timeout is None else time.monotonic() + timeout
    while task.is_running():
        if deadline is not None and time.monotonic() > deadline:
            break
        time.sleep(0.05)
    return task
