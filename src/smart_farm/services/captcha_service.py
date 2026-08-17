"""验证码服务（纯逻辑，无 Streamlit 依赖）。

对齐旧库体验：数字+字母混合、200x80 PNG、字符抖动、干扰线与干扰点、高斯模糊。
修复：旧版 4 位纯数字搜索空间仅 1 万，易被 OCR/爆破；升级为 5 位数字+大写字母
（约 6 千万空间）。返回 PNG bytes（配合 `st.image` 原生展示），不再返回 base64 HTML。
"""

import io
import random
import string

from PIL import Image, ImageDraw, ImageFilter, ImageFont

CAPTCHA_LENGTH = 5
WIDTH, HEIGHT = 200, 80
_CHARSET = string.digits + "ABCDEFGHJKLMNPQRSTUVWXYZ"  # 去除易混淆 I/O/0/1


def generate_captcha_text() -> str:
    """生成验证码文本（数字+大写字母，剔除易混淆字符）。"""
    return "".join(random.choices(_CHARSET, k=CAPTCHA_LENGTH))


def _load_font(size: int) -> ImageFont.ImageFont:
    """优先用系统常见字体，缺失时回退默认字体（对齐旧库容错）。"""
    for name in ("arial.ttf", "DejaVuSans-Bold.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_captcha_image(captcha_text: str, seed: int | None = None) -> bytes:
    """将验证码文本渲染为 200x80 PNG bytes。

    Args:
        captcha_text: 验证码文本（5 位数字+字母）。
        seed: 可选随机种子（测试用，保证输出可复现）。

    Returns:
        PNG 图片字节流。
    """
    rng = random.Random(seed)
    image = Image.new("RGB", (WIDTH, HEIGHT), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    font = _load_font(45)

    # 逐字符绘制，随机深色 + 位置抖动
    n = len(captcha_text)
    step = WIDTH // (n + 2)  # 自适应字符间距
    for i, char in enumerate(captcha_text):
        color = (rng.randint(0, 100), rng.randint(0, 100), rng.randint(0, 100))
        x = step + i * step + rng.randint(-4, 4)
        y = rng.randint(10, 20)
        draw.text((x, y), char, fill=color, font=font)

    # 干扰线 10-14 条（修复：增加数量对抗 OCR）
    for _ in range(rng.randint(10, 14)):
        x1, y1 = rng.randint(0, WIDTH), rng.randint(0, HEIGHT)
        x2, y2 = rng.randint(0, WIDTH), rng.randint(0, HEIGHT)
        draw.line(
            [(x1, y1), (x2, y2)],
            fill=(rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255)),
            width=1,
        )

    # 干扰点 100-160 个
    for _ in range(rng.randint(100, 160)):
        x, y = rng.randint(0, WIDTH - 1), rng.randint(0, HEIGHT - 1)
        draw.point((x, y), fill=(rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255)))

    # 高斯模糊（对齐旧库 0.2-0.8 强度）
    image = image.filter(ImageFilter.GaussianBlur(rng.uniform(0.2, 0.8)))

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def verify_captcha(user_input: str, expected: str) -> bool:
    """校验用户输入（忽略大小写与首尾空白）。"""
    return user_input.strip().lower() == (expected or "").strip().lower()
