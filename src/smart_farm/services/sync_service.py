"""数据库同步服务（纯逻辑，无 Streamlit 依赖，可单测）。

两类同步能力：

1. **时间戳增量同步**（`DatabaseSync`）：传感器 4 表 + 操作日志，
   基于最大时间戳双向增量，行级幂等去重。供「数据库同步」页手动触发。
2. **管理表按唯一键补齐**（`sync_management_tables`）：大棚 / 设备 / 用户 /
   用户-大棚授权，按业务唯一键（name / device_key / username）双向补齐，
   并维护跨库 **外键 id 映射**（两库自增 id 可能不同）。

`startup_sync()` 是程序启动时的自动同步入口（main.py 在网关启动前调用）：
管理表补齐 → 拿到 id 映射 → 传感器/日志增量同步（外键经映射转换），
保证故障转移期间写入备库的数据（含新注册的大棚/设备/用户）完整合并回主库。
"""

import re
import struct
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote_plus

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from smart_farm.data.models import (
    SENSOR_MODELS,
    AirTemperatureHumidity,
    Device,
    Greenhouse,
    LightIntensity,
    OperationLog,
    SoilMoisture,
    SoilNutrient,
    User,
    UserGreenhouse,
)

# (表名, 模型, 时间戳列) —— 时间戳增量同步的表集合
SYNC_TABLES = [
    ("air_temperature_humidity", AirTemperatureHumidity, "timestamp"),
    ("soil_moisture", SoilMoisture, "timestamp"),
    ("soil_nutrient", SoilNutrient, "timestamp"),
    ("light_intensity", LightIntensity, "timestamp"),
    ("operation_logs", OperationLog, "log_time"),
]


def validate_database_inputs(host: str, port: int, name: str, user: str, password: str) -> list[str]:
    """校验数据库连接参数（对齐旧版），返回错误列表（空 = 通过）。"""
    errors: list[str] = []
    if not re.match(r"^(\d{1,3}\.){3}\d{1,3}$|^localhost$|^[\w.-]+$", host):
        errors.append("无效的主机地址格式")
    try:
        port_num = int(port)
    except (TypeError, ValueError):
        errors.append("端口号必须是数字")
        return errors
    if not (1 <= port_num <= 65535):
        errors.append("端口号必须在 1-65535 之间")
    if not re.match(r"^[a-zA-Z0-9_]+$", name):
        errors.append("数据库名称只能包含字母、数字和下划线")
    if not user:
        errors.append("用户名不能为空")
    if password is None:
        errors.append("密码不能为 None")
    return errors


def build_mysql_url(host: str, port: int, name: str, user: str, password: str) -> str:
    """构造 mysql+pymysql URL（密码经 quote_plus 转义）。"""
    return f"mysql+pymysql://{user}:{quote_plus(password)}@{host}:{port}/{name}"


def _engine_for(url: str):
    """同步用引擎（MySQL 带 utf8mb4 与连接超时，与 data.database 行为对齐）。"""
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if url.startswith("mysql"):
        kwargs["connect_args"] = {"connect_timeout": 5, "charset": "utf8mb4"}
    return create_engine(url, **kwargs)


def _test_url(url: str) -> tuple[bool, str]:
    """探测 URL 可达性（SELECT 1）。"""
    try:
        eng = _engine_for(url)
        try:
            with eng.connect() as conn:
                conn.execute(select(1))
            return True, ""
        finally:
            eng.dispose()
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _norm_value(v):
    """跨方言归一化：MySQL FLOAT(32位)/DATETIME(秒级) 与 SQLite REAL(64位)/微秒 不对称。

    同一行往返两库后值有损（如 44.4378163512 → 44.43781661987305、
    20:51:23.289144 → 20:51:23），若直接按键比较会永不相等导致重复同步。
    统一按低精度视角归一：float 取 float32 表示、datetime 去微秒。
    """
    if isinstance(v, float):
        return struct.unpack("f", struct.pack("f", v))[0]
    if isinstance(v, datetime):
        return v.replace(microsecond=0)
    return v


