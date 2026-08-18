"""IoT 接入网关。

两种运行方式：
1. 随应用自动启动（默认）：Streamlit 入口 `main.py` 经 `st.cache_resource` 调用
   `start_gateway_background()`，进程内单例运行（多会话/rerun 仅一次）。
   通道由 `GATEWAY_CHANNELS` 配置（默认 http,udp；mqtt 需外部 Broker），
   `AUTO_START_GATEWAY=false` 可关闭。
2. 独立进程：`python -m smart_farm.iot_gateway [--only http|mqtt|udp | --no-*]`。

三协议统一走 `services.ingest_service.ingest_payload`（设备认证 + 归一化 + 入库）：

1. HTTP（FastAPI + uvicorn）
   POST /api/v1/ingest        Bearer <device_key> 或 X-Device-Key 头
   POST /api/v1/ingest/batch  同上，readings 数组
   GET  /api/v1/health        健康检查

2. MQTT（paho-mqtt，需外部 Broker）
   订阅 `{prefix}+/data`；device_key 取自 topic 或 payload，如：
   smart_farm/sf-abc.../data  →  {"metric": "soil_moisture", "value": 42.1}

3. UDP 局域网直推（标准库，无额外依赖）
   设备向 `udp://<host>:8601` 发 UTF-8 JSON（单条或 readings 批量），
   device_key 放在 JSON 中。适合无 HTTP 栈的廉价 MCU（ESP32/Arduino）。

用法：
    python -m smart_farm.iot_gateway                 # HTTP + UDP（MQTT 需 broker 配置时启用）
    python -m smart_farm.iot_gateway --only mqtt     # 只起 MQTT
    python -m smart_farm.iot_gateway --no-http       # 起 MQTT + UDP
"""

import argparse
import json
import logging
import socket
import threading
import time
from typing import Any, Optional, Sequence

from smart_farm.config import get_settings
from smart_farm.data.database import get_session
from smart_farm.services import ingest_service as ingest
from smart_farm.services.ingest_service import IngestError, IngestResult

logger = logging.getLogger("smart_farm.iot_gateway")
settings = get_settings()

VALID_CHANNELS = ("http", "mqtt", "udp")


# ----------------------------- 共享入库逻辑 -----------------------------


def process_payload(payload: dict, device_key: Optional[str] = None) -> IngestResult:
    """开独立 DB 会话处理一条 payload（线程安全：每调用一个 session）。"""
    with get_session() as session:
        return ingest.ingest_payload(session, payload, device_key=device_key)


# ----------------------------- UDP 局域网直推 -----------------------------


