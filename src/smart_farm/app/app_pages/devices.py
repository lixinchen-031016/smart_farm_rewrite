"""设备接入管理页（管理员专属）：IoT 设备注册 / 密钥 / 启停 / 多棚管理 / 接入说明。"""

from datetime import datetime

import pandas as pd
import streamlit as st

from smart_farm.app.guards import require_admin
from smart_farm.config import get_settings
from smart_farm.data import repositories as repo
from smart_farm.data.database import get_session
from smart_farm.services import ingest_service as ingest

st.title("设备接入")
if not require_admin():
    st.stop()

current_user = st.session_state.get("username")
settings = get_settings()
PROTOCOLS = {"http": "HTTP", "mqtt": "MQTT", "udp": "UDP 局域网"}

# ---------------- 设备列表 ----------------
with get_session() as s:
    devices = repo.list_devices(s)
    ghs = repo.list_greenhouses(s)
gh_names = {g.id: g.name for g in ghs}
now = datetime.now()

if devices:
    st.subheader("已注册设备")
    df = pd.DataFrame(
        [{
            "ID": d.id,
            "设备名": d.name,
            "协议": PROTOCOLS.get(d.protocol, d.protocol),
            "所属大棚": gh_names.get(d.greenhouse_id, "—"),
            "状态": "启用" if d.enabled else "停用",
            "最近上报": (
                f"{(now - d.last_seen_at).total_seconds() / 60:.0f} 分钟前"
                if d.last_seen_at else "从未"
            ),
            "密钥": f"{d.device_key[:10]}…",
        } for d in devices]
    )
    st.dataframe(df, width="stretch", hide_index=True)
else:
    st.info("暂无设备。在下方注册第一台 IoT 设备。")

# ---------------- 注册设备 ----------------
st.subheader("注册设备")
with st.form("register_device_form"):
    c1, c2 = st.columns(2)
    name = c1.text_input("设备名称", placeholder="如：一号棚-土壤湿度节点")
    protocol = c2.selectbox("接入协议", list(PROTOCOLS.keys()), format_func=lambda k: PROTOCOLS[k])
    gh_id = st.selectbox(
        "数据归属大棚",
        [g.id for g in ghs] + [0],
        format_func=lambda i: gh_names.get(i, "（不绑定）"),
    )
    note = st.text_input("备注（可选）")
    if st.form_submit_button("注册并生成密钥", type="primary", icon=":material/add:"):
        if not name:
            st.error("请填写设备名称。")
        elif gh_id == 0 and not ghs:
            st.warning("建议先创建大棚，否则上报数据不按棚隔离。")
            gh_id = 0
        with get_session() as s:
            key = ingest.generate_device_key()
            repo.create_device(
                s, name=name, protocol=protocol, device_key=key,
                greenhouse_id=gh_id or None, note=note or None,
            )
            repo.add_log(s, "INFO", current_user, "设备接入", f"注册设备 {name}（{protocol}）")
        st.session_state["new_device_key"] = key
        st.rerun()

if "new_device_key" in st.session_state:
    st.success("设备注册成功！密钥仅本次显示，请立即复制保存：")
    st.code(st.session_state["new_device_key"], language="text")
    if st.button("我已保存密钥", icon=":material/done:"):
        del st.session_state["new_device_key"]
        st.rerun()

# ---------------- 管理操作 ----------------
if devices:
    st.subheader("管理操作")
    with st.form("device_ops_form"):
        target = st.selectbox("选择设备", [f"{d.id} - {d.name}" for d in devices])
        op = st.selectbox("操作", ["启用", "停用", "删除"])
        if st.form_submit_button("执行"):
            dev_id = int(target.split(" - ")[0])
            with get_session() as s:
                if op == "删除":
                    repo.delete_device(s, dev_id)
                    repo.add_log(s, "INFO", current_user, "设备接入", f"删除设备 #{dev_id}")
                else:
                    repo.set_device_enabled(s, dev_id, op == "启用")
                    repo.add_log(s, "INFO", current_user, "设备接入", f"设备 #{dev_id} {op}")
            st.success(f"已执行：{op}。")
            st.rerun()

