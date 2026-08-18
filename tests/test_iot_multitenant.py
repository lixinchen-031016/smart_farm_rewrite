"""IoT 接入与多租户功能测试：ingest_service / repositories 新函数 / 网关 / seed。

fixture（session/gh/device）见 conftest.py。
"""

from datetime import datetime

import pytest

from smart_farm.data import repositories as repo
from smart_farm.data.models import Device, UserGreenhouse
from smart_farm.services import ingest_service as ingest
from smart_farm.services.ingest_service import IngestError

# ----------------------------- ingest_service -----------------------------


def test_authenticate_device_ok(session, device):
    d = ingest.authenticate_device(session, "sf-test-key-0001")
    assert d.id == device.id


def test_authenticate_device_failures(session, device):
    with pytest.raises(IngestError, match="缺少设备凭证"):
        ingest.authenticate_device(session, None)
    with pytest.raises(IngestError, match="未注册"):
        ingest.authenticate_device(session, "sf-unknown")
    repo.set_device_enabled(session, device.id, False)
    with pytest.raises(IngestError, match="停用"):
        ingest.authenticate_device(session, "sf-test-key-0001")


def test_ingest_single_value_with_alias(session, device, gh):
    """soil 别名 + 显式 key 参数 + ISO 时间戳。"""
    res = ingest.ingest_payload(
        session,
        {"metric": "soil", "value": 42.5, "timestamp": "2026-08-18T10:00:00"},
        device_key="sf-test-key-0001",
    )
    assert res.accepted == 1 and res.rejected == 0
    rows = repo.get_sensor_readings(session, "soil_moisture", greenhouse_id=gh.id)
    assert len(rows) == 1
    assert rows[0].value == 42.5
    assert rows[0].timestamp == datetime(2026, 8, 18, 10, 0, 0)


def test_ingest_air_dual_fields_and_default_now(session, device, gh):
    """air 双指标；缺省 timestamp → 服务器当前时间。"""
    before = datetime.now()
    res = ingest.ingest_payload(
        session,
        {"device_key": "sf-test-key-0001", "metric": "air", "temperature": 25.3, "humidity": 60.5},
    )
    assert res.accepted == 1
    row = repo.get_latest_sensor_reading(session, "air_temperature_humidity", greenhouse_id=gh.id)
    assert row.timestamp >= before.replace(microsecond=0)
    assert row.temperature == 25.3 and row.humidity == 60.5


def test_ingest_payload_with_key_in_body(session, device, gh):
    res = ingest.ingest_payload(
        session, {"device_key": "sf-test-key-0001", "metric": "light", "value": 15300.0}
    )
    assert res.accepted == 1
    row = repo.get_latest_sensor_reading(session, "light_intensity", greenhouse_id=gh.id)
    assert row.value == 15300.0


def test_ingest_batch(session, device, gh):
    res = ingest.ingest_payload(
        session,
        {
            "device_key": "sf-test-key-0001",
            "readings": [
                {"metric": "soil_moisture", "value": 1.0},
                {"metric": "soil_nutrient", "value": 2.0},
                {"metric": "nope", "value": 3.0},  # 拒绝
                {"metric": "soil_moisture", "value": "abc"},  # 拒绝
            ],
        },
    )
    assert res.accepted == 2
    assert res.rejected == 2
    assert len(res.errors) == 2


def test_ingest_metric_inference(session, device):
    """无 metric 字段时按取值列名推断。"""
    res = ingest.ingest_payload(
        session,
        {"device_key": "sf-test-key-0001", "temperature": 22.0, "humidity": 55.0},
    )
    assert res.accepted == 1
    row = repo.get_latest_sensor_reading(session, "air_temperature_humidity")
    assert row.temperature == 22.0


def test_ingest_epoch_timestamp(session, device, gh):
    res = ingest.ingest_payload(
        session,
        {"device_key": "sf-test-key-0001", "metric": "soil_moisture", "value": 10.0, "timestamp": 1787059200},
    )
    assert res.accepted == 1
    row = repo.get_sensor_readings(session, "soil_moisture", greenhouse_id=gh.id)[0]
    assert row.timestamp.year == 2026


