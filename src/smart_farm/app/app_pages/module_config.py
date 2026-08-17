"""模块配置页面（管理员专属）。

对齐旧库 module_config_ui：
- 按分类 tabs 展示各模块 expander（描述/依赖/权限）
- 「启用模块」toggle 实时写 JSON（含依赖检查：禁用被依赖模块被拒绝）
- 「仅管理员可用」toggle
- 状态概览 metric + 保存/恢复默认
启停经 `module_manager` 持久化到 module_config.json，main.py 按此真过滤导航。
"""

import pandas as pd
import streamlit as st

from smart_farm.app.guards import require_admin
from smart_farm.services import module_manager as mm

st.title("模块配置")
if not require_admin():
    st.stop()

manager = mm.get_module_manager()

# 分类 tabs
categories = manager.get_categories()
tabs = st.tabs(categories)
for tab, category in zip(tabs, categories):
    with tab:
        modules = manager.get_modules(category=category)
        for module in modules:
            with st.expander(f"{module.display_name}（{'启用' if module.enabled else '已禁用'}）"):
                st.caption(module.description)
                st.caption(f"依赖：{', '.join(module.dependencies) if module.dependencies else '无'}")
                enabled = st.toggle("启用模块", value=module.enabled, key=f"toggle_{module.name}")
                if enabled != module.enabled:
                    if enabled:
                        manager.enable_module(module.name)
                        st.rerun()
                    elif not manager.disable_module(module.name):
                        st.error(f"无法禁用 {module.display_name}：仍有启用模块依赖它。")
                        st.rerun()
                    else:
                        st.rerun()
                admin_only = st.toggle("仅管理员可用", value=module.admin_only, key=f"admin_only_{module.name}")
                if admin_only != module.admin_only:
                    manager.set_admin_only(module.name, admin_only)
                    st.rerun()

# 状态概览
st.subheader("状态概览")
all_modules = manager.get_modules()
enabled_count = sum(1 for m in all_modules if m.enabled)
admin_count = sum(1 for m in all_modules if m.admin_only)
c1, c2, c3, c4 = st.columns(4)
c1.metric("总模块", len(all_modules))
c2.metric("已启用", enabled_count)
c3.metric("已禁用", len(all_modules) - enabled_count)
c4.metric("管理员专用", admin_count)

df = pd.DataFrame([{
    "模块": m.display_name,
    "路由键": m.name,
    "分类": m.category,
    "启用状态": "启用" if m.enabled else "已禁用",
    "权限": "管理员" if m.admin_only else "普通",
} for m in all_modules])
st.dataframe(df, width="stretch")

c1, c2 = st.columns(2)
with c1:
    if st.button("保存配置", type="primary", icon=":material/save:"):
        manager.save_config()
        st.success("配置已保存。")
with c2:
    if st.button("恢复默认配置", icon=":material/restore:"):
        manager.restore_defaults()
        st.rerun()
