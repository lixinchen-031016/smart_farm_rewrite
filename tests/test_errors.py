"""errors 错误处理体系测试。"""


from smart_farm.services import errors


def test_smart_farm_error_hierarchy():
    e = errors.DatabaseError("连接失败")
    assert isinstance(e, errors.SmartFarmError)
    assert e.error_code == "DATABASE_ERROR"
    assert e.severity == "ERROR"


def test_handle_exception_known():
    try:
        raise errors.PredictionError("预测失败")
    except errors.PredictionError as e:
        info = errors.handle_exception(e, username="alice", operation="predict")
    assert info["error_type"] == "PredictionError"
    assert info["error_code"] == "PREDICTION_ERROR"
    assert info["operation"] == "predict"


def test_handle_exception_unknown():
    try:
        raise ValueError("boom")
    except ValueError as e:
        info = errors.handle_exception(e, operation="op")
    assert info["error_type"] == "UnexpectedError"
    assert info["error_code"] == "UNEXPECTED_ERROR"
    assert info["message"] == "boom"
    assert "traceback" in info


def test_exception_handler_decorator():
    @errors.exception_handler()
    def fail():
        raise RuntimeError("模拟失败")

    result = fail()
    assert result["error_code"] == "UNEXPECTED_ERROR"
    assert result["show_details"] is True


def test_exception_handler_success_passthrough():
    @errors.exception_handler()
    def ok():
        return 42

    assert ok() == 42


def test_safe_execute():
    def boom():
        raise ValueError("x")

    result = errors.safe_execute(boom)
    assert result["error_code"] == "UNEXPECTED_ERROR"
    assert result["show_details"] is False


def test_safe_execute_success():
    assert errors.safe_execute(lambda: 7) == 7