class DatabaseSync:
    """时间戳增量双向同步器（cloud_url ↔ local_url 为对等的两个连接串）。"""

    def __init__(self, cloud_url: str, local_url: str):
        self.cloud_url = cloud_url
        self.local_url = local_url
        self._cloud_engine = _engine_for(cloud_url)
        self._local_engine = _engine_for(local_url)

    def test_connection(self, url: Optional[str] = None) -> tuple[bool, str]:
        """测试连接：返回 (是否成功, 信息)。"""
        return _test_url(url or self.cloud_url)

    @staticmethod
    def _rows_after(session: Session, model, after: Optional[datetime], ts_col: str = "timestamp"):
        """拉取 after 之后（含相等）的行；after=None 拉全表（升序稳定）。"""
        stmt = select(model)
        ts_field = getattr(model, ts_col)
        if after is not None:
            stmt = stmt.where(ts_field >= after)  # >= 修复同一最大时间戳下多批次数据漏同步
        stmt = stmt.order_by(ts_field.asc(), model.id.asc())
        return session.execute(stmt).scalars().all()

    @staticmethod
    def _row_key(row, ts_col: str = "timestamp", gh_map: Optional[dict] = None) -> tuple:
        """行去重键：(时间戳, 各非 id 列值)。greenhouse_id 经映射转换、
        值经跨方言归一化（float32 / 秒级时间戳）后再比较。"""
        vals = []
        for c in row.__table__.columns:
            if c.name == "id":
                continue
            v = getattr(row, c.name)
            if c.name == "greenhouse_id" and gh_map and v is not None:
                v = gh_map.get(v, v)
            vals.append(str(_norm_value(v)))
        return (_norm_value(getattr(row, ts_col)), tuple(vals))

    @staticmethod
    def _copy_rows(session: Session, model, rows, ts_col: str = "timestamp", skip_keys: Optional[set] = None, gh_map: Optional[dict] = None) -> int:
        """幂等复制：跳过目标库已存在的行（按 时间戳+值 去重）；greenhouse_id
        经映射转换、值经跨方言归一化（保证两侧存储值一致，不因精度差漂移）。"""
        count = 0
        for row in rows:
            if skip_keys is not None and DatabaseSync._row_key(row, ts_col, gh_map) in skip_keys:
                continue
            kwargs = {
                c.name: _norm_value(getattr(row, c.name))
                for c in model.__table__.columns
                if c.name != "id"
            }
            if gh_map and kwargs.get("greenhouse_id") is not None:
                kwargs["greenhouse_id"] = gh_map.get(kwargs["greenhouse_id"], kwargs["greenhouse_id"])
            session.add(model(**kwargs))
            count += 1
        return count

    def sync_table_data(
        self,
        table_name: str,
        model=None,
        ts_column: Optional[str] = None,
        c2l_map: Optional[dict] = None,
        l2c_map: Optional[dict] = None,
    ) -> dict[str, Any]:
        """同步单表：返回 {table, cloud_to_local, local_to_cloud, conflicts}。

        c2l_map / l2c_map：greenhouse_id 跨库映射（cloud→local 方向用 c2l_map，
        反向用 l2c_map）；不传时视为两库 id 一致（手动同步场景保持旧行为）。
        """
        model = model or SENSOR_MODELS[table_name]
        ts_col = ts_column or next(
            (t for n, _, t in SYNC_TABLES if n == table_name), "timestamp"
        )
        stats: dict[str, Any] = {
            "table": table_name, "cloud_to_local": 0, "local_to_cloud": 0, "conflicts": 0,
        }

        with Session(self._cloud_engine) as cloud, Session(self._local_engine) as local:
            # 全量行键对账：两侧数据集各自求键（时间戳+值，greenhouse_id 经
            # 映射转换到目标侧语义），差集双向补齐。彻底覆盖历史空洞场景
            # （两库在启用同步前各自独立产生过历史数据，按 max 增量会漏）。
            # 数据量为万行级（30 分钟粒度 × 多棚），全表拉取秒级完成。
            cloud_rows = self._rows_after(cloud, model, None, ts_col)
            local_rows = self._rows_after(local, model, None, ts_col)
            cloud_keys = {self._row_key(r, ts_col, c2l_map) for r in cloud_rows}  # 目标=备库
            local_keys = {self._row_key(r, ts_col, l2c_map) for r in local_rows}  # 目标=主库

            if cloud_rows:
                new_rows = [r for r in cloud_rows if self._row_key(r, ts_col, c2l_map) not in local_keys]
                stats["cloud_to_local"] = self._copy_rows(local, model, new_rows, ts_col, None, c2l_map)
                local.commit()

            if local_rows:
                new_rows = [r for r in local_rows if self._row_key(r, ts_col, l2c_map) not in cloud_keys]
                stats["local_to_cloud"] = self._copy_rows(cloud, model, new_rows, ts_col, None, l2c_map)
                cloud.commit()

        return stats

    def sync_all_data(self, c2l_map: Optional[dict] = None, l2c_map: Optional[dict] = None) -> list[dict[str, Any]]:
        """同步全部传感器表 + 操作日志，返回各表统计列表。"""
        return [
            self.sync_table_data(name, model, ts_col, c2l_map, l2c_map)
            for name, model, ts_col in SYNC_TABLES
        ]

    def close(self) -> None:
        self._cloud_engine.dispose()
        self._local_engine.dispose()


