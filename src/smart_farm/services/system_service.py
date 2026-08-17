"""系统监控服务（纯逻辑，无 Streamlit 依赖）。

psutil 为可选依赖：未安装时 `is_psutil_available()` 返回 False，UI 降级提示。
对齐旧版 `utils/system_monitoring.py` 的指标与阈值。
"""

from typing import Any, Optional

PSUTIL_AVAILABLE = False
try:  # pragma: no cover
    import psutil  # type: ignore

    PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None  # type: ignore


def is_psutil_available() -> bool:
    return PSUTIL_AVAILABLE


def collect_system_metrics() -> dict[str, Any]:
    """收集系统性能指标（对齐旧版 collect_system_metrics）。

    Returns:
        {"cpu_percent", "memory_percent", "memory_used_gb", "memory_total_gb",
         "disk_percent", "disk_used_gb", "disk_total_gb"}
        psutil 不可用时返回空 dict。
    """
    if not PSUTIL_AVAILABLE:
        return {}
    try:
        cpu_percent = psutil.cpu_percent(interval=0.5)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return {
            "cpu_percent": cpu_percent,
            "memory_percent": memory.percent,
            "memory_used_gb": round(memory.used / (1024**3), 2),
            "memory_total_gb": round(memory.total / (1024**3), 2),
            "disk_percent": disk.percent,
            "disk_used_gb": round(disk.used / (1024**3), 2),
            "disk_total_gb": round(disk.total / (1024**3), 2),
        }
    except Exception:  # noqa: BLE001 任何采集失败返回空
        return {}


def get_performance_recommendations(
    cpu_percent: float, memory_percent: float, disk_percent: float
) -> list[str]:
    """性能优化建议（对齐旧版阈值：CPU>80 / 内存>80 / 磁盘>85）。"""
    recs: list[str] = []
    if cpu_percent > 80:
        recs.append("CPU 使用率较高，建议检查计算密集型操作")
    if memory_percent > 80:
        recs.append("内存使用率较高，建议检查内存泄漏或优化内存使用")
    if disk_percent > 85:
        recs.append("磁盘使用率较高，建议清理不必要的文件")
    if not recs:
        recs.append("系统性能表现良好")
    return recs


def collect_process_info() -> list[dict[str, Any]]:
    """当前进程资源（对齐旧版 show_realtime_monitoring 的进程部分）。"""
    if not PSUTIL_AVAILABLE:
        return []
    try:
        p = psutil.Process()
        return [
            {"指标": "PID", "值": p.pid},
            {"指标": "内存 RSS (MB)", "值": round(p.memory_info().rss / 1024 / 1024, 2)},
            {"指标": "CPU 占用 (%)", "值": p.cpu_percent(interval=0.3)},
        ]
    except Exception:  # noqa: BLE001
        return []


def collect_system_info() -> Optional[dict[str, Any]]:
    """系统信息（OS/Python/CPU/内存），供 st.json 展示。"""
    if not PSUTIL_AVAILABLE:
        return None
    try:
        return {
            "cpu_count": psutil.cpu_count(),
            "cpu_freq_mhz": psutil.cpu_freq().current if psutil.cpu_freq() else None,
            "memory_total_gb": round(psutil.virtual_memory().total / (1024**3), 2),
            "memory_available_gb": round(psutil.virtual_memory().available / (1024**3), 2),
            "boot_time": psutil.boot_time(),
        }
    except Exception:  # noqa: BLE001
        return None