def test_ingest_touches_device(session, device):
    assert device.last_seen_at is None
    ingest.ingest_payload(session, {"device_key": "sf-test-key-0001", "metric": "soil", "value": 1.0})
    assert device.last_seen_at is not None


def test_generate_device_key_unique():
    keys = {ingest.generate_device_key() for _ in range(50)}
    assert len(keys) == 50
    assert all(k.startswith("sf-") for k in keys)


# ----------------------------- 多租户仓库函数 -----------------------------


def test_greenhouse_crud(session):
    gh = repo.create_greenhouse(session, "A 棚", "北")
    assert repo.create_greenhouse(session, "A 棚").id == gh.id  # 幂等
    assert repo.update_greenhouse(session, gh.id, "A 棚改", "南")
    gh2 = repo.get_greenhouse(session, gh.id)
    assert gh2.name == "A 棚改"
    assert repo.delete_greenhouse(session, gh.id)
    assert repo.get_greenhouse(session, gh.id) is None


def test_delete_greenhouse_cleans_links(session):
    gh = repo.create_greenhouse(session, "B 棚")
    user = repo.create_user(session, "u1", "hash")
    dev = repo.create_device(session, "d1", "http", "sf-k1", greenhouse_id=gh.id)
    repo.set_user_greenhouses(session, user.id, [gh.id])
    repo.delete_greenhouse(session, gh.id)
    session.flush()
    links = session.query(UserGreenhouse).all()
    assert links == []
    assert dev.greenhouse_id is None


def test_user_greenhouse_grants_and_visibility(session):
    gh1 = repo.create_greenhouse(session, "一号")
    gh2 = repo.create_greenhouse(session, "二号")
    gh3 = repo.create_greenhouse(session, "三号")
    admin = repo.create_user(session, "admin", "h", role="admin")
    user = repo.create_user(session, "u", "h", role="user")

    repo.set_user_greenhouses(session, user.id, [gh1.id, gh2.id])

    assert repo.list_greenhouse_ids_for_user(session, user.id) == [gh1.id, gh2.id]
    # admin 全量可见
    assert len(repo.list_greenhouses_for_user(session, admin)) == 3
    # 普通用户仅授权棚
    visible = repo.list_greenhouses_for_user(session, user)
    assert {g.id for g in visible} == {gh1.id, gh2.id}

    # 鉴权
    assert repo.user_can_access_greenhouse(session, user, gh1.id)
    assert not repo.user_can_access_greenhouse(session, user, gh3.id)
    assert repo.user_can_access_greenhouse(session, admin, gh3.id)

    # 整体替换（取消一号，授予三号）
    repo.set_user_greenhouses(session, user.id, [gh2.id, gh3.id])
    assert repo.list_greenhouse_ids_for_user(session, user.id) == [gh2.id, gh3.id]


def test_legacy_user_greenhouse_field_compat(session):
    """旧字段 User.greenhouse_id 仍计入可见集合。"""
    gh = repo.create_greenhouse(session, "旧棚")
    user = repo.create_user(session, "legacy", "h", greenhouse_id=gh.id)
    visible = repo.list_greenhouses_for_user(session, user)
    assert [g.id for g in visible] == [gh.id]
    assert repo.user_can_access_greenhouse(session, user, gh.id)


# ----------------------------- 设备仓库 -----------------------------


def test_device_crud(session, gh):
    dev = repo.create_device(session, "节点", "udp", "sf-xyz", greenhouse_id=gh.id)
    assert repo.get_device_by_key(session, "sf-xyz").id == dev.id
    assert repo.list_devices(session, greenhouse_id=gh.id)[0].id == dev.id
    assert repo.set_device_enabled(session, dev.id, False)
    assert not repo.get_device_by_key(session, "sf-xyz").enabled
    repo.touch_device(session, dev.id)
    assert dev.last_seen_at is not None
    assert repo.delete_device(session, dev.id)
    assert repo.get_device_by_key(session, "sf-xyz") is None