# ----------------------------- 管理表按唯一键补齐 -----------------------------

def _copy_by_key(src: Session, dst: Session, model, key_attr: str, id_map: Optional[dict] = None, fk: Optional[tuple[str, dict]] = None) -> int:
    """按业务唯一键补齐：src 有而 dst 无的行插入 dst，返回新增数。

    - fk=(字段名, 映射)：外键值经映射转换（跨库自增 id 不同）。
    - id_map：记录 src.id → dst.id 的完整映射（新增与已存在均记录），
      供后续外键转换与关联表同步使用。
    """
    existing_keys = set(dst.execute(select(getattr(model, key_attr))).scalars())
    added = 0
    for row in src.execute(select(model)).scalars():
        key = getattr(row, key_attr)
        if key in existing_keys:
            if id_map is not None:
                dst_row = dst.execute(
                    select(model).where(getattr(model, key_attr) == key)
                ).scalars().first()
                if dst_row is not None:
                    id_map[row.id] = dst_row.id
            continue
        kwargs = {c.name: getattr(row, c.name) for c in model.__table__.columns if c.name != "id"}
        if fk:
            field, mapping = fk
            if kwargs.get(field) is not None:
                kwargs[field] = mapping.get(kwargs[field], kwargs[field])
        new = model(**kwargs)
        dst.add(new)
        dst.flush()
        if id_map is not None:
            id_map[row.id] = new.id
        existing_keys.add(key)
        added += 1
    dst.commit()
    return added


def _copy_links(src: Session, dst: Session, user_map: dict, gh_map: dict) -> int:
    """user_greenhouse 双向补齐（按映射后的 (user_id, greenhouse_id) 去重）。"""
    existing = {
        (r.user_id, r.greenhouse_id)
        for r in dst.execute(select(UserGreenhouse)).scalars()
    }
    added = 0
    for row in src.execute(select(UserGreenhouse)).scalars():
        uid = user_map.get(row.user_id, row.user_id)
        gid = gh_map.get(row.greenhouse_id, row.greenhouse_id)
        if (uid, gid) in existing:
            continue
        dst.add(UserGreenhouse(user_id=uid, greenhouse_id=gid, granted_at=row.granted_at))
        existing.add((uid, gid))
        added += 1
    dst.commit()
    return added