class UDPIngestServer(threading.Thread):
    """UDP JSON 收包线程：每包独立解析入库，单包失败不影响后续。"""

    MAX_PACKET = 64 * 1024

    def __init__(self, host: str, port: int):
        super().__init__(daemon=True, name="udp-ingest")
        self.host, self.port = host, port
        self._sock: Optional[socket.socket] = None
        self._stop = threading.Event()
        self.start_error: Optional[str] = None  # 绑定失败原因（端口占用等）
        self.ready = threading.Event()

    def run(self) -> None:
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind((self.host, self.port))
            self._sock.settimeout(1.0)
        except OSError as e:
            self.start_error = str(e)
            logger.error("UDP 绑定 %s:%d 失败：%s", self.host, self.port, e)
            return
        self.ready.set()
        logger.info("UDP 接入监听 %s:%d", self.host, self.port)
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(self.MAX_PACKET)
            except socket.timeout:
                continue
            except OSError:
                break  # socket 被 stop() 关闭
            self._handle_datagram(data, addr)

    def _handle_datagram(self, data: bytes, addr) -> None:
        try:
            payload = json.loads(data.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("payload 必须是 JSON 对象")
            result = process_payload(payload)
            logger.info("UDP %s:%s 接受 %d 条 / 拒绝 %d 条",
                        addr[0], addr[1], result.accepted, result.rejected)
        except (UnicodeDecodeError, json.JSONDecodeError, IngestError, ValueError) as e:
            logger.warning("UDP %s 数据被拒：%s", addr[0], e)

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            self._sock.close()


# ----------------------------- MQTT 订阅 -----------------------------


class MQTTIngestClient:
    """paho-mqtt 订阅器（后台 loop 线程）。需 `pip install paho-mqtt`。"""

    def __init__(self):
        import paho.mqtt.client as mqtt  # 可选依赖，懒加载

        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if settings.mqtt_username:
            self._client.username_pw_set(settings.mqtt_username, settings.mqtt_password or "")
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._topic = f"{settings.mqtt_topic_prefix}+/data"

    def start(self) -> None:
        self._client.connect_async(settings.mqtt_host, settings.mqtt_port, keepalive=60)
        self._client.loop_start()

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        client.subscribe(self._topic)
        logger.info("MQTT 已连接 %s:%d，订阅 %s",
                    settings.mqtt_host, settings.mqtt_port, self._topic)

    def _on_message(self, client, userdata, msg) -> None:
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("payload 必须是 JSON 对象")
            # device_key 优先从 payload 取，缺省回退 topic（smart_farm/<key>/data）
            key = payload.get("device_key") or _key_from_topic(msg.topic)
            result = process_payload(payload, device_key=key)
            logger.info("MQTT %s 接受 %d 条 / 拒绝 %d 条", msg.topic, result.accepted, result.rejected)
        except (UnicodeDecodeError, json.JSONDecodeError, IngestError, ValueError) as e:
            logger.warning("MQTT %s 数据被拒：%s", msg.topic, e)


def _key_from_topic(topic: str) -> Optional[str]:
    """`smart_farm/<device_key>/data` → device_key。"""
    parts = topic.split("/")
    if len(parts) == 3 and parts[0] == settings.mqtt_topic_prefix.rstrip("/"):
        return parts[1]
    return None


# ----------------------------- HTTP 网关（FastAPI） -----------------------------


def create_http_app():
    """构建 FastAPI 接入应用（可被 uvicorn 或测试 TestClient 复用）。"""
    from fastapi import FastAPI, Header, HTTPException, Response
    from pydantic import BaseModel

    app = FastAPI(title="SmartFarm IoT Gateway", version="1.0")

    class IngestBody(BaseModel):
        device_key: Optional[str] = None
        metric: Optional[str] = None
        value: Optional[float] = None
        temperature: Optional[float] = None
        humidity: Optional[float] = None
        timestamp: Optional[Any] = None
        readings: Optional[list[dict[str, Any]]] = None

    def _key_from_headers(authorization: Optional[str], x_device_key: Optional[str]) -> str:
        if x_device_key:
            return x_device_key
        if authorization and authorization.lower().startswith("bearer "):
            return authorization[7:].strip()
        raise HTTPException(status_code=401, detail="缺少设备凭证（Bearer 或 X-Device-Key）")

    @app.get("/api/v1/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/api/v1/ingest")
    def ingest_one(
        body: IngestBody,
        authorization: Optional[str] = Header(default=None),
        x_device_key: Optional[str] = Header(default=None),
    ) -> Response:
        key = _key_from_headers(authorization, x_device_key)
        try:
            result = process_payload(body.model_dump(exclude_none=True), device_key=key)
        except IngestError as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        status = 200 if result.accepted else 422
        return Response(
            content=json.dumps(
                {"accepted": result.accepted, "rejected": result.rejected, "errors": result.errors},
                ensure_ascii=False,
            ),
            status_code=status,
            media_type="application/json",
        )

    @app.post("/api/v1/ingest/batch")
    def ingest_batch(
        body: IngestBody,
        authorization: Optional[str] = Header(default=None),
        x_device_key: Optional[str] = Header(default=None),
    ) -> Response:
        if not body.readings:
            raise HTTPException(status_code=422, detail="readings 不能为空")
        return ingest_one(body, authorization=authorization, x_device_key=x_device_key)

    return app


# ----------------------------- 后台启动（应用进程内单例） -----------------------------

_gateway_lock = threading.Lock()
_gateway_state: dict[str, str] = {}  # channel -> "running" | "failed: 原因"
_gateway_runner: dict[str, Any] = {}  # channel -> 可 stop 的运行器


def parse_channels(spec: str) -> list[str]:
    """解析逗号分隔的通道配置，忽略非法项。"""
    return [c.strip() for c in spec.split(",") if c.strip() in VALID_CHANNELS]


def _start_http_channel() -> str:
    """线程内起 uvicorn，等待启动结果（started / 失败）。"""
    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(
            create_http_app(),
            host=settings.iot_http_host,
            port=settings.iot_http_port,
            log_level="warning",
        )
    )
    _gateway_runner["http"] = server

    def _run() -> None:
        try:
            server.run()
        except Exception as e:  # noqa: BLE001 端口占用等启动失败不能拖垮应用
            logger.error("HTTP 网关启动失败：%s", e)
            server.should_exit = True

    threading.Thread(target=_run, daemon=True, name="http-ingest").start()
    for _ in range(60):  # 最多等 3 秒确认启动
        if server.started or server.should_exit:
            break
        time.sleep(0.05)
    if server.started:
        return f"running:{settings.iot_http_host}:{settings.iot_http_port}"
    return f"failed:端口 {settings.iot_http_port} 启动失败（可能被占用或已由独立网关进程监听）"


