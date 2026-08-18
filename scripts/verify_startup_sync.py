"""启动同步端到端验证脚本（真实 MySQL 主库 + SQLite 备库）。"""

from sqlalchemy import create_engine, func, select
from streamlit.testing.v1 import AppTest

from smart_farm.data import database as db
from smart_farm.data.models import Device, Greenhouse, OperationLog, SoilMoisture, User
from smart_farm.services import sync_service as ss

at = AppTest.from_file("src/smart_farm/app/main.py", default_timeout=180)
at.run()
assert not at.exception, at.exception

r = ss.last_sync_result()
p2f, f2p = r["total"]["primary_to_fallback"], r["total"]["fallback_to_primary"]
print(f"第一次启动同步: {r['status']} | 主→备 {p2f} | 备→主 {f2p}")
assert p2f > 17000 and f2p == 0, "备库为空时应全量接收主库数据"

r2 = ss.startup_sync()
p2f2, f2p2 = r2["total"]["primary_to_fallback"], r2["total"]["fallback_to_primary"]
print(f"第二次同步: {r2['status']} | 主→备 {p2f2} | 备→主 {f2p2}")
assert p2f2 == 0 and f2p2 == 0, "数据一致后二次同步应零迁移"

fb = create_engine("sqlite:///./smart_farm.db")
models = {
    "soil_moisture": SoilMoisture, "greenhouse": Greenhouse,
    "device": Device, "user": User, "operation_logs": OperationLog,
}
with fb.connect() as c, db.get_session() as s:
    for name, m in models.items():
        nf = c.execute(select(func.count(m.id))).scalar()
        np_ = s.execute(select(func.count(m.id))).scalar()
        assert nf == np_, f"{name}: {nf} vs {np_}"
        print(f"  {name}: {nf} 行 一致")

print("端到端验证通过：启动同步全量推送 + 二次同步零迁移 + 主备全表一致 ✔")
