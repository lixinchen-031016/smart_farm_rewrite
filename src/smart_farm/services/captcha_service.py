"""验证码服务（纯逻辑，无 Streamlit 依赖）。

对齐旧库体验：数字+字母混合、200x80 PNG、字符抖动、干扰线与干扰点、高斯模糊。
修复：旧版 4 位纯数字搜索空间仅 1 万，易被 OCR/爆破；升级为 5 位数字+大写字母
（约 6 千万空间）。返回 PNG bytes（配合 `st.image` 原生展示），不再返回 base64 HTML。

OCR 抗性增强：逐字符独立图层随机旋转 ±25°、弧线+直线混合干扰、
背景浅色噪块降低前景/背景对比度、字符横向轻微重叠。
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


def _draw_rotated_char(image: Image.Image, char: str, font, x: int, y: int, color, angle: float) -> None:
    """在独立图层绘制单字符并旋转后贴回（OCR 抗性：打散字符朝向）。"""
    try:
        bbox = font.getbbox(char)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except AttributeError:  # pragma: no cover 极老版本 PIL
        w, h = 40, 45
    pad = 24
    layer = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.text((pad, pad), char, fill=color, font=font)
    layer = layer.rotate(angle, expand=True, resample=Image.BICUBIC)
    image.paste(layer, (x - pad, y - pad), layer)


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

    # 背景浅色噪块（降低前景/背景对比度，干扰二值化与 OCR）
    for _ in range(rng.randint(12, 20)):
        x, y = rng.randint(0, WIDTH - 1), rng.randint(0, HEIGHT - 1)
        w, h = rng.randint(10, 45), rng.randint(6, 25)
        gray = rng.randint(180, 240)
        draw.rectangle(
            [(x, y), (min(x + w, WIDTH - 1), min(y + h, HEIGHT - 1))],
            fill=(gray, gray, gray),
        )

    # 逐字符绘制：随机深色 + 位置抖动 + 独立图层旋转
    n = len(captcha_text)
    font = _load_font(42)
    step = int(WIDTH * 0.8) // max(n, 1)  # 间距略小于字宽，制造轻微重叠
    for i, char in enumerate(captcha_text):
        color = (rng.randint(0, 90), rng.randint(0, 90), rng.randint(0, 90))
        x = 12 + i * step + rng.randint(-3, 3)
        y = rng.randint(4, 26)
        angle = rng.uniform(-25, 25)
        _draw_rotated_char(image, char, font, x, y, color, angle)

    # 弧线干扰 6-10 条（曲线比直线更难被 OCR 滤除）
    for _ in range(rng.randint(6, 10)):
        x0r, x1r = sorted((rng.randint(-30, WIDTH), rng.randint(-30, WIDTH)))
        y0r, y1r = sorted((rng.randint(-20, HEIGHT + 20), rng.randint(-20, HEIGHT + 20)))
        draw.arc(
            [(x0r, y0r), (max(x1r, x0r + 10), max(y1r, y0r + 10))],
            start=rng.randint(0, 360),
            end=rng.randint(0, 360),
            fill=(rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255)),
            width=1,
        )

    # 直线干扰 6-10 条（穿过字符区域）
    for _ in range(rng.randint(6, 10)):
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
