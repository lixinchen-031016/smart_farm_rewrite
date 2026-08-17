"""模块配置服务（纯逻辑，无 Streamlit 依赖，可单测）。

对齐旧版 `utils/module_manager.py`：
- 声明式默认注册表（20 项，含依赖关系）
- JSON 持久化启停 + admin_only
- `disable_module` 依赖检查：被任一启用模块依赖时拒绝禁用
- 导航过滤：`enabled_modules_for_user` 供 main.py 真过滤（修复旧库"仅菜单隐藏"矛盾）
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# 模块默认注册表：name -> (display_name, description, category, icon, admin_only, enabled, dependencies)
DEFAULT_MODULES: dict[str, dict] = {
    "integrated_dashboard": {"name": "综合监控仪表板", "desc": "实时指标卡 + 趋势", "category": "核心",
                              "icon": "dashboard", "admin_only": False, "enabled": True, "deps": []},
    "data_overview": {"name": "数据概览", "desc": "DB 浏览 + 上传/导出", "category": "数据",
                      "icon": "table_view", "admin_only": False, "enabled": True, "deps": []},
    "data_cleaning": {"name": "数据清洗与异常", "desc": "规则模板 + 异常检测", "category": "数据",
                      "icon": "cleaning_services", "admin_only": False, "enabled": True, "deps": ["data_overview"]},
    "data_analysis": {"name": "数据分析", "desc": "描述统计/相关性/聚合", "category": "数据",
                      "icon": "analytics", "admin_only": False, "enabled": True, "deps": ["data_overview"]},
    "advanced_analysis": {"name": "高级分析", "desc": "分组聚合 + 智能洞察", "category": "数据",
                          "icon": "insights", "admin_only": False, "enabled": True, "deps": ["data_overview"]},
    "data_visualization": {"name": "可视化", "desc": "折线/直方图/箱线/散点", "category": "数据",
                           "icon": "bar_chart", "admin_only": False, "enabled": True, "deps": ["data_overview"]},
    "data_prediction": {"name": "本地数据预测", "desc": "Prophet/SARIMA/RF", "category": "智能",
                        "icon": "timeline", "admin_only": False, "enabled": True, "deps": ["data_overview"]},
    "automated_decision": {"name": "自动化决策", "desc": "规则引擎", "category": "智能",
                           "icon": "psychology", "admin_only": False, "enabled": True, "deps": []},
    "history_reports": {"name": "历史报告", "desc": "预测归档浏览", "category": "智能",
                        "icon": "history", "admin_only": True, "enabled": True, "deps": ["data_prediction"]},
    "user_management": {"name": "用户管理", "desc": "用户与角色", "category": "管理",
                        "icon": "group", "admin_only": True, "enabled": True, "deps": []},
    "system_monitoring": {"name": "系统监控", "desc": "数据量与 psutil", "category": "管理",
                          "icon": "monitor_heart", "admin_only": True, "enabled": True, "deps": []},
    "log_viewer": {"name": "操作日志", "desc": "审计日志", "category": "管理",
                   "icon": "receipt_long", "admin_only": True, "enabled": True, "deps": []},
    "data_backup": {"name": "备份与恢复", "desc": "数据导出/导入", "category": "管理",
                    "icon": "save", "admin_only": True, "enabled": True, "deps": []},
    "sync_databases": {"name": "数据库同步", "desc": "云端/本地增量双向", "category": "管理",
                       "icon": "sync", "admin_only": True, "enabled": True, "deps": []},
    "use_instruction": {"name": "使用说明", "desc": "操作指南与 FAQ", "category": "帮助",
                        "icon": "menu_book", "admin_only": False, "enabled": True, "deps": []},
    "module_config": {"name": "模块配置", "desc": "本页", "category": "管理",
                      "icon": "tune", "admin_only": True, "enabled": True, "deps": []},
    "debug_info": {"name": "调试信息", "desc": "DEBUG_MODE 门控", "category": "管理",
                   "icon": "bug_report", "admin_only": True, "enabled": False, "deps": []},
}

# 导航顺序（侧边栏展示顺序）
MODULE_ORDER = [
    "integrated_dashboard", "data_overview", "data_cleaning", "data_analysis",
    "advanced_analysis", "data_visualization", "data_prediction", "automated_decision",
    "history_reports", "user_management", "system_monitoring", "log_viewer",
    "data_backup", "sync_databases", "use_instruction", "module_config", "debug_info",
]

# 页面路由 key（st.Page title 映射：页面文件路由键 -> 页面标题）
MODULE_TO_PAGE = {
    "integrated_dashboard": "dashboard",
    "data_overview": "data_overview",
    "data_cleaning": "data_cleaning",
    "data_analysis": "data_analysis",
    "advanced_analysis": "advanced_analysis",
    "data_visualization": "visualization",
    "data_prediction": "prediction",
    "automated_decision": "decision",
    "history_reports": "history_reports",
    "user_management": "user_management",
    "system_monitoring": "system_monitoring",
    "log_viewer": "log_viewer",
    "data_backup": "backup_restore",
    "sync_databases": "sync_databases",
    "use_instruction": "use_instruction",
    "module_config": "module_config",
    "debug_info": "debug_info",
}


@dataclass
class ModuleConfig:
    name: str
    display_name: str
    description: str = ""
    category: str = "其他"
    icon: str = "apps"
    admin_only: bool = False
    enabled: bool = True
    dependencies: list[str] = field(default_factory=list)


class ModuleManager:
    """模块配置管理器：默认注册表 + JSON 覆盖 + 依赖检查。"""

    def __init__(self, config_file: Optional[Path] = None):
        self.config_file = config_file or Path(__file__).resolve().parents[3] / "module_config.json"
        self._modules: dict[str, ModuleConfig] = {}
        self._load_defaults()
        self._load_config()

    def _load_defaults(self) -> None:
        for key, cfg in DEFAULT_MODULES.items():
            self._modules[key] = ModuleConfig(
                name=key,
                display_name=cfg["name"],
                description=cfg["desc"],
                category=cfg["category"],
                icon=cfg["icon"],
                admin_only=cfg["admin_only"],
                enabled=cfg["enabled"],
                dependencies=list(cfg["deps"]),
            )

    def _load_config(self) -> None:
        if not self.config_file.exists():
            return
        try:
            data = json.loads(self.config_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # 修复：配置文件损坏时备份原始文件（防下次 save 覆盖丢失全部自定义），而非静默吞掉
            import shutil
            import time

            backup = self.config_file.with_suffix(f".corrupt.{int(time.time())}.json")
            try:
                shutil.copy2(self.config_file, backup)
            except OSError:
                pass
            return
        for key, entry in data.items():
            if key not in self._modules or not isinstance(entry, dict):
                continue
            module = self._modules[key]
            module.enabled = bool(entry.get("enabled", module.enabled))
            module.admin_only = bool(entry.get("admin_only", module.admin_only))

    def save_config(self) -> None:
        payload = {
            key: {"enabled": m.enabled, "admin_only": m.admin_only}
            for key, m in self._modules.items()
        }
        # 修复：原子写（tmp + rename），防止进程中断留下残缺 JSON
        tmp_path = self.config_file.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp_path.replace(self.config_file)

    def get_modules(self, category: Optional[str] = None, enabled_only: bool = False) -> list[ModuleConfig]:
        modules = list(self._modules.values())
        if category:
            modules = [m for m in modules if m.category == category]
        if enabled_only:
            modules = [m for m in modules if m.enabled]
        return sorted(modules, key=lambda m: MODULE_ORDER.index(m.name) if m.name in MODULE_ORDER else 999)

    def get_categories(self) -> list[str]:
        return sorted({m.category for m in self._modules.values()})

    def is_enabled(self, name: str) -> bool:
        module = self._modules.get(name)
        return bool(module and module.enabled)

    def enable_module(self, name: str) -> bool:
        module = self._modules.get(name)
        if not module:
            return False
        module.enabled = True
        self.save_config()
        return True

    def disable_module(self, name: str) -> bool:
        """禁用模块；若被任一启用模块依赖则拒绝（对齐旧版依赖检查）。"""
        module = self._modules.get(name)
        if not module:
            return False
        for other in self._modules.values():
            if other.enabled and name in other.dependencies:
                return False
        module.enabled = False
        self.save_config()
        return True

    def set_admin_only(self, name: str, admin_only: bool) -> bool:
        module = self._modules.get(name)
        if not module:
            return False
        module.admin_only = admin_only
        self.save_config()
        return True

    def enabled_modules_for_user(self, is_admin: bool = False) -> list[ModuleConfig]:
        """用户可见模块：启用 + （非 admin 时排除 admin_only）。供导航真过滤。"""
        return [m for m in self.get_modules(enabled_only=True) if is_admin or not m.admin_only]

    def restore_defaults(self) -> None:
        self._load_defaults()
        self.save_config()


def get_module_manager() -> ModuleManager:
    """全局单例（懒加载，避免测试间共享状态）。"""
    global _manager
    if _manager is None:
        _manager = ModuleManager()
    return _manager


_manager: Optional[ModuleManager] = None
