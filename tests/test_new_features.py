"""本轮补齐功能测试：系统监控 IO / 交叉透视 / 时间动画 / 限流后端 / seed 幂等。"""

from contextlib import contextmanager
from datetime import datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import smart_farm.data.seed as seed_mod
from smart_farm.data.models import Base
from smart_farm.services import analysis_service as az
from smart_farm.services import auth_service as auth
from smart_farm.services import system_service as ss
from smart_farm.services import visualization_service as vs

# ----------------------------- system_service：网络 / 磁盘 IO -----------------------------


def test_collect_network_io_shape():
    if not ss.is_psutil_available():
        pytest.skip("psutil 未安装")
    net = ss.collect_network_io(interval=0)
    assert "send_rate_mb_s" in net
    assert "recv_rate_mb_s" in net
    assert net["bytes_recv"] >= 0


def test_collect_disk_io_shape():
    if not ss.is_psutil_available():
        pytest.skip("psutil 未安装")
    disk = ss.collect_disk_io()
    # 部分平台不支持时返回空 dict，不抛异常
    if disk:
        assert disk["read_count"] >= 0
        assert disk["read_mb"] >= 0


def test_collect_disk_partitions_shape():
    if not ss.is_psutil_available():
        pytest.skip("psutil 未安装")
    parts = ss.collect_disk_partitions()
    for p in parts:
        assert {"设备", "挂载点", "文件系统", "使用率 (%)"} <= set(p)


def test_collect_network_io_without_psutil(monkeypatch):
    monkeypatch.setattr(ss, "PSUTIL_AVAILABLE", False)
    assert ss.collect_network_io() == {}
    assert ss.collect_disk_io() == {}
    assert ss.collect_disk_partitions() == []


# ----------------------------- analysis_service：交叉透视 -----------------------------


@pytest.fixture()
def cross_df():
    ts = pd.date_range("2026-01-01", periods=200, freq="3h")
    return pd.DataFrame(
        {
            "timestamp": ts,
            "星期": ts.dayofweek,
            "小时": ts.hour,
            "value": [20.0 + 5 * ((t.hour % 24) / 24) for t in ts],
        }
    )


def test_cross_pivot_shape(cross_df):
    pivot = az.cross_pivot(cross_df, "星期", "小时", "value", "平均值")
    assert pivot.shape[0] == cross_df["星期"].nunique()
    assert pivot.shape[1] == cross_df["小时"].nunique()
    # 值为各单元格均值，应落于数据范围内
    assert pivot.stack().min() >= cross_df["value"].min() - 0.01
    assert pivot.stack().max() <= cross_df["value"].max() + 0.01


def test_cross_pivot_validation(cross_df):
    with pytest.raises(ValueError, match="列不存在"):
        az.cross_pivot(cross_df, "星期", "不存在", "value", "平均值")
    with pytest.raises(ValueError, match="数值列"):
        az.cross_pivot(cross_df, "星期", "小时", "timestamp", "平均值")
    with pytest.raises(ValueError, match="聚合方式"):
        az.cross_pivot(cross_df, "星期", "小时", "value", "中位数")
    with pytest.raises(ValueError, match="不能相同"):
        az.cross_pivot(cross_df, "小时", "小时", "value", "平均值")


def test_cross_pivot_insight_text(cross_df):
    pivot = az.cross_pivot(cross_df, "星期", "小时", "value", "平均值")
    insight = az.cross_pivot_insight(pivot, "value", row_names={0: "周一"})
    assert "峰值" in insight and "谷值" in insight
    assert az.cross_pivot_insight(pd.DataFrame(), "value") is None


# ----------------------------- visualization_service：时间动画 -----------------------------


def _anim_df(n=600):
    ts = pd.date_range("2026-01-01", periods=n, freq="1h")
    return pd.DataFrame({"timestamp": ts, "value": [10.0 + (i % 24) for i in range(n)]})


def test_time_animation_chart_frames():
    fig = vs.create_time_animation_chart(_anim_df(), "timestamp", "value")
    assert len(fig.frames) >= 15  # 帧数落在下限
    assert len(fig.frames) <= 61  # 帧数不超过上限+1
    # 滑块与播放按钮存在
    assert fig.layout.sliders
    assert fig.layout.updatemenus