# ----------------------------- 网关 -----------------------------


def test_key_from_topic():
    from smart_farm import iot_gateway as gw

    assert gw._key_from_topic("smart_farm/sf-abc123/data") == "sf-abc123"
    assert gw._key_from_topic("other/sf-abc123/data") is None
    assert gw._key_from_topic("smart_farm/x") is None


def test_udp_handle_datagram(monkeypatch, session, device, gh):
    """UDP 数据包处理逻辑（不起 socket，直接调 handler；monkeypatch get_session）。"""
    from contextlib import contextmanager

    from smart_farm import iot_gateway as gw

    @contextmanager
    def fake_session():
        yield session

    monkeypatch.setattr(gw, "get_session", fake_session)
    server = gw.UDPIngestServer("127.0.0.1", 0)

    server._handle_datagram(
        b'{"device_key": "sf-test-key-0001", "metric": "soil", "value": 33.3}', ("1.2.3.4", 9)
    )
    assert repo.count_sensor_readings(session, "soil_moisture", greenhouse_id=gh.id) == 1

    # 坏包不抛异常、不入库
    server._handle_datagram(b"not-json", ("1.2.3.4", 9))
    server._handle_datagram(b'{"device_key": "sf-unknown", "metric": "soil", "value": 1}', ("1.2.3.4", 9))
    assert repo.count_sensor_readings(session, "soil_moisture", greenhouse_id=gh.id) == 1


def test_http_gateway_endpoints(monkeypatch, session, device, gh):
    """FastAPI 网关：健康检查 / 认证 / 入库 / 错误码。"""
    fastapi = pytest.importorskip("fastapi")
    fastapi.testclient = pytest.importorskip("fastapi.testclient")
    from contextlib import contextmanager

    from smart_farm import iot_gateway as gw

    @contextmanager
    def fake_session():
        yield session

    monkeypatch.setattr(gw, "get_session", fake_session)
    client = fastapi.testclient.TestClient(gw.create_http_app())

    # 健康
    assert client.get("/api/v1/health").json() == {"status": "ok"}

    # 缺凭证 → 401
    r = client.post("/api/v1/ingest", json={"metric": "soil", "value": 1.0})
    assert r.status_code == 401

    # Bearer 认证入库
    r = client.post(
        "/api/v1/ingest",
        json={"metric": "soil_moisture", "value": 45.0},
        headers={"Authorization": "Bearer sf-test-key-0001"},
    )
    assert r.status_code == 200
    assert r.json()["accepted"] == 1

    # X-Device-Key + 批量
    r = client.post(
        "/api/v1/ingest/batch",
        json={"readings": [
            {"metric": "soil", "value": 1.0},
            {"metric": "light", "value": 900.0},
        ]},
        headers={"X-Device-Key": "sf-test-key-0001"},
    )
    assert r.status_code == 200
    assert r.json()["accepted"] == 2

    # 全部拒绝 → 422
    r = client.post(
        "/api/v1/ingest",
        json={"metric": "bad", "value": 1.0},
        headers={"Authorization": "Bearer sf-test-key-0001"},
    )
    assert r.status_code == 422

    # 未注册 key → 403
    r = client.post(
        "/api/v1/ingest",
        json={"metric": "soil", "value": 1.0},
        headers={"Authorization": "Bearer sf-ghost"},
    )
    assert r.status_code == 403

    assert repo.count_sensor_readings(session, "soil_moisture", greenhouse_id=gh.id) == 2
    assert repo.count_sensor_readings(session, "light_intensity", greenhouse_id=gh.id) == 1


def test_device_model_defaults(session):
    d = Device(device_key="sf-x", name="x", protocol="udp", created_at=datetime.now())
    session.add(d)
    session.flush()
    assert d.enabled is True