def sync_management_tables(primary_engine, fallback_engine) -> tuple[dict, dict, dict]:
    """管理表（greenhouse/device/user/user_greenhouse）按唯一键双向补齐。

    已存在的同键实体保留各自现状（不更新字段）；外键 greenhouse_id / 关联表
    经跨库 id 映射转换，保证故障转移期间新建的大棚/设备/用户完整合并。

    Returns:
        (统计, f2p, p2f)：f2p/p2f 为 greenhouse id 的 备库→主库 / 主库→备库 映射，
        供传感器数据同步转换 greenhouse_id 外键。
    """
    stats = {"greenhouse": 0, "device": 0, "user": 0, "user_greenhouse": 0}
    f2p, p2f = {}, {}  # greenhouse id：备→主 / 主→备
    with Session(primary_engine) as p, Session(fallback_engine) as f:
        # 1. 大棚（name 唯一）——先同步以建立 id 映射
        stats["greenhouse"] += _copy_by_key(f, p, Greenhouse, "name", id_map=f2p)
        stats["greenhouse"] += _copy_by_key(p, f, Greenhouse, "name", id_map=p2f)
        # 2. 设备（device_key 唯一；greenhouse_id 经映射）
        stats["device"] += _copy_by_key(f, p, Device, "device_key", fk=("greenhouse_id", f2p))
        stats["device"] += _copy_by_key(p, f, Device, "device_key", fk=("greenhouse_id", p2f))
        # 3. 用户（username 唯一；旧字段 greenhouse_id 经映射）
        u_f2p, u_p2f = {}, {}
        stats["user"] += _copy_by_key(f, p, User, "username", id_map=u_f2p, fk=("greenhouse_id", f2p))
        stats["user"] += _copy_by_key(p, f, User, "username", id_map=u_p2f, fk=("greenhouse_id", p2f))
        # 4. 用户-大棚授权（映射后的 id 对去重）
        stats["user_greenhouse"] += _copy_links(f, p, u_f2p, f2p)
        stats["user_greenhouse"] += _copy_links(p, f, u_p2f, p2f)
    return stats, f2p, p2f


# ----------------------------- 启动同步编排 -----------------------------

_last_sync_result: dict = {}


def last_sync_result() -> dict:
    """最近一次启动同步的结果（系统监控页展示）。"""
    return dict(_last_sync_result)


def startup_sync(primary_url: Optional[str] = None, fallback_url: Optional[str] = None) -> dict:
    """程序启动时主备库自动同步（在 IoT 网关启动前执行，保证数据先于业务合并）。

    流程：管理表按唯一键双向补齐（建立外键 id 映射）→ 传感器/操作日志表
    按时间戳双向增量（greenhouse_id 经映射转换）。

    跳过条件（不视为错误，原因写入结果）：运行于备库（主库不可达）、
    未配置备库、主备同库、任一库不可达。

    显式传参供测试注入；缺省从 `data.database` 读取主备 URL 与运行状态。
    """
    global _last_sync_result
    from smart_farm.data import database as db_mod

    def _finish(result: dict) -> dict:
        global _last_sync_result
        _last_sync_result = result
        return result

    explicit = primary_url is not None or fallback_url is not None
    if not explicit:
        primary_url, fallback_url = db_mod.get_database_urls()
        if db_mod.active_database_info()["role"] != "primary":
            return _finish({"status": "skipped", "reason": "主库不可达（当前运行于备用库），跳过启动同步"})

    if not fallback_url:
        return _finish({"status": "skipped", "reason": "未配置备用数据库（DATABASE_FALLBACK_URL）"})
    if fallback_url == primary_url:
        return _finish({"status": "skipped", "reason": "主备库相同，无需同步"})

    for url, label in ((primary_url, "主库"), (fallback_url, "备库")):
        ok, err = _test_url(url)
        if not ok:
            return _finish({"status": "skipped", "reason": f"{label}不可达：{err}"})

    # 1. 管理表补齐 + 外键映射
    p_eng, f_eng = _engine_for(primary_url), _engine_for(fallback_url)
    try:
        mgmt, f2p, p2f = sync_management_tables(p_eng, f_eng)
    finally:
        p_eng.dispose()
        f_eng.dispose()

    # 2. 传感器/日志增量（cloud=主库, local=备库；主→备用 p2f，备→主用 f2p）
    sync = DatabaseSync(primary_url, fallback_url)
    try:
        tables = sync.sync_all_data(c2l_map=p2f, l2c_map=f2p)
    finally:
        sync.close()

    total = {
        "primary_to_fallback": sum(t["cloud_to_local"] for t in tables),
        "fallback_to_primary": sum(t["local_to_cloud"] for t in tables),
    }
    return _finish({
        "status": "ok",
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tables": {
            t["table"]: {"primary_to_fallback": t["cloud_to_local"], "fallback_to_primary": t["local_to_cloud"]}
            for t in tables
        },
        "management": mgmt,
        "total": total,
    })
