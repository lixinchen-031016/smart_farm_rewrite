"""log_analysis_service 测试（纯函数，模拟日志对象）。"""

from datetime import datetime, timedelta

import pytest

from smart_farm.services import log_analysis_service as las


class FakeLog:
    def __init__(self, level, username, action, time=None, details=""):
        self.log_level = level
        self.username = username
        self.action_type = action
        self.log_time = time or datetime.now()
        self.action_details = details


def _logs():
    now = datetime.now()
    return [
        FakeLog("INFO", "alice", "登录", now - timedelta(hours=1)),
        FakeLog("INFO", "alice", "查询", now - timedelta(hours=2)),
        FakeLog("ERROR", "alice", "预测", now - timedelta(hours=3)),
        FakeLog("ERROR", "bob", "删除", now - timedelta(hours=4)),
        FakeLog("INFO", "bob", "备份", now - timedelta(hours=5)),
        FakeLog("ERROR", "bob", "预测", now - timedelta(days=3)),  # 超出 24h
    ]


def test_error_stats_only_error_recent():
    stats = las.analyze_error_stats(_logs(), hours=24)
    assert stats == {"预测": 1, "删除": 1}  # 3 天前的不算


def test_user_activity():
    activity = las.analyze_user_activity(_logs(), hours=24)
    assert activity["alice"]["total_actions"] == 3
    assert activity["alice"]["error_actions"] == 1
    assert activity["alice"]["error_rate_pct"] == pytest.approx(33.33, abs=0.1)
    assert activity["bob"]["total_actions"] == 2  # 3 天前的不算


def test_log_trends():
    trends = las.analyze_log_trends(_logs(), days=7)
    assert trends  # 至少一天有数据
    # 所有 key 都是日期字符串
    assert all(len(k) == 10 for k in trends)


def test_top_actions():
    top = las.analyze_top_actions(_logs(), limit=10, hours=24)
    names = [t for t, _ in top]
    assert "预测" in names
    assert "删除" in names
    # 降序
    counts = [c for _, c in top]
    assert counts == sorted(counts, reverse=True)


def test_error_peaks():
    peaks = las.analyze_error_peaks(_logs(), hours=24)
    assert sum(peaks.values()) == 2
    assert all(":" in k for k in peaks)