def test_time_animation_chart_sparse_uses_raw_points():
    df = _anim_df(10)
    fig = vs.create_time_animation_chart(df, "timestamp", "value")
    assert len(fig.frames) == 10


def test_time_animation_chart_errors():
    df = _anim_df(10)
    with pytest.raises(ValueError, match="时间列"):
        vs.create_time_animation_chart(df, "不存在", "value")
    with pytest.raises(ValueError, match="为空"):
        vs.create_time_animation_chart(df.iloc[:0], "timestamp", "value")
    # 单点无法生成动画
    with pytest.raises(ValueError, match="不足以生成动画"):
        vs.create_time_animation_chart(df.iloc[:1], "timestamp", "value")


# ----------------------------- auth_service：可插拔限流后端 -----------------------------


class _FakeBackend:
    """协议实现示例：外部计数，验证 LoginLimiter 真正委托后端。"""

    def __init__(self):
        self.counts: dict[str, int] = {}

    def recent_failure_count(self, key: str) -> int:
        return self.counts.get(key, 0)

    def register_failure(self, key: str) -> None:
        self.counts[key] = self.counts.get(key, 0) + 1

    def reset(self, key: str) -> None:
        self.counts.pop(key, None)


def test_limiter_delegates_to_custom_backend():
    backend = _FakeBackend()
    limiter = auth.LoginLimiter(max_attempts=3, window_seconds=30, backend=backend)
    limiter.register_failure("u1")
    limiter.register_failure("u1")
    assert not limiter.is_blocked("u1")
    limiter.register_failure("u1")
    assert limiter.is_blocked("u1")
    limiter.reset("u1")
    assert not limiter.is_blocked("u1")
    assert backend.counts.get("u1") is None  # reset 已透传后端


def test_in_memory_backend_window_expiry():
    backend = auth.InMemoryRateLimitBackend(window_seconds=30)
    # 记录时间早于窗口 → 计数时被清理
    backend._store["u"] = [datetime.now() - timedelta(seconds=31)]
    assert backend.recent_failure_count("u") == 0


class _StubRedis:
    """最小 Redis 客户端桩：实现 zadd/zcard/zremrangebyscore/expire/delete。"""

    def __init__(self):
        self.data: dict[str, dict[str, float]] = {}

    def _k(self, key):
        return self.data.setdefault(key, {})

    def zadd(self, key, mapping):
        self._k(key).update(mapping)

    def zcard(self, key):
        return len(self._k(key))

    def zremrangebyscore(self, key, lo, hi):
        store = self._k(key)
        for m in [m for m, s in store.items() if lo <= s <= hi]:
            store.pop(m)

    def expire(self, key, ttl):
        pass

    def delete(self, key):
        self.data.pop(key, None)


def test_redis_backend_counts_and_prunes():
    import time as _time

    stub = _StubRedis()
    backend = auth.RedisRateLimitBackend(stub, window_seconds=30)
    backend.register_failure("u")
    backend.register_failure("u")
    assert backend.recent_failure_count("u") == 2
    # 注入一条已过期记录（31 秒前），应被清理
    backend.client.zadd(backend._key("u"), {"old": _time.time() - 31})
    assert backend.recent_failure_count("u") == 2
    backend.reset("u")
    assert backend.recent_failure_count("u") == 0


# ----------------------------- seed：幂等 -----------------------------


@pytest.fixture()
def mem_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    yield s
    s.close()
    engine.dispose()


def test_seed_idempotent(monkeypatch, mem_session):
    monkeypatch.setattr(seed_mod, "init_db", lambda: None)

    @contextmanager
    def fake_get_session():
        yield mem_session

    monkeypatch.setattr(seed_mod, "get_session", fake_get_session)

    added1 = seed_mod.seed(days=2, interval_minutes=60)
    assert added1 > 0
    # 立即重跑：网格一致 → 增量为 0（幂等）
    added2 = seed_mod.seed(days=2, interval_minutes=60)
    assert added2 == 0
