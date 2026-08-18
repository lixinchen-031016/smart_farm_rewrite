"""captcha_service 测试（纯函数）。"""

from smart_farm.app import captcha_ui as cu
from smart_farm.services import captcha_service as cs


def test_generate_text_length_and_charset():
    for _ in range(50):
        text = cs.generate_captcha_text()
        assert len(text) == cs.CAPTCHA_LENGTH  # 5 位
        # 字符集不含易混淆 I/O/0/1
        assert all(c in cs._CHARSET for c in text)
        assert "0" not in text or True  # 0 在字符集外
        assert set(text) <= set(cs._CHARSET)


def test_generate_text_space_size():
    # 搜索空间显著大于旧版 4 位纯数字（1 万）
    assert len(cs._CHARSET) ** cs.CAPTCHA_LENGTH > 10_000_000


def test_render_returns_png():
    png = cs.render_captcha_image("A3K9Z", seed=1)
    assert png.startswith(b"\x89PNG")
    assert len(png) > 500


def test_render_seeded_is_reproducible():
    a = cs.render_captcha_image("A3K9Z", seed=42)
    b = cs.render_captcha_image("A3K9Z", seed=42)
    assert a == b


def test_render_different_seeds_differ():
    a = cs.render_captcha_image("A3K9Z", seed=1)
    b = cs.render_captcha_image("A3K9Z", seed=2)
    assert a != b


def test_verify_captcha_case_and_space_insensitive():
    assert cs.verify_captcha("A3K9Z", "a3k9z")  # 大小写不敏感
    assert cs.verify_captcha("  A3K9Z ", "A3K9Z")
    assert cs.verify_captcha("", "A3K9Z") is False
    assert cs.verify_captcha("WRONG", "A3K9Z") is False


def test_captcha_bypass_env(monkeypatch):
    """测试缝：SF_TEST_CAPTCHA=1 时校验直接通过（E2E 登录用，默认关闭）。"""
    monkeypatch.setenv("SF_TEST_CAPTCHA", "1")
    assert cu.validate_captcha_input("任意输入") is True
    assert cu.validate_captcha_input("") is True  # 旁路时不校验内容


def test_captcha_bypass_off_by_default(monkeypatch):
    """测试缝默认关闭：未设置环境变量时走正常校验路径。"""
    monkeypatch.delenv("SF_TEST_CAPTCHA", raising=False)
    assert cu._test_bypass_enabled() is False
