"""captcha_service 测试（纯函数）。"""

from smart_farm.services import captcha_service as cs


def test_generate_text_is_4_digits():
    for _ in range(20):
        text = cs.generate_captcha_text()
        assert len(text) == 4
        assert text.isdigit()


def test_render_returns_png():
    png = cs.render_captcha_image("1234", seed=1)
    assert png.startswith(b"\x89PNG")
    assert len(png) > 500


def test_render_seeded_is_reproducible():
    a = cs.render_captcha_image("1234", seed=42)
    b = cs.render_captcha_image("1234", seed=42)
    assert a == b


def test_render_different_seeds_differ():
    a = cs.render_captcha_image("1234", seed=1)
    b = cs.render_captcha_image("1234", seed=2)
    assert a != b


def test_verify_captcha_case_and_space_insensitive():
    assert cs.verify_captcha("1234", "1234")
    assert cs.verify_captcha("  1234 ", "1234")
    assert cs.verify_captcha("", "1234") is False
    assert cs.verify_captcha("9999", "1234") is False
    assert cs.verify_captcha("abcd", "1234") is False
