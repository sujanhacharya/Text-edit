"""Image loading, style sampling, text removal and text re-rendering.

The pipeline is intentionally lightweight:

1. OCR gives us word boxes for each line.
2. For edited lines we build a pixel mask around the old words and repair the
   background with a local median/colour estimate (or inpainting for busy
   photos).
3. The new text is drawn with a real font, fitted to the original line box and
   tinted with the sampled foreground colour.

It is not a diffusion model.  AnyText/IOPaint produce prettier results on
complex photos, but need gigabytes of VRAM and torch.  For the documents,
screenshots and posters InkPatch targets, this gets most of the result at a
fraction of the resource cost.
"""

from __future__ import annotations

import io
import logging
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import (
    Image,
    ImageDraw,
    ImageFilter,
    ImageFont,
    ImageOps,
    UnidentifiedImageError,
)

from .config import FONT_DIR, MAX_IMAGE_PIXELS
from .fonts import (
    FontFace,
    default_face,
    face_by_id,
    family as font_family,
    find_font_by_names,
    match_faces,
    variants_for_face,
)

log = logging.getLogger(__name__)

Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

DEFAULT_LIGHT = "#f5f2e8"
DEFAULT_DARK = "#22241f"
CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]")
ARABIC_RE = re.compile(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff]")
HEBREW_RE = re.compile(r"[\u0590-\u05ff]")
DEVANAGARI_RE = re.compile(r"[\u0900-\u097f]")
KANNADA_RE = re.compile(r"[\u0c80-\u0cff]")

# Typeface matching is limited to scripts the Latin/DejaVu/Liberation/Noto
# families can draw.  Greek and Cyrillic are included because the common
# document fonts cover them too.  The punctuation/math/symbol blocks are
# included as well: a line such as "24 °C", "© 2026", "R&D" or "Next →" still
# has ordinary Latin glyphs to compare, and excluding the symbol used to make
# style detection fall back to a generic sans face for the whole line.
LATIN_STYLE_RE = re.compile(
    r"^[\x20-\x7e\u00a0-\u036f\u0370-\u03ff\u0400-\u052f"
    r"\u1e00-\u1fff\u2000-\u2bff\u2e00-\u2e7f\ua700-\ua7ff"
    r"\ufe00-\ufe0f\U0001d100-\U0001d3ff\U0001f000-\U0001faff]+$"
)
STYLE_FAMILIES = ("sans", "serif", "mono")
# Comparing/blurring every line at a fixed size keeps the MSE values stable
# regardless of the source image resolution.
STYLE_COMPARE_SIZE = (192, 64)
BOLD_RELATIVE_GAIN = 0.12
ITALIC_RELATIVE_GAIN = 0.135
FAMILY_MARGIN_MIN = 0.055
# Characters that are almost always rules, table borders or ornaments when
# they form a standalone token at the edge of a line.  They stay meaningful
# *inside* text ("well-known", "a | b", "x_y"), so only edge tokens are pruned.
RULE_TOKEN_CHARS = set("|_¦‖~^`\\-–—―=•·∙▪▫●○■□◆◇*/<>")
# Textual symbols that can legitimately be the only character in a token.
# They are preserved when they are confident and have a normal-sized box.
MEANINGFUL_SYMBOLS = set("&$€£¥#@©®™°‰§¶†‡…*+=<>→←↔≤≥≠±×÷")

CJK_FONTS = {
    False: [
        "NotoSansCJK-Regular.ttc",
        "NotoSansCJK.ttc",
        "NotoSansCJKsc-Regular.otf",
        "DroidSansFallbackFull.ttf",
        "Msyh.ttc",
        "PingFang.ttc",
    ],
    True: [
        "NotoSansCJK-Bold.ttc",
        "NotoSansCJKsc-Bold.otf",
        "Msyhbd.ttc",
    ],
}
ARABIC_FONTS = {
    False: ["NotoNaskhArabic-Regular.ttf", "NotoSansArabic-Regular.ttf"],
    True: ["NotoNaskhArabic-Bold.ttf", "NotoSansArabic-Bold.ttf"],
}
HEBREW_FONTS = {
    False: ["NotoSansHebrew-Regular.ttf", "NotoSerifHebrew-Regular.ttf"],
    True: ["NotoSansHebrew-Bold.ttf", "NotoSerifHebrew-Bold.ttf"],
}
DEVANAGARI_FONTS = {
    False: [
        "NotoSansDevanagari-Regular.ttf",
        "NotoSerifDevanagari-Regular.ttf",
        "NotoSansDevanagari[wdth,wght].ttf",
        "Nirmala.ttf",
        "Mangal.ttf",
        "Devanagari Sangam MN.ttc",
    ],
    True: [
        "NotoSansDevanagari-Bold.ttf",
        "NotoSerifDevanagari-Bold.ttf",
        "MangalB.ttf",
    ],
}
KANNADA_FONTS = {
    False: [
        "NotoSansKannada-Regular.ttf",
        "NotoSerifKannada-Regular.ttf",
        "NotoSansKannada[wdth,wght].ttf",
        "Tunga.ttf",
        "Kannada Sangam MN.ttc",
        "KannadaSangamMN.ttf",
    ],
    True: [
        "NotoSansKannada-Bold.ttf",
        "NotoSerifKannada-Bold.ttf",
        "Tungab.ttf",
    ],
}


class ImageError(ValueError):
    """A user-facing image validation error."""


class RenderError(ValueError):
    """A user-facing rendering error."""


# --------------------------------------------------------------------- loading


def open_image(raw: bytes) -> Image.Image:
    if not raw:
        raise ImageError("The uploaded file is empty.")
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            image = ImageOps.exif_transpose(opened)
            if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
                rgba = image.convert("RGBA")
                canvas = Image.new("RGB", rgba.size, (255, 255, 255))
                canvas.paste(rgba, mask=rgba.getchannel("A"))
                image = canvas
            else:
                image = image.convert("RGB")
            image.load()
            return image
    except Image.DecompressionBombError as exc:
        raise ImageError("That image is too large to process safely.") from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageError("That file does not look like a supported image (PNG, JPG or WebP).") from exc


def limit_size(image: Image.Image, max_side: int) -> Image.Image:
    width, height = image.size
    if max(width, height) <= max_side:
        return image
    scale = max_side / max(width, height)
    return image.resize((max(1, round(width * scale)), max(1, round(height * scale))), Image.Resampling.LANCZOS)


def save_png(image: Image.Image, path: Path) -> None:
    tmp = path.with_suffix(".tmp.png")
    image.save(tmp, format="PNG", compress_level=6)
    tmp.replace(path)


# ---------------------------------------------------------------------- colours


def _hex(color: tuple[int, int, int] | np.ndarray) -> str:
    values = [max(0, min(255, int(round(float(channel))))) for channel in color[:3]]
    return "#" + "".join(f"{value:02x}" for value in values)


