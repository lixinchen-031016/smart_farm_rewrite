"""IoT 接入一键测试脚本：模拟设备通过 HTTP / UDP 向运行中的应用发送数据，并验证入库。

用法：
    venv/bin/python scripts/send_test_data.py            # HTTP + UDP 各发一批
    venv/bin/python scripts/send_test_data.py --http     # 仅 HTTP
    venv/bin/python scripts/send_test_data.py --udp      # 仅 UDP
    venv/bin/python scripts/send_test_data.py --loop 5   # 每秒发一轮，共 5 轮

前提：应用正在运行（网关随应用自动启动）：
    venv/bin/streamlit run src/smart_farm/app/main.py

流程：
    1. 自动准备测试设备（复用名为「HTTP/UDP 测试设备」的设备，没有则创建并绑定第一个大棚）
    2. HTTP POST /api/v1/ingest（单条 + 批量）
    3. UDP 直推 JSON 数据包（单条 + air 双列）
    4. 直接查询数据库，回读该设备大棚的最新读数，验证「发送后程序即获取数据」
"""

import argparse
import json
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402

from smart_farm.config import get_settings  # noqa: E402
from smart_farm.data import repositories as repo  # noqa: E402
from smart_farm.data.database import get_session  # noqa: E402
from smart_farm.services.ingest_service import generate_device_key  # noqa: E402

settings = get_settings()
TEST_DEVICE_NAME = "HTTP/UDP 测试设备"


# ----------------------------- 准备测试设备 -----------------------------

def ensure_test_device() -> tuple[str, int]:
    """复用或创建测试设备，返回 (device_key, greenhouse_id)。"""
    with get_session() as s:
        for d in repo.list_devices(s):
            if d.name == TEST_DEVICE_NAME and d.enabled:
                return d.device_key, d.greenhouse_id
        # 没有现成测试设备 → 绑定第一个大棚（无大棚则顺手建一个）
        gh = repo.list_greenhouses(s)
        greenhouse_id = gh[0].id if gh else repo.create_greenhouse(s, "测试棚", "脚本自动创建").id
        device = repo.create_device(
            s, name=TEST_DEVICE_NAME, protocol="http",
            device_key=generate_device_key(), greenhouse_id=greenhouse_id,
            note="scripts/send_test_data.py 自动创建",
        )
        return device.device_key, device.greenhouse_id


# ----------------------------- 发送：HTTP -----------------------------

def send_http(device_key: str) -> bool:
    """HTTP 单条 + 批量各发一次，返回是否全部成功。"""
    url = f"http://127.0.0.1:{settings.iot_http_port}/api/v1/ingest"
    headers = {"X-Device-Key": device_key, "Content-Type": "application/json"}
    now = datetime.now().isoformat(timespec="seconds")

    single = {"metric": "soil_moisture", "value": round(38 + 10 * time.time() % 1, 1), "timestamp": now}
    batch = {"readings": [
        {"metric": "air", "temperature": 24.6, "humidity": 61.2, "timestamp": now},
        {"metric": "light_intensity", "value": 28500.0, "timestamp": now},
    ]}
    ok = True
    for label, payload, expect in (("单条", single, 1), ("批量", batch, 2)):
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=5)
            body = resp.json()
            accepted = body.get("accepted", 0)
            mark = "OK" if resp.status_code == 200 and accepted == expect else "FAIL"
            print(f"  [HTTP {label}] HTTP {resp.status_code} accepted={accepted} → {mark}")
            ok = ok and mark == "OK"
        except httpx.HTTPError as e:
            print(f"  [HTTP {label}] 连接失败：{e}")
            ok = False
    return ok


# ----------------------------- 发送：UDP -----------------------------

def send_udp(device_key: str) -> bool:
    """UDP 直推两个 JSON 包，返回是否发送成功（送达不代表入库，入库由回读验证）。"""
    addr = ("127.0.0.1", settings.iot_udp_port)
    now = datetime.now().isoformat(timespec="seconds")
    packets = [
        {"device_key": device_key, "metric": "soil_nutrient", "value": round(1.2 + time.time() % 1, 2), "timestamp": now},
        {"device_key": device_key, "temperature": 25.1, "humidity": 63.4, "timestamp": now},
    ]
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(3)
            for i, payload in enumerate(packets, 1):
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                sock.sendto(data, addr)
                print(f"  [UDP 包{i}] → {addr[0]}:{addr[1]}  {json.dumps(payload, ensure_ascii=False)}")
        return True
    except OSError as e:
        print(f"  [UDP] 发送失败：{e}")
        return False


# ----------------------------- 验证：回读数据库 -----------------------------

def verify(greenhouse_id: int, rounds_sent: int) -> bool:
    """回读该大棚各指标最新读数，验证本轮数据已入库。"""
    time.sleep(0.5)  # 等网关线程落库
    print("\n== 回读验证（数据库最新读数）==")
    ok = True
    with get_session() as s:
        for metric in ("soil_moisture", "air_temperature_humidity", "light_intensity", "soil_nutrient"):
            row = repo.get_latest_sensor_reading(s, metric, greenhouse_id=greenhouse_id)
            if row is None:
                print(f"  {metric:<28} 无数据")
                ok = False
                continue
            value = getattr(row, "value", None)
            temp, hum = getattr(row, "temperature", None), getattr(row, "humidity", None)
            shown = f"value={value}" if value is not None else f"temperature={temp}, humidity={hum}"
            age = (datetime.now() - row.timestamp).total_seconds()
            fresh = age < 60  # 本轮发送的数据时间戳都在 1 分钟内
            print(f"  {metric:<28} {shown}  @ {row.timestamp:%H:%M:%S}（{age:.1f} 秒前）{'OK' if fresh else 'STALE'}")
            ok = ok and fresh
    print(f"\n结论：{'数据已成功接入并可被程序读取 ✔' if ok else '部分数据未入库，请检查上方失败项 ✘'}")
    return ok


# ----------------------------- 主流程 -----------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="向运行中的 SmartFarm 发送 HTTP/UDP 测试数据")
    parser.add_argument("--http", action="store_true", help="只测 HTTP")
    parser.add_argument("--udp", action="store_true", help="只测 UDP")
    parser.add_argument("--loop", type=int, default=1, metavar="N", help="连续发 N 轮（每轮间隔 1 秒）")
    args = parser.parse_args()
    do_http, do_udp = not args.udp, not args.http  # 默认两者都测

    device_key, greenhouse_id = ensure_test_device()
    print(f"测试设备就绪：{TEST_DEVICE_NAME}（大棚 #{greenhouse_id}）")
    print(f"目标：HTTP 127.0.0.1:{settings.iot_http_port} / UDP 127.0.0.1:{settings.iot_udp_port}\n")

    all_ok = True
    for round_no in range(1, args.loop + 1):
        if args.loop > 1:
            print(f"—— 第 {round_no}/{args.loop} 轮 ——")
        if do_http:
            print("[1/2] HTTP 发送")
            all_ok = send_http(device_key) and all_ok
        if do_udp:
            print("[2/2] UDP 发送")
            all_ok = send_udp(device_key) and all_ok
        if round_no < args.loop:
            time.sleep(1)

    return 0 if verify(greenhouse_id, args.loop) and all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
