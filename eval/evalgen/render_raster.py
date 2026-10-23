"""Raster output with Pillow: page bitmaps, scanner-like and photo-like degradations.

All randomness comes from the ``random.Random`` instance passed in, never from
Pillow's own generators, so output is reproducible for a given seed.
"""

import io
import random
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from evalgen.layout import FONT_FILES, Line, Page, Rect, Text, font_name

RGB = tuple[int, int, int]


def _rgb(color: tuple[float, float, float]) -> RGB:
    return tuple(round(c * 255) for c in color)  # type: ignore[return-value]


@lru_cache(maxsize=512)
def _font(serif: bool, bold: bool, px: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_FILES[font_name(serif, bold)]), px)


def rasterize(page: Page, dpi: int) -> Image.Image:
    """Render one page to an RGB bitmap at ``dpi`` (white paper, exact layout positions)."""
    s = dpi / 72.0
    img = Image.new("RGB", (round(page.width * s), round(page.height * s)), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    for op in page.ops:
        if isinstance(op, Rect):
            box = (op.x * s, op.y * s, (op.x + op.w) * s, (op.y + op.h) * s)
            draw.rectangle(
                box,
                fill=_rgb(op.fill) if op.fill else None,
                outline=_rgb(op.stroke) if op.stroke else None,
                width=max(1, round(op.width * s)),
            )
        elif isinstance(op, Line):
            draw.line(
                (op.x1 * s, op.y1 * s, op.x2 * s, op.y2 * s),
                fill=_rgb(op.color),
                width=max(1, round(op.width * s)),
            )
        elif isinstance(op, Text):
            draw.text(
                (op.x * s, op.y * s),
                op.text,
                font=_font(op.serif, op.bold, op.size * s),
                fill=_rgb(op.color),
                anchor="ls",
            )
    return img


# --- noise and gradients --------------------------------------------------------------


def _noise(rng: random.Random, size: tuple[int, int], amplitude: float) -> Image.Image:
    """Zero-mean uniform noise as an 'L' image centred on 128, scaled by ``amplitude``."""
    w, h = size
    raw = Image.frombytes("L", (w, h), rng.randbytes(w * h))
    return raw.point(lambda v: int(128 + (v - 128) * amplitude))


def _add_noise(img: Image.Image, rng: random.Random, amplitude: float) -> Image.Image:
    noise = _noise(rng, img.size, amplitude)
    if img.mode == "RGB":
        noise = Image.merge("RGB", (noise, noise, noise))
    return ImageChops.add(img, noise, 1.0, -128)


def _gradient(rng: random.Random, size: tuple[int, int], low: float, high: float) -> Image.Image:
    """Linear brightness gradient (as 'L') with random direction, values low..high (0..1).

    Built at a small size and scaled up: identical look, much faster than
    rotating a full-resolution gradient.
    """
    w, h = size
    small = 96
    grad = Image.linear_gradient("L").resize((small * 2, small * 2))
    grad = grad.rotate(rng.uniform(0, 360), resample=Image.Resampling.BILINEAR)
    grad = grad.crop((small // 2, small // 2, small // 2 + small, small // 2 + small))
    lo, hi = int(low * 255), int(high * 255)
    grad = grad.point(lambda v: lo + (hi - lo) * v // 255)
    return grad.resize((w, h), Image.Resampling.BILINEAR)


def _multiply(img: Image.Image, mask: Image.Image) -> Image.Image:
    if img.mode == "RGB":
        mask = Image.merge("RGB", (mask, mask, mask))
    return ImageChops.multiply(img, mask)


# --- scan --------------------------------------------------------------------------------


def scan_page(page_img: Image.Image, rng: random.Random, quality: int) -> bytes:
    """Grayscale scan: uneven brightness, slight rotation, noise, blur; returns JPEG bytes."""
    gray = page_img.convert("L")
    gray = _multiply(gray, _gradient(rng, gray.size, 0.86, 1.0))
    gray = gray.point(lambda v: min(255, int(v * 0.97 + 4)))
    angle = rng.uniform(-2.0, 2.0)
    gray = gray.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=245)
    gray = gray.filter(ImageFilter.GaussianBlur(rng.uniform(0.45, 0.85)))
    gray = _add_noise(gray, rng, rng.uniform(0.10, 0.18))
    out = io.BytesIO()
    gray.save(out, "JPEG", quality=quality, optimize=True)
    return out.getvalue()


# --- photo ---------------------------------------------------------------------------------


def _solve8(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """Gaussian elimination for the 8x8 perspective system (no numpy)."""
    n = 8
    a = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        a[col], a[pivot] = a[pivot], a[col]
        p = a[col][col]
        for j in range(col, n + 1):
            a[col][j] /= p
        for r in range(n):
            if r != col and a[r][col]:
                f = a[r][col]
                for j in range(col, n + 1):
                    a[r][j] -= f * a[col][j]
    return [a[i][n] for i in range(n)]


def perspective_coeffs(
    dst: list[tuple[float, float]], src: list[tuple[float, float]]
) -> list[float]:
    """Coefficients for Image.transform(PERSPECTIVE) mapping output quad ``dst`` to ``src``."""
    matrix = []
    rhs = []
    for (x, y), (u, v) in zip(dst, src, strict=True):
        matrix.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        matrix.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        rhs.extend([u, v])
    return _solve8(matrix, rhs)


def photo_page(page_img: Image.Image, rng: random.Random, long_side: int, quality: int) -> bytes:
    """Phone photo: warm paper, lighting gradient and shadow, table background, perspective,
    rotation up to 5 degrees, slight blur and colour cast; returns JPEG bytes."""
    w, h = page_img.size
    paper = ImageChops.multiply(page_img, Image.new("RGB", page_img.size, (250, 246, 238)))
    paper = _multiply(paper, _gradient(rng, paper.size, 0.72, 1.0))
    # soft shadow band along one edge
    band = _gradient(rng, paper.size, 0.9, 1.0)
    paper = _multiply(paper, band)
    paper = _add_noise(paper, rng, rng.uniform(0.05, 0.1))

    cw, ch = int(w * 1.18), int(h * 1.14)
    table_color = rng.choice([(96, 72, 52), (120, 104, 88), (70, 70, 74), (140, 118, 92)])
    table = Image.new("RGB", (cw, ch), table_color)
    # low-frequency texture: coarse noise, blurred, upscaled -> desk/wood-like mottling
    coarse = _noise(rng, (cw // 40 + 1, ch // 40 + 1), 0.9).filter(ImageFilter.GaussianBlur(0.8))
    texture = coarse.resize((cw, ch), Image.Resampling.BICUBIC).point(
        lambda v: int(128 + (v - 128) * 0.45)
    )
    table = ImageChops.add(table, Image.merge("RGB", (texture, texture, texture)), 1.0, -128)
    table = _add_noise(table, rng, 0.06)
    table = _multiply(table, _gradient(rng, table.size, 0.6, 1.0))

    ox, oy = (cw - w) / 2, (ch - h) / 2
    jitter = 0.04
    corners = [(ox, oy), (ox + w, oy), (ox + w, oy + h), (ox, oy + h)]
    dst = [
        (x + rng.uniform(-jitter, jitter) * w, y + rng.uniform(-jitter, jitter) * h)
        for x, y in corners
    ]
    src = [(0, 0), (w, 0), (w, h), (0, h)]
    coeffs = perspective_coeffs(dst, src)
    rgba = paper.convert("RGBA")
    warped = rgba.transform((cw, ch), Image.Transform.PERSPECTIVE, coeffs, Image.Resampling.BICUBIC)
    # drop shadow: warped alpha, offset and blurred
    alpha = warped.getchannel("A")
    shadow = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    shadow.putalpha(alpha.point(lambda v: int(v * 0.55)))
    shadow = shadow.transform(
        (cw, ch), Image.Transform.AFFINE, (1, 0, -rng.randint(6, 14), 0, 1, -rng.randint(6, 14))
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(rng.uniform(8, 14)))
    canvas = table.convert("RGBA")
    canvas.alpha_composite(shadow)
    canvas.alpha_composite(warped)
    photo = canvas.convert("RGB")
    photo = photo.rotate(
        rng.uniform(-5.0, 5.0), resample=Image.Resampling.BICUBIC, fillcolor=table_color
    )
    photo = photo.filter(ImageFilter.GaussianBlur(rng.uniform(0.6, 1.1)))
    photo = ImageEnhance.Color(photo).enhance(rng.uniform(0.9, 1.15))
    cast = rng.choice([(255, 250, 240), (245, 248, 255), (255, 244, 234), (250, 250, 250)])
    photo = ImageChops.multiply(photo, Image.new("RGB", photo.size, cast))
    scale = long_side / max(photo.size)
    photo = photo.resize(
        (round(photo.width * scale), round(photo.height * scale)), Image.Resampling.LANCZOS
    )
    out = io.BytesIO()
    photo.save(out, "JPEG", quality=quality, optimize=True)
    return out.getvalue()
