"""验证码 UI（仅渲染 + session_state 管理）。

- 用 `st.image` 原生展示 PNG（不用 base64 HTML，遵循技能规范）。
- 验证码文本与图片存于 `st.session_state[f"captcha_{key}"]` / `[f"captcha_{key}_image"]`。
- 提供刷新按钮，点按重新生成并 rerun。
"""

import streamlit as st

from smart_farm.services import captcha_service as cs


def initialize_captcha(session_key: str = "login") -> None:
    """确保会话中存在验证码（无则生成）。"""
    text_key = f"captcha_{session_key}"
    img_key = f"captcha_{session_key}_image"
    if text_key not in st.session_state:
        text = cs.generate_captcha_text()
        st.session_state[text_key] = text
        st.session_state[img_key] = cs.render_captcha_image(text)


def refresh_captcha(session_key: str = "login") -> None:
    """重新生成验证码并覆盖会话值。"""
    text = cs.generate_captcha_text()
    st.session_state[f"captcha_{session_key}"] = text
    st.session_state[f"captcha_{session_key}_image"] = cs.render_captcha_image(text)


def create_captcha_widget(session_key: str = "login", show_refresh: bool = True) -> None:
    """渲染验证码图片 + 可选的刷新按钮（须在 form 外调用，避免 st.button 冲突）。"""
    initialize_captcha(session_key)
    img_key = f"captcha_{session_key}_image"
    with st.container(horizontal=True):
        st.image(st.session_state[img_key], width=160)
        if show_refresh:
            if st.button("刷新验证码", icon=":material/refresh:", key=f"refresh_captcha_{session_key}"):
                refresh_captcha(session_key)
                st.rerun()


def validate_captcha_input(
    user_input: str,
    session_key: str = "login",
    field_name: str = "验证码",
) -> bool:
    """校验验证码输入；失败给出提示并刷新验证码。"""
    expected = st.session_state.get(f"captcha_{session_key}", "")
    if not user_input:
        st.error(f"请输入{field_name}。")
        return False
    if not cs.verify_captcha(user_input, expected):
        st.error(f"{field_name}错误，请重新输入。")
        refresh_captcha(session_key)
        return False
    return True