def _luminance(color: tuple[int, int, int] | np.ndarray) -> float:
    red, green, blue = [float(channel) for channel in color[:3]]
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _parse_hex(value: str) -> tuple[int, int, int]:
    """Parse ``#rrggbb`` (and ``#rgb``) with a safe fallback.

    Edit colours are validated by the API, but styles stored by older
    documents are not.  A malformed saved colour should not turn a render into
    a server error.
    """
    text = str(value or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    if len(text) != 6:
        return (34, 36, 31)
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except ValueError:
        return (34, 36, 31)


def _border_pixels(rgb: np.ndarray, thickness: int = 2) -> np.ndarray:
    if rgb.ndim == 2:
        rgb = rgb[:, :, None]
    rows, cols, channels = rgb.shape
    thickness = max(1, min(thickness, max(1, rows // 2), max(1, cols // 2)))
    parts = [
        rgb[:thickness, :, :].reshape(-1, channels),
        rgb[-thickness:, :, :].reshape(-1, channels),
        rgb[:, :thickness, :].reshape(-1, channels),
        rgb[:, -thickness:, :].reshape(-1, channels),
    ]
    return np.concatenate(parts, axis=0)


def _mask_for_border(rgb: np.ndarray, ring: int) -> np.ndarray:
    """Boolean mask selecting the outer band of an expanded crop."""
    rows, cols = rgb.shape[:2]
    ring = max(1, min(ring, rows // 2, cols // 2))
    mask = np.zeros((rows, cols), dtype=bool)
    mask[:ring, :] = True
    mask[-ring:, :] = True
    mask[:, :ring] = True
    mask[:, -ring:] = True
    return mask


def _robust_median(values: np.ndarray, axis: int = 0, trim: float = 12.0) -> np.ndarray:
    """Median after discarding bright/dark outliers such as glyphs or shadows."""
    array = np.asarray(values, dtype=np.float32)
    if array.size == 0:
        if array.ndim == 1:
            return np.float32(0.0)
        return np.zeros(array.shape[-1], dtype=np.float32)
    if array.ndim == 1:
        low, high = np.percentile(array, [trim, 100.0 - trim])
        kept = array[(array >= low) & (array <= high)]
        return np.median(kept if kept.size else array)
    if array.ndim == 2:
        # One row per sample, one column per channel.  Compute one trimmed
        # range per channel, then keep only rows whose every channel lies
        # inside its range.  The boolean mask therefore lives on axis 1 (the
        # row/sample axis), not on axis 0.  Indexing with an axis-0 mask was
        # the cause of "boolean index did not match indexed array".
        if axis not in (0, -2):
            array = np.moveaxis(array, axis, 0)
        low = np.percentile(array, trim, axis=0)
        high = np.percentile(array, 100.0 - trim, axis=0)
        kept = np.all((array >= low) & (array <= high), axis=1)
        if not np.any(kept):
            return np.median(array, axis=0)
        return np.median(array[kept], axis=0)
    flat = array.reshape(-1, array.shape[-1])
    return _robust_median(flat, axis=0, trim=trim)


def _tone_spread(values: np.ndarray) -> float:
    """Spread of the background tone, ignoring glyphs and shadows.

    A plain percentile range is easily inflated by the very ink and drop
    shadow the mask is trying to remove.  That made flat paper take the
    textured repair path, which left a grey halo behind edited text.  The
    trimmed spread describes the background majority instead, so a clean
    patch is recognised as flat even when a word box clips a shadow.
    """
    array = np.asarray(values, dtype=np.float32).reshape(-1)
    if array.size == 0:
        return 0.0
    if array.size < 8:
        return float(array.max() - array.min())
    low, high = np.percentile(array, [20.0, 80.0])
    return float(high - low)



def _odd(value: int) -> int:
    value = max(1, int(value))
    return value if value % 2 else value + 1


# ----------------------------------------------------------------- font masks


@lru_cache(maxsize=512)
def _render_font_mask(text: str, face: FontFace) -> np.ndarray | None:
    """Rasterise *text* at a neutral size and return a tight binary mask."""
    try:
        font = ImageFont.truetype(str(face.path), size=96, index=face.index)
        scratch = Image.new("L", (8, 8), 0)
        bbox = ImageDraw.Draw(scratch).textbbox((0, 0), text, font=font)
    except (OSError, ValueError, RuntimeError):
        return None
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    if text_width < 1 or text_height < 1:
        return None
    canvas = Image.new("L", (text_width + 8, text_height + 8), 0)
    ImageDraw.Draw(canvas).text((4 - bbox[0], 4 - bbox[1]), text, font=font, fill=255)
    clipped = canvas.crop(canvas.getbbox())
    if clipped.width < 2 or clipped.height < 2:
        return None
    return np.asarray(clipped, dtype=np.uint8)


def _mask_similarity(source: np.ndarray, candidate: np.ndarray | None) -> float:
    """Compare two masks after size normalisation, with aspect/ink penalties."""
    if candidate is None:
        return float("inf")
    size = STYLE_COMPARE_SIZE
    source_ratio = source.shape[1] / max(1, source.shape[0])
    candidate_ratio = candidate.shape[1] / max(1, candidate.shape[0])
    left = cv2.resize(source, size, interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    right = cv2.resize(candidate, size, interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    left = cv2.GaussianBlur(left, (0, 0), 1.1)
    right = cv2.GaussianBlur(right, (0, 0), 1.1)
    diff = left - right
    mse = float(np.mean(diff * diff)) * 255.0 * 255.0
    # A candidate with the right glyph shapes but a completely different width
    # (condensed vs normal, tracked small caps, etc.) should not win.
    ratio_penalty = abs(math.log(max(1e-3, source_ratio) / max(1e-3, candidate_ratio)))
    coverage_penalty = abs(float(left.mean()) - float(right.mean()))
    return mse + min(3500.0, ratio_penalty * 1600.0) + coverage_penalty * 3800.0


def _crop_glyph_mask(
    gray: np.ndarray,
    background_gray: float,
    contrast: float,
    solid: bool = False,
    dark_text: bool | None = None,
) -> np.ndarray | None:
    """Extract a full-size text mask from a crop using the local background.

    The polarity is chosen from the actual pixels rather than from the
    background luminance, so light text on an orange banner and dark text on a
    mid-tone photo are both handled correctly.

    ``solid`` raises the threshold so only the solid ink core is kept.  Style
    matching must not measure a drop shadow or the antialiasing halo: doing so
    makes the glyphs look taller/bolder and changes the detected size, weight
    and family.  Removal masks continue to use the wider default threshold so
    the whole antialiased stroke and its halo are erased.

    ``dark_text`` restricts the search to one polarity when the caller has
    already sampled the foreground colour.  Without it, a light face carrying
    a dark drop shadow can win the polarity contest with its shadow instead of
    its glyphs, which corrupts style matching on exactly the photos that need
    it most.
    """
    ratios = (0.55, 0.35) if solid else (0.35,)
    minimum_coverage = 0.002 if solid else 0.004
    # Keep the original 48-level cap for removal masks and for the solid
    # fallback pass.  Raising it there would leave a ring of antialiased halo
    # pixels behind the erased text.
    for ratio in ratios:
        cap = 64.0 if solid and ratio >= 0.5 else 48.0
        delta = max(12.0, min(cap, contrast * ratio))
        candidates: list[np.ndarray] = []
        if dark_text is not False:
            candidates.append(gray < background_gray - delta)
        if dark_text is not True:
            candidates.append(gray > background_gray + delta)
        choices: list[tuple[float, np.ndarray]] = []
        for mask in candidates:
            if not mask.any():
                continue
            coverage = float(mask.mean())
            if not minimum_coverage <= coverage <= 0.88:
                continue
            median_value = float(np.median(gray[mask]))
            strength = abs(background_gray - median_value) * math.sqrt(max(coverage, 1e-4))
            choices.append((strength, mask))
        if not choices:
            continue
        choices.sort(key=lambda item: item[0], reverse=True)
        mask = choices[0][1].astype(np.uint8) * 255
        kernel = np.ones((2, 2), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        if not mask.any():
            continue
        # A higher threshold can shatter a very light face.  Retry with the
        # normal threshold rather than handing a useless mask to the matcher.
        if solid and float(mask.mean()) / 255.0 < minimum_coverage:
            continue
        return mask
    return None


def _glyph_mask(
    gray: np.ndarray,
    background_gray: float,
    solid: bool = False,
    dark_text: bool | None = None,
) -> np.ndarray | None:
    """Extract a tight text mask from a calm crop using the sampled background."""
    diff = np.abs(gray.astype(np.float32) - background_gray)
    contrast = float(np.percentile(diff, 96)) if diff.size else 0.0
    mask = _crop_glyph_mask(
        gray, background_gray, contrast, solid=solid, dark_text=dark_text
    )
    if mask is None:
        return None
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    if rows.size == 0 or cols.size == 0:
        return None
    tight = mask[rows[0] : rows[-1] + 1, cols[0] : cols[-1] + 1]
    if tight.shape[0] < 4 or tight.shape[1] < 4:
        return None
    coverage = float(tight.mean()) / 255.0
    if not 0.006 <= coverage <= 0.92:
        return None
    return tight.astype(np.uint8)


def _shift_mask(mask: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """Shift a binary mask without wrapping pixels around the crop edge."""
    height, width = mask.shape[:2]
    shifted = np.zeros_like(mask)
    src_x0 = max(0, -dx)
    src_x1 = min(width, width - dx)
    src_y0 = max(0, -dy)
    src_y1 = min(height, height - dy)
    if src_x1 <= src_x0 or src_y1 <= src_y0:
        return shifted
    shifted[src_y0 + dy : src_y1 + dy, src_x0 + dx : src_x1 + dx] = mask[
        src_y0:src_y1, src_x0:src_x1
    ]
    return shifted


def _detect_drop_shadow(
    gray: np.ndarray,
    rgb: np.ndarray,
    background: tuple[int, int, int],
    foreground: tuple[int, int, int],
    box_height: int,
    background_spread: float = 0.0,
) -> dict[str, Any] | None:
    """Detect an original drop shadow so it can be reproduced after editing.

    The old renderer only erased text and never recreated a shadow, so a real
    shadow disappeared while a badly repaired halo looked like a new one.  This
    looks for a consistent, directionally offset second copy of the glyphs:

    * strong ink pixels define the glyph core;
    * a shifted copy of the core must land mostly on pixels that deviate from
      the background in one direction;
    * the opposite offset must score much lower (otherwise it is just a bold
      stroke or a symmetric outline, not a drop shadow).

    The detected colour, offset, and approximate softness are stored in the
    line style and drawn behind replacement text.  Detection is deliberately
    conservative: no shadow is better than inventing one on clean text.
    """
    height, width = gray.shape[:2]
    if height < 8 or width < 8 or box_height < 6:
        return None
    if background_spread > 60.0:
        # A busy photo/background produces too many false matches.  Keep the
        # old behaviour (no synthetic shadow) rather than inventing one.
        return None
    background_lum = _luminance(background)
    foreground_lum = _luminance(foreground)
    ink_delta = foreground_lum - background_lum
    if abs(ink_delta) < 15.0:
        return None
    ink_sign = 1.0 if ink_delta > 0 else -1.0
    signed = (gray.astype(np.float32) - background_lum) * ink_sign
    contrast = float(np.percentile(signed, 97)) if signed.size else 0.0
    if contrast < 18.0:
        return None

    # Keep only solid strokes.  A shadow is lighter than the ink (or opposite
    # in polarity), so it stays outside this high threshold.
    core = signed >= max(14.0, contrast * 0.80)
    if int(core.sum()) < 24:
        core = signed >= max(12.0, contrast * 0.64)
    core_pixels = int(core.sum())
    if core_pixels < 16:
        return None
    core_u8 = core.astype(np.uint8)
    core_dilated = cv2.dilate(core_u8, np.ones((3, 3), np.uint8), iterations=1) > 0

    max_offset = max(2, min(18, int(round(box_height * 0.32))))
    min_distance = max(2.0, box_height * 0.055)
    min_region = max(24, int(core_pixels * 0.04))
    low = max(2.5, contrast * 0.045)

    scores: dict[tuple[int, int, float], tuple[float, float, float]] = {}
    for polarity in (1.0, -1.0):
        shadow_values = signed * polarity
        for dy in range(-max_offset, max_offset + 1):
            for dx in range(-max_offset, max_offset + 1):
                distance = math.hypot(dx, dy)
                if distance < min_distance or distance > max_offset:
                    continue
                shifted = _shift_mask(core_u8, dx, dy) > 0
                region = shifted & ~core_dilated
                count = int(region.sum())
                if count < min_region:
                    continue
                values = shadow_values[region]
                lit = values >= low
                # Other glyphs/strokes may land in the shifted region.  Their
                # ink is far stronger than a shadow; excluding it stops a
                # neighbouring letter from being mistaken for a shadow.
                lit &= values < contrast * 0.90
                if not np.any(lit):
                    continue
                coverage = float(np.count_nonzero(lit)) / float(count)
                if coverage < 0.30:
                    continue
                magnitude = float(np.median(values[lit]))
                if magnitude < max(3.0, contrast * 0.05):
                    continue
                score = (
                    coverage
                    * math.sqrt(min(magnitude, contrast))
                    * (1.0 + min(count / max(1, core_pixels), 1.0))
                )
                if score > 0:
                    scores[(dx, dy, polarity)] = (score, coverage, magnitude)

    if not scores:
        return None
    best_key = max(scores, key=lambda key: scores[key][0])
    best_score, _coverage, _magnitude = scores[best_key]
    best_dx, best_dy, best_polarity = best_key
    # Clean, high-contrast text can produce a weak directional match from
    # antialiasing alone (a demo banner scored 3.46 here).  Genuine hard or soft
    # shadows score far higher, so 5.0 keeps the detector conservative without
    # losing real shadows.
    if best_score < 5.0:
        return None
    opposite = scores.get((-best_dx, -best_dy, best_polarity))
    if opposite is not None and best_score < opposite[0] * 1.30 + 0.5:
        # Symmetric thickening, outline or blur, not a directional shadow.
        return None

    # Refine the offset from the actual shadow extent.  The candidate search
    # peaks near the outer edge of a soft shadow, so the widest observed
    # extension is a useful upper bound for the visible offset.
    shadow_values = signed * best_polarity
    band = shadow_values >= max(2.0, contrast * 0.035)
    ys = np.flatnonzero(core.any(axis=1))
    xs = np.flatnonzero(core.any(axis=0))
    if ys.size == 0 or xs.size == 0:
        return None

    def _edge_extent(values: list[float]) -> float:
        if not values:
            return 0.0
        return float(np.percentile(np.asarray(values, dtype=np.float32), 75))

    right: list[float] = []
    left: list[float] = []
    for row in ys:
        core_xs = np.flatnonzero(core[row])
        band_xs = np.flatnonzero(band[row])
        if core_xs.size == 0 or band_xs.size == 0:
            continue
        right.append(float(band_xs[-1] - core_xs[-1]))
        left.append(float(core_xs[0] - band_xs[0]))

    bottom: list[float] = []
    top: list[float] = []
    for col in xs:
        core_ys = np.flatnonzero(core[:, col])
        band_ys = np.flatnonzero(band[:, col])
        if core_ys.size == 0 or band_ys.size == 0:
            continue
        bottom.append(float(band_ys[-1] - core_ys[-1]))
        top.append(float(core_ys[0] - band_ys[0]))

    extent_x = 0.0
    extent_y = 0.0
    if best_dx > 0:
        extent_x = _edge_extent(right)
    elif best_dx < 0:
        extent_x = _edge_extent(left)
    if best_dy > 0:
        extent_y = _edge_extent(bottom)
    elif best_dy < 0:
        extent_y = _edge_extent(top)

    # A stray noise pixel must not stretch the shadow across the crop.
    extent_x = min(max(0.0, extent_x), abs(best_dx) + 2.0)
    extent_y = min(max(0.0, extent_y), abs(best_dy) + 2.0)
    offset_x = abs(best_dx)
    offset_y = abs(best_dy)
    if extent_x >= 1.5:
        offset_x = min(offset_x, max(1, int(round(extent_x))))
    if extent_y >= 1.5:
        offset_y = min(offset_y, max(1, int(round(extent_y))))
    offset_x = max(1, offset_x) if best_dx else 0
    offset_y = max(1, offset_y) if best_dy else 0

    excess = max(0.0, extent_x - offset_x, extent_y - offset_y)
    blur = int(max(0, min(3, round(excess * 0.60))))

    # Sample the shadow colour from its strongest visible pixels.  On a flat
    # background this is the shadow's blended colour; drawing it opaquely at
    # the same offset reproduces the original appearance.
    shifted = _shift_mask(core_u8, best_dx, best_dy) > 0
    region = shifted & ~core_dilated
    region_y, region_x = np.nonzero(region)
    if region_y.size == 0:
        return None
    values = shadow_values[region_y, region_x]
    keep = (values >= low) & (values < contrast * 0.90)
    if not np.any(keep):
        return None
    kept_values = values[keep]
    pixels = rgb[region_y[keep], region_x[keep]].astype(np.float32)
    target = float(np.percentile(kept_values, 70))
    spread = float(np.percentile(kept_values, 90) - np.percentile(kept_values, 10))
    band_half = max(4.0, spread * 0.22)
    near = np.abs(kept_values - target) <= band_half
    if np.any(near):
        pixels = pixels[near]
    colour = _robust_median(pixels, axis=0)
    shadow_color = _hex(colour)
    # A "shadow" indistinguishable from the background is not worth drawing.
    if abs(_luminance(colour) - background_lum) < 2.5:
        return None

    return {
        "color": shadow_color,
        "dx": int(offset_x if best_dx > 0 else -offset_x),
        "dy": int(offset_y if best_dy > 0 else -offset_y),
        "blur": blur,
    }


def _line_shadow(
    image: Image.Image,
    line: dict[str, Any],
    background: tuple[int, int, int],
    foreground: tuple[int, int, int],
    background_spread: float | None = None,
) -> dict[str, Any] | None:
    """Sample a line crop and return its original shadow, if any."""
    box = line.get("box") or {}
    width, height = image.size
    x0 = max(0, int(box.get("x", 0.0)))
    y0 = max(0, int(box.get("y", 0.0)))
    x1 = min(width, x0 + max(1, int(box.get("width", 1.0))))
    y1 = min(height, y0 + max(1, int(box.get("height", 1.0))))
    if x1 <= x0 or y1 <= y0:
        return None
    line_height = y1 - y0
    if background_spread is None:
        ring = max(5, int(round(line_height * 0.55)))
        expanded = np.asarray(
            image.crop(
                (
                    max(0, x0 - ring),
                    max(0, y0 - ring),
                    min(width, x1 + ring),
                    min(height, y1 + ring),
                )
            ).convert("RGB"),
            dtype=np.uint8,
        )
        ring_pixels = _border_pixels(
            expanded,
            max(2, min(ring, expanded.shape[0] // 2, expanded.shape[1] // 2)),
        )
        gray_border = cv2.cvtColor(
            np.ascontiguousarray(ring_pixels.reshape(-1, 1, 3)),
            cv2.COLOR_RGB2GRAY,
        ).reshape(-1)
        background_spread = _tone_spread(gray_border) if gray_border.size else 0.0

    pad = max(2, min(18, int(round(line_height * 0.28))))
    crop_rgb = np.asarray(
        image.crop(
            (max(0, x0 - pad), max(0, y0 - pad), min(width, x1 + pad), min(height, y1 + pad))
        ).convert("RGB"),
        dtype=np.uint8,
    )
    if crop_rgb.size == 0:
        return None
    crop_gray = cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2GRAY)
    return _detect_drop_shadow(
        crop_gray,
        crop_rgb,
        background,
        foreground,
        line_height,
        float(background_spread),
    )


def _estimate_font_size(
    text: str,
    face: FontFace,
    ink_height: int,
    ink_width: int | None = None,
    box_height: int | None = None,
) -> int | None:
    """Infer the original font size from the height of its ink."""
    if ink_height < 4:
        return None
    try:
        font = ImageFont.truetype(str(face.path), size=96, index=face.index)
        scratch = Image.new("L", (8, 8), 0)
        bbox = ImageDraw.Draw(scratch).textbbox((0, 0), text, font=font)
    except (OSError, ValueError, RuntimeError):
        return None
    rendered_height = bbox[3] - bbox[1]
    rendered_width = bbox[2] - bbox[0]
    if rendered_height < 4:
        return None
    height_size = 96.0 * ink_height / rendered_height
    size = height_size
    if ink_width and rendered_width > 4:
        width_size = 96.0 * ink_width / rendered_width
        # The tight height mask is the distance between the topmost and
        # bottommost ink pixels.  A single speck of photo texture, a clipped
        # neighbour row or a stray descender can inflate it by half, which
        # produced replacement text far larger than the source.  For a long
        # run the width averaged over many glyphs is much more stable, so use
        # it when it is smaller than the height estimate.  When width is
        # larger, extra tracking or a condensed source is the likely cause and
        # the height stays authoritative.  Short strings keep the old
        # agreement rule because one wide glyph dominates their width.
        compact = re.sub(r"\s+", "", text)
        if len(compact) >= 8 and width_size < height_size:
            size = width_size
        elif 0.80 <= width_size / max(1.0, height_size) <= 1.20:
            size = (height_size + width_size) / 2.0
    size = int(round(size))
    if box_height:
        size = min(size, max(6, int(round(box_height * 1.8))))
    return max(6, min(400, size))


def _detect_typeface(
    text: str,
    rgb: np.ndarray,
    background_gray: float,
    box_height: int,
    foreground_gray: float | None = None,
) -> dict[str, Any]:
    """Pick the closest available face and estimate the original point size."""
    fallback = default_face("sans")
    fallback_result = {
        "font": "sans",
        "bold": False,
        "italic": False,
        "font_id": fallback.font_id,
        "font_name": fallback.family,
        "font_confidence": 0,
        "font_size": None,
    }
    if not text or "\n" in text or not LATIN_STYLE_RE.fullmatch(text):
        return fallback_result

    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    dark_text: bool | None = None
    if (
        foreground_gray is not None
        and math.isfinite(foreground_gray)
        and abs(foreground_gray - background_gray) >= 24.0
    ):
        # Only trust the sampled colour when it is clearly distinct from the
        # paper; near-equal luminance gives no reliable polarity signal.
        dark_text = foreground_gray < background_gray
    mask = _glyph_mask(gray, background_gray, solid=True, dark_text=dark_text)
    if mask is None and dark_text is not None:
        # The foreground sample can belong to an antialiased edge whose
        # luminance sits on the "wrong" side.  Retry automatically rather than
        # dropping the whole line to the generic fallback.
        mask = _glyph_mask(gray, background_gray, solid=True)
    if mask is None:
        return fallback_result

    first_pass: list[tuple[float, str, FontFace]] = []
    for category in STYLE_FAMILIES:
        for face in match_faces(category, limit=4):
            candidate = _render_font_mask(text, face)
            score = _mask_similarity(mask, candidate)
            if math.isfinite(score):
                first_pass.append((score, category, face))
    if not first_pass:
        return fallback_result

    first_pass.sort(key=lambda item: item[0])
    best_score, category, best_face = first_pass[0]
    second_score = first_pass[1][0] if len(first_pass) > 1 else best_score
    margin = (second_score - best_score) / max(second_score, 1.0)
    family_confident = margin >= FAMILY_MARGIN_MIN

    # When the family margin is thin, prefer the category's stable default
    # face.  Guessing an arbitrary system font from a near-tie is how an
    # unchanged line ended up rendered in a completely different typeface
    # after a small edit.  Keep the closest face only when it is clearly
    # better than that default; otherwise a document using Liberation/Noto
    # would silently be repainted in the bundled DejaVu face.
    if not family_confident:
        default_candidate = default_face(category, False, False)
        candidate = _render_font_mask(text, default_candidate)
        default_score = _mask_similarity(mask, candidate)
        if math.isfinite(default_score) and default_score <= best_score * 1.06:
            best_face = default_candidate
            best_score = default_score

    quality = max(0.0, min(1.0, 1.0 - best_score / 12000.0))
    regular_score = best_score
    regular_face = best_face
    bold = False
    italic = False
    chosen = best_face

    # Score every variant of the chosen family once.  The old loop let a
    # moderately good bold-italic overwrite a better bold upright choice (and
    # compared italic only against the regular face).  Weight and slant are
    # separate properties: bold must win against the upright counterpart,
    # italic against the *same-weight* upright counterpart.
    if quality >= 0.20:
        variant_scores: dict[tuple[bool, bool], tuple[float, FontFace]] = {}
        for variant in variants_for_face(best_face):
            if variant.font_id == regular_face.font_id:
                score = regular_score
            else:
                candidate = _render_font_mask(text, variant)
                score = _mask_similarity(mask, candidate)
            if not math.isfinite(score):
                continue
            key = (bool(variant.bold), bool(variant.italic))
            existing = variant_scores.get(key)
            if existing is None or score < existing[0]:
                variant_scores[key] = (score, variant)

        def variant_score(key: tuple[bool, bool]) -> float:
            value = variant_scores.get(key)
            return value[0] if value is not None else float("inf")

        upright = variant_score((False, False))
        bold_upright = variant_score((True, False))
        italic_upright = variant_score((False, True))
        bold_italic = variant_score((True, True))
        bold_from_upright = (
            math.isfinite(bold_upright)
            and upright > 0
            and bold_upright <= upright * (1.0 - BOLD_RELATIVE_GAIN)
        )
        bold_from_italic = (
            math.isfinite(bold_italic)
            and math.isfinite(italic_upright)
            and italic_upright > 0
            and bold_italic <= italic_upright * (1.0 - BOLD_RELATIVE_GAIN)
        )
        italic_from_upright = (
            math.isfinite(italic_upright)
            and upright > 0
            and italic_upright <= upright * (1.0 - ITALIC_RELATIVE_GAIN)
        )
        italic_from_bold = (
            math.isfinite(bold_italic)
            and math.isfinite(bold_upright)
            and bold_upright > 0
            and bold_italic <= bold_upright * (1.0 - ITALIC_RELATIVE_GAIN)
        )
        bold = bold_from_upright or bold_from_italic
        italic = italic_from_upright or italic_from_bold

        desired = variant_scores.get((bold, italic))
        if desired is not None:
            chosen = desired[1]
        elif bold or italic:
            # The family may not ship every combination.  Keep the strongest
            # available variant rather than silently falling back to regular.
            available = [
                entry
                for key, entry in variant_scores.items()
                if key != (False, False)
            ]
            chosen = min(available, key=lambda item: item[0])[1] if available else regular_face
        else:
            chosen = regular_face
        # Keep the reported flags consistent with the face that will render.
        bold = bool(chosen.bold)
        italic = bool(chosen.italic)

    confidence = int(round(max(0.0, min(1.0, margin * 2.5)) * quality * 100))
    size = _estimate_font_size(text, chosen, mask.shape[0], mask.shape[1], box_height)

    return {
        "font": category,
        "bold": bold,
        "italic": italic,
        "font_id": chosen.font_id,
        "font_name": chosen.family,
        "font_confidence": confidence,
        "font_size": size,
    }


def _sample_palette(image: Image.Image, box: dict[str, Any]) -> dict[str, Any] | None:
    """Sample local background and dominant foreground colour for a box.

    Both the whole-line style sampler and the per-word style splitter use this
    same code, so a colour comparison between two words is measured with the
    same antialiasing/shadow rules as the final stored line colour.
    """
    width, height = image.size
    x0 = max(0, int(box.get("x", 0.0)))
    y0 = max(0, int(box.get("y", 0.0)))
    x1 = min(width, x0 + max(1, int(box.get("width", 1.0))))
    y1 = min(height, y0 + max(1, int(box.get("height", 1.0))))
    if x1 <= x0 or y1 <= y0:
        return None

    # A tight OCR box cuts through glyph tops/bottoms.  Sample the background
    # from a ring *outside* the box instead of the box border.
    box_height = y1 - y0
    ring = max(5, int(round(box_height * 0.55)))
    ex0 = max(0, x0 - ring)
    ey0 = max(0, y0 - ring)
    ex1 = min(width, x1 + ring)
    ey1 = min(height, y1 + ring)
    expanded = np.asarray(image.crop((ex0, ey0, ex1, ey1)).convert("RGB"), dtype=np.uint8)
    border = _border_pixels(
        expanded, max(2, min(ring, expanded.shape[0] // 2, expanded.shape[1] // 2))
    )
    background = _robust_median(border, axis=0)
    bg_lum = _luminance(background)

    rgb = np.asarray(image.crop((x0, y0, x1, y1)).convert("RGB"), dtype=np.uint8)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    border_gray = cv2.cvtColor(expanded, cv2.COLOR_RGB2GRAY)[_mask_for_border(expanded, ring)]
    bg_gray = (
        float(_robust_median(border_gray))
        if border_gray.size
        else float(_robust_median(gray.reshape(-1)))
    )
    background_spread = _tone_spread(border_gray) if border_gray.size else 0.0
    diff = np.abs(gray.astype(np.int16) - bg_gray)
    contrast = float(np.percentile(diff, 96)) if diff.size else 0.0

    foreground: tuple[int, int, int]
    selected = np.zeros_like(gray, dtype=bool)
    if contrast >= 14:
        threshold = max(14.0, contrast * 0.36)
        selected = diff > threshold
        coverage = float(selected.mean())
        if 0.004 <= coverage <= 0.86:
            pixels = rgb[selected].astype(np.float32)
            # Pick the dominant stroke colour, not the average of strokes,
            # antialiasing and the drop shadow.  Shadows form a gradient below
            # the glyphs; the solid ink occupies the darkest (or lightest)
            # cluster for the corresponding polarity.
            luminance = (
                pixels[:, 0] * 0.2126 + pixels[:, 1] * 0.7152 + pixels[:, 2] * 0.0722
            )
            if bg_lum >= 128.0:
                target = float(np.percentile(luminance, 18))
            else:
                target = float(np.percentile(luminance, 82))
            spread = float(np.percentile(luminance, 90) - np.percentile(luminance, 10))
            band = max(10.0, spread * 0.22)
            dominant = pixels[np.abs(luminance - target) <= band]
            if dominant.size == 0:
                dominant = pixels
            colour = _robust_median(dominant, axis=0)
            foreground = tuple(int(round(float(value))) for value in colour[:3])
        else:
            foreground = (22, 22, 20) if bg_lum > 128 else (245, 243, 234)
    else:
        foreground = (22, 22, 20) if bg_lum > 128 else (245, 243, 234)

    if abs(_luminance(foreground) - bg_lum) < 38:
        foreground = (22, 22, 20) if bg_lum > 128 else (245, 243, 234)

    return {
        "foreground": foreground,
        "background": tuple(int(round(float(value))) for value in background[:3]),
        "bg_gray": bg_gray,
        "spread": background_spread,
        "contrast": contrast,
        "rgb": rgb,
        "gray": gray,
    }


def _longest_bridged_run(
    values: Any,
    max_gap: int,
    gap_check: Any = None,
) -> tuple[int, int, int]:
    """Longest horizontal ink run allowing small gaps between letters.

    Returns ``(start, end, ink)`` where ``end`` is exclusive and ``ink`` is the
    number of actually set pixels.  Underlines and strikethroughs are one
    continuous stroke, so their fill fraction is high; the bottom edge of a
    word is mostly empty space between stems and therefore scores low even
    though a permissive gap bridge can connect it.

    ``gap_check`` optionally receives ``(gap_start, gap_end)`` and decides
    whether a non-zero gap may be bridged.  Underline detection uses it to
    join the rule across a descender without also joining ordinary letter
    gaps or serif feet.
    """
    best = (0, 0, 0)
    start: int | None = None
    last = -1
    ink = 0
    for index, value in enumerate(values):
        if not bool(value):
            continue
        if start is None:
            start = index
            last = index
            ink = 1
            continue
        gap = index - last - 1
        if gap <= max_gap and (
            gap == 0 or gap_check is None or gap_check(last + 1, index)
        ):
            last = index
            ink += 1
            continue
        length = last - start + 1
        if length > best[1] - best[0]:
            best = (start, last + 1, ink)
        start = index
        last = index
        ink = 1
    if start is not None:
        length = last - start + 1
        if length > best[1] - best[0]:
            best = (start, last + 1, ink)
    return best


def _detect_decorations_from_mask(
    mask: Any,
    box_height: float = 0.0,
    text: str | None = None,
    rule_bounds: tuple[float, float] | None = None,
    max_extension: float = 0.0,
    text_bounds: tuple[float, float] | None = None,
    relaxed: bool = False,
) -> dict[str, Any]:
    """Detect underline/strikethrough strokes in a tight ink mask.

    ``text`` is used to reject decorations on very short crops.  A one- to
    three-character crop can be a single glyph whose own crossbar or bottom
    bar (``H``, ``e``, ``fi``, ``www``, ``E``) spans the whole crop and looks
    exactly like a rule.  Suppressing the decision for very short words is
    safer than adding a decoration to ordinary text; longer words have several
    glyph components, so a rule crossing all of them is real evidence.

    ``rule_bounds`` is the original text box's horizontal span inside this
    mask.  It lets a line-level sample reject a card/table border that happens
    to sit just below the glyphs: a real underline stays close to the text
    width, while a border continues well past it.  Word-level samples omit the
    bounds because a line-wide underline legitimately continues past one word.

    ``text_bounds`` is the target line's vertical span inside the mask.  The
    crop is padded to include an underline and therefore often catches the top
    of the next line too.  Without the bounds that neighbouring ink made an
    underline look like a strike (ink both above and below the rule); a real
    strikethrough has ink above and below it *within the same text line*.
    """
    empty = {"underline": False, "strikethrough": False}
    if mask is None:
        return empty
    try:
        height, width = int(mask.shape[0]), int(mask.shape[1])
    except (AttributeError, IndexError, TypeError, ValueError):
        return empty
    if height < 6 or width < 8:
        return empty
    # A strikethrough is a solid, continuous stroke spanning nearly the whole
    # word.  An underline can be interrupted by a descender (browsers skip the
    # rule around "q", "y", and so on), so it gets a small bridge, but only
    # across gaps that actually contain ink below the rule.  Serif feet have no
    # ink below them, and ordinary letter gaps do not either, so they cannot be
    # joined into a false rule.
    if rule_bounds is not None:
        # The crop is deliberately wider than the text so a border's true
        # extent is visible; measure the rule against the text span, not the
        # padded crop.
        span = max(1.0, float(rule_bounds[1]) - float(rule_bounds[0]))
    else:
        span = max(1.0, float(width))
    strike_minimum = max(10.0, span * 0.80)
    underline_minimum = max(10.0, span * 0.75)

    strike_rows: list[tuple[int, int, int, float]] = []
    for row_index in range(height):
        start, end, ink = _longest_bridged_run(mask[row_index], 0)
        length = end - start
        if length < strike_minimum:
            continue
        fill = ink / max(1.0, float(length))
        if fill >= 0.90:
            strike_rows.append((row_index, start, end, fill))

    # Printed and scanned rules fade at word spaces and around faint glyphs,
    # so a real strike can arrive as several long runs instead of one.  A row
    # whose ink still covers the text span is a rule, as long as the rows next
    # to it are ordinary text rather than another dense band (a solid graphic
    # would otherwise qualify on every row).
    strike_row_indexes = {row[0] for row in strike_rows}
    for row_index in range(height):
        if row_index in strike_row_indexes:
            continue
        row = mask[row_index]
        coverage = np.count_nonzero(row) / max(1.0, span)
        if coverage < 0.90:
            continue
        columns = np.flatnonzero(row)
        if columns.size == 0:
            continue
        start = int(columns[0])
        end = int(columns[-1]) + 1
        if end - start < strike_minimum:
            continue
        flanked = False
        for neighbour in (row_index - 1, row_index + 1):
            if 0 <= neighbour < height:
                neighbour_coverage = np.count_nonzero(mask[neighbour]) / max(
                    1.0, span
                )
                if 0.75 < neighbour_coverage < 0.90:
                    flanked = True
                    break
        if flanked:
            continue
        strike_rows.append((row_index, start, end, min(1.0, coverage)))
    strike_rows.sort(key=lambda row: row[0])

    underline_rows: list[tuple[int, int, int, float]] = []
    underline_bridge = max(1, int(round(height * 0.25)))
    for row_index in range(height):
        if text_bounds is not None:
            below_limit = min(
                height - 1,
                int(round(float(text_bounds[1]) + max(2.0, height * 0.10))),
            )
        else:
            below_limit = min(
                height - 1,
                row_index + max(2, int(round(height * 0.15))),
            )

        def gap_has_ink_below(
            gap_start: int,
            gap_end: int,
            row_index: int = row_index,
            below_limit: int = below_limit,
        ) -> bool:
            for x in range(gap_start, gap_end):
                for y in range(row_index + 1, below_limit + 1):
                    if mask[y][x]:
                        return True
            if (
                relaxed
                and text_bounds is not None
                and row_index >= float(text_bounds[1]) - 1.0
            ):
                # A line-level underline at the bottom of the text has no
                # glyph descenders below it, so JPEG noise can break a faint
                # rule into 1-5 px segments.  The fill/zone checks still
                # reject ordinary glyph bottoms, which are mostly empty.
                return (gap_end - gap_start) <= underline_bridge
            return False

        start, end, ink = _longest_bridged_run(
            mask[row_index], underline_bridge, gap_has_ink_below
        )
        length = end - start
        if length < underline_minimum:
            continue
        fill = ink / max(1.0, float(length))
        if fill >= 0.90:
            underline_rows.append((row_index, start, end, fill))

    if not strike_rows and not underline_rows:
        return empty

    def grouped(
        rows: list[tuple[int, int, int, float]],
    ) -> list[list[tuple[int, int, int, float]]]:
        if not rows:
            return []
        groups: list[list[tuple[int, int, int, float]]] = [[rows[0]]]
        for row in rows[1:]:
            if row[0] - groups[-1][-1][0] <= 1:
                groups[-1].append(row)
            else:
                groups.append([row])
        return groups

    limit = max(3.0, height * 0.18)
    ink_rows = [
        row_index
        for row_index in range(height)
        if any(bool(value) for value in mask[row_index])
    ]
    underline = False
    strikethrough = False

    def text_span() -> tuple[float, float, float]:
        if text_bounds is not None:
            text_top = float(text_bounds[0])
            text_bottom = float(text_bounds[1])
            return text_top, text_bottom, max(1.0, text_bottom - text_top)
        return 0.0, float(height), max(1.0, float(height))

    def rule_is_outside_text(group: list[tuple[int, int, int, float]]) -> bool:
        if rule_bounds is None or max_extension <= 0.0:
            return False
        rule_start = min(row[1] for row in group)
        rule_end = max(row[2] for row in group)
        extension = max(
            max(0.0, float(rule_bounds[0]) - rule_start),
            max(0.0, rule_end - float(rule_bounds[1])),
        )
        # A rule far wider than the text is a card/table border or an
        # underline belonging to a different line, not this line's decoration.
        # Allow a little side bearing and antialiasing.
        return extension > max_extension or (
            rule_end - rule_start > max(span * 1.35, span + 12.0)
        )

    for group in grouped(strike_rows):
        if len(group) > limit or rule_is_outside_text(group):
            continue
        start, end = group[0][0], group[-1][0]
        text_top, text_bottom, reference_height = text_span()
        centre = ((start + end) / 2.0 - text_top) / reference_height
        if not (0.27 <= centre <= 0.68):
            continue
        rule_rows = {row[0] for row in strike_rows}
        above = [
            row_index
            for row_index in ink_rows
            if text_top <= row_index < start and row_index not in rule_rows
        ]
        below = [
            row_index
            for row_index in ink_rows
            if end < row_index <= text_bottom and row_index not in rule_rows
        ]
        # A strikethrough passes through the glyph mass: there must be text
        # ink both above and below it.  This rejects a horizontal table border
        # at the top or bottom of the crop.
        if above and below:
            strikethrough = True

    for group in grouped(underline_rows):
        if len(group) > limit or rule_is_outside_text(group):
            continue
        start, end = group[0][0], group[-1][0]
        text_top, text_bottom, reference_height = text_span()
        if (
            text_bounds is not None
            and start > text_bottom + max(3.0, reference_height * 0.25) + 1.0
        ):
            # The padded crop can reach the next line's rule.  A real
            # underline hugs this box's bottom; a stroke ten rows below it is
            # the next row's decoration, not this one's.
            continue
        centre = ((start + end) / 2.0 - text_top) / reference_height
        if centre < 0.72:
            continue
        rule_rows = {row[0] for row in underline_rows}
        above = [
            row_index
            for row_index in ink_rows
            if text_top <= row_index < start and row_index not in rule_rows
        ]
        if not above:
            continue
        gap = start - max(above) - 1
        # A real underline often touches the lowest antialiased pixel of a
        # descender, so a zero gap is allowed once we know the rule is in this
        # line's underline zone.  Without text bounds keep the stricter one-row
        # gap used to reject serif feet.
        minimum_gap = 0 if text_bounds is not None else 1
        # The crop is padded below the box to reveal an underline, so allow a
        # rule that sits just outside a tight OCR box.  The cap keeps a border
        # farther down from being mistaken for one.
        padding_allowance = min(
            reference_height * 0.50,
            max(0.0, height - reference_height) * 0.60,
        )
        maximum_gap = max(2.0, reference_height * 0.22, padding_allowance)
        box_bottom_offset = start - text_bottom
        if (
            relaxed
            and minimum_gap <= gap
            and text_bounds is not None
            and 0 <= box_bottom_offset <= max(3.0, reference_height * 0.25)
        ):
            # The tight OCR box ends at the glyphs, not at the font descent.
            # A short word without descenders can put its underline a few
            # rows below the last ink while still sitting just under the box.
            underline = True
        elif minimum_gap <= gap <= maximum_gap:
            underline = True
    if text is not None:
        compact = re.sub(r"\s+", "", text)
        if len(compact) < 4:
            # A short crop cannot prove either decoration: its own glyph
            # strokes (the flat bottom of ``E``, an icon frame, ``[fy``) can
            # span the whole box and look exactly like a rule.  A short word
            # inside a genuinely underlined/struck line inherits the line's
            # decision later, in ``_word_feature``.
            strikethrough = False
            underline = False
    return {"underline": underline, "strikethrough": strikethrough}


def _decorations_for_box(
    image: Image.Image,
    box: dict[str, Any],
    text: str = "",
    bounded: bool = False,
    sensitive: bool = False,
) -> dict[str, Any]:
    """Sample underline/strikethrough around a word or line box.

    OCR boxes are ink-tight for glyphs but do not include a decoration below or
    through the text.  The crop is expanded slightly so the rule is visible,
    while the limited padding avoids reaching the neighbouring line.  ``text``
    lets the detector distinguish a real strike from a glyph crossbar.

    ``bounded`` is used for a whole line/selection.  It widens the crop
    horizontally so a nearby card or table border is visible beyond the text
    box and can be rejected; a genuine underline ends with the text.
    """
    width, height = image.size
    try:
        x0 = float(box.get("x", 0.0))
        y0 = float(box.get("y", 0.0))
        w = max(1.0, float(box.get("width", 1.0)))
        h = max(1.0, float(box.get("height", 1.0)))
    except (TypeError, ValueError):
        return {"underline": False, "strikethrough": False}
    pad_top = max(1.0, h * 0.14)
    pad_bottom = max(2.0, h * 0.42)
    pad_x = max(1.0, min(h * 0.20, 6.0))
    if bounded:
        # A line-level crop needs enough context to tell a text underline from
        # a card/table border that continues past the line box.
        pad_x = max(pad_x, min(h * 0.80, 60.0))
    expanded = {
        "x": max(0, int(math.floor(x0 - pad_x))),
        "y": max(0, int(math.floor(y0 - pad_top))),
        "width": max(1, int(math.ceil(w + pad_x * 2))),
        "height": max(1, int(math.ceil(h + pad_top + pad_bottom))),
    }
    if expanded["x"] + expanded["width"] > width:
        expanded["width"] = width - expanded["x"]
    if expanded["y"] + expanded["height"] > height:
        expanded["height"] = height - expanded["y"]
    palette = _sample_palette(image, expanded)
    if palette is None:
        return {"underline": False, "strikethrough": False}
    background_gray = float(palette["bg_gray"])
    foreground_gray = _luminance(palette["foreground"])
    dark_text: bool | None = None
    if abs(foreground_gray - background_gray) >= 24.0:
        dark_text = foreground_gray < background_gray
    gray = palette["gray"]
    diff = np.abs(gray.astype(np.float32) - background_gray)
    contrast = float(np.percentile(diff, 96)) if diff.size else 0.0
    if contrast < 14.0:
        return {"underline": False, "strikethrough": False}
    delta = max(12.0, min(48.0, contrast * (0.16 if sensitive else 0.35)))
    raw = diff > delta
    if dark_text is not None:
        # Keep the text/decoration polarity.  This rejects the opposite edge
        # of an embossed background gradient while retaining antialiasing.
        raw &= (gray < background_gray) if dark_text else (gray > background_gray)
    if not raw.any():
        return {"underline": False, "strikethrough": False}
    rows = np.flatnonzero(raw.any(axis=1))
    cols = np.flatnonzero(raw.any(axis=0))
    if rows.size == 0 or cols.size == 0:
        return {"underline": False, "strikethrough": False}
    # Deliberately do *not* use the solid glyph mask here: its 2x2 opening can
    # erase a one-pixel underline, which is exactly the decoration we need to
    # find.  The raw threshold keeps thin strokes intact.
    raw_mask = raw[
        rows[0] : rows[-1] + 1,
        cols[0] : cols[-1] + 1,
    ].astype(np.uint8) * 255
    rule_bounds = None
    max_extension = 0.0
    if bounded:
        left = (x0 - expanded["x"]) - float(cols[0])
        right = (x0 + w - expanded["x"]) - float(cols[0])
        rule_bounds = (left, right)
        max_extension = max(6.0, h * 0.50, w * 0.05 if sensitive else 0.0)
    # The crop is padded vertically to reveal a decoration, which also catches
    # the neighbouring line.  Pass the target box's own span so the detector
    # only treats ink from this line as evidence for a strikethrough.
    text_top = (y0 - expanded["y"]) - float(rows[0])
    text_bottom = (y0 + h - expanded["y"]) - float(rows[0])
    return _detect_decorations_from_mask(
        raw_mask,
        h,
        text,
        rule_bounds,
        max_extension,
        (text_top, text_bottom),
        relaxed=sensitive,
    )


def sample_line_style(image: Image.Image, line: dict[str, Any]) -> dict[str, Any]:
    """Estimate foreground/background colours and the original typography."""
    width, height = image.size
    box = line["box"]
    x0 = max(0, int(box["x"]))
    y0 = max(0, int(box["y"]))
    x1 = min(width, x0 + max(1, int(box["width"])))
    y1 = min(height, y0 + max(1, int(box["height"])))
    fallback_face = default_face("sans")
    palette = _sample_palette(image, box)
    if palette is None:
        return {
            "color": DEFAULT_DARK,
            "background": DEFAULT_LIGHT,
            "font_size": 24,
            "font": "sans",
            "bold": False,
            "italic": False,
            "font_id": fallback_face.font_id,
            "font_name": fallback_face.family,
            "font_confidence": 0,
            "underline": False,
            "strikethrough": False,
        }

    decorations = _decorations_for_box(
        image, box, str(line.get("text", "")), bounded=True
    )
    foreground = palette["foreground"]
    background = palette["background"]
    bg_gray = palette["bg_gray"]
    rgb = palette["rgb"]
    fallback_size = max(9, round((y1 - y0) * 1.14))
    detected = _detect_typeface(
        str(line.get("text", "")),
        rgb,
        bg_gray,
        y1 - y0,
        foreground_gray=_luminance(foreground),
    )
    detected["font_size"] = detected.get("font_size") or fallback_size

    return {
        "color": _hex(foreground),
        "background": _hex(background),
        "font_size": detected["font_size"],
        "font": detected["font"],
        "bold": detected["bold"],
        "italic": detected["italic"],
        "font_id": detected["font_id"],
        "font_name": detected["font_name"],
        "font_confidence": detected["font_confidence"],
        "underline": bool(decorations.get("underline")),
        "strikethrough": bool(decorations.get("strikethrough")),
    }


def refine_line_decorations(
    image: Image.Image, lines: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Re-sample underline/strikethrough on the final, joined line boxes.

    Fragment-level sampling is deliberately strict: a rule that spans the
    original row often covers only part of a fragment's crop, so treating that
    mismatch as a style change fragments the row.  After merging, the joined
    box sees the whole rule.  Sampling it here can then recover a faint
    printed underline or strike without reopening the split/merge cycle.
    """
    for line in lines:
        style = line.get("style")
        if not isinstance(style, dict):
            continue
        decorations = _decorations_for_box(
            image,
            line.get("box") or {},
            str(line.get("text") or ""),
            bounded=True,
            sensitive=True,
        )
        style["underline"] = bool(decorations.get("underline"))
        style["strikethrough"] = bool(decorations.get("strikethrough"))
    return lines


# ------------------------------------------------------- style segmentation


def _median(values: list[float]) -> float:
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _colour_distance(first: Any, second: Any) -> float:
    try:
        return math.sqrt(
            sum(
                (float(first[index]) - float(second[index])) ** 2
                for index in range(3)
            )
        )
    except (TypeError, ValueError, IndexError):
        return 0.0


def _feature_gap(first: dict[str, Any], second: dict[str, Any]) -> float:
    """Horizontal gap between two word features in either reading direction."""
    first_x = float(first.get("x", 0.0))
    first_w = max(1.0, float(first.get("width", 1.0)))
    second_x = float(second.get("x", 0.0))
    second_w = max(1.0, float(second.get("width", 1.0)))
    if second_x >= first_x:
        return max(0.0, second_x - (first_x + first_w))
    return max(0.0, first_x - (second_x + second_w))


def _font_key(font: dict[str, Any] | None) -> tuple[Any, ...] | None:
    if not font:
        return None
    return (
        font.get("font_id"),
        font.get("category"),
        font.get("family"),
        bool(font.get("bold")),
        bool(font.get("italic")),
    )


def _font_conflict(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """True when two words have *clearly* different typefaces or variants.

    Per-word typeface matching is much noisier than a whole-line match: a short
    word gives the matcher very little glyph evidence, and antialiasing can
    make regular text look bold or a plain sans face look serif.  The previous
    thresholds (18/32 confidence) treated those one-word near-ties as real
    style changes, which is what fragmented ordinary sentences into many
    selections.  Different categories are still the strongest signal, but they
    also need a reasonable confidence; weight/slant need more; a same-category
    family change needs very strong agreement because those faces are visually
    close.
    """
    first_font = first.get("font")
    second_font = second.get("font")
    if not first_font or not second_font:
        return False
    try:
        first_conf = float(first.get("font_confidence") or 0.0)
        second_conf = float(second.get("font_confidence") or 0.0)
    except (TypeError, ValueError):
        return False
    confidence = min(first_conf, second_conf)
    if confidence < 30.0:
        return False
    if first_font.get("category") != second_font.get("category"):
        return confidence >= 45.0
    first_bold = bool(first_font.get("bold"))
    second_bold = bool(second_font.get("bold"))
    first_italic = bool(first_font.get("italic"))
    second_italic = bool(second_font.get("italic"))
    if first_bold != second_bold or first_italic != second_italic:
        return confidence >= 40.0
    if first_font.get("family") != second_font.get("family"):
        return confidence >= 65.0
    return False


def _comparable_heights(features: list[dict[str, Any]]) -> float:
    """Median text height after discarding oversized logo/junk boxes."""
    heights = [
        max(1.0, float(feature.get("height") or 1.0))
        for feature in features
        if int(feature.get("alnum") or 0) >= 1
    ]
    if not heights:
        heights = [max(1.0, float(feature.get("height") or 1.0)) for feature in features]
    ordered = sorted(heights)
    # A 40th-percentile start keeps a single giant logo box from moving the
    # centre when a line has only two or three words.
    median = ordered[int(round((len(ordered) - 1) * 0.40))]
    for _ in range(2):
        if median <= 0:
            break
        filtered = [
            height
            for height in heights
            if median * 0.55 <= height <= median * 1.75
        ]
        if not filtered:
            break
        median = _median(filtered)
    return median


def _comparable_char_width(features: list[dict[str, Any]], median_height: float) -> float:
    """Median character width from normal-sized words only.

    A giant low-confidence box (a logo mistaken for two letters) used to
    dominate this median and inflate every gap threshold, which glued table
    columns together.  Heights are checked against the robust line height and
    the resulting widths are trimmed once more around their own median.
    """
    widths: list[float] = []
    for feature in features:
        if int(feature.get("alnum") or 0) < 1:
            continue
        height = max(1.0, float(feature.get("height") or 1.0))
        if median_height > 0 and not (
            median_height * 0.55 <= height <= median_height * 1.75
        ):
            continue
        characters = max(1, int(feature.get("characters") or 1))
        widths.append(max(1.0, float(feature.get("width") or 1.0)) / characters)
    if not widths:
        return 0.0
    ordered = sorted(widths)
    median = ordered[int(round((len(ordered) - 1) * 0.40))]
    for _ in range(2):
        if median <= 0:
            break
        filtered = [
            width for width in widths if median * 0.45 <= width <= median * 2.2
        ]
        if not filtered:
            break
        median = _median(filtered)
    return median


def _style_boundaries(features: list[dict[str, Any]]) -> set[int]:
    """Return indexes ``i`` where a new segment starts at word ``i + 1``.

    The decision is intentionally evidence-based.  Normal word spacing stays
    together even when the line is tracked; an exceptional gap, an icon token,
    a colour change, a clear size change, or a different face/variant starts a
    new selection.
    """
    count = len(features)
    if count < 2:
        return set()

    icon_boundaries: set[int] = set()
    for index, feature in enumerate(features):
        if not feature.get("icon"):
            continue
        if index > 0:
            icon_boundaries.add(index - 1)
        if index + 1 < count:
            icon_boundaries.add(index)

    median_height = _comparable_heights(features)
    median_char = _comparable_char_width(features, median_height)
    gaps = [
        _feature_gap(features[index], features[index + 1])
        for index in range(count - 1)
    ]
    positive = [gap for gap in gaps if gap > 1.0]
    gap_boundaries: set[int] = set()
    if len(gaps) == 1:
        if gaps[0] > max(median_height * 1.8, median_char * 3.0, 14.0):
            gap_boundaries.add(0)
    elif positive:
        small_gaps = [gap for gap in positive if gap <= median_height * 1.6]
        normal_gap = _median(small_gaps or positive)
        threshold = max(
            median_height * 1.8,
            median_char * 3.0,
            normal_gap * 4.0,
            14.0,
        )
        for index, gap in enumerate(gaps):
            if gap > threshold and gap > normal_gap * 3.2:
                gap_boundaries.add(index)

    colour_boundaries: set[int] = set()
    size_boundaries: set[int] = set()
    font_boundaries: set[int] = set()
    for index in range(count - 1):
        first = features[index]
        second = features[index + 1]
        if first.get("icon") or second.get("icon"):
            continue
        first_colour = first.get("color")
        second_colour = second.get("color")
        if (
            first_colour
            and second_colour
            and int(first.get("ink_area") or 0) >= 8
            and int(second.get("ink_area") or 0) >= 8
            and _colour_distance(first_colour, second_colour) > 52.0
        ):
            colour_boundaries.add(index)
        first_size = first.get("font_size")
        second_size = second.get("font_size")
        try:
            first_size_value = float(first_size or 0.0)
            second_size_value = float(second_size or 0.0)
        except (TypeError, ValueError):
            first_size_value = second_size_value = 0.0
        if first_size_value and second_size_value:
            ratio = max(first_size_value, second_size_value) / max(
                1.0, min(first_size_value, second_size_value)
            )
            if ratio > 1.35:
                size_boundaries.add(index)
        if _font_conflict(first, second):
            font_boundaries.add(index)

    boundaries = (
        icon_boundaries
        | gap_boundaries
        | colour_boundaries
        | size_boundaries
        | font_boundaries
    )

    # A single word between two identical runs is often a per-word matching
    # miss, not a real style island.  Keep it only when its own face evidence
    # is strong; otherwise joining the two neighbours is safer than creating
    # three selections.
    for index in range(1, count - 1):
        if index - 1 not in font_boundaries or index not in font_boundaries:
            continue
        if (
            index - 1 in colour_boundaries
            or index in colour_boundaries
            or index - 1 in size_boundaries
            or index in size_boundaries
            or index - 1 in gap_boundaries
            or index in gap_boundaries
        ):
            continue
        previous = _font_key(features[index - 1].get("font"))
        current = _font_key(features[index].get("font"))
        following = _font_key(features[index + 1].get("font"))
        if previous is not None and previous == following and current != previous:
            if float(features[index].get("font_confidence") or 0.0) < 55.0:
                boundaries.discard(index - 1)
                boundaries.discard(index)

    # A single low-confidence face outlier in an otherwise consistent line is
    # overwhelmingly a per-word matching miss.  Whole-line style sampling has
    # far more glyph evidence, so do not let one short word fragment it.  A
    # genuine weight/family change usually either scores close to the rest of
    # the line or has high confidence; both keep their boundary here.
    valid = [
        index
        for index, feature in enumerate(features)
        if not feature.get("icon") and _font_key(feature.get("font")) is not None
    ]
    if len(valid) >= 3:
        counts: dict[tuple[Any, ...], int] = {}
        for index in valid:
            key = _font_key(features[index].get("font"))
            if key is not None:
                counts[key] = counts.get(key, 0) + 1
        if counts:
            majority_key, majority_count = max(
                counts.items(), key=lambda item: (item[1], str(item[0]))
            )
            if majority_count >= max(2, int(math.ceil(len(valid) * 0.65))):
                majority_scores = [
                    float(features[index].get("font_score") or 0.0)
                    for index in valid
                    if _font_key(features[index].get("font")) == majority_key
                    and math.isfinite(
                        float(features[index].get("font_score") or 0.0)
                    )
                ]
                majority_score = _median(majority_scores)
                majority_category = majority_key[1]
                for index in valid:
                    key = _font_key(features[index].get("font"))
                    if key is None or key == majority_key:
                        continue
                    if key[1] != majority_category:
                        # Category differences are strong visual evidence and
                        # must remain separate selections.
                        continue
                    feature = features[index]
                    confidence = float(feature.get("font_confidence") or 0.0)
                    score = float(feature.get("font_score") or 0.0)
                    if confidence >= 60.0:
                        continue
                    if (
                        majority_score > 0
                        and math.isfinite(score)
                        and score <= majority_score * 1.8
                    ):
                        continue
                    for edge in (index - 1, index):
                        if (
                            edge in font_boundaries
                            and edge not in colour_boundaries
                            and edge not in size_boundaries
                            and edge not in gap_boundaries
                            and edge not in icon_boundaries
                        ):
                            boundaries.discard(edge)
    return boundaries


def _partition_word_features(features: list[dict[str, Any]]) -> list[list[int]]:
    """Partition word features, dropping icon words and splitting around them."""
    boundaries = _style_boundaries(features)
    groups: list[list[int]] = []
    current: list[int] = []
    for index in range(len(features)):
        if index > 0 and index - 1 in boundaries:
            if current:
                groups.append(current)
            current = []
        current.append(index)
    if current:
        groups.append(current)

    segments: list[list[int]] = []
    for group in groups:
        run: list[int] = []
        for index in group:
            if features[index].get("icon"):
                if run:
                    segments.append(run)
                    run = []
            else:
                run.append(index)
        if run:
            segments.append(run)
    return segments


def _estimate_word_size(
    text: str,
    face: FontFace,
    ink_height: int,
    ink_width: int,
    box_height: int,
) -> int | None:
    candidate = _render_font_mask(text, face)
    if candidate is None or candidate.shape[0] < 2:
        return None
    height_size = 96.0 * ink_height / candidate.shape[0]
    size = height_size
    if candidate.shape[1] > 2:
        width_size = 96.0 * ink_width / candidate.shape[1]
        if 0.80 <= width_size / max(1.0, height_size) <= 1.20:
            size = (height_size + width_size) / 2.0
    if box_height:
        size = min(size, max(6.0, box_height * 1.8))
    return max(6, min(400, int(round(size))))


def _reference_face(style: dict[str, Any]) -> FontFace:
    face = face_by_id(style.get("font_id"))
    if face is not None:
        return face
    family = str(style.get("font") or "sans")
    if family not in STYLE_FAMILIES:
        family = "sans"
    return default_face(family, bool(style.get("bold")), bool(style.get("italic")))


def _font_from_style(style: dict[str, Any], face: FontFace) -> dict[str, Any]:
    return {
        "font_id": face.font_id,
        "category": face.category,
        "family": face.family,
        "bold": face.bold,
        "italic": face.italic,
    }


def _analyse_word_font(
    text: str,
    mask: np.ndarray,
    style: dict[str, Any],
    reference_face: FontFace,
    rgb: np.ndarray,
    background_gray: float,
    box_height: int,
    foreground_gray: float,
) -> dict[str, Any]:
    variants = variants_for_face(reference_face)
    scores: list[tuple[float, FontFace]] = []
    for variant in variants:
        candidate = _render_font_mask(text, variant)
        score = _mask_similarity(mask, candidate)
        if math.isfinite(score):
            scores.append((score, variant))
    if not scores:
        return {}
    scores.sort(key=lambda item: item[0])
    best_score, best_face = scores[0]
    # Prefer the stored/reference variant on a near-tie: the whole line has
    # much more glyph evidence than one short word.
    for score, variant in scores:
        if variant.font_id == reference_face.font_id and score <= best_score * 1.03:
            best_face = variant
            best_score = score
            break
    confidence = 80.0
    if _font_key(_font_from_style(style, best_face)) != _font_key(
        _font_from_style(style, reference_face)
    ):
        second_score = scores[1][0] if len(scores) > 1 else best_score
        margin = (second_score - best_score) / max(second_score, 1.0)
        # A single word has very little glyph evidence.  Only report a real
        # weight/slant change when it beats the runner-up by a solid margin;
        # otherwise the conservative style-boundary check would still reject
        # the guessed change and the word would simply stay in the line.
        confidence = max(0.0, min(90.0, round((margin - 0.06) * 320.0)))
    size = _estimate_word_size(
        text,
        best_face,
        max(1, int(mask.shape[0])),
        max(1, int(mask.shape[1])),
        box_height,
    )
    return {
        "font": _font_from_style(style, best_face),
        "font_confidence": confidence,
        "font_size": size,
        "font_score": best_score,
    }


def _word_feature(
    image: Image.Image,
    word: dict[str, Any],
    style: dict[str, Any],
) -> dict[str, Any]:
    box = word.get("box") or {}
    text = str(word.get("text") or "")
    feature: dict[str, Any] = {
        "text": text,
        "characters": len(text),
        "alnum": sum(1 for char in text if char.isalnum()),
        "x": float(box.get("x", 0.0)),
        "y": float(box.get("y", 0.0)),
        "width": max(1.0, float(box.get("width", 1.0))),
        "height": max(1.0, float(box.get("height", 1.0))),
        "confidence": float(word.get("confidence") or 0.0),
        "color": None,
        "ink_area": 0,
        "font": None,
        "font_confidence": 0.0,
        "font_size": None,
        "font_score": float("inf"),
        "underline": False,
        "strikethrough": False,
        "icon": False,
    }
    decorations = _decorations_for_box(image, box, text)
    feature["underline"] = bool(decorations.get("underline"))
    feature["strikethrough"] = bool(decorations.get("strikethrough"))
    compact = re.sub(r"\s+", "", text)
    if len(compact) < 4:
        # A short word cannot prove a decoration by itself (see the
        # detector), but when the whole line was sampled with it the word is
        # part of that run.
        if not feature["strikethrough"] and bool(style.get("strikethrough")):
            feature["strikethrough"] = True
        if not feature["underline"] and bool(style.get("underline")):
            feature["underline"] = True
    palette = _sample_palette(image, box)
    if palette is None:
        return feature
    feature["color"] = palette["foreground"]
    gray = palette["gray"]
    background_gray = float(palette["bg_gray"])
    foreground_gray = _luminance(palette["foreground"])
    dark_text: bool | None = None
    if abs(foreground_gray - background_gray) >= 24.0:
        dark_text = foreground_gray < background_gray
    mask = _glyph_mask(
        gray,
        background_gray,
        solid=True,
        dark_text=dark_text,
    )
    if mask is None and dark_text is not None:
        mask = _glyph_mask(gray, background_gray, solid=True)
    if mask is None:
        return feature
    feature["ink_area"] = int(np.count_nonzero(mask))
    if text and feature["alnum"] >= 2 and LATIN_STYLE_RE.fullmatch(text):
        reference_face = _reference_face(style)
        analysis = _analyse_word_font(
            text,
            mask,
            style,
            reference_face,
            palette["rgb"],
            background_gray,
            int(feature["height"]),
            foreground_gray,
        )
        if analysis:
            feature.update(analysis)
    return feature


def _looks_like_icon_word(
    feature: dict[str, Any],
    reference_key: tuple[Any, ...] | None,
    median_score: float,
    median_height: float,
) -> bool:
    """Conservatively identify a short alphanumeric token misread from an icon."""
    if feature.get("icon"):
        return True
    if feature.get("alnum") == 0:
        # Text-only icon tokens were removed during OCR.  A remaining
        # non-alphanumeric token is punctuation/symbolism, not an icon.
        return False
    if feature.get("alnum", 0) > 2:
        return False
    text = str(feature.get("text") or "").casefold()
    if text in {"a", "i"}:
        return False
    if _font_key(feature.get("font")) != reference_key:
        # A real bold/italic/family change was detected.  Do not confuse it
        # with an icon just because the glyphs differ from the line face.
        return False
    confidence = float(feature.get("confidence") or 0.0)
    if feature.get("alnum") == 1:
        if confidence >= 35.0:
            return False
    elif confidence >= 15.0:
        return False
    score = float(feature.get("font_score") or 0.0)
    if not math.isfinite(score) or score <= 0:
        return False
    threshold = max(3800.0, median_score * 2.2)
    if score < threshold:
        return False
    width = max(1.0, float(feature.get("width") or 1.0))
    height = max(1.0, float(feature.get("height") or 1.0))
    ratio = width / height
    squareish = 0.5 <= ratio <= 2.5
    height_outlier = abs(height - median_height) > median_height * 0.45
    return squareish or height_outlier


def _join_segment_words(words: list[dict[str, Any]]) -> str:
    """Join a segment with the same punctuation rules used during OCR."""
    from .ocr import TesseractEngine

    proxies: list[dict[str, Any]] = []
    for word in words:
        box = word.get("box") or {}
        proxies.append(
            {
                "text": str(word.get("text") or ""),
                "x": int(box.get("x", 0)),
                "y": int(box.get("y", 0)),
                "w": max(1, int(box.get("width", 1))),
                "h": max(1, int(box.get("height", 1))),
                "processed_x": int(box.get("x", 0)),
                "processed_w": max(1, int(box.get("width", 1))),
            }
        )
    return TesseractEngine._join_words(proxies)


def _mask_boxes_for_segment(
    mask_boxes: list[dict[str, Any]],
    segment_box: dict[str, int],
    segment_height: float,
) -> list[dict[str, int]]:
    x0 = float(segment_box["x"])
    y0 = float(segment_box["y"])
    x1 = x0 + float(segment_box["width"])
    y1 = y0 + float(segment_box["height"])
    tolerance = max(2.0, segment_height * 0.22)
    selected: list[dict[str, int]] = []
    for box in mask_boxes:
        try:
            bx = float(box.get("x", 0.0))
            by = float(box.get("y", 0.0))
            bw = max(1.0, float(box.get("width", 1.0)))
            bh = max(1.0, float(box.get("height", 1.0)))
        except (TypeError, ValueError):
            continue
        centre = bx + bw / 2.0
        vertical_overlap = min(y1, by + bh) - max(y0, by)
        horizontal_overlap = min(x1, bx + bw) - max(x0, bx)
        if vertical_overlap <= 0:
            continue
        if horizontal_overlap <= 0:
            if not (x0 - tolerance <= centre <= x1 + tolerance):
                continue
        selected.append(
            {
                "x": int(round(bx)),
                "y": int(round(by)),
                "width": max(1, int(round(bw))),
                "height": max(1, int(round(bh))),
            }
        )
    return selected


def _segment_line(
    line: dict[str, Any],
    words: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not words:
        return None
    boxes = [word.get("box") or {} for word in words]
    x0 = min(int(box.get("x", 0)) for box in boxes)
    y0 = min(int(box.get("y", 0)) for box in boxes)
    x1 = max(int(box.get("x", 0)) + max(1, int(box.get("width", 1))) for box in boxes)
    y1 = max(int(box.get("y", 0)) + max(1, int(box.get("height", 1))) for box in boxes)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    text = _join_segment_words(words)
    if not text.strip():
        return None
    weights = [max(1, sum(1 for char in str(word.get("text") or "") if char.isalnum())) for word in words]
    total_weight = sum(weights)
    confidence = sum(
        float(word.get("confidence") or 0.0) * weight
        for word, weight in zip(words, weights)
    ) / max(1, total_weight)
    segment_box = {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}
    mask_boxes = _mask_boxes_for_segment(
        line.get("mask_boxes") or [],
        segment_box,
        float(y1 - y0),
    )
    if not mask_boxes:
        mask_boxes = [
            {
                "x": int(box.get("x", 0)),
                "y": int(box.get("y", 0)),
                "width": max(1, int(box.get("width", 1))),
                "height": max(1, int(box.get("height", 1))),
            }
            for box in boxes
        ]
    clone = {
        key: value
        for key, value in line.items()
        if key not in {"box", "words", "mask_boxes", "style"}
    }
    clone["text"] = text
    clone["box"] = segment_box
    clone["confidence"] = round(confidence, 1)
    clone["words"] = [
        {
            "text": str(word.get("text") or ""),
            "box": dict(word.get("box") or {}),
            "confidence": round(float(word.get("confidence") or 0.0), 1),
        }
        for word in words
    ]
    clone["mask_boxes"] = mask_boxes
    clone["word_count"] = len(words)
    return clone


def split_styled_lines(
    image: Image.Image, lines: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Split OCR lines into selections with one consistent visual style.

    A single Tesseract line can contain adjacent labels with different colours,
    weights, slants, sizes or families, and a toolbar row can mix real words
    with icons.  Keeping those together makes an edit repaint half the row in
    the wrong style.  This pass samples each word, splits at clear style/gap
    changes, and removes short icon-like tokens.  Ambiguous one-word lines are
    left untouched.
    """
    result: list[dict[str, Any]] = []
    for line in lines:
        words = [word for word in (line.get("words") or []) if word.get("box")]
        if len(words) < 2:
            result.append(line)
            continue
        style = line.get("style") or {}
        if not style:
            style = sample_line_style(image, line)
            line["style"] = style
        features = [_word_feature(image, word, style) for word in words]
        scores = [
            float(feature.get("font_score") or 0.0)
            for feature in features
            if feature.get("alnum", 0) >= 2
            and math.isfinite(float(feature.get("font_score") or 0.0))
        ]
        median_score = _median(scores) if scores else 0.0
        median_height = _comparable_heights(features)
        reference_face = _reference_face(style)
        reference_key = _font_key(_font_from_style(style, reference_face))

        # A word whose glyphs do not match any variant of the line face may
        # belong to another family.  Run the full matcher only for those
        # outliers; ordinary words keep the cheap variant comparison.
        threshold = max(3200.0, median_score * 2.1)
        for feature in features:
            if feature.get("alnum", 0) < 2:
                continue
            score = float(feature.get("font_score") or 0.0)
            if not math.isfinite(score) or score <= threshold:
                continue
            palette = _sample_palette(image, {
                "x": feature["x"],
                "y": feature["y"],
                "width": feature["width"],
                "height": feature["height"],
            })
            if palette is None:
                continue
            detected = _detect_typeface(
                str(feature.get("text") or ""),
                palette["rgb"],
                float(palette["bg_gray"]),
                int(feature["height"]),
                foreground_gray=_luminance(palette["foreground"]),
            )
            if int(detected.get("font_confidence") or 0) >= 18:
                feature["font"] = {
                    "font_id": detected.get("font_id"),
                    "category": detected.get("font"),
                    "family": detected.get("font_name"),
                    "bold": bool(detected.get("bold")),
                    "italic": bool(detected.get("italic")),
                }
                feature["font_confidence"] = float(
                    detected.get("font_confidence") or 0.0
                )
                if detected.get("font_size"):
                    feature["font_size"] = detected["font_size"]

        for feature in features:
            if _looks_like_icon_word(
                feature,
                reference_key,
                median_score,
                median_height,
            ):
                feature["icon"] = True

        segments = _partition_word_features(features)
        if not segments:
            continue
        if len(segments) == 1 and segments[0] == list(range(len(words))):
            result.append(line)
            continue
        for segment in segments:
            rebuilt = _segment_line(line, [words[index] for index in segment])
            if rebuilt is not None:
                result.append(rebuilt)
    return result


def _style_colour(style: dict[str, Any]) -> tuple[int, int, int] | None:
    value = style.get("color")
    if not value:
        return None
    if isinstance(value, str):
        return _parse_hex(value)
    try:
        return tuple(int(round(float(channel))) for channel in value[:3])
    except (TypeError, ValueError, IndexError):
        return None


def _style_float(style: dict[str, Any], key: str) -> float:
    try:
        return float(style.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _shadows_compatible(
    first: dict[str, Any], second: dict[str, Any]
) -> bool:
    first_shadow = first.get("shadow")
    second_shadow = second.get("shadow")
    first_has = isinstance(first_shadow, dict) and bool(first_shadow)
    second_has = isinstance(second_shadow, dict) and bool(second_shadow)
    if first_has != second_has:
        return False
    if not first_has or not second_has:
        return True
    try:
        first_dx = float(first_shadow.get("dx", 0))
        first_dy = float(first_shadow.get("dy", 0))
        second_dx = float(second_shadow.get("dx", 0))
        second_dy = float(second_shadow.get("dy", 0))
    except (TypeError, ValueError):
        return False
    return abs(first_dx - second_dx) <= 2.0 and abs(first_dy - second_dy) <= 2.0


def _styles_compatible(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """True when two adjacent segments look like one unchanged text run."""
    if not first or not second:
        return False
    first_colour = _style_colour(first)
    second_colour = _style_colour(second)
    if first_colour is not None and second_colour is not None:
        distance = _colour_distance(first_colour, second_colour)
        # Near-black ink is the normal case on a scan.  Anti-aliasing, paper
        # grain and a strike/underline band shift individual RGB samples by
        # tens of levels without any visible colour change, and Euclidean RGB
        # distance exaggerates those shifts at the dark end.  Only reject a
        # large dark-on-dark difference; a genuine black-to-red or
        # black-to-grey change is still well above the tolerance.
        limit = 100.0 if max(*first_colour, *second_colour) < 80 else 52.0
        if distance > limit:
            return False
    first_size = _style_float(first, "font_size")
    second_size = _style_float(second, "font_size")
    if first_size > 0 and second_size > 0:
        ratio = max(first_size, second_size) / max(1.0, min(first_size, second_size))
        # Match the per-word size boundary: a smaller difference was not
        # considered a real style change when the line was split, so it must
        # not keep two fragments of that line apart now.  A low-confidence
        # size sample on a single word ("must", "137.") can be far from the
        # real type size, so only trust the size mismatch when at least one
        # fragment's style evidence is reasonably confident.
        if ratio > 1.35:
            confidence = min(
                _style_float(first, "font_confidence"),
                _style_float(second, "font_confidence"),
            )
            if confidence > 5.0:
                return False
    if not _shadows_compatible(first, second):
        return False
    # Alignment is a property of the finished line, not of a fragment.  A
    # fragment's provisional align can differ from its neighbour's simply
    # because the fragment is short; using it here would keep the pieces of a
    # single visual line apart.  Alignment is inferred again after merging.

    first_face = face_by_id(first.get("font_id"))
    second_face = face_by_id(second.get("font_id"))
    if first_face is not None and second_face is not None:
        first_conf = _style_float(first, "font_confidence")
        second_conf = _style_float(second, "font_confidence")
        confidence = min(first_conf, second_conf)
        # Mirror ``_font_conflict``.  If the per-word evidence was not strong
        # enough to justify a boundary in the first place, it is also not
        # strong enough to keep the resulting fragments apart.  Whole-segment
        # sampling is more reliable than a single short word, but the same
        # visual thresholds still apply.
        if confidence >= 30.0:
            if (
                first_face.category != second_face.category
                and confidence >= 45.0
            ):
                return False
            if (
                first_face.bold != second_face.bold
                or first_face.italic != second_face.italic
            ) and confidence >= 40.0:
                return False
            if (
                first_face.family != second_face.family
                and confidence >= 65.0
            ):
                return False
    return True


def _segment_text_is_rtl(first: dict[str, Any], second: dict[str, Any]) -> bool:
    text = str(first.get("text") or "") + str(second.get("text") or "")
    return bool(ARABIC_RE.search(text) or HEBREW_RE.search(text))


def _merge_segment_pair(
    first: dict[str, Any], second: dict[str, Any]
) -> dict[str, Any]:
    words: list[dict[str, Any]] = []
    for line in (first, second):
        for word in line.get("words") or []:
            box = word.get("box") or {}
            if not box:
                continue
            words.append(
                {
                    "text": str(word.get("text") or ""),
                    "box": dict(box),
                    "confidence": round(float(word.get("confidence") or 0.0), 1),
                }
            )
    reverse = _segment_text_is_rtl(first, second)
    words.sort(
        key=lambda word: float((word.get("box") or {}).get("x", 0.0)),
        reverse=reverse,
    )
    # Segments are disjoint, but overlapping OCR passes can still repeat a
    # token.  Drop exact duplicates before joining so the merge cannot double
    # a word.
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    for word in words:
        box = word.get("box") or {}
        signature = (
            word.get("text") or "",
            int(round(float(box.get("x", 0.0)))) // 2,
            int(round(float(box.get("y", 0.0)))) // 2,
        )
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(word)
    words = unique
    if not words:
        return first
    combined = dict(first)
    combined["mask_boxes"] = [
        dict(box)
        for box in [
            *(first.get("mask_boxes") or []),
            *(second.get("mask_boxes") or []),
        ]
    ]
    rebuilt = _segment_line(combined, words)
    if rebuilt is None:
        return first
    first_conf = _style_float(first.get("style") or {}, "font_confidence")
    second_conf = _style_float(second.get("style") or {}, "font_confidence")
    style = dict(
        (second.get("style") if second_conf > first_conf else first.get("style"))
        or first.get("style")
        or {}
    )
    # The size/face was sampled for one fragment.  Re-estimate the size from
    # the joined text, using the median word height instead of the union box:
    # a single tall box or a speck of photo noise would otherwise inflate the
    # replacement text far beyond the source.
    face = face_by_id(style.get("font_id"))
    word_boxes = [word["box"] for word in words if word.get("box")]
    heights = [
        float(box.get("height", 0.0))
        for box in word_boxes
        if float(box.get("height", 0.0)) > 0
    ]
    if face is not None and heights:
        text_height = max(1, int(round(_median(heights))))
        text_width = max(
            1,
            int(
                round(
                    max(
                        float(box.get("x", 0.0))
                        + max(1.0, float(box.get("width", 1.0)))
                        for box in word_boxes
                    )
                    - min(float(box.get("x", 0.0)) for box in word_boxes)
                )
            ),
        )
        size = _estimate_font_size(
            rebuilt["text"], face, text_height, text_width, text_height
        )
        if size:
            style["font_size"] = size
    rebuilt["style"] = style
    return rebuilt


def _segment_text_height(line: dict[str, Any], fallback: float) -> float:
    """Return the typical word height, ignoring one tall outlier sample."""
    heights = []
    for word in line.get("words") or []:
        box = word.get("box") or {}
        try:
            height = float(box.get("height", 0.0))
        except (TypeError, ValueError):
            continue
        if height > 0:
            heights.append(height)
    if not heights:
        return fallback
    return max(1.0, _median(heights))


def _segment_baseline(line: dict[str, Any], fallback: float) -> float:
    """Return the typical word centre, ignoring one tall outlier sample."""
    centres = []
    for word in line.get("words") or []:
        box = word.get("box") or {}
        try:
            centre = float(box.get("y", 0.0)) + max(
                1.0, float(box.get("height", 1.0))
            ) / 2.0
        except (TypeError, ValueError):
            continue
        centres.append(centre)
    if not centres:
        return fallback
    return _median(centres)


def _row_adjusted_baseline_delta(
    first: dict[str, Any], second: dict[str, Any]
) -> float | None:
    """Return the baseline offset after removing the page's row slope.

    On a warped or photographed page one visual row can slope: the left half
    sits lower than the right half by more than a word height.  Comparing the
    two fragments' median word centres would then reject a genuine rejoin.
    Fitting a straight line through all their word centres and comparing the
    median residual of each fragment separates that shared slope from a real
    row-to-row offset.  ``None`` means there is not enough word geometry to
    make the correction, and the caller must use the raw centres.
    """
    groups: list[list[tuple[float, float]]] = [[], []]
    for index, line in enumerate((first, second)):
        for word in line.get("words") or []:
            box = word.get("box") or {}
            if not box:
                continue
            try:
                x = float(box.get("x", 0.0)) + max(
                    1.0, float(box.get("width", 1.0))
                ) / 2.0
                y = float(box.get("y", 0.0)) + max(
                    1.0, float(box.get("height", 1.0))
                ) / 2.0
            except (TypeError, ValueError):
                continue
            groups[index].append((x, y))
    points = groups[0] + groups[1]
    if not groups[0] or not groups[1] or len(points) < 3:
        return None
    mean_x = sum(x for x, _ in points) / len(points)
    mean_y = sum(y for _, y in points) / len(points)
    spread = sum((x - mean_x) ** 2 for x, _ in points)
    slope = 0.0
    if spread > 1e-6:
        slope = (
            sum((x - mean_x) * (y - mean_y) for x, y in points) / spread
        )
    offsets = [
        _median([y - (mean_y + slope * (x - mean_x)) for x, y in group])
        for group in groups
    ]
    return abs(offsets[0] - offsets[1])


def _segments_mergeable(
    first: dict[str, Any], second: dict[str, Any]
) -> bool:
    first_box = first.get("box") or {}
    second_box = second.get("box") or {}
    if not first_box or not second_box:
        return False
    try:
        ax0 = float(first_box.get("x", 0.0))
        ay0 = float(first_box.get("y", 0.0))
        ax1 = ax0 + max(1.0, float(first_box.get("width", 1.0)))
        ay1 = ay0 + max(1.0, float(first_box.get("height", 1.0)))
        bx0 = float(second_box.get("x", 0.0))
        by0 = float(second_box.get("y", 0.0))
        bx1 = bx0 + max(1.0, float(second_box.get("width", 1.0)))
        by1 = by0 + max(1.0, float(second_box.get("height", 1.0)))
    except (TypeError, ValueError):
        return False
    first_height = _segment_text_height(first, max(1.0, ay1 - ay0))
    second_height = _segment_text_height(second, max(1.0, by1 - by0))
    shorter = min(first_height, second_height)
    height_ratio = shorter / max(first_height, second_height)
    # Word-centre baselines must agree even when both segments have similar
    # heights.  Tall union boxes can still overlap while their words belong to
    # two adjacent rows; the overlap test below cannot see that.  Compare the
    # centres after removing the row slope so a warped page does not split a
    # genuine same-row rejoin.
    baseline_delta = _row_adjusted_baseline_delta(first, second)
    if baseline_delta is None:
        baseline_delta = abs(
            _segment_baseline(first, (ay0 + ay1) / 2.0)
            - _segment_baseline(second, (by0 + by1) / 2.0)
        )
    if baseline_delta > max(4.0, shorter * 0.50):
        return False
    # Two segments that overlap horizontally are competing readings or two
    # different rows.  Style-split fragments of one OCR line are adjacent, so
    # an overlap is never a valid rejoin and used to glue the next text row
    # into the line above on warped or tightly spaced documents.
    horizontal_overlap = min(ax1, bx1) - max(ax0, bx0)
    if horizontal_overlap > 0:
        narrower = min(ax1 - ax0, bx1 - bx0)
        if horizontal_overlap / max(1.0, narrower) > 0.20:
            return False
    if height_ratio < 0.72:
        # OCR word boxes cover only the ink of their own characters.  A short
        # word without ascenders or descenders ("on") can be a third shorter
        # than the rest of its line, so box height alone must not keep it
        # apart.  Require the centres to agree and the size difference to stay
        # plausible; a genuinely smaller label is either vertically offset or
        # has a different sampled font size in ``_styles_compatible``.
        if height_ratio < 0.50:
            return False
    overlap = min(ay1, by1) - max(ay0, by0)
    if overlap / max(1.0, min(ay1 - ay0, by1 - by0)) < 0.55:
        return False
    if bx0 >= ax1:
        gap = bx0 - ax1
    elif ax0 >= bx1:
        gap = ax0 - bx1
    else:
        gap = 0.0
    characters = [
        max(1.0, float(box.get("width", 1.0)))
        / max(1, len(str(box.get("text") or "")))
        for line in (first, second)
        for box in (line.get("words") or [])
        if str(box.get("text") or "")
    ]
    median_char = _median(characters)
    if median_char > 0:
        filtered = [
            width
            for width in characters
            if median_char * 0.45 <= width <= median_char * 2.2
        ]
        if filtered:
            median_char = _median(filtered)
    threshold = max(min(first_height, second_height) * 1.95, median_char * 3.0, 14.0)
    if gap > threshold:
        return False
    # Check the two words that actually meet at the join.  On a sloped or
    # warped page a whole-line baseline test is unreliable, but ink from two
    # different rows still fails to overlap vertically at the boundary.  This
    # keeps a same-row fragment with a tall sample ("must") while rejecting an
    # adjacent lower row that starts immediately after the previous word.
    first_words = [word for word in first.get("words") or [] if word.get("box")]
    second_words = [word for word in second.get("words") or [] if word.get("box")]
    if first_words and second_words:
        if (ax0 + ax1) / 2.0 <= (bx0 + bx1) / 2.0:
            left_words, right_words = first_words, second_words
        else:
            left_words, right_words = second_words, first_words
        left_word = max(
            left_words,
            key=lambda word: float((word.get("box") or {}).get("x", 0.0))
            + max(1.0, float((word.get("box") or {}).get("width", 1.0))),
        )
        right_word = min(
            right_words,
            key=lambda word: float((word.get("box") or {}).get("x", 0.0)),
        )
        left_box = left_word.get("box") or {}
        right_box = right_word.get("box") or {}
        try:
            ly0 = float(left_box.get("y", 0.0))
            ly1 = ly0 + max(1.0, float(left_box.get("height", 1.0)))
            ry0 = float(right_box.get("y", 0.0))
            ry1 = ry0 + max(1.0, float(right_box.get("height", 1.0)))
        except (TypeError, ValueError):
            return False
        vertical_overlap = min(ly1, ry1) - max(ly0, ry0)
        if vertical_overlap < 0.35 * max(1.0, min(ly1 - ly0, ry1 - ry0)):
            return False
    return _styles_compatible(
        first.get("style") or {},
        second.get("style") or {},
    )


def _row_ordered_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort lines by reading row, then left-to-right within each row.

    A global ``(y, x)`` sort breaks when two fragments of the same visual line
    differ by a pixel or two in y: the right-hand fragment is visited first and
    can be joined to the wrong neighbour.  Grouping by the same 55% vertical
    overlap used by the OCR fragment stitcher keeps a row together before the
    merge pass decides which pieces are adjacent.
    """
    rows: list[list[dict[str, Any]]] = []
    for line in sorted(
        lines,
        key=lambda item: (
            float((item.get("box") or {}).get("y", 0.0)),
            float((item.get("box") or {}).get("x", 0.0)),
        ),
    ):
        box = line.get("box") or {}
        try:
            ly0 = float(box.get("y", 0.0))
            ly1 = ly0 + max(1.0, float(box.get("height", 1.0)))
        except (TypeError, ValueError):
            rows.append([line])
            continue
        height = max(1.0, ly1 - ly0)
        placed = False
        for row in rows:
            for member in row:
                member_box = member.get("box") or {}
                try:
                    my0 = float(member_box.get("y", 0.0))
                    my1 = my0 + max(1.0, float(member_box.get("height", 1.0)))
                except (TypeError, ValueError):
                    continue
                member_height = max(1.0, my1 - my0)
                overlap = min(ly1, my1) - max(ly0, my0)
                if overlap / max(1.0, min(height, member_height)) >= 0.55:
                    row.append(line)
                    placed = True
                    break
            if placed:
                break
        if not placed:
            rows.append([line])

    ordered: list[dict[str, Any]] = []
    for row in rows:
        row.sort(
            key=lambda item: (
                float((item.get("box") or {}).get("x", 0.0)),
                float((item.get("box") or {}).get("y", 0.0)),
            )
        )
        ordered.extend(row)
    return ordered


def merge_compatible_segments(
    lines: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rejoin adjacent same-line selections whose sampled styles agree.

    ``split_styled_lines`` runs before per-segment style sampling.  A one-word
    font mismatch can still create a boundary even after the conservative
    boundary checks; once each segment has its own properly sampled colour,
    size and typeface, that error is often obvious.  This pass only merges
    fragments on the same baseline, separated by ordinary word spacing, with
    compatible styles.  Genuine column gaps, colour changes and weight changes
    remain separate.
    """
    if not lines:
        return []
    ordered = _row_ordered_lines(lines)
    merged: list[dict[str, Any]] = []
    for line in ordered:
        target = None
        for index, existing in enumerate(merged):
            if _segments_mergeable(existing, line):
                target = index
                break
        if target is None:
            merged.append(line)
        else:
            merged[target] = _merge_segment_pair(merged[target], line)
    merged = _drop_covered_segments(merged)
    merged.sort(
        key=lambda line: (
            float((line.get("box") or {}).get("y", 0.0)),
            float((line.get("box") or {}).get("x", 0.0)),
        )
    )
    return merged


def _drop_covered_segments(
    lines: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop fragments whose box is already covered by a wider same-row line.

    Splitting and rejoining can leave the tail of the discarded reading behind
    (``payro:`` next to the full ``141. All remarks ...`` line).  Keeping both
    would let the first edit erase the other's source pixels, and the shorter
    one is never a usable selection when the wider one contains it.
    """
    if len(lines) < 2:
        return lines
    areas = [_box_area(line) for line in lines]
    order = sorted(range(len(lines)), key=lambda index: areas[index], reverse=True)
    dropped: set[int] = set()
    for position, index in enumerate(order):
        if index in dropped:
            continue
        for other_index in order[position + 1 :]:
            if other_index in dropped:
                continue
            if _segment_coverage(lines[index], lines[other_index]) >= 0.60:
                dropped.add(other_index)
    return [line for index, line in enumerate(lines) if index not in dropped]


def _box_area(line: dict[str, Any]) -> float:
    box = line.get("box") or {}
    try:
        return max(1.0, float(box.get("width", 1.0))) * max(
            1.0, float(box.get("height", 1.0))
        )
    except (TypeError, ValueError):
        return 0.0


def _segment_coverage(container: dict[str, Any], line: dict[str, Any]) -> float:
    """Return the fraction of ``line``'s box covered by ``container``."""
    first = container.get("box") or {}
    second = line.get("box") or {}
    if not first or not second:
        return 0.0
    first_height = _segment_text_height(
        container, max(1.0, float(first.get("height", 1.0) or 1.0))
    )
    second_height = _segment_text_height(
        line, max(1.0, float(second.get("height", 1.0) or 1.0))
    )
    if (
        abs(_segment_baseline(container, 0.0) - _segment_baseline(line, 0.0))
        > max(4.0, min(first_height, second_height) * 0.45)
    ):
        return 0.0
    try:
        ax0 = float(first.get("x", 0.0))
        ay0 = float(first.get("y", 0.0))
        ax1 = ax0 + max(1.0, float(first.get("width", 1.0)))
        ay1 = ay0 + max(1.0, float(first.get("height", 1.0)))
        bx0 = float(second.get("x", 0.0))
        by0 = float(second.get("y", 0.0))
        bx1 = bx0 + max(1.0, float(second.get("width", 1.0)))
        by1 = by0 + max(1.0, float(second.get("height", 1.0)))
    except (TypeError, ValueError):
        return 0.0
    overlap_width = min(ax1, bx1) - max(ax0, bx0)
    overlap_height = min(ay1, by1) - max(ay0, by0)
    if overlap_width <= 0 or overlap_height <= 0:
        return 0.0
    area = max(1.0, (bx1 - bx0) * (by1 - by0))
    return (overlap_width * overlap_height) / area


def _shadow_is_cached(style: dict[str, Any]) -> bool:
    """True when ``style`` already records the shadow decision.

    A missing key means "not sampled yet", so the first edit detects the
    shadow.  A stored ``None`` or a usable direction/blur is a completed
    decision and must not be re-detected on every render.  An empty or
    malformed dictionary is not a decision: older/hand-edited documents could
    contain ``"shadow": {}``, which would otherwise skip detection and leave
    the original shadow behind as a ghost.
    """
    if "shadow" not in style:
        return False
    shadow = style.get("shadow")
    if shadow is None:
        return True
    if not isinstance(shadow, dict):
        return False
    try:
        return any(
            int(round(float(shadow.get(key, 0) or 0)))
            for key in ("dx", "dy", "blur")
        )
    except (TypeError, ValueError, OverflowError):
        return False


def _merge_sampled_style(
    sampled: dict[str, Any], stored: dict[str, Any]
) -> dict[str, Any]:
    """Keep reliable old style values while filling in what OCR now knows.

    Documents created before typeface sampling only stored colour, background
    and a rough font size.  Re-detecting the typeface is safe, but blindly
    keeping every stored value would preserve a size that was merely derived
    from the OCR box height (typically 20-40% too small).  Keep a stored size
    only when it is close to the freshly measured size; otherwise use the
    measurement so unchanged text is not shrunk on its first edit.
    """
    merged = dict(sampled)
    sampled_size = 0.0
    try:
        sampled_size = float(sampled.get("font_size") or 0.0)
    except (TypeError, ValueError):
        sampled_size = 0.0

    for key, value in stored.items():
        if key == "shadow":
            # A cached negative result is stored as None.  It is still a
            # completed decision and must survive sampling; otherwise the
            # detector runs again on every render of that line.
            if _shadow_is_cached({"shadow": value}):
                merged[key] = value
            continue
        if value is None or value == "":
            continue
        if key == "font_id" and face_by_id(value) is None:
            continue
        if key == "font_name" and face_by_id(stored.get("font_id")) is None:
            # The face was re-detected, so an old display name belongs to the
            # discarded font id and would label the UI incorrectly.
            continue
        if key == "font_size":
            try:
                stored_size = float(value)
            except (TypeError, ValueError):
                continue
            if sampled_size > 0 and stored_size > 0:
                ratio = stored_size / sampled_size
                if 0.85 <= ratio <= 1.15:
                    merged[key] = value
                continue
            merged[key] = value
            continue
        merged[key] = value
    return merged


def stabilize_typefaces(lines: list[dict[str, Any]]) -> None:
    """Snap very uncertain per-line typeface guesses to the document's family.

    Typeface matching is per line, so a short label can lose a close race and
    be repainted in a different face from the sentence directly below it.  The
    document itself is the missing evidence: when most of the text in one
    category (sans/serif/mono) uses a family, a line whose match was a near-tie
    should inherit that family rather than a one-off guess.

    Only low-confidence lines are changed, the category must have a clear
    dominant family, and at least one other line must support it.  Explicit
    font choices are stored separately on the edit request, so this never
    overrides a user override.
    """
    groups: dict[str, list[tuple[dict[str, Any], dict[str, Any], FontFace]]] = {}
    for line in lines:
        style = line.get("style") or {}
        face = face_by_id(style.get("font_id"))
        if face is None:
            continue
        groups.setdefault(face.category, []).append((line, style, face))

    for entries in groups.values():
        if len(entries) < 2:
            continue
        votes: dict[str, float] = {}
        for line, style, face in entries:
            try:
                confidence = max(0.0, min(100.0, float(style.get("font_confidence") or 0.0)))
            except (TypeError, ValueError):
                confidence = 0.0
            # A confident, longer line is stronger evidence than a two-letter
            # label.  The weight still lets several medium-confidence lines
            # outvote one uncertain guess.
            length = len(str(line.get("text") or ""))
            weight = max(1.0, min(12.0, length / 4.0)) * (0.3 + confidence / 60.0)
            votes[face.family] = votes.get(face.family, 0.0) + weight
        total = sum(votes.values())
        if total <= 0:
            continue
        dominant, dominant_weight = max(votes.items(), key=lambda item: item[1])
        if dominant_weight / total < 0.65:
            continue
        if not any(face.family == dominant for _, _, face in entries):
            continue
        dominant_family = font_family(dominant)
        if dominant_family is None:
            continue
        for line, style, face in entries:
            if face.family == dominant:
                continue
            try:
                confidence = float(style.get("font_confidence") or 0.0)
            except (TypeError, ValueError):
                continue
            if confidence >= 15.0:
                continue
            replacement = dominant_family.face(
                bool(style.get("bold")), bool(style.get("italic"))
            )
            if replacement is None:
                replacement = dominant_family.face(False, False)
            if replacement is None:
                continue
            style["font"] = replacement.category
            style["font_id"] = replacement.font_id
            style["font_name"] = replacement.family


def stabilize_monospaced_document(lines: list[dict[str, Any]]) -> None:
    """Use page-wide character pitch to correct per-line faces.

    Typewritten pages and terminal screenshots have a near-constant advance
    width per character.  Per-line face matching on a blurry photo often
    splits such a page between mono, serif and sans because their scores are
    nearly tied.  A document-level pitch test is much stronger evidence than
    one noisy line, so unify low-confidence lines and moderate-confidence
    outliers on the dominant mono family.  A clearly confident different face
    survives, and lines that already use mono keep their weight, slant, and
    size.  Proportional documents leave the test far above the coefficient
    threshold and are not touched.
    """
    if len(lines) < 3:
        return
    pitches: list[float] = []
    heights: list[float] = []
    for line in lines:
        for word in line.get("words") or []:
            box = word.get("box") or {}
            # Count every non-space character, not only alphanumerics.  A
            # typewritten line such as ``145.`` or ``A.B.C.`` would otherwise
            # divide the word width by a smaller count, inflating the pitch
            # and making the page-wide size estimate drift upward.
            compact = re.sub(r"\s+", "", str(word.get("text") or ""))
            if len(compact) < 2:
                continue
            try:
                width = float(box.get("width", 0.0))
                height = float(box.get("height", 0.0))
            except (TypeError, ValueError):
                continue
            if width <= 0 or height <= 0:
                continue
            pitches.append(width / len(compact))
            heights.append(height)
    if len(pitches) < 20:
        return
    median_pitch = _median(pitches)
    median_height = _median(heights)
    if median_pitch <= 0 or median_height <= 0:
        return
    mean = sum(pitches) / len(pitches)
    deviation = math.sqrt(sum((value - mean) ** 2 for value in pitches) / len(pitches))
    if mean <= 0 or deviation / mean > 0.14:
        return
    if not 0.60 <= median_pitch / median_height <= 0.92:
        return
    letters = [
        char
        for line in lines
        for char in str(line.get("text") or "")
        if char.isalpha()
    ]
    if not letters or sum(1 for char in letters if char.islower()) / len(letters) < 0.55:
        return

    votes: dict[str, float] = {}
    for line in lines:
        face = face_by_id((line.get("style") or {}).get("font_id"))
        if face is None or face.category != "mono":
            continue
        weight = max(1.0, min(12.0, len(str(line.get("text") or "")) / 4.0))
        votes[face.family] = votes.get(face.family, 0.0) + weight
    family = font_family(max(votes, key=votes.get)) if votes else None
    if family is None:
        family = font_family(default_face("mono").family)
    if family is None:
        return
    regular = family.face(False, False)
    if regular is None:
        return

    # A mono face has one advance for every glyph, so the page's median pitch
    # (image pixels per character) maps directly to the point size that would
    # reproduce it.  This is much steadier than measuring one line's tight ink
    # height, which drifts on blurry typewritten scans and all-caps rows.
    try:
        probe = ImageFont.truetype(str(regular.path), size=96, index=regular.index)
        advance_at_96 = float(probe.getlength("M"))
    except (OSError, ValueError, RuntimeError):
        advance_at_96 = 0.0
    expected_size = 0
    if advance_at_96 > 0:
        expected_size = int(round(96.0 * median_pitch / advance_at_96))
        if not 6 <= expected_size <= 400:
            expected_size = 0

    for line in lines:
        style = line.get("style") or {}
        face = face_by_id(style.get("font_id"))
        try:
            confidence = float(style.get("font_confidence") or 0.0)
        except (TypeError, ValueError):
            continue
        already_mono = face is not None and face.category == "mono"
        family_locked = False
        if already_mono:
            if confidence >= 15.0:
                # The family is right, so keep the sampled weight and slant.
                # The stored size can still inherit the page-wide pitch
                # correction below when the line's own pitch agrees.
                family_locked = True
        elif confidence >= 40.0:
            # A clearly confident proportional face survives: the page may be
            # mostly monospaced but include a real heading in another family.
            continue
        if not family_locked:
            style["font"] = "mono"
            style["font_id"] = regular.font_id
            style["font_name"] = regular.family
        if confidence < 15.0:
            # Weak face evidence cannot prove a bold/italic variant; a drawn
            # rule, strike band or blurry stroke often looks like one.  Keep
            # the sampled weight/slant only on confident mono lines.
            style["bold"] = False
            style["italic"] = False
        word_boxes = [
            word["box"]
            for word in line.get("words") or []
            if word.get("box")
        ]
        word_heights = [
            float(box.get("height", 0.0))
            for box in word_boxes
            if float(box.get("height", 0.0)) > 0
        ]
        text = str(line.get("text") or "")
        if not word_boxes or not word_heights or not text:
            continue
        text_height = max(1, int(round(_median(word_heights))))
        text_width = max(
            1,
            int(
                round(
                    max(
                        float(box.get("x", 0.0))
                        + max(1.0, float(box.get("width", 1.0)))
                        for box in word_boxes
                    )
                    - min(float(box.get("x", 0.0)) for box in word_boxes)
                )
            ),
        )
        line_pitches: list[float] = []
        pitch_chars = 0
        for word in line.get("words") or []:
            box = word.get("box") or {}
            compact = re.sub(r"\s+", "", str(word.get("text") or ""))
            if len(compact) < 2:
                continue
            try:
                width = float(box.get("width", 0.0))
            except (TypeError, ValueError):
                continue
            if width > 0:
                line_pitches.append(width / len(compact))
                pitch_chars += len(compact)
        size: int | None = None
        if expected_size and len(line_pitches) >= 2 and pitch_chars >= 6:
            line_pitch = _median(line_pitches)
            # Only trust the page-wide size when this line's own pitch agrees.
            # A genuinely larger heading keeps its sampled size instead of
            # being forced down to the body-text advance.
            if line_pitch > 0 and 0.82 <= line_pitch / median_pitch <= 1.22:
                size = expected_size
        if size is None and not family_locked:
            size = _estimate_font_size(
                text, regular, text_height, text_width, text_height
            )
        if size:
            style["font_size"] = size


def infer_alignment(lines: list[dict[str, Any]], image_width: int, image_height: int) -> None:
    """Fill ``style.align`` for each line using the document's margins.

    Tesseract does not report alignment.  Comparing the left and right margins
    of a line with the content block works well for menus, posters and
    right-aligned prices, and is deliberately conservative: anything ambiguous
    stays left-aligned.

    A pure centred or right-aligned block is tricky because its widest line
    defines the content edge and therefore has a zero margin on both sides.  If
    every line is centred on the image, or every line ends on the same right
    edge, the whole block is classified first and the widest line is not left
    stranded.
    """
    if not lines:
        return
    boxes = [line.get("box") or {} for line in lines]
    lefts = [float(box.get("x", 0)) for box in boxes]
    rights = [float(box.get("x", 0)) + float(box.get("width", 0)) for box in boxes]
    content_left = min(lefts)
    content_right = max(rights)
    content_width = max(1.0, content_right - content_left)
    image_center = image_width / 2.0
    tolerance = max(3.0, content_width * 0.02)

    records: list[tuple[dict[str, Any], str, float, float, float]] = []
    for line in lines:
        style = line.setdefault("style", {})
        box = line.get("box") or {}
        x = float(box.get("x", 0))
        width = max(1.0, float(box.get("width", 0)))
        center = x + width / 2.0
        left_margin = x - content_left
        right_margin = content_right - (x + width)
        image_left = x
        image_right = image_width - (x + width)

        # Centred lines sit on the image's centre line with roughly equal
        # margins.  The width guard keeps a full-bleed banner from being
        # classified as centred just because its centre happens to match.
        near_image_center = (
            abs(center - image_center) <= max(2.0, image_width * 0.015)
            and width <= image_width * 0.94
        )
        image_symmetric = (
            min(image_left, image_right) >= max(2.0, image_width * 0.012)
            and abs(image_left - image_right) <= max(3.0, image_width * 0.025)
            and width <= image_width * 0.94
        )
        block_symmetric = (
            left_margin > tolerance
            and right_margin > tolerance
            and abs(left_margin - right_margin) <= max(tolerance, content_width * 0.035)
        )

        if near_image_center or image_symmetric or block_symmetric:
            align = "center"
        elif right_margin <= tolerance and left_margin > content_width * 0.12:
            align = "right"
        elif (
            len(lines) == 1
            and x > image_width * 0.35
            and x + width > image_width * 0.86
        ):
            align = "right"
        else:
            align = "left"
        records.append((style, align, left_margin, right_margin, center))

    if len(records) >= 2:
        content_center = (content_left + content_right) / 2.0
        centers_aligned = all(
            abs(center - image_center) <= max(4.0, image_width * 0.03)
            or abs(center - content_center) <= max(4.0, content_width * 0.03)
            for _, _, _, _, center in records
        )
        right_block = all(
            right_margin <= max(4.0, content_width * 0.025)
            for _, _, _, right_margin, _ in records
        ) and any(
            left_margin > content_width * 0.08
            for _, _, left_margin, _, _ in records
        )
        if centers_aligned:
            for style, *_ in records:
                style["align"] = "center"
            return
        if right_block:
            for style, *_ in records:
                style["align"] = "right"
            return

        # A left-aligned paragraph's longest line can coincidentally end on the
        # right content edge, or sit on the image centre line.  Looking at one
        # line at a time then labels it right/centre.  Two or more lines sharing
        # the same left edge are much stronger evidence of a left-aligned block.
        edge_tolerance = max(3.0, min(18.0, content_width * 0.02))
        ordered = sorted(
            range(len(records)),
            key=lambda index: content_left + records[index][2],
        )
        clustered: set[int] = set()
        cluster: list[int] = []
        cluster_start = 0.0
        for index in ordered:
            left = content_left + records[index][2]
            if not cluster:
                cluster = [index]
                cluster_start = left
            elif left - cluster_start <= edge_tolerance:
                cluster.append(index)
            else:
                if len(cluster) >= 2:
                    clustered.update(cluster)
                cluster = [index]
                cluster_start = left
        if len(cluster) >= 2:
            clustered.update(cluster)
        if clustered:
            for index, (style, align, *_) in enumerate(records):
                style["align"] = "left" if index in clustered else align
            return

    for style, align, *_ in records:
        style["align"] = align


# ------------------------------------------------------------------ font choice


def _font_for(
    text: str,
    face: FontFace | None,
    family: str,
    bold: bool,
    italic: bool,
) -> tuple[Path, int, str | None, FontFace | None]:
    """Pick a Latin face, falling back to system fonts for non-Latin scripts."""
    direction: str | None = None
    if CJK_RE.search(text):
        candidate = find_font_by_names(tuple(CJK_FONTS[bold] + CJK_FONTS[False]))
        if candidate:
            return candidate, 0, None, None
    if ARABIC_RE.search(text):
        candidate = find_font_by_names(tuple(ARABIC_FONTS[bold] + ARABIC_FONTS[False]))
        if candidate:
            return candidate, 0, "rtl", None
    if HEBREW_RE.search(text):
        candidate = find_font_by_names(tuple(HEBREW_FONTS[bold] + HEBREW_FONTS[False]))
        if candidate:
            return candidate, 0, "rtl", None
    if DEVANAGARI_RE.search(text):
        candidate = find_font_by_names(tuple(DEVANAGARI_FONTS[bold] + DEVANAGARI_FONTS[False]))
        if candidate:
            return candidate, 0, None, None
    if KANNADA_RE.search(text):
        candidate = find_font_by_names(tuple(KANNADA_FONTS[bold] + KANNADA_FONTS[False]))
        if candidate:
            return candidate, 0, None, None

    chosen = face or default_face(family, bold, italic)
    return chosen.path, chosen.index, None, chosen


def _load_font(path: Path, size: int, index: int = 0) -> ImageFont.FreeTypeFont:
    size = max(6, min(400, int(size)))
    try:
        return ImageFont.truetype(str(path), size=size, index=index)
    except (OSError, ValueError, RuntimeError) as exc:
        log.warning("Font %s failed (%s), falling back to DejaVu Sans", path, exc)
        return ImageFont.truetype(str(FONT_DIR / "DejaVuSans.ttf"), size=size)


# ----------------------------------------------------------------------- masks


def _word_boxes(line: dict[str, Any]) -> list[dict[str, Any]]:
    # ``mask_boxes`` contains every plausible word box (including weak or
    # filtered tokens) and is what prevents old text from surviving as a
    # shadow.  Old documents have only ``words``, so keep that fallback.
    masks = line.get("mask_boxes") or []
    if masks:
        return [box for box in masks if box]
    words = line.get("words") or []
    if words:
        return [word["box"] for word in words if word.get("box")]
    box = line.get("box")
    return [box] if box else []


def _line_is_graphic_like(
    line: dict[str, Any], image_width: int, image_height: int
) -> bool:
    """True for a huge OCR box holding only a couple of characters.

    Logos and illustrations are sometimes recognised as a two-letter word
    spanning a large part of the image.  Treating such a box as text is bad in
    two ways: it becomes editable junk, and filling its full rectangular patch
    smears the whole picture.  Real display text is usually several characters
    and/or much narrower than the image.
    """
    compact = re.sub(r"\s+", "", str(line.get("text") or ""))
    if not compact or len(compact) > 6:
        return False
    box = line.get("box") or {}
    try:
        width = max(1.0, float(box.get("width", 1.0)))
        height = max(1.0, float(box.get("height", 1.0)))
        confidence = float(line.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return False
    if image_width <= 0 or image_height <= 0:
        return False
    if (
        len(compact) <= 2
        and width >= image_width * 0.70
        and height >= image_height * 0.14
        and confidence < 85.0
    ):
        return True
    if (
        len(compact) <= 4
        and width >= image_width * 0.55
        and height >= image_height * 0.12
        and confidence < 75.0
        and width / len(compact) >= height * 1.4
    ):
        return True
    if (
        len(compact) <= 3
        and height >= image_height * 0.18
        and width >= image_width * 0.12
        and confidence < 60.0
    ):
        return True
    return False


def _word_box_is_oversized(
    box: dict[str, Any],
    line: dict[str, Any],
    image_width: int,
    image_height: int,
) -> bool:
    """True when a word box is really a graphic patch, not a text word."""
    compact = re.sub(r"\s+", "", str(line.get("text") or ""))
    if not compact or len(compact) > 6:
        return False
    try:
        width = max(1.0, float(box.get("width", 1.0)))
        height = max(1.0, float(box.get("height", 1.0)))
    except (TypeError, ValueError):
        return False
    return (
        width >= image_width * 0.35
        and height >= image_height * 0.10
    )


def _shadow_padding(line: dict[str, Any]) -> tuple[int, int, int, int]:
    """Extra mask room (left, top, right, bottom) for a cached shadow.

    ``build_text_mask`` normally grows right and down because most drop
    shadows fall that way.  A detected shadow can point in any direction, and
    a soft one extends a few pixels past its offset.  Using the cached values
    keeps the original shadow from surviving as a grey ghost when the text is
    replaced.
    """
    shadow = (line.get("style") or {}).get("shadow")
    if not isinstance(shadow, dict):
        return 0, 0, 0, 0
    try:
        dx = int(round(float(shadow.get("dx", 0))))
        dy = int(round(float(shadow.get("dy", 0))))
        blur = int(round(float(shadow.get("blur", 0))))
    except (TypeError, ValueError, OverflowError):
        return 0, 0, 0, 0
    blur = max(0, min(4, blur))
    return (
        max(0, -dx) + blur,
        max(0, -dy) + blur,
        max(0, dx) + blur,
        max(0, dy) + blur,
    )


def _extend_mask_for_shadow(mask: np.ndarray, line: dict[str, Any]) -> np.ndarray:
    """Add the cached shadow's offset/blur to a glyph mask.

    A textured background is repaired from the actual ink shape rather than a
    full rectangle.  The old code only shifted that shape right and down, so a
    shadow pointing left/up or carrying a soft blur could survive as a grey
    fringe.  The detected offset is applied here as well; when no shadow was
    detected this is a no-op.
    """
    shadow = (line.get("style") or {}).get("shadow")
    if not isinstance(shadow, dict):
        return mask
    try:
        dx = int(round(float(shadow.get("dx", 0))))
        dy = int(round(float(shadow.get("dy", 0))))
        blur = int(round(float(shadow.get("blur", 0))))
    except (TypeError, ValueError, OverflowError):
        return mask
    if dx or dy:
        mask = np.maximum(mask, _shift_mask(mask, dx, dy))
    blur = max(0, min(4, blur))
    if blur:
        kernel = np.ones((2 * blur + 1, 2 * blur + 1), np.uint8)
        mask = cv2.dilate(mask, kernel, iterations=1)
    return mask


def _line_vertical_limits(
    line: dict[str, Any],
    lines: list[dict[str, Any]] | None,
    image_height: int,
) -> tuple[int, int]:
    """Vertical room around a line before it reaches a neighbouring line.

    The removal mask deliberately grows right and down to catch drop shadows,
    but an unbounded patch can shave the ascenders off the line below when the
    leading is tight.  Bounding the patch by nearby line boxes keeps the
    neighbouring text untouched.
    """
    box = line.get("box") or {}
    if not box:
        return 0, image_height
    y = float(box.get("y", 0.0))
    h = max(1.0, float(box.get("height", 0.0)))
    left = float(box.get("x", 0.0))
    right = left + max(1.0, float(box.get("width", 0.0)))
    # The default band already leaves 32% of the line height above and below.
    # Add any cached shadow offset/blur so a soft shadow whose far edge falls
    # just outside that margin is not left behind as a grey fringe.  Neighbour
    # clamps below still protect an adjacent line when space is tight.
    _, shadow_top, _, shadow_bottom = _shadow_padding(line)
    top = max(0, int(math.floor(y - h * 0.32 - shadow_top)))
    bottom = min(
        image_height,
        int(math.ceil(y + h * 1.32 + shadow_bottom)),
    )
    for other in lines or []:
        if other is line or other.get("id") == line.get("id"):
            continue
        other_box = other.get("box") or {}
        ox = float(other_box.get("x", 0.0))
        ow = max(1.0, float(other_box.get("width", 0.0)))
        if min(right, ox + ow) - max(left, ox) <= 0:
            continue
        oy = float(other_box.get("y", 0.0))
        oh = max(1.0, float(other_box.get("height", 0.0)))
        if oy + oh <= y + h * 0.45:
            top = max(top, int(math.ceil(oy + oh + 2)))
        elif oy >= y + h * 0.55:
            bottom = min(bottom, int(math.floor(oy - 2)))
    # Never let a tight neighbour cut into this line's own glyphs.  The mask
    # must at least cover the box it is replacing, even if two OCR boxes touch.
    top = min(top, max(0, int(math.floor(y))))
    bottom = max(bottom, min(image_height, int(math.ceil(y + h))))
    if bottom <= top:
        top = max(0, int(math.floor(y)))
        bottom = min(image_height, int(math.ceil(y + h)))
    return top, bottom


def _mask_line_band(
    bgr: np.ndarray,
    line: dict[str, Any],
    mask: np.ndarray,
    lines: list[dict[str, Any]] | None = None,
) -> None:
    """Cover ink that has no word box, such as a word Tesseract missed.

    Word-box masks alone leave a gap exactly where recognition failed, so the
    old word can show through underneath the replacement as a ghost or shadow.
    On a flat background the whole line band is cleared.  On a textured
    background only ink-like pixels in the band are added, so the grain and
    colour gradient survive.
    """
    box = line.get("box") or {}
    if not box or not _word_boxes(line):
        return
    height, width = bgr.shape[:2]
    if _line_is_graphic_like(line, width, height):
        return
    x = int(round(float(box.get("x", 0.0))))
    y = int(round(float(box.get("y", 0.0))))
    bw = max(1, int(round(float(box.get("width", 1.0)))))
    bh = max(1, int(round(float(box.get("height", 1.0)))))
    if bw < 3 or bh < 4:
        return

    top_limit, bottom_limit = _line_vertical_limits(line, lines, height)
    shadow_left, shadow_top, shadow_right, shadow_bottom = _shadow_padding(line)
    band_x0 = max(0, x - max(max(1, int(round(bh * 0.06))), shadow_left + 2))
    band_y0 = max(top_limit, y - max(max(1, int(round(bh * 0.06))), shadow_top + 2))
    band_x1 = min(
        width,
        x + bw + max(max(3, int(round(bh * 0.26))), shadow_right + 2),
    )
    band_y1 = min(
        bottom_limit,
        y + bh + max(max(2, int(round(bh * 0.24))), shadow_bottom + 2),
    )
    # Do not let the band grow into a side-by-side column (menu item and
    # right-aligned price).  The word boxes themselves are safe; only the
    # padding can reach a neighbouring line, so clamp it to the gap.
    for other in lines or []:
        if other is line or other.get("id") == line.get("id"):
            continue
        other_box = other.get("box") or {}
        if not other_box:
            continue
        ox = int(round(float(other_box.get("x", 0.0))))
        oy = int(round(float(other_box.get("y", 0.0))))
        ow = max(1, int(round(float(other_box.get("width", 1.0)))))
        oh = max(1, int(round(float(other_box.get("height", 1.0)))))
        overlap_y = min(y + bh, oy + oh) - max(y, oy)
        if overlap_y <= min(bh, oh) * 0.35:
            continue
        if ox + ow <= x:
            band_x0 = max(band_x0, ox + ow + 2)
        elif ox >= x + bw:
            band_x1 = min(band_x1, ox - 2)
    if band_x1 - band_x0 < 3 or band_y1 - band_y0 < 3:
        return
    # A malformed/merged OCR box can cover a large fraction of the canvas.
    # Clearing that band would destroy the whole image; the word boxes still
    # get their glyph-level removal below.
    if (band_x1 - band_x0) * (band_y1 - band_y0) > width * height * 0.10:
        return

    pad = max(6, int(round(bh * 0.55)))
    crop_x0 = max(0, band_x0 - pad)
    crop_y0 = max(0, band_y0 - pad)
    crop_x1 = min(width, band_x1 + pad)
    crop_y1 = min(height, band_y1 + pad)
    if crop_x1 - crop_x0 < 4 or crop_y1 - crop_y0 < 4:
        return
    crop = bgr[crop_y0:crop_y1, crop_x0:crop_x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    ring = max(2, min(pad, gray.shape[0] // 3, gray.shape[1] // 3))
    border = gray[_mask_for_border(gray, ring)]
    if border.size == 0:
        return
    bg_gray = float(_robust_median(border))
    spread = _tone_spread(border)
    diff = np.abs(gray.astype(np.float32) - bg_gray)
    contrast = float(np.percentile(diff, 95)) if diff.size else 0.0

    if spread < 8.0:
        # Flat paper/banner: clear the entire line band, including the gaps
        # where an unrecognised word and its shadow sit.
        mask[band_y0:band_y1, band_x0:band_x1] = 255
        return

    if contrast < 18.0:
        return
    inner = gray[
        band_y0 - crop_y0 : band_y1 - crop_y0,
        band_x0 - crop_x0 : band_x1 - crop_x0,
    ]
    glyph = _crop_glyph_mask(inner, bg_gray, contrast)
    if glyph is None:
        return
    coverage = float(glyph.mean()) / 255.0
    if not 0.004 <= coverage <= 0.88:
        return

    radius = max(2, min(12, int(round(bh * 0.15))))
    glyph = cv2.dilate(
        glyph,
        np.ones((2 * radius + 1, 2 * radius + 1), np.uint8),
        iterations=1,
    )
    shadow = max(2, int(round(bh * 0.20)))
    if shadow < glyph.shape[0] and shadow < glyph.shape[1]:
        shifted = np.zeros_like(glyph)
        shifted[shadow:, shadow:] = glyph[:-shadow, :-shadow]
        glyph = np.maximum(glyph, shifted)
        sideways = np.zeros_like(glyph)
        sideways[:, shadow:] = glyph[:, :-shadow]
        glyph = np.maximum(glyph, sideways)
    glyph = _extend_mask_for_shadow(glyph, line)
    region = mask[band_y0:band_y1, band_x0:band_x1]
    mask[band_y0:band_y1, band_x0:band_x1] = np.maximum(region, glyph)


def build_text_mask(
    bgr: np.ndarray,
    line: dict[str, Any],
    lines: list[dict[str, Any]] | None = None,
) -> np.ndarray:
    """Build a mask for the old glyphs, preserving as much background as possible."""
    height, width = bgr.shape[:2]
    mask = np.zeros((height, width), dtype=np.uint8)
    graphic_line = _line_is_graphic_like(line, width, height)
    top_limit, bottom_limit = _line_vertical_limits(line, lines, height)
    shadow_left, shadow_top, shadow_right, shadow_bottom = _shadow_padding(line)

    for box in _word_boxes(line):
        bh = max(1, int(box["height"]))
        bw = max(1, int(box["width"]))
        oversized = graphic_line or _word_box_is_oversized(
            box, line, width, height
        )
        # Generous padding gives the local background estimator clean pixels
        # and also catches ascenders/descenders that Tesseract boxes tightly.
        pad_x = max(4, int(round(bh * 0.35)))
        pad_y = max(4, int(round(bh * 0.42)))
        x0 = max(0, int(box["x"]) - pad_x)
        y0 = max(top_limit, int(box["y"]) - pad_y)
        x1 = min(width, int(box["x"]) + bw + pad_x)
        y1 = min(bottom_limit, int(box["y"]) + bh + pad_y)
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue

        # Tight removal patch around the word.  It stays well inside the larger
        # crop below, leaving a clean border for the background estimate.  Drop
        # shadows usually fall right and down, so the patch grows further in
        # those directions; the top/left stay tighter to avoid clipping a
        # closely spaced line above.
        left = max(2, int(round(bh * 0.10)))
        top = max(2, int(round(bh * 0.10)))
        right = max(4, int(round(bh * 0.26)))
        bottom = max(4, int(round(bh * 0.30)))
        # Include a detected shadow's offset and softness so its far edge is
        # removed with the glyph rather than left behind as a grey halo.
        left = max(left, shadow_left + 2)
        top = max(top, shadow_top + 2)
        right = max(right, shadow_right + 2)
        bottom = max(bottom, shadow_bottom + 2)
        patch_x0 = max(x0, int(box["x"]) - left)
        patch_y0 = max(top_limit, int(box["y"]) - top)
        patch_x1 = min(x1, int(box["x"]) + bw + right)
        patch_y1 = min(bottom_limit, int(box["y"]) + bh + bottom)
        patch_area = max(0, patch_x1 - patch_x0) * max(0, patch_y1 - patch_y0)

        crop = bgr[y0:y1, x0:x1]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        ring = max(2, min(pad_y, gray.shape[0] // 3, gray.shape[1] // 3))
        border = gray[_mask_for_border(gray, ring)]
        bg_gray = float(_robust_median(border)) if border.size else float(_robust_median(gray.reshape(-1)))
        spread = _tone_spread(border) if border.size else 0.0
        diff = np.abs(gray.astype(np.float32) - bg_gray)
        contrast = float(np.percentile(diff, 95)) if diff.size else 0.0

        glyph = None
        if contrast >= 18 and spread < 62:
            glyph = _crop_glyph_mask(gray, bg_gray, contrast)
        if glyph is not None:
            coverage = float(glyph.mean()) / 255.0
            if 0.006 <= coverage <= 0.88:
                if (
                    spread < 8
                    and not oversized
                    and patch_area <= width * height * 0.08
                ):
                    # Flat banner/paper: fill the tight word patch.  A flat
                    # fill removes antialiased edges and drop shadows that a
                    # glyph-only mask leaves behind as a dark halo.  Oversized
                    # logo/junk boxes are excluded: filling their rectangle is
                    # what smeared a whole image.
                    mask[patch_y0:patch_y1, patch_x0:patch_x1] = 255
                else:
                    # Textured/gradient background (or an oversized box):
                    # dilate generously so the repair pass also covers the
                    # original antialiasing halo, but never fill the full
                    # rectangle.
                    radius = max(2, min(12, int(round(bh * 0.15))))
                    glyph = cv2.dilate(
                        glyph,
                        np.ones((2 * radius + 1, 2 * radius + 1), np.uint8),
                        iterations=1,
                    )
                    # Drop shadows usually fall right and down; extend the mask
                    # in that direction without eating into the line above.
                    shadow = max(2, int(round(bh * 0.18)))
                    if shadow < glyph.shape[0] and shadow < glyph.shape[1]:
                        shifted = np.zeros_like(glyph)
                        shifted[shadow:, shadow:] = glyph[:-shadow, :-shadow]
                        glyph = np.maximum(glyph, shifted)
                        sideways = np.zeros_like(glyph)
                        sideways[:, shadow:] = glyph[:, :-shadow]
                        glyph = np.maximum(glyph, sideways)
                    glyph = _extend_mask_for_shadow(glyph, line)
                    region = mask[y0:y1, x0:x1]
                    mask[y0:y1, x0:x1] = np.maximum(region, glyph)
                continue

        # A busy background or a very low-contrast crop: remove the full word
        # patch.  It is safer than leaving half a glyph behind, and staying
        # inside the repair crop keeps a clean border for the background
        # estimate.  Never do this for an oversized graphic box or a patch
        # covering a large part of the canvas; leaving the artwork alone is
        # much better than smearing the whole region.
        if oversized or patch_area > width * height * 0.08:
            continue
        mask[patch_y0:patch_y1, patch_x0:patch_x1] = 255

    _mask_line_band(bgr, line, mask, lines)
    if mask.any():
        mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
    return mask


def _repair_region(repaired: np.ndarray, crop_mask: np.ndarray, x0: int, y0: int) -> None:
    crop = repaired[y0 : y0 + crop_mask.shape[0], x0 : x0 + crop_mask.shape[1]]
    if crop.size == 0 or not crop_mask.any():
        return
    ring = max(2, min(9, min(crop.shape[:2]) // 8))
    border_mask = _mask_for_border(crop, ring)
    if crop_mask.any():
        # Never sample the old glyph/shadow pixels themselves; the mask is
        # deliberately larger than the text to remove halos.
        border_mask &= crop_mask == 0
    border = crop[border_mask]
    if border.size == 0:
        clean = crop[crop_mask == 0]
        border = clean if clean.size else _border_pixels(crop, ring)
    spread = 0.0
    if border.size:
        if border.ndim == 2 and border.shape[1] >= 3:
            # BGR rows.  Spread must describe spatial variation in luminance,
            # not the difference between colour channels: a flat orange banner
            # has very different B/G/R values but is still a flat background.
            tone = (
                border[:, 0].astype(np.float32) * 0.0722
                + border[:, 1].astype(np.float32) * 0.7152
                + border[:, 2].astype(np.float32) * 0.2126
            )
            spread = _tone_spread(tone)
        else:
            spread = _tone_spread(border)
    bg_color = (
        _robust_median(border, axis=0)
        if border.size
        else _robust_median(crop.reshape(-1, crop.shape[2]), axis=0)
    )

    if spread < 8.0:
        # Flat colour: a clean fill avoids the dark smudge that inpainting can
        # leave around bold glyph edges.  Feather only *outside* the mask so
        # the old glyph edge is replaced completely rather than blended into
        # the new background (which reads as a drop shadow).
        hard = (crop_mask > 0).astype(np.float32)
        alpha = np.maximum(hard, cv2.GaussianBlur(hard, (0, 0), 1.0))
        alpha = np.clip(alpha, 0.0, 1.0)[..., None]
        flat = np.empty_like(crop)
        flat[:, :] = np.asarray(bg_color, dtype=np.float32)
        blended = crop.astype(np.float32) * (1.0 - alpha) + flat * alpha
        crop[:] = np.clip(blended, 0, 255).astype(np.uint8)
        return

    if spread < 42.0:
        # Gentle gradient / paper texture: median-filter the crop so the glyphs
        # disappear while the surrounding tone and grain remain.
        k = _odd(max(5, min(41, int(max(crop.shape[:2]) * 0.5))))
        # medianBlur needs an odd kernel no larger than the crop.
        limit = min(crop.shape[:2])
        if limit % 2 == 0:
            limit -= 1
        k = min(k, max(1, limit))
        if k >= 3:
            estimate = cv2.medianBlur(crop, k)
            hard = (crop_mask > 0).astype(np.float32)
            alpha = np.maximum(hard, cv2.GaussianBlur(hard, (0, 0), 1.0))
            alpha = np.clip(alpha, 0.0, 1.0)[..., None]
            blended = crop.astype(np.float32) * (1.0 - alpha) + estimate.astype(np.float32) * alpha
            crop[:] = np.clip(blended, 0, 255).astype(np.uint8)
            return

    # Busy photo: content-aware inpainting is the best available local option.
    radius = max(3, min(15, int(max(crop.shape[:2]) * 0.14)))
    estimate = cv2.inpaint(crop, crop_mask, radius, cv2.INPAINT_TELEA)
    crop[crop_mask > 0] = estimate[crop_mask > 0]


def repair_background(bgr: np.ndarray, mask: np.ndarray, lines: list[dict[str, Any]]) -> np.ndarray:
    """Remove every masked glyph/shadow using a local background estimate.

    The mask now covers line-level gaps as well as individual word boxes, so
    repairing only the word boxes would leave those gap pixels untouched — the
    exact failure that made old text look like a shadow under the replacement.
    Connected regions merge letters into words/lines and guarantee that every
    masked pixel is repaired.
    """
    repaired = bgr.copy()
    height, width = repaired.shape[:2]
    if mask is None or not mask.any():
        return repaired
    # Merge letters into word/line groups before repairing; a single glyph
    # leaves too little clean border for a reliable background estimate.
    grouped = cv2.dilate(mask, np.ones((5, 5), np.uint8), iterations=1)
    try:
        count, _, stats, _ = cv2.connectedComponentsWithStats(grouped, connectivity=8)
    except cv2.error:
        return repaired
    for index in range(1, count):
        x, y, w, h, area = (int(value) for value in stats[index])
        if area < 2 or w < 1 or h < 1:
            continue
        # Keep enough clean border for the estimate without reaching into a
        # neighbouring line.  Cap the pad so tall display text cannot sample
        # the row above/below.
        pad = max(5, min(16, int(round(min(w, h) * 0.45))))
        x0 = max(0, x - pad)
        y0 = max(0, y - pad)
        x1 = min(width, x + w + pad)
        y1 = min(height, y + h + pad)
        if x1 <= x0 or y1 <= y0:
            continue
        crop_mask = mask[y0:y1, x0:x1]
        if crop_mask.any():
            _repair_region(repaired, crop_mask, x0, y0)
    return repaired


# --------------------------------------------------------------------- fitting


def _text_metrics(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    spacing: int,
    direction: str | None,
) -> tuple[int, int, tuple[int, int, int, int]]:
    kwargs: dict[str, Any] = {"font": font, "spacing": spacing, "align": "left"}
    if direction:
        kwargs["direction"] = direction
    bbox = draw.multiline_textbbox((0, 0), text, **kwargs)
    return bbox[2] - bbox[0], bbox[3] - bbox[1], bbox


def _fit_font_size(
    text: str,
    font_path: Path,
    font_index: int,
    max_width: int,
    max_height: int,
    direction: str | None,
    start: int | None = None,
    preserve: bool = False,
) -> int:
    draw = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    start_size = max(6, start or max(8, int(max_height * 0.92)))

    def fits(size: int, tolerance: float = 1.0) -> bool:
        font = _load_font(font_path, size, font_index)
        spacing = max(1, size // 6)
        text_width, text_height, _ = _text_metrics(draw, text, font, spacing, direction)
        return text_width <= max_width * tolerance and text_height <= max_height * tolerance

    if fits(start_size):
        return start_size
    # Unchanged text should keep its original point size.  Allow a small
    # overflow instead of shrinking a line merely because its OCR box was tight.
    if preserve and fits(start_size, 1.12):
        return start_size

    # Text metrics grow monotonically with the point size, so a binary search
    # avoids opening the font file hundreds of times for a large scale-up.
    low, high = 6, start_size - 1
    best = 6
    while low <= high:
        middle = (low + high) // 2
        if fits(middle):
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    return best


def _expand_box(
    box: dict[str, int],
    all_lines: list[dict[str, Any]],
    line_id: str,
    image_width: int,
    image_height: int,
    align: str,
) -> tuple[int, int, int, int]:
    """Allow breathing room for a longer replacement while keeping the centre."""
    x, y, width, height = box["x"], box["y"], box["width"], box["height"]
    left_limit = 4
    right_limit = image_width - 4
    top, bottom = y, y + height
    for other in all_lines:
        if other["id"] == line_id:
            continue
        ob = other["box"]
        overlap = min(bottom, ob["y"] + ob["height"]) - max(top, ob["y"])
        if overlap > min(height, ob["height"]) * 0.35:
            if ob["x"] + ob["width"] <= x + width * 0.45:
                left_limit = max(left_limit, ob["x"] + ob["width"] + 5)
            elif ob["x"] >= x + width * 0.45:
                right_limit = min(right_limit, ob["x"] - 5)

    if align == "right":
        # Grow into the free space to the left instead of capping the box at
        # 1.9x.  A longer replacement should keep its point size and use the
        # empty middle of a menu row, not shrink to fit the old price width.
        max_width = max(width, x + width - left_limit)
        new_x = max(left_limit, x + width - max_width)
    elif align == "center":
        center = x + width / 2.0
        max_width = max(
            width, int(2 * min(center - left_limit, right_limit - center))
        )
        new_x = int(round(center - max_width / 2.0))
    else:
        max_width = max(width, right_limit - x)
        new_x = x

    new_x = max(0, min(image_width - max_width, new_x))
    # A tight OCR box is ink-tight.  Font ascenders/descenders need roughly
    # 1.35-1.45x the ink height; using only 1.12x forced the fitter to shrink
    # unchanged text.  Keep the original line centre instead of only padding
    # the bottom.
    new_height = max(8, int(round(height * 1.42)))
    centre = y + height / 2.0
    new_y = int(round(centre - new_height / 2.0))
    new_y = max(0, min(max(0, image_height - new_height), new_y))
    return new_x, new_y, max(4, max_width), new_height


# -------------------------------------------------------------------- render


def _draw_text_decorations(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    spacing: int,
    direction: str | None,
    block_width: float,
    align: str,
    color: Any,
    size: int,
    underline: bool,
    strikethrough: bool,
) -> None:
    """Redraw original underline/strikethrough strokes under new text."""
    if not (underline or strikethrough) or not text:
        return
    try:
        ascent, descent = font.getmetrics()
    except (AttributeError, OSError, ValueError):
        ascent, descent = max(1, int(size)), max(1, int(round(size * 0.25)))
    step = max(1, ascent + descent + spacing)
    thickness = max(1, int(round(max(1, size) * 0.055)))
    for index, line_text in enumerate(text.split("\n")):
        if not line_text:
            continue
        kwargs: dict[str, Any] = {"font": font}
        if direction:
            kwargs["direction"] = direction
        try:
            line_width = float(draw.textlength(line_text, **kwargs))
        except (AttributeError, OSError, ValueError):
            line_width = float(block_width)
        if align == "center":
            line_x = float(xy[0]) + (float(block_width) - line_width) / 2.0
        elif align == "right":
            line_x = float(xy[0]) + float(block_width) - line_width
        else:
            line_x = float(xy[0])
        baseline = float(xy[1]) + ascent + index * step
        if underline:
            y = baseline + max(1.0, descent * 0.20)
            draw.line(
                [(line_x, y), (line_x + line_width, y)],
                fill=color,
                width=thickness,
            )
        if strikethrough:
            y = baseline - ascent * 0.32
            draw.line(
                [(line_x, y), (line_x + line_width, y)],
                fill=color,
                width=thickness,
            )


def _effect_override(edit: Any, name: str) -> bool | None:
    """Return an explicit True/False effect override, or ``None`` to inherit."""
    value = getattr(edit, name, None)
    if value is None:
        return None
    return bool(value)


def _resolve_shadow(
    edit: Any, style: dict[str, Any], font_size: int
) -> dict[str, Any] | None:
    """Resolve a shadow override against the style sampled during OCR.

    ``None`` inherits the detected shadow (which may be absent), ``False``
    removes it, and ``True`` keeps a detected shadow or adds a subtle
    user-requested drop shadow when the source had none.  Automatic rendering
    never calls this with ``True``, so clean lines stay shadow-free unless the
    user explicitly asks for the effect.
    """
    override = _effect_override(edit, "shadow")
    if override is False:
        return None
    shadow = style.get("shadow")
    if isinstance(shadow, dict) and shadow:
        return shadow
    if override is not True:
        return None
    size = max(6, int(font_size))
    offset = max(1, int(round(size * 0.045)))
    blur = max(1, int(round(size * 0.035)))
    return {
        "dx": offset,
        "dy": offset,
        "blur": blur,
        "color": "#000000",
        "opacity": 110,
    }


def _box_rect(box: dict[str, Any]) -> tuple[float, float, float, float] | None:
    """Return ``(x0, y0, x1, y1)`` for a line/box dictionary."""
    if not box:
        return None
    try:
        left = float(box.get("x", 0.0))
        top = float(box.get("y", 0.0))
        width = float(box.get("width", 0.0))
        height = float(box.get("height", 0.0))
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(value) for value in (left, top, width, height)):
        return None
    if width <= 0 or height <= 0:
        return None
    return left, top, left + width, top + height


def _clip_rect(
    rect: tuple[float, float, float, float],
    image_width: int,
    image_height: int,
) -> tuple[int, int, int, int] | None:
    """Clip a float rectangle to the canvas and round it outwards."""
    x0, y0, x1, y1 = rect
    if x1 <= x0 or y1 <= y0:
        return None
    left = max(0, int(math.floor(x0)))
    top = max(0, int(math.floor(y0)))
    right = min(image_width, int(math.ceil(x1)))
    bottom = min(image_height, int(math.ceil(y1)))
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _subtract_rect(
    rect: tuple[int, int, int, int],
    obstacle: tuple[int, int, int, int],
) -> list[tuple[int, int, int, int]]:
    """Split ``rect`` into the parts that do not intersect ``obstacle``."""
    x0, y0, x1, y1 = rect
    ox0, oy0, ox1, oy1 = obstacle
    if ox1 <= x0 or ox0 >= x1 or oy1 <= y0 or oy0 >= y1:
        return [rect]
    parts: list[tuple[int, int, int, int]] = []
    if oy0 > y0:
        parts.append((x0, y0, x1, oy0))
    if oy1 < y1:
        parts.append((x0, oy1, x1, y1))
    middle_top = max(y0, oy0)
    middle_bottom = min(y1, oy1)
    if middle_bottom > middle_top:
        if ox0 > x0:
            parts.append((x0, middle_top, ox0, middle_bottom))
        if ox1 < x1:
            parts.append((ox1, middle_top, x1, middle_bottom))
    return [part for part in parts if part[2] > part[0] and part[3] > part[1]]


def _background_fill_rects(
    line: dict[str, Any],
    lines: list[dict[str, Any]] | None,
    image_width: int,
    image_height: int,
    target: tuple[float, float, float, float] | None = None,
) -> list[tuple[int, int, int, int]]:
    """Return the paintable parts of ``target`` that avoid every other box.

    A line whose text has grown can make the fill target larger than its
    original OCR box.  The target is therefore split around neighbouring
    selections instead of being reduced to a single largest rectangle; the
    grown text keeps its background wherever there is free room, and no other
    text box is ever painted over.
    """
    raw = target if target is not None else _box_rect(line.get("box") or {})
    if raw is None:
        return []
    clipped = _clip_rect(raw, image_width, image_height)
    if clipped is None:
        return []

    selected_id = line.get("id")
    rects: list[tuple[int, int, int, int]] = [clipped]
    for other in lines or []:
        if other is line:
            continue
        if selected_id is not None and other.get("id") == selected_id:
            continue
        obstacle = _box_rect(other.get("box") or {})
        if obstacle is None:
            continue
        clipped_obstacle = _clip_rect(obstacle, image_width, image_height)
        if clipped_obstacle is None:
            continue
        next_rects: list[tuple[int, int, int, int]] = []
        for rect in rects:
            next_rects.extend(_subtract_rect(rect, clipped_obstacle))
        rects = next_rects
        if not rects:
            return []
    return rects


def _background_fill_box(
    line: dict[str, Any],
    lines: list[dict[str, Any]] | None,
    image_width: int,
    image_height: int,
) -> tuple[int, int, int, int] | None:
    """Return the largest paintable part of a line box, or ``None``.

    Kept as a single-rectangle convenience wrapper for tests and callers that
    only need the main filled region.
    """
    rects = _background_fill_rects(line, lines, image_width, image_height)
    if not rects:
        return None
    return max(rects, key=lambda rect: (rect[2] - rect[0]) * (rect[3] - rect[1]))


def _paint_background_fill(
    output: Image.Image,
    line: dict[str, Any],
    lines: list[dict[str, Any]] | None,
    background: str,
    target: tuple[float, float, float, float] | None = None,
) -> tuple[int, int, int, int] | None:
    """Paste a flat fill into the allowed part of one line's area.

    ``target`` may extend past the original OCR box when replacement text has
    grown.  Every part that would overlap another selection is skipped, so the
    fill can never tint another line.  Returns the largest painted rectangle,
    or ``None`` when the line has no free room at all.
    """
    rects = _background_fill_rects(
        line, lines, output.width, output.height, target=target
    )
    if not rects:
        return None
    color = _parse_hex(background)
    for x0, y0, x1, y1 in rects:
        output.paste(Image.new("RGB", (x1 - x0, y1 - y0), color), (x0, y0))
    return max(rects, key=lambda rect: (rect[2] - rect[0]) * (rect[3] - rect[1]))


def _edit_value(edit: Any, name: str, default: Any = None) -> Any:
    """Read an optional render-edit field from a model or plain mapping."""
    if isinstance(edit, dict):
        return edit.get(name, default)
    return getattr(edit, name, default)


def _new_line_from_edit(
    edit: Any, image_width: int, image_height: int
) -> dict[str, Any] | None:
    """Build a synthetic line for a user-created text box.

    Created boxes have no source glyphs, so they are never masked or sampled
    from the image.  Their default style is a plain sans face; the client sends
    explicit font/weight/colour/size overrides when the user picks them or
    copies them from another selection.
    """
    box = _edit_value(edit, "box")
    if not box:
        return None

    def field(name: str) -> Any:
        if isinstance(box, dict):
            return box.get(name, 0.0)
        return getattr(box, name, 0.0)

    try:
        left = float(field("x"))
        top = float(field("y"))
        width = float(field("width"))
        height = float(field("height"))
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(value) for value in (left, top, width, height)):
        return None
    if width <= 0 or height <= 0:
        return None

    x0 = max(0, min(image_width - 1, int(round(left))))
    y0 = max(0, min(image_height - 1, int(round(top))))
    x1 = max(x0 + 1, min(image_width, int(round(left + width))))
    y1 = max(y0 + 1, min(image_height, int(round(top + height))))
    rect_width = max(1, x1 - x0)
    rect_height = max(1, y1 - y0)

    font_size = _edit_value(edit, "font_size")
    try:
        base_size = float(font_size) if font_size is not None else rect_height * 0.62
    except (TypeError, ValueError, OverflowError):
        base_size = rect_height * 0.62
    if not math.isfinite(base_size):
        base_size = rect_height * 0.62
    base_size = max(4.0, min(800.0, base_size))

    category = str(_edit_value(edit, "font", "auto") or "auto")
    if category not in STYLE_FAMILIES:
        category = "sans"
    align = str(_edit_value(edit, "align", "left") or "left")
    if align not in {"left", "center", "right"}:
        align = "left"

    def tri_state(name: str) -> bool:
        value = _edit_value(edit, name)
        return bool(value) if value is not None else False

    style = {
        "color": _edit_value(edit, "color") or DEFAULT_DARK,
        "background": None,
        "font_size": base_size,
        "font": category,
        "bold": tri_state("bold"),
        "italic": tri_state("italic"),
        "font_id": None,
        "font_name": str(_edit_value(edit, "font_family") or "").strip(),
        "align": align,
        "underline": tri_state("underline"),
        "strikethrough": tri_state("strikethrough"),
        "shadow": None,
    }
    return {
        "id": str(_edit_value(edit, "line_id", "") or ""),
        "text": "",
        "box": {"x": x0, "y": y0, "width": rect_width, "height": rect_height},
        "confidence": 100,
        "words": [],
        "mask_boxes": [],
        "style": style,
        "is_new": True,
    }


def _union_rect(
    first: tuple[float, float, float, float] | None,
    second: tuple[float, float, float, float] | None,
) -> tuple[float, float, float, float] | None:
    if first is None:
        return second
    if second is None:
        return first
    return (
        min(first[0], second[0]),
        min(first[1], second[1]),
        max(first[2], second[2]),
        max(first[3], second[3]),
    )


def _prepare_render_job(
    output: Image.Image,
    draw: ImageDraw.ImageDraw,
    edit: Any,
    line: dict[str, Any],
    style: dict[str, Any],
    font_spec: tuple[Path, int, str | None, FontFace | None],
    preferred: int,
    align: str,
    lines: list[dict[str, Any]],
    is_new: bool,
) -> dict[str, Any]:
    """Lay out one replacement so fills can be painted before any text."""
    text = (str(_edit_value(edit, "text", "") or "")).replace("\r\n", "\n").replace(
        "\r", "\n"
    )
    base_box = _expand_box(
        line["box"], lines, line["id"], output.width, output.height, align
    )
    x, y, box_width, box_height = base_box
    margin = max(1, min(6, line["box"]["height"] // 18))
    line_rect = _box_rect(line.get("box") or {})

    if not text.strip():
        return {
            "edit": edit,
            "line": line,
            "style": style,
            "text": text,
            "empty": True,
            "fill_target": line_rect,
            "applied": {
                "line_id": line["id"],
                "mode": "added" if is_new else "removed",
                "box": {
                    "x": x,
                    "y": y,
                    "width": box_width,
                    "height": box_height,
                },
                "font_size": 0,
                "color": style.get("color") or DEFAULT_DARK,
            },
        }

    font_path, font_index, direction, _face = font_spec
    max_width = max(4, box_width - margin * 2)
    max_height = max(6, box_height)
    # The detected point size is part of the look that must survive an edit.
    # Only an explicit scale override may change it; replacement text that fits
    # the expanded line box keeps the original size even when the wording is
    # longer or shorter.  An explicit scale is an instruction, so do not
    # silently fit it back down to the old box.
    scale = float(_edit_value(edit, "scale", 1.0) or 1.0)
    preserve_size = abs(scale - 1.0) < 1e-6
    if preserve_size:
        size = _fit_font_size(
            text,
            font_path,
            font_index,
            max_width,
            max_height,
            direction,
            start=preferred,
            preserve=True,
        )
    else:
        size = preferred
    font = _load_font(font_path, size, font_index)
    spacing = max(1, size // 6)
    text_width, text_height, bbox = _text_metrics(
        draw, text, font, spacing, direction
    )

    if align == "center":
        draw_x = x + (box_width - text_width) / 2 - bbox[0]
    elif align == "right":
        draw_x = x + box_width - margin - text_width - bbox[0]
    else:
        draw_x = x + margin - bbox[0]
    draw_y = y + (box_height - text_height) / 2 - bbox[1]
    draw_x = max(0, min(output.width - max(1, text_width), draw_x))
    draw_y = max(-bbox[1], draw_y)

    # The paint area follows the actual replacement glyph box, not only the
    # tight OCR box.  Growing the size therefore keeps the background behind
    # the new text.  The fill helper splits this rectangle around every other
    # selection, so it still never covers another line.
    content_rect = (
        draw_x + bbox[0],
        draw_y + bbox[1],
        draw_x + bbox[0] + text_width,
        draw_y + bbox[1] + text_height,
    )
    fill_target = _union_rect(line_rect, content_rect)
    if fill_target is not None:
        fill_target = (
            fill_target[0] - 2,
            fill_target[1] - 2,
            fill_target[2] + 2,
            fill_target[3] + 2,
        )

    return {
        "edit": edit,
        "line": line,
        "style": style,
        "text": text,
        "empty": False,
        "font": font,
        "font_size": size,
        "spacing": spacing,
        "direction": direction,
        "text_width": text_width,
        "text_height": text_height,
        "bbox": bbox,
        "draw_x": draw_x,
        "draw_y": draw_y,
        "align": align,
        "fill_target": fill_target,
        "applied": {
            "line_id": line["id"],
            "mode": "added" if is_new else "edited",
            "box": {
                "x": int(round(draw_x + bbox[0])),
                "y": int(round(draw_y + bbox[1])),
                "width": text_width,
                "height": text_height,
            },
            "font_size": size,
            "color": _edit_value(edit, "color") or style.get("color") or DEFAULT_DARK,
        },
    }


def render_document(
    image_path: Path,
    lines: list[dict[str, Any]],
    edits: list[Any],
    output_path: Path,
) -> list[dict[str, Any]]:
    """Apply edits to the source image and write a new preview PNG."""
    try:
        with Image.open(image_path) as opened:
            image = opened.convert("RGB")
    except (OSError, ValueError) as exc:
        raise RenderError("The working image could not be read.") from exc
    bgr = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR).copy()

    # Older documents predate alignment/style sampling.  Fill in what we can
    # before resolving the per-edit jobs.
    if any(not (line.get("style") or {}).get("align") for line in lines):
        infer_alignment(lines, image.width, image.height)

    by_id = {line["id"]: line for line in lines}
    # OCR lines plus synthetic boxes created by the user.  Layout and fill
    # collision checks must see both: an edited line may not grow over a new
    # box, and a new box must not paint its fill over an existing line.
    render_lines: list[dict[str, Any]] = list(lines)
    jobs: list[
        tuple[
            Any,
            dict[str, Any],
            dict[str, Any],
            tuple[Path, int, str | None, FontFace | None],
            int,
            str,
            bool,
        ]
    ] = []
    combined = np.zeros(bgr.shape[:2], dtype=np.uint8)

    for edit in edits:
        is_new = _edit_value(edit, "box") is not None
        if is_new:
            # A created box has no source glyphs, so it is never masked or
            # sampled.  Its style comes from the edit, optionally copied from
            # another selection by the client (background excluded there).
            line = _new_line_from_edit(edit, image.width, image.height)
            if line is None:
                raise RenderError(f"Invalid text box: {edit.line_id}")
            used_ids = {str(item.get("id")) for item in render_lines}
            if line["id"] in used_ids:
                # A synthetic id must not shadow an OCR line's fill obstacle.
                suffix = 1
                while f"{line['id']}-{suffix}" in used_ids:
                    suffix += 1
                line["id"] = f"{line['id']}-{suffix}"
            render_lines.append(line)
            style = dict(line.get("style") or {})
        else:
            line = by_id.get(edit.line_id)
            if line is None:
                raise RenderError(f"Unknown text line: {edit.line_id}")
            # Styles are sampled once when OCR runs.  Old documents created
            # before typeface sampling fall back to the detector on first render.
            style = dict(line.get("style") or {})
            if (
                not style.get("color")
                or not style.get("font")
                or face_by_id(style.get("font_id")) is None
            ):
                # Sampling only fills gaps in an old style.  Existing colour,
                # weight, slant, and alignment are authoritative, but a stale
                # size is reconciled when the fresh measurement disagrees.
                sampled = sample_line_style(image, line)
                style = _merge_sampled_style(sampled, style)
                line["style"] = style
            if not _shadow_is_cached(style):
                # Shadow sampling is deferred until a line is actually edited:
                # OCR samples hundreds of lines, while rendering samples only
                # the ones the user changed.  Old documents lack the key
                # entirely, so this also preserves their original shadow on the
                # first edit.
                style["shadow"] = _line_shadow(
                    image,
                    line,
                    _parse_hex(style.get("background") or DEFAULT_LIGHT),
                    _parse_hex(style.get("color") or DEFAULT_DARK),
                )
                line["style"] = style
            if "underline" not in style or "strikethrough" not in style:
                # Older documents predate decoration sampling.  Detect it on
                # the first edit, like the shadow, rather than losing the
                # original underline/strikethrough.
                decorations = _decorations_for_box(
                    image,
                    line.get("box") or {},
                    str(line.get("text") or ""),
                    bounded=True,
                )
                style.setdefault("underline", bool(decorations.get("underline")))
                style.setdefault(
                    "strikethrough", bool(decorations.get("strikethrough"))
                )
                line["style"] = style
            # Only real OCR glyphs are erased.  A new box never adds to the
            # removal mask, so it cannot make existing text disappear.
            combined = np.maximum(combined, build_text_mask(bgr, line, lines))

        edit_font = _edit_value(edit, "font", "auto")
        family = (
            edit_font
            if edit_font in STYLE_FAMILIES
            else str(style.get("font", "sans"))
        )
        bold_override = _edit_value(edit, "bold")
        bold = (
            bool(style.get("bold", False))
            if bold_override is None
            else bool(bold_override)
        )
        italic_override = _edit_value(edit, "italic")
        italic = (
            bool(style.get("italic", False))
            if italic_override is None
            else bool(italic_override)
        )

        requested_family = str(_edit_value(edit, "font_family", "") or "").strip()
        entry = font_family(requested_family) if requested_family else None
        if entry is not None:
            family = entry.category
            # Honour the exact requested family when it ships the requested
            # weight/slant.  If it does not, prefer another family in the same
            # category that does instead of silently dropping the override.
            face = entry.faces.get((bold, italic)) or default_face(family, bold, italic)
        elif edit_font in STYLE_FAMILIES:
            face = default_face(family, bold, italic)
        else:
            # Keep the specific face detected during OCR, but honour explicit
            # weight/slant overrides by taking the matching variant of the same
            # family when one exists.
            detected = face_by_id(style.get("font_id"))
            if detected is not None:
                detected_family = font_family(detected.family)
                if detected_family is not None:
                    # Prefer the exact variant in the detected family.  When
                    # the user asks for a weight/slant that family does not
                    # ship, fall back to another family that has it rather
                    # than silently ignoring the override.
                    face = detected_family.face(bold, italic) or default_face(
                        family, bold, italic
                    )
                else:
                    face = default_face(family, bold, italic)
            else:
                face = default_face(family, bold, italic)
        edit_align = _edit_value(edit, "align", "auto")
        align = (
            edit_align
            if edit_align != "auto"
            else str(style.get("align", "left"))
        )
        font = _font_for(
            str(_edit_value(edit, "text", "") or ""), face, family, bold, italic
        )
        try:
            scale = float(_edit_value(edit, "scale", 1.0) or 1.0)
        except (TypeError, ValueError, OverflowError):
            scale = 1.0
        # An explicit size is used by new boxes and by cross-line style copies.
        # It is a base size in working-image pixels, so the scale slider still
        # applies on top of it.
        explicit_size = _edit_value(edit, "font_size")
        try:
            base_size = (
                float(explicit_size)
                if explicit_size is not None
                else float(style.get("font_size") or 24)
            )
        except (TypeError, ValueError, OverflowError):
            base_size = 24.0
        if not math.isfinite(base_size) or base_size <= 0:
            base_size = 24.0
        preferred = int(round(base_size * scale))
        jobs.append(
            (edit, line, style, font, max(6, min(400, preferred)), align, is_new)
        )

    if combined.any():
        repaired = repair_background(bgr, combined, lines)
    else:
        repaired = bgr.copy()

    output = Image.fromarray(cv2.cvtColor(repaired, cv2.COLOR_BGR2RGB))

    draw = ImageDraw.Draw(output)

    # Lay out every replacement first.  Fills need the final glyph extent, and
    # painting all fills before any glyph keeps a later line's text on top.
    prepared = [
        _prepare_render_job(
            output,
            draw,
            edit,
            line,
            style,
            font_spec,
            preferred,
            align,
            render_lines,
            is_new,
        )
        for edit, line, style, font_spec, preferred, align, is_new in jobs
    ]

    # Background fills are painted before any replacement text so a fill can
    # never cover another line's new glyphs.  The target follows the actual
    # replacement glyphs, so growing text keeps its background, while the
    # helper splits the target around every other selection box.
    for item in prepared:
        background = _edit_value(item["edit"], "background")
        if not background:
            continue
        _paint_background_fill(
            output,
            item["line"],
            render_lines,
            str(background),
            target=item["fill_target"],
        )

    applied: list[dict[str, Any]] = []

    for item in prepared:
        if item["empty"]:
            applied.append(item["applied"])
            continue
        edit = item["edit"]
        style = item["style"]
        text = item["text"]
        font = item["font"]
        size = item["font_size"]
        spacing = item["spacing"]
        direction = item["direction"]
        text_width = item["text_width"]
        align = item["align"]
        draw_x = item["draw_x"]
        draw_y = item["draw_y"]

        color_value = _edit_value(edit, "color")
        color = (
            _parse_hex(color_value)
            if color_value
            else _parse_hex(style.get("color") or DEFAULT_DARK)
        )
        kwargs: dict[str, Any] = {
            "font": font,
            "fill": color,
            "spacing": spacing,
            "align": align,
        }
        if direction:
            kwargs["direction"] = direction
        underline = bool(style.get("underline"))
        underline_override = _effect_override(edit, "underline")
        if underline_override is not None:
            underline = underline_override
        strikethrough = bool(style.get("strikethrough"))
        strikethrough_override = _effect_override(edit, "strikethrough")
        if strikethrough_override is not None:
            strikethrough = strikethrough_override

        # Reproduce an original drop shadow or honour an explicit effect
        # override.  Automatic renders inherit the sampled style, so clean
        # text never gains one; the old artefacts were caused by unrepaired
        # glyph pixels, not by this pass.  Drawing the shadow on its own layer
        # lets it blur without softening the replacement text.
        shadow = _resolve_shadow(edit, style, size)
        if isinstance(shadow, dict):
            try:
                shadow_dx = int(round(float(shadow.get("dx", 0))))
                shadow_dy = int(round(float(shadow.get("dy", 0))))
                shadow_blur = int(round(float(shadow.get("blur", 0))))
            except (TypeError, ValueError, OverflowError):
                shadow_dx = shadow_dy = shadow_blur = 0
            shadow_blur = max(0, min(4, shadow_blur))
            try:
                shadow_opacity = int(round(float(shadow.get("opacity", 255))))
            except (TypeError, ValueError, OverflowError):
                shadow_opacity = 255
            shadow_opacity = max(0, min(255, shadow_opacity))
            if shadow_dx or shadow_dy:
                shadow_color = _parse_hex(shadow.get("color") or "#000000")
                layer = Image.new("RGBA", output.size, (0, 0, 0, 0))
                layer_draw = ImageDraw.Draw(layer)
                layer_kwargs = dict(kwargs)
                layer_kwargs["fill"] = (*shadow_color, shadow_opacity)
                layer_draw.multiline_text(
                    (draw_x + shadow_dx, draw_y + shadow_dy),
                    text,
                    **layer_kwargs,
                )
                _draw_text_decorations(
                    layer_draw,
                    (draw_x + shadow_dx, draw_y + shadow_dy),
                    text,
                    font,
                    spacing,
                    direction,
                    text_width,
                    align,
                    (*shadow_color, shadow_opacity),
                    size,
                    underline,
                    strikethrough,
                )
                if shadow_blur:
                    layer = layer.filter(ImageFilter.GaussianBlur(shadow_blur))
                output.paste(layer, (0, 0), layer.getchannel("A"))

        draw.multiline_text((draw_x, draw_y), text, **kwargs)
        _draw_text_decorations(
            draw,
            (draw_x, draw_y),
            text,
            font,
            spacing,
            direction,
            text_width,
            align,
            color,
            size,
            underline,
            strikethrough,
        )

        applied.append(item["applied"])

    tmp_path = output_path.with_suffix(".tmp.png")
    output.save(tmp_path, format="PNG", compress_level=6)
    tmp_path.replace(output_path)
    return applied