# ---------------- 大棚管理（多棚） ----------------
st.subheader("大棚管理")
c_new, c_edit = st.columns(2)
with c_new:
    with st.form("create_gh_form"):
        gh_name = st.text_input("新大棚名称")
        gh_loc = st.text_input("位置（可选）")
        if st.form_submit_button("新建大棚", icon=":material/add_home:"):
            if not gh_name:
                st.error("请填写大棚名称。")
            else:
                with get_session() as s:
                    repo.create_greenhouse(s, gh_name, gh_loc or None)
                    repo.add_log(s, "INFO", current_user, "大棚管理", f"新建大棚 {gh_name}")
                st.success(f"大棚「{gh_name}」已就绪。")
                st.rerun()
with c_edit:
    if ghs:
        with st.form("edit_gh_form"):
            gh_target = st.selectbox("选择大棚", [f"{g.id} - {g.name}" for g in ghs])
            gh_new_name = st.text_input("新名称")
            gh_new_loc = st.text_input("新位置")
            gh_op = st.selectbox("操作", ["保存修改", "删除大棚"])
            if st.form_submit_button("执行"):
                gid = int(gh_target.split(" - ")[0])
                with get_session() as s:
                    if gh_op == "删除大棚":
                        if repo.delete_greenhouse(s, gid):
                            repo.add_log(s, "INFO", current_user, "大棚管理", f"删除大棚 #{gid}")
                            st.success("大棚已删除（设备归属已清空，用户授权已解除）。")
                    elif not gh_new_name:
                        st.error("请填写新名称。")
                    elif repo.update_greenhouse(s, gid, gh_new_name, gh_new_loc or None):
                        repo.add_log(s, "INFO", current_user, "大棚管理", f"修改大棚 #{gid}")
                        st.success("大棚信息已更新。")
                st.rerun()

# ---------------- 接入说明 ----------------
st.subheader("设备接入说明")
with st.expander("HTTP（适合网关 / 树莓派 / 任何能发 HTTP 的设备）", expanded=False):
    st.markdown(f"""
**端点**：`POST http://<服务器>:{settings.iot_http_port}/api/v1/ingest`

```bash
curl -X POST http://localhost:{settings.iot_http_port}/api/v1/ingest \\
  -H "Authorization: Bearer <device_key>" \\
  -H "Content-Type: application/json" \\
  -d '{{"metric": "soil_moisture", "value": 42.1}}'
```

- 认证：`Authorization: Bearer <device_key>` 或 `X-Device-Key` 头。
- 空气温湿度双指标：`{{"metric": "air", "temperature": 25.3, "humidity": 60.5}}`。
- 批量：`POST /api/v1/ingest/batch`，body 用 `{{"readings": [{{...}}, {{...}}]}}`。
- `timestamp` 可省略（服务器时间），支持 ISO 字符串与 epoch 秒/毫秒。
""")
with st.expander("MQTT（适合低功耗节点，需 Broker 如 Mosquitto/EMQX）", expanded=False):
    st.markdown(f"""
设备向 **`{settings.mqtt_topic_prefix}<device_key>/data`** 主题发布 JSON：

```bash
mosquitto_pub -h {settings.mqtt_host} -p {settings.mqtt_port} \\
  -t '{settings.mqtt_topic_prefix}<device_key>/data' \\
  -m '{{"metric": "light", "value": 15300}}'
```

- `device_key` 也可写在 JSON 里，此时 topic 任意。
- 网关启动 MQTT 通道：`python -m smart_farm.iot_gateway --only mqtt`（需 `uv pip install -e '.[iot]'`）。
""")
with st.expander("UDP 局域网直推（适合 ESP32/Arduino 等无 HTTP 栈的 MCU）", expanded=False):
    st.markdown(f"""
设备向 **`udp://<服务器>:{settings.iot_udp_port}`** 发送 UTF-8 JSON 单包：

```json
{{"device_key": "sf-xxxx", "metric": "soil_moisture", "value": 42.1}}
```

批量：`{{"device_key": "sf-xxxx", "readings": [{{...}}, {{...}}]}}`。单包 ≤ 64KB。
""")
with st.expander("启动网关", expanded=False):
    st.markdown(f"""
```bash
python -m smart_farm.iot_gateway             # HTTP(:{settings.iot_http_port}) + UDP(:{settings.iot_udp_port})
python -m smart_farm.iot_gateway --only mqtt # 仅 MQTT
python -m smart_farm.iot_gateway --no-http   # MQTT + UDP
```

可选依赖：`uv pip install -e '.[iot]'`（fastapi / uvicorn / paho-mqtt）。
""")
