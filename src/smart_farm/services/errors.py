"""错误处理体系（对齐旧版 utils/error_handling.py，纯逻辑可单测）。

- SmartFarmError 异常体系（DatabaseError/AuthenticationError/DataProcessingError/PredictionError/ModuleError）
- handle_exception：把任意异常转成结构化错误信息
- exception_handler / safe_execute：装饰器/包装器（UI 层可选接入）
"""

import logging
import traceback
from datetime import datetime
from functools import wraps
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class SmartFarmError(Exception):
    """平台基础异常。"""

    def __init__(self, message: str, error_code: str = "GENERAL_ERROR", severity: str = "ERROR"):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.severity = severity


class DatabaseError(SmartFarmError):
    def __init__(self, message: str):
        super().__init__(message, "DATABASE_ERROR", "ERROR")


class AuthenticationError(SmartFarmError):
    def __init__(self, message: str):
        super().__init__(message, "AUTH_ERROR", "WARNING")


class DataProcessingError(SmartFarmError):
    def __init__(self, message: str):
        super().__init__(message, "DATA_PROCESSING_ERROR", "ERROR")


class PredictionError(SmartFarmError):
    def __init__(self, message: str):
        super().__init__(message, "PREDICTION_ERROR", "ERROR")


class ModuleError(SmartFarmError):
    def __init__(self, message: str):
        super().__init__(message, "MODULE_ERROR", "WARNING")


def handle_exception(e: Exception, username: Optional[str] = None, operation: str = "未知操作") -> dict[str, Any]:
    """把异常转成结构化错误信息（对齐旧版返回结构）。"""
    if isinstance(e, SmartFarmError):
        error_type = type(e).__name__
        error_code = e.error_code
        severity = e.severity
        message = e.message
    else:
        error_type = "UnexpectedError"
        error_code = "UNEXPECTED_ERROR"
        severity = "ERROR"
        message = str(e) or repr(e)
    info = {
        "error_type": error_type,
        "error_code": error_code,
        "message": message,
        "severity": severity,
        "timestamp": datetime.now().isoformat(),
        "operation": operation,
        "traceback": traceback.format_exc() if e.__traceback__ else "",
    }
    logger.error("[%s] %s @ %s: %s", error_code, operation, username or "system", message)
    return info


def exception_handler(
    username_getter: Optional[Callable[[], Optional[str]]] = None,
    show_details: bool = True,
) -> Callable:
    """装饰器：捕获一切异常，返回结构化错误信息 dict（UI 可据此渲染）。"""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception as e:  # noqa: BLE001
                username = username_getter() if username_getter else None
                info = handle_exception(e, username=username, operation=func.__name__)
                info["show_details"] = show_details
                return info

        return wrapper

    return decorator


def safe_execute(func: Callable, *args: Any, **kwargs: Any) -> Any:
    """同步包装：执行函数，异常转结构化错误（show_details=False，对齐旧版）。"""
    try:
        return func(*args, **kwargs)
    except Exception as e:  # noqa: BLE001
        info = handle_exception(e, operation=getattr(func, "__name__", "unknown"))
        info["show_details"] = False
        return info
