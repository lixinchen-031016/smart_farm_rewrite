"""模块配置页面（管理员专属）。声明式模块注册表，统一入口、消除矛盾。

说明：本版以「代码内声明式注册表」呈现各功能模块与启用状态，替代旧版
"默认禁用却可 URL 直访"的矛盾入口。后续如需 DB 开关，可在此注册表基础上
增加 `module_enabled` 表并由 `main.py` 动态过滤菜单。
"""

import pandas as pd
import streamlit as st

from smart_farm.app.guards import require_admin

# 声明式模块注册表（单一事实来源）
MODULE_REGISTRY = [
    ("综合监控仪表板", "dashboard", "启用", "实时指标卡 + 趋势"),
    ("数据概览", "data_overview", "启用", "DB 浏览 + 上传/导出"),
    ("数据清洗与异常", "data_cleaning", "启用", "异常检测 + 清洗"),
    ("数据分析", "data_analysis", "启用", "描述统计/相关性/聚合"),
    ("可视化", "visualization", "启用", "折线/直方图/箱线/散点"),
    ("本地数据预测", "prediction", "启用", "Prophet/SARIMA/naive"),
    ("自动化决策", "decision", "启用", "规则引擎"),
    ("用户管理", "user_management", "管理员", "用户与角色"),
    ("操作日志", "log_viewer", "管理员", "审计日志"),
    ("系统监控", "system_monitoring", "管理员", "数据量与质量"),
    ("模块配置", "module_config", "管理员", "本页"),
    ("备份与恢复", "backup_restore", "管理员", "数据导出/导入"),
]

st.title("模块配置")
if not require_admin():
    st.stop()

st.caption("声明式模块注册表：统一入口与启用状态，避免「禁用却可直访」的矛盾。")
df = pd.DataFrame(MODULE_REGISTRY, columns=["模块", "路由键", "启用状态", "说明"])
st.dataframe(df, width="stretch")
st.info("所有页面均经 `main.py` 的 `st.navigation` 统一路由，无独立可直访 URL 入口。")
