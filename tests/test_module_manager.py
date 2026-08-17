"""module_manager 测试（临时 JSON 文件隔离）。"""

import pytest

from smart_farm.services import module_manager as mm


@pytest.fixture()
def manager(tmp_path):
    return mm.ModuleManager(config_file=tmp_path / "modules.json")


def test_defaults_loaded(manager):
    assert manager.is_enabled("integrated_dashboard")
    assert manager.is_enabled("data_cleaning")
    assert manager.get_modules(enabled_only=True)
    assert len(manager.get_modules()) == len(mm.DEFAULT_MODULES)


def test_enable_disable_roundtrip(manager):
    assert manager.disable_module("data_visualization") is True
    assert not manager.is_enabled("data_visualization")
    assert manager.enable_module("data_visualization") is True
    assert manager.is_enabled("data_visualization")


def test_disable_dependency_blocked(manager):
    # data_analysis 依赖 data_overview 且已启用 → 禁用 data_overview 应被拒绝
    assert manager.is_enabled("data_overview")
    assert manager.disable_module("data_overview") is False
    assert manager.is_enabled("data_overview")


def test_admin_only_filtering(manager):
    user_modules = {m.name for m in manager.enabled_modules_for_user(is_admin=False)}
    admin_modules = {m.name for m in manager.enabled_modules_for_user(is_admin=True)}
    assert "user_management" not in user_modules
    assert "user_management" in admin_modules
    assert "integrated_dashboard" in user_modules


def test_save_and_reload(tmp_path):
    path = tmp_path / "modules.json"
    m1 = mm.ModuleManager(config_file=path)
    m1.disable_module("debug_info")
    m1.set_admin_only("log_viewer", True)
    m2 = mm.ModuleManager(config_file=path)
    assert not m2.is_enabled("debug_info")
    assert m2.get_modules()[0].admin_only or True  # 至少不报错
    # 精确验证 reload
    m2_log = [m for m in m2.get_modules() if m.name == "log_viewer"][0]
    assert m2_log.admin_only is True


def test_restore_defaults(manager):
    manager.disable_module("debug_info")
    manager.restore_defaults()
    assert manager.is_enabled("debug_info") is False  # 默认即禁用
    assert manager.is_enabled("integrated_dashboard")


def test_unknown_module_returns_false(manager):
    assert manager.enable_module("not_exist") is False
    assert manager.disable_module("not_exist") is False
    assert manager.set_admin_only("not_exist", True) is False
