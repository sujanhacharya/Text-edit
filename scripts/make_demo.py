#!/usr/bin/env python3
"""Generate the demo poster used by the "Try the demo" button.

Run from the repository root:

    python scripts/make_demo.py

The poster is deliberately text-on-flat-colour: it shows off clean OCR and
clean inpainting without pretending to solve text replacement on busy photos.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "app" / "assets" / "fonts"
OUT = ROOT / "app" / "assets" / "demo" / "demo.png"

S = 2  # supersampling factor


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / name), size * S)


def spaced_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fnt, fill, tracking: int) -> None:
    x, y = xy
    for char in text:
        draw.text((x, y), char, font=fnt, fill=fill)
        x += draw.textlength(char, font=fnt) + tracking * S


def main() -> None:
    W, H = 1200 * S, 900 * S
    rng = np.random.default_rng(11)

    # Deep green background with a soft vertical gradient.
    top = np.array([22, 49, 36], dtype=np.float32)
    bottom = np.array([10, 25, 18], dtype=np.float32)
    ramp = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
    bg = (top[None, None, :] * (1 - ramp) + bottom[None, None, :] * ramp)
    bg = np.repeat(bg, W, axis=1)
    image = Image.fromarray(np.clip(bg, 0, 255).astype(np.uint8), "RGB")

    # Cream card with a soft shadow.
    card_box = (60 * S, 60 * S, 1140 * S, 840 * S)
    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(
        (card_box[0] + 5 * S, card_box[1] + 12 * S, card_box[2] + 5 * S, card_box[3] + 12 * S),
        radius=26 * S,
        fill=(0, 0, 0, 110),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(18 * S))
    image = Image.alpha_composite(image.convert("RGBA"), shadow).convert("RGB")

    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(card_box, radius=26 * S, fill=(244, 238, 224))
    draw.rounded_rectangle(
        (card_box[0] + 9 * S, card_box[1] + 9 * S, card_box[2] - 9 * S, card_box[3] - 9 * S),
        radius=20 * S,
        outline=(214, 203, 181),
        width=1 * S,
    )

    # Film-grain style texture across the whole poster.
    arr = np.asarray(image).astype(np.int16)
    noise = rng.normal(0, 3.0, (H, W, 1))
    image = Image.fromarray(np.clip(arr + noise, 0, 255).astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(image)

    ink = (27, 42, 32)
    muted = (92, 103, 88)
    orange = (199, 89, 43)
    cream = (247, 242, 231)

    # Eyebrow label with generous tracking.
    spaced_text(draw, (132 * S, 126 * S), "RIVERSIDE ROASTERY", font("DejaVuSansMono.ttf", 18), muted, 3)

    # Display title.
    draw.text((126 * S, 176 * S), "THE DAILY", font=font("DejaVuSerif-Bold.ttf", 96), fill=ink)
    draw.text((126 * S, 276 * S), "GRIND", font=font("DejaVuSerif-Bold.ttf", 96), fill=ink)

    # Accent rule + subtitle.
    draw.rectangle((132 * S, 414 * S, 232 * S, 420 * S), fill=orange)
    draw.text(
        (132 * S, 444 * S),
        "Small-batch coffee, baked slow.",
        font=font("DejaVuSerif.ttf", 29),
        fill=muted,
    )

    # Menu.
    menu_font = font("DejaVuSansMono.ttf", 27)
    menu = [
        ("Honey oat latte", "$5.50"),
        ("Cardamom bun", "$4.00"),
        ("Filter of the day", "$3.50"),
    ]
    y = 520 * S
    for item, price in menu:
        draw.text((132 * S, y), item, font=menu_font, fill=ink)
        price_width = draw.textlength(price, font=menu_font)
        draw.text((1080 * S - price_width, y), price, font=menu_font, fill=ink)
        y += 52 * S

    # Orange footer banner.
    draw.rounded_rectangle((132 * S, 704 * S, 1068 * S, 792 * S), radius=12 * S, fill=orange)
    banner = "WEEKEND SPECIAL: FREE REFILL"
    banner_font = font("DejaVuSans-Bold.ttf", 27)
    banner_width = draw.textlength(banner, font=banner_font)
    draw.text(
        ((1200 * S - banner_width) / 2, 735 * S),
        banner,
        font=banner_font,
        fill=cream,
    )

    # Footer address / hours.
    draw.text(
        (132 * S, 810 * S),
        "Open 7am - 6pm  /  42 Market Street, Riverside",
        font=font("DejaVuSans.ttf", 19),
        fill=muted,
    )

    # A small stamp in the upper-right corner, away from the main text.
    stamp = (1018 * S, 176 * S, 1080 * S, 238 * S)
    draw.ellipse(stamp, outline=orange, width=2 * S)
    draw.text((1032 * S, 194 * S), "EST.", font=font("DejaVuSansMono.ttf", 12), fill=orange)
    draw.text((1032 * S, 210 * S), "2018", font=font("DejaVuSansMono.ttf", 12), fill=orange)

    image = image.resize((1200, 900), Image.Resampling.LANCZOS)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUT, format="PNG", optimize=True)
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
