"""网关自动启动测试：幂等后台启动 / 通道状态 / UDP 端到端 / 端口占用容错 / 应用入口自动拉起。"""

import json
import socket
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from smart_farm import iot_gateway as gw


@pytest.fixture()
def reset_gateway(monkeypatch):
    """重置网关全局状态并隔离端口/会话（每用例独立）。"""
    monkeypatch.setattr(gw, "_gateway_state", {})
    monkeypatch.setattr(gw, "_gateway_runner", {})
    yield


def _free_tcp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_parse_channels():
    assert gw.parse_channels("http,udp") == ["http", "udp"]
    assert gw.parse_channels(" http , mqtt ,,bad,udp ") == ["http", "mqtt", "udp"]
    assert gw.parse_channels("bad") == []


def test_start_gateway_idempotent(reset_gateway):
    """重复调用只启动一次（第二次直接返回现状，不重复绑定端口）。"""
    calls = []
    monkey_orig = gw._CHANNEL_STARTERS

    def _fake_starter(name):
        def starter():
            calls.append(name)
            return f"running:fake:{name}"

        return starter

    gw._CHANNEL_STARTERS = {k: _fake_starter(k) for k in monkey_orig}
    try:
        s1 = gw.start_gateway_background(["http", "udp"])
        s2 = gw.start_gateway_background(["http", "udp"])
    finally:
        gw._CHANNEL_STARTERS = monkey_orig
    assert s1 == s2 == {"http": "running:fake:http", "udp": "running:fake:udp"}
    assert sorted(calls) == ["http", "udp"]  # 每通道仅一次


def test_udp_channel_end_to_end(reset_gateway, monkeypatch, session, device, gh):
    """UDP 通道真实收包入库：起网关 → 发 UDP 包 → 数据落库。"""
    from smart_farm.data import repositories as repo

    # 随机可用端口，避免与其他测试/本地网关冲突
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    monkeypatch.setattr(gw.settings, "iot_udp_host", "127.0.0.1")
    monkeypatch.setattr(gw.settings, "iot_udp_port", port)

    @contextmanager
    def fake_session():
        yield session

    monkeypatch.setattr(gw, "get_session", fake_session)

    state = gw.start_gateway_background(["udp"])
    assert state["udp"].startswith("running")
    assert gw.gateway_status()["udp"].startswith("running")

    # 发送真实 UDP 数据包（重试几次，等接收线程就绪）
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    payload = json.dumps(
        {"device_key": device.device_key, "metric": "soil", "value": 41.5}
    ).encode("utf-8")
    for _ in range(20):
        sock.sendto(payload, ("127.0.0.1", port))
        if repo.count_sensor_readings(session, "soil_moisture", greenhouse_id=gh.id) >= 1:
            break
        time.sleep(0.1)
    sock.close()

    assert repo.count_sensor_readings(session, "soil_moisture", greenhouse_id=gh.id) == 1
    row = repo.get_latest_sensor_reading(session, "soil_moisture", greenhouse_id=gh.id)
    assert row.value == 41.5


def test_udp_port_conflict_reports_failed(reset_gateway, monkeypatch):
    """端口被占用时通道标记 failed，不抛异常。"""
    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.bind(("127.0.0.1", 0))
    port = blocker.getsockname()[1]

    monkeypatch.setattr(gw.settings, "iot_udp_host", "127.0.0.1")
    monkeypatch.setattr(gw.settings, "iot_udp_port", port)

    state = gw.start_gateway_background(["udp"])
    assert state["udp"].startswith("failed")
    blocker.close()


def test_app_main_autostarts_gateway(reset_gateway, monkeypatch):
    """应用入口自动启动网关（AppTest 驱动 main.py 全脚本执行）。"""
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    import smart_farm.data.database as db
    from smart_farm.data.models import Base

    # 随机端口，避免与本地独立网关/其他测试冲突
    monkeypatch.setattr(gw.settings, "iot_http_port", _free_tcp_port())
    monkeypatch.setattr(gw.settings, "iot_udp_port", _free_udp_port())
    monkeypatch.setattr(gw.settings, "iot_http_host", "127.0.0.1")
    monkeypatch.setattr(gw.settings, "iot_udp_host", "127.0.0.1")

    # 内存库：登录页/大棚上下文查询不依赖本地 DB 文件
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))

    main_py = Path(gw.__file__).parent / "app" / "main.py"
    at = AppTest.from_file(str(main_py), default_timeout=60)
    at.run()

    assert not at.exception
    state = gw.gateway_status()
    assert state.get("http", "").startswith("running"), state
    assert state.get("udp", "").startswith("running"), state