def _start_udp_channel() -> str:
    server = UDPIngestServer(settings.iot_udp_host, settings.iot_udp_port)
    _gateway_runner["udp"] = server
    server.start()
    server.ready.wait(timeout=3)
    if server.start_error:
        return f"failed:{server.start_error}"
    return f"running:{settings.iot_udp_host}:{settings.iot_udp_port}"


def _start_mqtt_channel() -> str:
    try:
        client = MQTTIngestClient()
        client.start()
    except Exception as e:  # noqa: BLE001 Broker 不可达等由 paho 后台自动重连
        return f"failed:{e}"
    _gateway_runner["mqtt"] = client
    return f"running:{settings.mqtt_host}:{settings.mqtt_port}（Broker 不可达时自动重连）"


_CHANNEL_STARTERS = {
    "http": _start_http_channel,
    "udp": _start_udp_channel,
    "mqtt": _start_mqtt_channel,
}


def start_gateway_background(channels: Optional[Sequence[str]] = None) -> dict[str, str]:
    """幂等启动接入通道（应用进程内单例，多次调用安全）。

    供 Streamlit 入口在应用启动时调用；每个通道独立容错，
    单通道失败（端口占用等）不影响其他通道与应用本体。

    Returns:
        {channel: 状态描述}，状态以 "running:" 开头表示正常。
    """
    with _gateway_lock:
        if _gateway_state:
            return dict(_gateway_state)  # 已启动，直接返回现状
        wanted = list(channels) if channels else ["http", "udp"]
        for channel in wanted:
            if channel not in VALID_CHANNELS:
                continue
            starter = _CHANNEL_STARTERS[channel]
            try:
                _gateway_state[channel] = starter()
            except Exception as e:  # noqa: BLE001 单通道失败不阻断其余通道
                _gateway_state[channel] = f"failed:{e}"
                logger.error("网关通道 %s 启动异常：%s", channel, e)
        logger.info("IoT 网关（内嵌）状态：%s", _gateway_state)
        return dict(_gateway_state)


def gateway_status() -> dict[str, str]:
    """当前网关通道状态（未启动返回空 dict）。"""
    with _gateway_lock:
        return dict(_gateway_state)


def _stop_gateway() -> None:
    """停止所有通道（仅独立网关进程退出时用）。"""
    for name, runner in _gateway_runner.items():
        try:
            if hasattr(runner, "stop"):
                runner.stop()
            elif hasattr(runner, "should_exit"):
                runner.should_exit = True
        except Exception:  # noqa: BLE001 关停尽力而为
            logger.exception("停止 %s 通道失败", name)


# ----------------------------- 入口（独立网关进程） -----------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="SmartFarm IoT 接入网关")
    parser.add_argument("--only", choices=list(VALID_CHANNELS), help="只启动指定协议")
    parser.add_argument("--no-http", action="store_true", help="不启动 HTTP")
    parser.add_argument("--no-mqtt", action="store_true", help="不启动 MQTT")
    parser.add_argument("--no-udp", action="store_true", help="不启动 UDP")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if args.only:
        channels = [args.only]
    else:
        channels = [
            c for c in ("http", "mqtt", "udp")
            if not {"http": args.no_http, "mqtt": args.no_mqtt, "udp": args.no_udp}[c]
        ]
    if not channels:
        logger.error("没有任何接入通道被启动（检查 --only/--no-* 参数）")
        return 1

    try:
        state = start_gateway_background(channels)
        if not any(v.startswith("running") for v in state.values()):
            logger.error("全部通道启动失败：%s", state)
            return 1
        logger.info("IoT 网关已启动：%s", state)
        threading.Event().wait()  # 主线程常驻
    except KeyboardInterrupt:
        logger.info("收到中断，网关退出")
    finally:
        _stop_gateway()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
