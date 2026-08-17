"""使用说明页面。展示功能说明 + FAQ + 生成 docx 说明书。"""

import streamlit as st

from smart_farm.services import docx_manual
from smart_farm.services.instruction_data import FAQ, SUPPORT, get_full_instruction

st.title("使用说明")
st.caption("智慧大棚数据管理平台：数据接入、清洗、分析、预测与运维一体化。")

instruction = get_full_instruction()

exp1, exp2, exp3, exp4 = st.expander("核心功能", expanded=False), \
    st.expander("智能分析与预测", expanded=False), \
    st.expander("系统管理", expanded=False), \
    st.expander("常见问题", expanded=True)

for exp, category in ((exp1, "核心功能"), (exp2, "智能分析与预测"), (exp3, "系统管理")):
    with exp:
        for feature in instruction.get(category, []):
            st.markdown(f"**{feature['name']}**")
            st.markdown(f"*{feature['desc']}*")
            st.markdown("操作步骤：")
            for step in feature["steps"]:
                st.markdown(f"- {step}")
            if feature["notes"]:
                st.caption(f"注意：{feature['notes']}")

with exp4:
    for q, a in FAQ:
        st.markdown(f"**Q：{q}**")
        st.markdown(f"A：{a}")

st.subheader("技术支持")
c1, c2, c3 = st.columns(3)
c1.markdown(f"**邮箱**\n\n{SUPPORT['email']}")
c2.markdown(f"**项目**\n\n{SUPPORT['repo']}")
with c3:
    if st.button("生成使用说明书 (docx)", icon=":material/description:"):
        try:
            path = docx_manual.generate_docx_manual()
            st.success(f"已生成：{path}")
        except RuntimeError as e:
            st.error(str(e))
