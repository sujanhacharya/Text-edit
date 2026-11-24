"""Tesseract discovery and OCR.

The app deliberately talks to the Tesseract command line instead of binding to
a Python wrapper.  That keeps the dependency surface tiny and lets users point
``TESSERACT_CMD`` at a system install, a Homebrew install, or a portable
AppImage extracted under ``vendor/tesseract``.

OCR is more than a single Tesseract call here.  Phone photos need illumination
flattening, perspective correction, deskewing and a light-text-on-dark pass.
The recognised words are also filtered by confidence and character content so
decorative shapes and noisy symbols do not turn into editable "text".
"""

from __future__ import annotations

import csv
import io
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from .config import (
    MAX_LINES,
    OCR_LINE_RECOVERY,
    OCR_MIN_CONFIDENCE,
    OCR_RECOVERY_BUDGET,
    OCR_RECOVERY_MAX_LINES,
    OCR_TARGET_SIDE,
    PHOTO_CORRECT,
    VENDOR_TESSERACT,
)

log = logging.getLogger(__name__)

LANGUAGE_NAMES = {
    "afr": "Afrikaans",
    "ara": "Arabic",
    "aze": "Azerbaijani",
    "bel": "Belarusian",
    "ben": "Bengali",
    "bul": "Bulgarian",
    "cat": "Catalan",
    "ces": "Czech",
    "chi_sim": "Chinese (Simplified)",
    "chi_tra": "Chinese (Traditional)",
    "chr": "Cherokee",
    "dan": "Danish",
    "deu": "German",
    "ell": "Greek",
    "eng": "English",
    "enm": "Middle English",
    "est": "Estonian",
    "eus": "Basque",
    "fas": "Persian",
    "fin": "Finnish",
    "fra": "French",
    "frk": "Frankish",
    "frm": "Middle French",
    "glg": "Galician",
    "grc": "Ancient Greek",
    "guj": "Gujarati",
    "heb": "Hebrew",
    "hin": "Hindi",
    "hrv": "Croatian",
    "hun": "Hungarian",
    "ind": "Indonesian",
    "isl": "Icelandic",
    "ita": "Italian",
    "jpn": "Japanese",
    "kan": "Kannada",
    "kat": "Georgian",
    "kor": "Korean",
    "lat": "Latin",
    "lav": "Latvian",
    "lit": "Lithuanian",
    "mal": "Malayalam",
    "mar": "Marathi",
    "mkd": "Macedonian",
    "mlt": "Maltese",
    "msa": "Malay",
    "nld": "Dutch",
    "nor": "Norwegian",
    "pan": "Punjabi",
    "pol": "Polish",
    "por": "Portuguese",
    "ron": "Romanian",
    "rus": "Russian",
    "sin": "Sinhala",
    "slk": "Slovak",
    "slv": "Slovenian",
    "spa": "Spanish",
    "srp": "Serbian",
    "swe": "Swedish",
    "syr": "Syriac",
    "tam": "Tamil",
    "tel": "Telugu",
    "tha": "Thai",
    "tur": "Turkish",
    "ukr": "Ukrainian",
    "urd": "Urdu",
    "vie": "Vietnamese",
}

CLOSING_PUNCTUATION = {
    ".", ",", "!", "?", ":", ";", "%", ")", "]", "}", "…", "”", "’", "»", "›",
    "‼", "⁇", "⁈", "⁉", "†", "‡",
}
OPENING_PUNCTUATION = {"(", "[", "{", "‘", "“", "«", "‹", "¡", "¿"}
DASHES = {"-", "–", "—", "―", "−"}
# Common prefix signs.  _is_prefix_symbol() does not rely on this list: it
# accepts every Unicode ``Sc`` currency sign, including ₹ (U+20B9).
CURRENCY_SYMBOLS = {"$", "€", "£", "¥", "₹"}
PREFIX_SYMBOLS = CURRENCY_SYMBOLS | {"#", "@"}
# Symbols that carry meaning in ordinary text.  They must survive even when a
# token contains no letters or digits: Tesseract frequently emits "&", "%",
# currency signs, math operators and legal marks as separate words, and the old
# filter threw all of them away.  The Unicode category checks below extend this
# to every textual symbol rather than an ever-growing hand-maintained list.
TEXT_SYMBOLS = set("&@#$%€£¥₹§¶†‡…*+=<>©®™°№℅")
# Arrows and mathematical marks that occur in ordinary prose.  Other symbols
# in the arrow/technical blocks are toolbar/OS icons (reload, back, search,
# power, keyboard keys) rather than editable words.  Keeping an allow-list is
# what stops Tesseract's "↻" or "⟳" guess from becoming text.
TEXTUAL_ARROWS = set("←↑→↓↔↕↖↗↘↙⇐⇒⇔")
TEXTUAL_MATH = set(
    "±×÷≈≠≤≥∞√∑∏∫∂∆∇∈∉⊂⊃∪∩∧∨¬⋅≡∼≅∝∴∵≪≫⊕⊗⊥∥∠∟⌐∓∔"
    "⟶⟵⟷⟹⟸⟺|\\~¦‖"
)
TEXTUAL_MODIFIERS = set("^`¯´¨˜˚˝")
TEXT_PUNCTUATION = CLOSING_PUNCTUATION | OPENING_PUNCTUATION | DASHES | {'"', "'", "/"}
# Kept for callers/tests that imported the old name.
PUNCTUATION = CLOSING_PUNCTUATION
OPENERS = tuple(OPENING_PUNCTUATION)
# Pictographs, ornamental blocks and icon-only symbol ranges.  These may still
# occur *inside* a real text line (for example "Done ✓"), where they are kept
# and masked with the rest of the line, but a line made only of them is a
# decoration/icon and must not become editable text.
DECORATIVE_SYMBOL_RANGES = (
    (0x2190, 0x21FF),  # Arrows (reload/back/forward toolbar buttons)
    (0x2300, 0x23FF),  # Miscellaneous Technical (clock, keyboard, etc.)
    (0x2460, 0x24FF),  # Enclosed alphanumerics (info/close badges)
    (0x2500, 0x257F),  # Box Drawing
    (0x2580, 0x259F),  # Block Elements
    (0x25A0, 0x25FF),  # Geometric Shapes
    (0x2600, 0x26FF),  # Miscellaneous Symbols
    (0x2700, 0x27BF),  # Dingbats
    (0x27C0, 0x27FF),  # Misc Mathematical Symbols / Supplemental Arrows-A
    (0x2800, 0x28FF),  # Braille Patterns
    (0x2900, 0x297F),  # Supplemental Arrows-B
    (0x2B00, 0x2BFF),  # Miscellaneous Symbols and Arrows
    (0x1D000, 0x1D24F),  # Musical Symbols
    (0x1F000, 0x1FAFF),  # Emoji and pictographs
    (0x1FB00, 0x1FBFF),  # Symbols for Legacy Computing
)
# Punctuation that is meaningful even when it is the only content of a line.
# Ordinary sentence punctuation is kept inside real lines but is not enough to
# turn a rule, border or row of dots into an editable line on its own.  This
# deliberately covers textual marks such as per-mille, daggers, primes and
# reference/interrobang marks as well as the common ampersand/percent/at signs.
SEMANTIC_PUNCTUATION = set("&@#%‰‱§¶†‡…‽⁇⁈⁉※′″‴‵‶‷*")
# List markers and dividers.  A bullet inside a real line ("• Gluten free")
# belongs to the text and must be kept/masked, but a token made only of bullets
# is a marker or decoration, not an editable line.  Most of these are already
# non-semantic punctuation or decorative shapes; the operator-like ones
# (∙, ⦁, ⋅, ⦿, ⧫) are Unicode ``Sm`` and need this explicit exclusion.
LIST_BULLET_SYMBOLS = set("•‣⁃⁌⁍·∙⦁⋅◦▪▫●○■□◆◇⦿⧫")
# Repeated runs of these characters are dividers, not text.  Currency signs
# are deliberately absent so "$$$" can still be text, and a two-character run
# such as "++" stays text because repeated rules require at least three.  The
# ASCII full stop is absent from RULE_REPEAT_CHARS so "..." is not mistaken for
# a repeated divider; it remains in RULE_ONLY_CHARS so mixed dot leaders are
# still rejected, while `_all_rule_characters()` special-cases a pure ellipsis.
# Dot-only lines are rejected because "." is not a semantic character.
RULE_REPEAT_CHARS = set("-–—―_·•=*#~|/\\<>+^`")
RULE_ONLY_CHARS = set("-–—―_.·•=~|/\\<>*")
# Kept for callers/tests that imported the old name.  Unicode categories now
# decide what is editable; old "junk-only" marks such as ©, ®, ™ and ° are
# genuine text and must no longer be discarded.
JUNK_ONLY = set("|~^`\\")
ZERO_WIDTH = {"\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"}

INSTALL_HINT = (
    "Tesseract was not found. Fedora: 'sudo dnf install tesseract tesseract-langpack-eng'. "
    "Debian/Ubuntu: 'sudo apt install tesseract-ocr tesseract-ocr-eng'. "
    "macOS: 'brew install tesseract'. Then reload this page. "
    "You can also point TESSERACT_CMD at an existing binary."
)


class OcrError(RuntimeError):
    """Raised when OCR cannot run or returns unusable data."""


class OcrUnavailable(OcrError):
    """Raised when the Tesseract binary is missing."""


def _is_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _odd(value: int) -> int:
    value = max(1, int(value))
    return value if value % 2 else value + 1


def _clean_word_text(value: str) -> str:
    """Remove OCR control junk without destroying legitimate punctuation."""
    if not value:
        return ""
    out: list[str] = []
    for char in value:
        if char in ZERO_WIDTH:
            continue
        category = unicodedata.category(char)
        if category.startswith("C"):
            continue
        if char == "\ufffd":
            return ""
        if char.isspace():
            continue
        out.append(char)
    return "".join(out).strip()


def _alnum_count(value: str) -> int:
    return sum(1 for char in value if char.isalnum())


def _is_decorative_symbol(char: str) -> bool:
    """True for pictographs and ornamental blocks that act as icons.

    A few arrows occur in real prose ("Next →"); those are exempt.  Everything
    else in the arrow/technical/dingbat/emoji blocks is treated as an icon even
    when Tesseract places it next to ordinary words.
    """
    if char in TEXTUAL_ARROWS or char in TEXTUAL_MATH:
        return False
    code = ord(char)
    return any(start <= code <= end for start, end in DECORATIVE_SYMBOL_RANGES)


def _is_safe_symbol(char: str) -> bool:
    """True for punctuation/symbols that can stand for real text.

    This is deliberately narrower than ``_is_text_character``.  The broad
    predicate is used for characters *inside* a recognised word so that a
    degree sign or copyright mark is not lost.  A symbol-only token, however,
    may be a browser/OS icon, so it must be one of the known textual marks.
    """
    if char.isalnum():
        return True
    if char in TEXT_SYMBOLS or char in TEXTUAL_ARROWS or char in TEXTUAL_MATH:
        return True
    if char in TEXTUAL_MODIFIERS or char in SEMANTIC_PUNCTUATION:
        return True
    category = unicodedata.category(char)
    if category == "Sc":
        # Any Unicode currency sign is textual, not an icon.
        return True
    if category.startswith("P"):
        return True
    return False


def _is_icon_token(text: str) -> bool:
    """True when a Tesseract token is only an icon/ornament.

    The token must contain no letters or digits.  If every character is known
    to be textual punctuation/symbolism it is kept (for masking and for lines
    such as "© 2026"), otherwise the token is treated as a toolbar/OS icon and
    removed *before* it can join a nearby text selection.
    """
    if not text:
        return False
    if any(char.isalnum() for char in text):
        return False
    return any(not _is_safe_symbol(char) for char in text)


def _is_text_character(char: str) -> bool:
    """True for characters that can legitimately occur inside a text line.

    Letters, digits, combining marks, all punctuation and all Unicode symbols
    are accepted.  This is deliberately broader than the old whitelist: a
    degree sign, copyright mark, arrow, mathematical operator or local
    punctuation mark is part of the sentence and must not be dropped (which
    used to leave the character behind as a grey ghost after an edit).  The
    stricter test for a symbol-only line lives in ``_is_semantic_character``.
    """
    if not char or char.isspace() or char in ZERO_WIDTH:
        return False
    category = unicodedata.category(char)
    if category.startswith("C"):
        return False
    return category[0] in {"L", "M", "N", "P", "S"}


def _is_semantic_character(char: str) -> bool:
    """True when a character carries meaning on its own.

    All letters and digits count.  Currency, mathematical and modifier symbols
    count.  ``So`` symbols count unless they belong to an icon/ornamental range.
    A small set of punctuation marks (ampersand, percent, section, dagger,
    asterisk, and friends) also carries enough meaning to stand alone.  Other
    sentence punctuation is retained inside a line but does not by itself turn
    a row of dots or a border rule into editable text.
    """
    if char.isalnum():
        return True
    if char in LIST_BULLET_SYMBOLS:
        # A lone bullet/marker is decoration; inside a real line it is kept by
        # ``_is_text_character`` and only the standalone line is rejected.
        return False
    category = unicodedata.category(char)
    if category == "Sc":
        return True
    if category in {"Sm", "Sk", "So"}:
        # Not every mathematical/technical symbol is text: toolbar icons such
        # as ↻, ⌕, ⌘, ⏻ and ✕ are also symbols.  Only known textual marks are
        # allowed to make a symbol-only line editable.
        return _is_safe_symbol(char)
    if char in SEMANTIC_PUNCTUATION:
        return True
    return False


def _content_count(value: str) -> int:
    """Count characters that carry editable meaning (letters, digits, symbols)."""
    return sum(1 for char in value if _is_semantic_character(char))


def _is_symbol_token(value: str) -> bool:
    """True for a token made only of punctuation/symbols, not letters/digits."""
    return bool(value) and all(
        not char.isalnum() and _is_text_character(char) for char in value
    )


def _is_prefix_symbol(value: str) -> bool:
    """True for a standalone currency/hashtag/at prefix token.

    Currency signs are Unicode category ``Sc``.  Checking the category rather
    than a four-entry whitelist means ``₹``, ``₽``, ``₩`` and other scripts'
    currency marks join their amount just like ``$`` and ``€``.
    """
    if not value:
        return False
    if value in {"#", "@"}:
        return True
    # A currency prefix is normally a single symbol; "US$" is also a prefix,
    # but a number followed by a currency ("5$") is not.
    if any(char.isdigit() for char in value):
        return False
    return unicodedata.category(value[-1]) == "Sc"


_URL_SCHEME_RE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*://|www\.)", re.IGNORECASE)
_DOMAIN_PATH_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}(?:[/:?#]|$)"
)


def _looks_like_url(value: str) -> bool:
    """True for a single token that is a URL or a domain-with-path.

    Tesseract frequently splits a browser URL into a scheme/domain fragment
    and the remaining path.  Keeping those tokens together avoids inserting a
    bogus space into the address.  Ordinary prose containing a slash or a
    decimal point does not match because the token must have no whitespace and
    either a URL scheme or a dotted host followed by a path/query.
    """
    text = (value or "").strip()
    if not text or any(char.isspace() for char in text):
        return False
    if _URL_SCHEME_RE.match(text):
        return True
    return bool(_DOMAIN_PATH_RE.match(text))


def _visual_gap(left: dict[str, Any], right: dict[str, Any]) -> float:
    """Horizontal distance between two word boxes, whatever the reading order.

    ``_join_words`` used to subtract ``left.x + left.w`` from ``right.x``, which
    is only valid for left-to-right text.  In an RTL line the next logical word
    sits to the *left*, so that formula goes negative and every word would be
    glued together.  Measuring the actual gap keeps LTR behaviour identical
    while letting RTL lines use ordinary spaces.
    """
    left_x = float(left.get("processed_x", left.get("x", 0.0)))
    left_w = float(left.get("processed_w", left.get("w", 1.0)))
    right_x = float(right.get("processed_x", right.get("x", 0.0)))
    right_w = float(right.get("processed_w", right.get("w", 1.0)))
    if right_x >= left_x:
        gap = right_x - (left_x + left_w)
    else:
        gap = left_x - (right_x + right_w)
    return max(0.0, gap)


def _text_direction(words: list[dict[str, Any]]) -> str:
    """Return ``"rtl"`` when most strong characters are Arabic/Hebrew etc."""
    rtl = 0
    ltr = 0
    for word in words:
        for char in str(word.get("text", "")):
            bidi = unicodedata.bidirectional(char)
            if bidi in {"R", "AL"}:
                rtl += 1
            elif bidi == "L":
                ltr += 1
    return "rtl" if rtl and rtl >= ltr else "ltr"


def _word_height(word: dict[str, Any]) -> float:
    try:
        return float(word.get("h", word.get("height", 0.0)))
    except (TypeError, ValueError):
        return 0.0


def _robust_word_metrics(words: list[dict[str, Any]]) -> tuple[float, float]:
    """Median text height and character width, ignoring junk outliers.

    Tesseract occasionally reports a logo or illustration as a word box that
    is many times the size of the surrounding text, or reports a two-letter
    token with no confidence.  A plain median over every word let those boxes
    inflate the gap/character thresholds, which then glued real table columns
    together.  Trim heights around the median, then trim character widths
    around their own median.
    """
    heights: list[float] = []
    entries: list[tuple[float, float, int]] = []
    for word in words:
        text = str(word.get("text") or "")
        if not text:
            continue
        try:
            height = _word_height(word)
            width = float(
                word.get(
                    "processed_w",
                    word.get("w", word.get("width", 0.0)),
                )
                or 0.0
            )
        except (TypeError, ValueError):
            continue
        if height <= 0 or width <= 0:
            continue
        heights.append(height)
        entries.append((height, width, max(1, len(text))))
    if not heights:
        return 0.0, 0.0
    ordered_heights = sorted(heights)
    median_height = ordered_heights[
        int(round((len(ordered_heights) - 1) * 0.40))
    ]
    for _ in range(2):
        filtered = [
            height
            for height in heights
            if median_height * 0.55 <= height <= median_height * 1.75
        ]
        if not filtered:
            break
        ordered = sorted(filtered)
        middle = len(ordered) // 2
        median_height = (
            ordered[middle]
            if len(ordered) % 2
            else (ordered[middle - 1] + ordered[middle]) / 2.0
        )
    char_widths = [
        width / characters
        for height, width, characters in entries
        if median_height * 0.55 <= height <= median_height * 1.75
    ]
    if not char_widths:
        return median_height, 0.0
    ordered = sorted(char_widths)
    median_char = ordered[int(round((len(ordered) - 1) * 0.40))]
    for _ in range(2):
        filtered = [
            width
            for width in char_widths
            if median_char * 0.45 <= width <= median_char * 2.2
        ]
        if not filtered:
            break
        ordered = sorted(filtered)
        middle = len(ordered) // 2
        median_char = (
            ordered[middle]
            if len(ordered) % 2
            else (ordered[middle - 1] + ordered[middle]) / 2.0
        )
    return median_height, median_char


def _looks_like_graphic_text_line(
    text: str,
    box: dict[str, Any],
    confidence: float,
    image_size: tuple[int, int],
) -> bool:
    """True for a huge OCR box that is really a logo/illustration patch.

    A two-character token spanning most of the image is not editable text.
    Rejecting it is important not only for the text list but also for removal:
    filling a box that large was what smeared the entire picture during an
    edit.
    """
    compact = re.sub(r"\s+", "", text or "")
    if not compact or len(compact) > 6:
        return False
    try:
        image_width, image_height = int(image_size[0]), int(image_size[1])
        width = max(1.0, float(box.get("width", 1.0)))
        height = max(1.0, float(box.get("height", 1.0)))
    except (TypeError, ValueError, IndexError):
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


def _has_oversized_glyph_cells(line: dict[str, Any]) -> bool:
    """True when a word is too wide to be a horizontal text run.

    Tesseract reports an icon cluster as one ``word`` whose box spans several
    unrelated shapes.  For a horizontal Latin word, even a wide or tracked face
    keeps the mean character cell below the ink height; a much wider cell means
    the box is a picture, not text.  The comparison uses the line's median word
    height, because a short word in a clean line can have a clipped box that
    would otherwise look wide.  Two-character tokens are exempt because short
    caps ("MW") can legitimately be almost square.
    """
    words = []
    for word in line.get("words") or []:
        text = re.sub(r"\s+", "", str(word.get("text") or ""))
        if len(text) < 3:
            continue
        letters = sum(1 for char in text if char.isalpha())
        if letters < len(text) * 0.7:
            continue
        box = word.get("box") or {}
        try:
            width = float(box.get("width", 0.0))
            height = float(box.get("height", 0.0))
        except (TypeError, ValueError):
            continue
        if height <= 0:
            continue
        words.append((text, width, height))
    if not words:
        return False
    median_height = sorted(height for _, _, height in words)[len(words) // 2]
    if median_height <= 0:
        return False
    for text, width, _ in words:
        if (width / len(text)) / median_height > 1.10:
            return True
    return False


def _resolve_ambiguous_pipes(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn a tall ``|`` token between words into a capital ``I``.

    Italic uppercase I is a vertical stroke with no serifs.  Tesseract often
    reports it as a pipe, which used to produce text such as ``How do| feel``.
    A table separator is not taller than the surrounding letters, so requiring
    the token to stand above the line's median glyph height keeps real vertical
    rules intact.
    """
    if not words:
        return words
    heights = [
        _word_height(word)
        for word in words
        if _alnum_count(str(word.get("text", ""))) >= 2
    ]
    heights = [height for height in heights if height > 0]
    if not heights:
        return words
    ordered = sorted(heights)
    middle = len(ordered) // 2
    median_height = (
        ordered[middle]
        if len(ordered) % 2
        else (ordered[middle - 1] + ordered[middle]) / 2.0
    )
    if median_height <= 0:
        return words
    resolved: list[dict[str, Any]] = []
    for index, word in enumerate(words):
        text = str(word.get("text", ""))
        if text == "|" and _word_height(word) >= median_height * 1.18:
            before = str(words[index - 1].get("text", ""))[-1:] if index > 0 else ""
            after = (
                str(words[index + 1].get("text", ""))[:1]
                if index + 1 < len(words)
                else ""
            )
            if before.isalpha() and after.isalpha():
                word = {**word, "text": "I"}
        resolved.append(word)
    return resolved


def _fragment_separator(
    previous_text: str,
    text: str,
    gap: float,
    median_char: float,
) -> str | None:
    """Separator for a boundary between two stitched OCR fragments.

    Word boxes from separate Tesseract lines can overlap, so the ordinary
    tight-box rules would glue ``Attendance`` and ``Hub`` together.  At a
    fragment boundary we therefore default to a space unless the tokens are
    clearly punctuation, a currency prefix, a hyphenated compound, or two
    pieces of one URL.  ``None`` means "let the normal rules decide".
    """
    if not text:
        return None
    if text in {'"', "'"}:
        # Quote handling needs the running open/closed quotation state.
        return None
    if text[0] in CLOSING_PUNCTUATION or unicodedata.category(text[0]) in {
        "Pe",
        "Pf",
    }:
        return None
    if previous_text and (
        previous_text[-1] in OPENING_PUNCTUATION
        or unicodedata.category(previous_text[-1]) in {"Ps", "Pi"}
    ):
        return None
    if _is_prefix_symbol(previous_text):
        return None
    if previous_text.endswith(("-", "/", "\\")):
        return None
    if _looks_like_url(previous_text):
        if _looks_like_url(text):
            if gap > max(2.5, median_char * 0.6):
                return " "
            remainder = previous_text.split("://", 1)[-1]
            # "https://app" followed by "example.com/..." lost the
            # separating dot during OCR.  A single-label host followed by a
            # dotted host/path is the one unambiguous repair.
            if "://" in previous_text and "." not in remainder and "." in text:
                return "."
            return ""
        if text.startswith(("/", "?", "&", "=", "#")):
            return ""
        return " "
    if _looks_like_url(text):
        # A normal word before a URL keeps its space ("Visit https://...").
        return " "
    if text.startswith(("-", "/")) and gap <= median_char * 0.5:
        return None
    return " "


def _all_rule_characters(value: str) -> bool:
    compact = "".join(char for char in value if not char.isspace())
    if len(compact) < 3:
        return False
    if all(char == "." for char in compact):
        # A run of ASCII full stops is an ellipsis token, not a divider.  It
        # must survive after real text (and be masked with it) even though a
        # dot-only *line* is still rejected by the semantic-content test.
        return False
    return all(char in RULE_ONLY_CHARS for char in compact)


def _mask_worthy(text: str) -> bool:
    """True for tokens that may represent real ink worth removing.

    This is the content test used by ``_keep_word`` without the confidence
    floor.  It deliberately accepts low-confidence real words so their pixels
    can be removed, and it accepts punctuation/symbol tokens because they are
    part of the surrounding line, including a split ``...`` ellipsis.  Only
    obvious repeated rules and characters that cannot occur in text are
    rejected; a dot-only line is rejected by the semantic-content test.
    """
    if not text:
        return False
    if not any(_is_text_character(char) for char in text):
        return False
    # Repeated rules ("-----", "_____", "*****", "#####") are decorations,
    # not text.  Longer mixed rule patterns are rejected at line level.
    if len(text) >= 3 and len(set(text)) == 1 and text[0] in RULE_REPEAT_CHARS:
        return False
    if _all_rule_characters(text):
        return False
    return True


def _keep_word(text: str, confidence: float, minimum: float) -> bool:
    """Return True when a Tesseract word token is plausible editable text.

    Low-confidence words are deliberately retained.  Tesseract often gives the
    first word of a handwritten line a high score and the rest a much lower one;
    dropping those words before line assembly is what produced "only the first
    word" results.  Line-level filtering below still requires a confident
    anchor, so isolated noise does not become an editable line.
    """
    if not _mask_worthy(text):
        return False
    # Toolbar/OS pictographs are never editable text, even when Tesseract
    # places them on the same line as real words.
    if _is_icon_token(text):
        return False
    # A very long token with almost no confidence is usually a hallucinated
    # border or texture fragment, not a word.
    if len(text) > 24 and confidence < max(1.0, minimum * 0.15):
        return False
    return True


def _keep_line(text: str, confidence: float, words: list[dict[str, Any]]) -> bool:
    if not words or not text.strip():
        return False
    meaningful = _content_count(text)
    if meaningful == 0:
        # A line made only of closing punctuation is a rule or border fragment.
        return False
    # A browser URL is long, real text even when Tesseract is unsure about its
    # characters.  Dropping it would make the address bar uneditable.
    if len(text) >= 8 and _looks_like_url(text):
        return True
    best = max((float(word.get("conf", 0.0)) for word in words), default=0.0)
    # Keep a full line when at least one word is a credible anchor.  The rest of
    # the words may be much weaker (common in handwriting and low-contrast
    # photos) but they still belong to the line.
    anchor = max(18.0, OCR_MIN_CONFIDENCE * 0.55)
    if best < anchor and confidence < 25.0:
        return False
    if confidence < 8.0 and len(text) <= 4:
        return False
    return True


class TesseractEngine:
    def __init__(self) -> None:
        self._cached: dict[str, Any] | None = None
        self._cached_at = 0.0
        # OCR is CPU-heavy.  Serialising it keeps a double-click (or two open
        # tabs) from starting several Tesseract processes at once.
        self._run_lock = threading.Lock()
        # Health checks may arrive in parallel (the browser requests /health
        # and /languages together).  The lock stops them from spawning
        # duplicate version/language subprocesses during a forced refresh.
        self._status_lock = threading.Lock()

    # ------------------------------------------------------------------ paths

    def _resolve_command(self) -> Path | None:
        raw = os.environ.get("TESSERACT_CMD", "").strip()
        candidates: list[Path] = []

        if raw:
            candidate = Path(raw).expanduser()
            if candidate.is_dir():
                candidates.extend([candidate / "AppRun", candidate / "tesseract"])
            else:
                candidates.append(candidate)

        on_path = shutil.which("tesseract")
        if on_path:
            candidates.append(Path(on_path))

        candidates.extend(
            [
                VENDOR_TESSERACT / "AppRun",
                VENDOR_TESSERACT / "usr" / "bin" / "tesseract",
                Path("/usr/bin/tesseract"),
                Path("/usr/local/bin/tesseract"),
                Path("/opt/homebrew/bin/tesseract"),
                Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
            ]
        )

        for candidate in candidates:
            if _is_executable(candidate):
                return candidate.resolve()
        return None

    def _resolve_tessdata(self, command: Path | None) -> Path | None:
        raw = os.environ.get("TESSDATA_PREFIX", "").strip()
        candidates: list[Path] = []
        if raw:
            candidates.append(Path(raw).expanduser())

        if command is not None:
            for parent in [command.parent, *command.parents]:
                if parent.name == "tesseract" and (parent / "usr").is_dir():
                    candidates.extend(sorted((parent / "usr" / "share" / "tesseract-ocr").glob("*/tessdata")))
                    break

        candidates.append(VENDOR_TESSERACT / "usr" / "share" / "tesseract-ocr" / "5" / "tessdata")
        candidates.extend(sorted(Path("/usr/share/tesseract-ocr").glob("*/tessdata")))
        candidates.extend(
            [
                Path("/usr/share/tesseract-ocr/4.00/tessdata"),
                Path("/usr/share/tesseract-ocr/5/tessdata"),
                Path("/usr/share/tesseract/tessdata"),
                Path("/usr/share/tessdata"),
                Path("/opt/homebrew/share/tessdata"),
                Path("/usr/local/share/tessdata"),
            ]
        )
        if os.name == "nt":
            candidates.append(Path(r"C:\Program Files\Tesseract-OCR\tessdata"))

        with_eng = [path for path in candidates if path.is_dir() and (path / "eng.traineddata").exists()]
        if with_eng:
            return with_eng[0].resolve()
        for path in candidates:
            if path.is_dir():
                return path.resolve()
        return None

    # ----------------------------------------------------------------- status

    def status(self, refresh: bool = False) -> dict[str, Any]:
        requested_at = time.monotonic()
        with self._status_lock:
            now = time.monotonic()
            if self._cached and (
                (not refresh and now - self._cached_at < 30)
                or (refresh and self._cached_at >= requested_at)
            ):
                # Either the cache is fresh, or another concurrent forced
                # refresh finished after this request started.
                return self._cached

            command = self._resolve_command()
            tessdata = self._resolve_tessdata(command)
            status: dict[str, Any] = {
                "available": False,
                "version": None,
                "command": str(command) if command else None,
                "tessdata": str(tessdata) if tessdata else None,
                "languages": [],
                "error": None,
                "hint": INSTALL_HINT,
            }

            if command is None:
                status["error"] = "Tesseract is not installed."
                self._cached, self._cached_at = status, time.monotonic()
                return status

            version = self._version(command, tessdata)
            if not version:
                status["error"] = "The Tesseract binary could not be started."
                self._cached, self._cached_at = status, time.monotonic()
                return status

            languages = self._languages(command, tessdata)
            status.update(
                {
                    "available": bool(languages),
                    "version": version,
                    "languages": languages,
                    "error": None if languages else "No Tesseract language data was found.",
                }
            )
            self._cached, self._cached_at = status, time.monotonic()
            return status

    def _base_args(self, command: Path, tessdata: Path | None) -> list[str]:
        args = [str(command)]
        # Passing the data directory explicitly avoids AppRun/env differences.
        if tessdata is not None:
            args.extend(["--tessdata-dir", str(tessdata)])
        return args

    def _version(self, command: Path, tessdata: Path | None) -> str | None:
        try:
            proc = subprocess.run(
                self._base_args(command, tessdata) + ["--version"],
                capture_output=True,
                text=True,
                timeout=15,
                env=self._env(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("Could not run tesseract: %s", exc)
            return None
        text = (proc.stdout or "") + "\n" + (proc.stderr or "")
        match = re.search(r"tesseract\s+([0-9][^\s]*)", text, re.IGNORECASE)
        if match:
            return match.group(1)
        return "unknown" if proc.returncode == 0 else None

    def _languages(self, command: Path, tessdata: Path | None) -> list[dict[str, str]]:
        codes: set[str] = set()
        if tessdata is not None:
            codes = {p.stem for p in tessdata.glob("*.traineddata") if p.stem != "osd"}
        if not codes:
            try:
                proc = subprocess.run(
                    self._base_args(command, tessdata) + ["--list-langs"],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    env=self._env(),
                )
                for line in ((proc.stdout or "") + "\n" + (proc.stderr or "")).splitlines():
                    line = line.strip()
                    if line and re.fullmatch(r"[a-z0-9_]{2,20}", line):
                        codes.add(line)
            except (OSError, subprocess.TimeoutExpired):
                pass
        codes.discard("osd")

        def sort_key(code: str) -> tuple[int, str]:
            return (0 if code == "eng" else 1, LANGUAGE_NAMES.get(code, code))

        return [
            {"code": code, "name": LANGUAGE_NAMES.get(code, code.replace("_", " ").title())}
            for code in sorted(codes, key=sort_key)
        ]

    @staticmethod
    def _env() -> dict[str, str]:
        env = os.environ.copy()
        # Tesseract + leptonica can otherwise spawn a thread per core and use
        # far more memory than a laptop needs for this workload.
        env.setdefault("OMP_THREAD_LIMIT", "2")
        env.setdefault("OMP_NUM_THREADS", "2")
        return env

    # ------------------------------------------------------------ preprocess

    @staticmethod
    def _find_document_quad(gray: np.ndarray) -> np.ndarray | None:
        """Find a large, skewed document/page quadrilateral in a photo."""
        height, width = gray.shape[:2]
        if min(height, width) < 240:
            return None
        longest = max(height, width)
        scale = min(1.0, 900.0 / longest)
        small = cv2.resize(gray, (max(1, int(width * scale)), max(1, int(height * scale))))
        blur = cv2.GaussianBlur(small, (5, 5), 0)
        edges = cv2.Canny(blur, 40, 120)
        edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
        try:
            contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        except ValueError:
            return None
        if not contours:
            return None
        small_area = float(small.shape[0] * small.shape[1])
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:16]:
            area = float(cv2.contourArea(contour))
            ratio = area / max(1.0, small_area)
            # A photographed page fills most of the frame.  A smaller internal
            # panel (a card, label or poster inset) must not be mistaken for the
            # page, because cropping to it removes all surrounding text.
            if ratio < 0.45 or ratio > 0.96:
                continue
            perimeter = cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, 0.025 * perimeter, True)
            if len(approx) != 4 or not cv2.isContourConvex(approx):
                continue
            points = approx.reshape(4, 2).astype(np.float32) / max(scale, 1e-6)
            ordered = TesseractEngine._order_quad(points)

            box_width = float(ordered[:, 0].max() - ordered[:, 0].min())
            box_height = float(ordered[:, 1].max() - ordered[:, 1].min())
            if box_width < width * 0.62 or box_height < height * 0.62:
                continue

            # Opposite sides of a perspective-distorted page stay broadly
            # similar.  A wildly trapezoidal contour is usually a decorative
            # shape, a shadow, or a panel seen at an extreme angle.
            sides = [
                float(np.linalg.norm(ordered[index] - ordered[(index + 1) % 4]))
                for index in range(4)
            ]
            if min(sides) < 10.0:
                continue
            if max(sides[0], sides[2]) > min(sides[0], sides[2]) * 2.6:
                continue
            if max(sides[1], sides[3]) > min(sides[1], sides[3]) * 2.6:
                continue

            # Require the quad to sit against the image border like a page on a
            # table.  An internal panel is usually surrounded by a margin on
            # every side; cropping to it would cut away real text.
            margin_x = width * 0.10
            margin_y = height * 0.10
            touches = (
                ordered[:, 1].min() <= margin_y,
                ordered[:, 1].max() >= height - margin_y,
                ordered[:, 0].min() <= margin_x,
                ordered[:, 0].max() >= width - margin_x,
            )
            if sum(touches) < 3 and ratio < 0.82:
                continue

            # Ignore nearly axis-aligned rectangles: they are already square
            # to the camera (screenshots, cards) and cropping them can remove
            # useful surrounding text.
            angles: list[float] = []
            for index in range(4):
                a = ordered[index]
                b = ordered[(index + 1) % 4]
                angles.append(math.degrees(math.atan2(float(b[1] - a[1]), float(b[0] - a[0]))) % 90.0)
            if all(min(angle, 90.0 - angle) < 4.0 for angle in angles):
                continue

            # Reject quads that are basically the whole frame.
            frame_x = width * 0.04
            frame_y = height * 0.04
            if (
                ordered[:, 0].min() < frame_x
                and ordered[:, 1].min() < frame_y
                and ordered[:, 0].max() > width - frame_x
                and ordered[:, 1].max() > height - frame_y
            ):
                continue
            return ordered
        return None

    @staticmethod
    def _order_quad(points: np.ndarray) -> np.ndarray:
        points = points.reshape(4, 2).astype(np.float32)
        ordered = np.zeros((4, 2), dtype=np.float32)
        sums = points.sum(axis=1)
        diffs = np.diff(points, axis=1).reshape(-1)
        ordered[0] = points[np.argmin(sums)]
        ordered[2] = points[np.argmax(sums)]
        ordered[1] = points[np.argmin(diffs)]
        ordered[3] = points[np.argmax(diffs)]
        return ordered

    @staticmethod
    def _warp_document(gray: np.ndarray, quad: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        top_left, top_right, bottom_right, bottom_left = quad
        width = int(max(
            np.linalg.norm(top_right - top_left),
            np.linalg.norm(bottom_right - bottom_left),
        ))
        height = int(max(
            np.linalg.norm(bottom_left - top_left),
            np.linalg.norm(bottom_right - top_right),
        ))
        width = max(40, width)
        height = max(40, height)
        margin = max(12, int(round(min(width, height) * 0.025)))
        destination = np.array(
            [
                [margin, margin],
                [margin + width, margin],
                [margin + width, margin + height],
                [margin, margin + height],
            ],
            dtype=np.float32,
        )
        matrix = cv2.getPerspectiveTransform(quad.astype(np.float32), destination)
        warped = cv2.warpPerspective(
            gray,
            matrix,
            (width + margin * 2, height + margin * 2),
            flags=cv2.INTER_CUBIC,
            borderValue=255,
        )
        return warped, matrix.astype(np.float64)

    @staticmethod
    def _estimate_skew(gray: np.ndarray) -> float:
        """Estimate page skew, but only when the evidence agrees.

        The Hough pass below is reliable when a page has long straight text
        rows, but its estimate can be pulled sideways by borders, icons, or
        photo noise.  The projection profile is independent and usually more
        stable for ordinary text, so it is preferred when it finds a clear
        peak; Hough remains the fallback for larger rotations outside the
        projection search window.
        """
        angle = TesseractEngine._projection_skew(gray)
        if angle:
            return angle
        return TesseractEngine._hough_skew(gray)

    @staticmethod
    def _hough_skew(gray: np.ndarray) -> float:
        """Estimate skew from long straight edges, when several agree."""
        height, width = gray.shape[:2]
        if min(height, width) < 120:
            return 0.0
        # Ignore the outer frame: page edges and decorative borders are often
        # perfectly horizontal while the text inside them is not.
        margin_x = max(2, int(width * 0.04))
        margin_y = max(2, int(height * 0.04))
        region = gray[margin_y : height - margin_y, margin_x : width - margin_x]
        if min(region.shape[:2]) < 80:
            region = gray
            margin_y = 0
        longest = max(region.shape[:2])
        edges = cv2.Canny(region, 50, 150)
        threshold = max(35, int(longest * 0.075))
        min_length = max(24, int(longest * 0.15))
        try:
            lines = cv2.HoughLinesP(
                edges,
                1,
                np.pi / 180.0,
                threshold=threshold,
                minLineLength=min_length,
                maxLineGap=max(8, int(longest * 0.012)),
            )
        except cv2.error:
            return 0.0
        if lines is None:
            return 0.0

        samples: list[tuple[float, float, float]] = []
        for line in lines[:, 0]:
            x1, y1, x2, y2 = [float(value) for value in line]
            angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
            length = math.hypot(x2 - x1, y2 - y1)
            # Very steep lines are table rules/side borders, not baselines.
            if abs(angle) <= 10.0 and length >= min_length * 0.8:
                samples.append((angle, length, (y1 + y2) / 2.0 + margin_y))

        if len(samples) < 3:
            return 0.0

        angles = np.asarray([sample[0] for sample in samples], dtype=np.float64)
        median = float(np.median(angles))
        mad = float(np.median(np.abs(angles - median)))
        # A broad spread means the detector mixed text with rules, frames, or
        # perspective-distorted edges.  Doing nothing is safer than a bad deskew.
        if mad > 2.2:
            return 0.0

        inliers = [sample for sample in samples if abs(sample[0] - median) <= 2.5]
        if len(inliers) < max(3, int(math.ceil(len(samples) * 0.6))):
            return 0.0

        vertical_span = max(sample[2] for sample in inliers) - min(sample[2] for sample in inliers)
        if vertical_span < height * 0.18:
            return 0.0

        total_length = sum(sample[1] for sample in samples)
        inlier_length = sum(sample[1] for sample in inliers)
        if total_length <= 0 or inlier_length / total_length < 0.55:
            return 0.0

        angle = float(np.median([sample[0] for sample in inliers]))
        if abs(angle) < 0.45:
            return 0.0
        return max(-8.0, min(8.0, angle))

    @staticmethod
    def _projection_skew(gray: np.ndarray) -> float:
        """Find a small page rotation from the text-row projection profile.

        Straight text produces sharp dark/light alternation along the vertical
        axis.  Rotating the binary page and measuring the variance of each
        row's ink total therefore peaks at the true baseline angle, even when
        no single edge is long enough for the Hough pass.  The search is
        deliberately narrow (a couple of degrees) and requires a clear peak,
        because a guessed rotation is worse than leaving the page untouched.
        """
        height, width = gray.shape[:2]
        if min(height, width) < 120:
            return 0.0
        scale = min(1.0, 900.0 / float(max(height, width)))
        if scale < 1.0:
            work = cv2.resize(
                gray,
                (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
                interpolation=cv2.INTER_AREA,
            )
        else:
            work = gray
        work_height, work_width = work.shape[:2]
        margin_x = max(2, int(work_width * 0.03))
        margin_y = max(2, int(work_height * 0.03))
        region = work[margin_y : work_height - margin_y, margin_x : work_width - margin_x]
        if min(region.shape[:2]) < 80:
            region = work
        try:
            blurred = cv2.GaussianBlur(region, (0, 0), 0.8)
            _, binary = cv2.threshold(
                blurred, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU
            )
        except (AttributeError, TypeError, cv2.error):
            return 0.0
        ink = float(binary.sum()) / 255.0
        if ink < region.size * 0.01:
            return 0.0
        center = (region.shape[1] / 2.0, region.shape[0] / 2.0)
        scores: list[tuple[float, float]] = []
        for step in range(-25, 26):
            angle = step * 0.1
            matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
            rotated = cv2.warpAffine(
                binary,
                matrix,
                (region.shape[1], region.shape[0]),
                flags=cv2.INTER_NEAREST,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=0,
            )
            row_ink = rotated.sum(axis=1, dtype=np.float64)
            total = float(row_ink.sum())
            if total <= 0:
                continue
            # Normalising by total ink keeps the border loss at the ends of the
            # sweep from masquerading as a stronger row alignment.
            scores.append((angle, float(np.var(row_ink)) / (total * total)))
        if len(scores) < 7:
            return 0.0
        smoothed = []
        for index in range(len(scores)):
            start = max(0, index - 1)
            end = min(len(scores), index + 2)
            smoothed.append(
                (scores[index][0], sum(score for _, score in scores[start:end]) / (end - start))
            )
        best_angle, best_score = max(smoothed, key=lambda item: item[1])
        zero_score = min(smoothed, key=lambda item: abs(item[0]))[1]
        if abs(best_angle) < 0.5 or best_angle <= -2.4 or best_angle >= 2.4:
            return 0.0
        if zero_score <= 0.0 or best_score < zero_score * 1.06:
            return 0.0
        return float(best_angle)

    @staticmethod
    def _deskew(gray: np.ndarray, angle: float) -> tuple[np.ndarray, np.ndarray]:
        height, width = gray.shape[:2]
        center = (width / 2.0, height / 2.0)
        matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(
            gray,
            matrix,
            (width, height),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=255,
        )
        matrix3 = np.vstack([matrix, [0.0, 0.0, 1.0]])
        return rotated, matrix3.astype(np.float64)

    @staticmethod
    def _flatten_uneven_illumination(gray: np.ndarray) -> np.ndarray:
        """Aggressive normalisation for photos with uneven lighting."""
        height, width = gray.shape[:2]
        longest = max(height, width)
        sigma = max(6.0, min(80.0, longest * 0.035))
        background = cv2.GaussianBlur(gray, (0, 0), sigmaX=sigma, sigmaY=sigma)
        flattened = cv2.add(
            gray.astype(np.int16),
            128 - background.astype(np.int16),
        )
        flattened = np.clip(flattened, 0, 255).astype(np.uint8)
        clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
        normalised = clahe.apply(flattened)
        sharpened = cv2.addWeighted(
            normalised,
            1.35,
            cv2.GaussianBlur(normalised, (0, 0), 1.0),
            -0.35,
            0,
        )
        return np.clip(sharpened, 0, 255).astype(np.uint8)

    @staticmethod
    def _flatten_illumination(gray: np.ndarray) -> np.ndarray:
        """Remove the paper/lighting gradient without destroying clean text.

        The aggressive subtract/CLAHE/sharpen path is useful for photographed
        signs and pages with a strong lighting gradient, but it amplifies paper
        grain and drawn rule/strike bands on an evenly lit scan.  On a clean
        typewritten page that turned a full page of text into OCR fragments
        (and made the synthetic strike variant drop from 20 rows to 11).  A
        divide by the low-frequency background is the gentler, standard
        correction for a uniform bright page: it keeps glyph shape and local
        contrast, and it is also faster.
        """
        height, width = gray.shape[:2]
        block = max(16, min(height, width) // 12)
        papers: list[float] = []
        for y in range(0, max(1, height - block + 1), block):
            for x in range(0, max(1, width - block + 1), block):
                papers.append(
                    float(np.percentile(gray[y : y + block, x : x + block], 90))
                )
        paper_std = float(np.std(papers)) if papers else 999.0
        paper_median = float(np.median(papers)) if papers else 0.0
        if paper_std <= 12.0 and paper_median >= 200.0:
            longest = max(height, width)
            sigma = max(6.0, min(80.0, longest * 0.035))
            background = cv2.GaussianBlur(
                gray, (0, 0), sigmaX=sigma, sigmaY=sigma
            )
            return cv2.divide(gray, np.maximum(background, 1), scale=255)
        return TesseractEngine._flatten_uneven_illumination(gray)

    def _prepare_image(
        self, image_path: Path, rectify: bool = True
    ) -> tuple[np.ndarray, np.ndarray, tuple[int, int], np.ndarray]:
        with Image.open(image_path) as opened:
            rgb = np.asarray(opened.convert("RGB"), dtype=np.uint8)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        original_height, original_width = gray.shape[:2]
        transform = np.eye(3, dtype=np.float64)

        longest = max(original_width, original_height)
        scale = 1.0
        if longest < OCR_TARGET_SIDE:
            scale = min(2.6, OCR_TARGET_SIDE / max(1, longest))
        elif longest > OCR_TARGET_SIDE * 1.3:
            scale = OCR_TARGET_SIDE / longest
        if abs(scale - 1.0) > 0.01:
            gray = cv2.resize(
                gray,
                (max(1, int(round(original_width * scale))), max(1, int(round(original_height * scale)))),
                interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA,
            )
            scale_matrix = np.array([[scale, 0.0, 0.0], [0.0, scale, 0.0], [0.0, 0.0, 1.0]])
            transform = scale_matrix @ transform

        if PHOTO_CORRECT and rectify:
            quad = self._find_document_quad(gray)
            if quad is not None:
                try:
                    gray, matrix = self._warp_document(gray, quad)
                    transform = matrix @ transform
                except cv2.error:
                    log.debug("Perspective correction failed", exc_info=True)

        angle = self._estimate_skew(gray) if rectify and PHOTO_CORRECT else 0.0
        if angle:
            gray, matrix = self._deskew(gray, angle)
            transform = matrix @ transform

        normal = self._flatten_illumination(gray)
        inverted = cv2.bitwise_not(normal)
        return normal, inverted, (original_width, original_height), transform

    @staticmethod
    def _threshold_variants(normal: np.ndarray) -> list[tuple[str, np.ndarray]]:
        height, width = normal.shape[:2]
        block = _odd(max(15, min(75, min(height, width) // 12)))
        # adaptiveThreshold needs an odd block no larger than the image.
        limit = min(height, width)
        if limit % 2 == 0:
            limit -= 1
        block = min(block, max(3, limit))
        if block < 3:
            return []
        try:
            binary = cv2.adaptiveThreshold(
                normal,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                block,
                9,
            )
            binary = cv2.medianBlur(binary, 3)
            # A light closing pass reconnects faint or broken strokes in
            # pencil/pen photos without thickening clean print much.
            closed = cv2.morphologyEx(
                binary, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8), iterations=1
            )
        except cv2.error:
            return []
        return [
            ("threshold", binary),
            ("threshold-inverted", cv2.bitwise_not(binary)),
            ("threshold-closed", closed),
        ]

    # -------------------------------------------------------------------- OCR

    def recognize(
        self,
        image_path: Path,
        lang: str = "eng",
        psm: int = 3,
        rectify: bool = True,
    ) -> list[dict[str, Any]]:
        with self._run_lock:
            return self._recognize(image_path, lang, psm, rectify=rectify)

    def _recognize(
        self,
        image_path: Path,
        lang: str = "eng",
        psm: int = 3,
        rectify: bool = True,
    ) -> list[dict[str, Any]]:
        image_path = Path(image_path)
        status = self.status()
        if not status["available"]:
            raise OcrUnavailable(status.get("error") or INSTALL_HINT)

        available = {item["code"] for item in status["languages"]}
        requested = [code for code in lang.split("+") if code]
        missing = [code for code in requested if code not in available]
        if missing:
            raise OcrError(f"Language data is not installed for: {', '.join(missing)}.")

        command = self._resolve_command()
        tessdata = self._resolve_tessdata(command)
        if command is None:
            raise OcrUnavailable(INSTALL_HINT)

        normal, inverted, original_size, transform = self._prepare_image(
            image_path, rectify=rectify
        )
        # Dark images usually contain light text; trying the inverted pass
        # first saves one Tesseract invocation in that common case.
        variants: list[tuple[str, np.ndarray]] = [("normal", normal), ("inverted", inverted)]
        if float(np.mean(normal)) < 112:
            variants.reverse()

        passes: list[dict[str, Any]] = []
        last_error: str | None = None

        def run_pass(name: str, variant: np.ndarray, mode: int) -> dict[str, Any] | None:
            nonlocal last_error
            try:
                payload = self._run_tesseract(variant, command, tessdata, lang, mode, image_path)
            except OcrError as exc:
                last_error = str(exc)
                return None
            lines = self._split_multibaseline_lines(
                self._parse_tsv(payload, transform, original_size)
            )
            record = {
                "name": name,
                "lines": lines,
                "score": self._result_score(lines),
                "variant": variant,
                "psm": mode,
            }
            passes.append(record)
            return record

        for name, variant in variants:
            run_pass(name, variant, psm)

        provisional = max(passes, key=lambda item: item["score"]) if passes else None
        provisional_lines = provisional["lines"] if provisional else []
        provisional_score = provisional["score"] if provisional else 0.0
        provisional_mean = self._mean_confidence(provisional_lines) if provisional_lines else 0.0

        # Adaptive thresholding is slower, so only run it when the cheap
        # polarity passes did not already cover the page well.
        if provisional_score < 320.0 or provisional_mean < 72.0 or len(provisional_lines) < 2:
            for name, variant in self._threshold_variants(normal):
                record = run_pass(name, variant, psm)
                if record and record["score"] > provisional_score:
                    provisional, provisional_score = record, record["score"]
                    provisional_mean = self._mean_confidence(record["lines"])

        # Tesseract's page segmentation is not stable across PSM modes: one
        # mode can miss a line that another sees (tesseract issue #1339).
        # Running the uniform-block and sparse modes as *additional* passes and
        # merging their lines is what prevents whole lines from disappearing.
        if psm == 3:
            fallback_variant = provisional["variant"] if provisional is not None else normal
            for fallback_psm in (6, 11):
                run_pass(f"psm-{fallback_psm}", fallback_variant, fallback_psm)

        if not passes:
            best_lines: list[dict[str, Any]] = []
            best_score = 0.0
            best_name = ""
            best_variant = normal
        else:
            ranked = sorted(passes, key=lambda item: item["score"], reverse=True)
            best_record = ranked[0]
            best_lines = [self._clone_line(line) for line in best_record["lines"]]
            best_score = best_record["score"]
            best_name = best_record["name"]
            best_variant = best_record["variant"]
            for record in ranked[1:]:
                if record["lines"]:
                    best_lines = self._merge_line_sets(best_lines, record["lines"])
            merged_score = self._result_score(best_lines)
            if merged_score > best_score:
                best_score = merged_score

        # Line-by-line recovery for lines the page passes missed or truncated.
        # This is the workaround recommended by the Tesseract quality docs for
        # page segmentation that keeps dropping rows.  Keep both polarities and
        # the winning threshold variant available; a light-on-dark banner and
        # the surrounding light page need different treatments.
        recovery_variants: list[tuple[str, np.ndarray]] = []
        for name, variant in (
            ("primary", best_variant),
            ("dark-ink", normal),
            ("light-ink", inverted),
        ):
            if all(variant is not existing for _, existing in recovery_variants):
                recovery_variants.append((name, variant))
        recovered_before = len(best_lines)
        best_lines = self._recover_lines(
            normal,
            recovery_variants,
            best_lines,
            command,
            tessdata,
            lang,
            image_path,
            transform,
            original_size,
        )
        if len(best_lines) != recovered_before:
            best_score = self._result_score(best_lines)

        if not best_lines:
            if last_error:
                raise OcrError(f"Tesseract failed: {last_error}")
            raise OcrError(
                "No text was found. Try a sharper image, a different language, "
                "or a layout mode such as 'sparse text'."
            )

        # Rebuild visual lines before style sampling.  Sparse Tesseract output
        # often returns one paragraph line as several high-confidence fragments,
        # which breaks reading order and makes each fragment look differently
        # styled.  This pass is deliberately after line recovery so recovered
        # fragments join the same row.
        best_lines = self._merge_adjacent_fragments(best_lines)
        # A merged selection whose words sit on two different baselines would
        # paint one row's replacement across the next row.  This happens on
        # perspective-warped or clipped photos where Tesseract's own rows are
        # unstable.  Losing that selection is safer than corrupting two rows.
        best_lines = [line for line in best_lines if not self._line_spans_rows(line)]
        best_lines = self._drop_isolated_glyph_lines(best_lines, original_size)
        best_lines = self._drop_graphic_lines(best_lines, original_size)
        best_lines = [
            line for line in self._dedupe_lines(best_lines) if self._plausible_line(line)
        ]
        # Two selections that substantially overlap cannot both be right: the
        # first edit would erase the other's source pixels.  Keep the stronger
        # reading and drop the competing fragment.
        best_lines = self._resolve_overlapping_lines(best_lines)
        best_lines.sort(key=lambda item: (item["box"]["y"], item["box"]["x"]))
        best_lines = best_lines[:MAX_LINES]
        for index, line in enumerate(best_lines, start=1):
            line["id"] = f"L{index:03d}"
            line["word_count"] = len(line.get("words") or [])

        if best_name:
            log.info(
                "OCR used the %s pass and merged fallbacks (%s lines, score %.0f)",
                best_name,
                len(best_lines),
                best_score,
            )
        return best_lines

    def _run_tesseract(
        self,
        variant: np.ndarray,
        command: Path,
        tessdata: Path | None,
        lang: str,
        psm: int,
        image_path: Path,
        timeout: float = 120.0,
    ) -> str:
        tmp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix="ocr-", suffix=".png", dir=image_path.parent, delete=False
            ) as tmp:
                tmp_name = tmp.name
            Image.fromarray(variant).save(tmp_name, format="PNG")

            def run(extra: list[str]) -> subprocess.CompletedProcess[str]:
                try:
                    return subprocess.run(
                        self._base_args(command, tessdata)
                        + [
                            tmp_name,
                            "stdout",
                            "-l",
                            lang,
                            "--psm",
                            str(psm),
                            *extra,
                            "tsv",
                        ],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=max(1.0, float(timeout)),
                        env=self._env(),
                    )
                except subprocess.TimeoutExpired as exc:
                    raise OcrError(
                        "Tesseract timed out. Try a smaller image or a simpler layout mode."
                    ) from exc
                except OSError as exc:
                    raise OcrError(f"Could not start Tesseract: {exc}") from exc

            extras = ["--oem", "1", "-c", "user_defined_dpi=300", "-c", "preserve_interword_spaces=1"]
            proc = run(extras)
            if proc.returncode != 0:
                detail = self._clean_stderr(proc.stderr)
                if any(word in detail.lower() for word in ("oem", "parameter", "dpi")):
                    # Older Tesseract builds may not know a parameter.  Retry
                    # with the portable minimum rather than failing OCR.
                    proc = run([])
                if proc.returncode != 0:
                    detail = self._clean_stderr(proc.stderr)
                    raise OcrError(detail or "unknown error")
            if not proc.stdout.strip():
                raise OcrError("Tesseract returned no data. Is the image readable?")
            return proc.stdout
        finally:
            if tmp_name:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass

    @staticmethod
    def _mean_confidence(lines: list[dict[str, Any]]) -> float:
        weighted = 0.0
        weight = 0.0
        for line in lines:
            content = max(1, _content_count(str(line.get("text", ""))))
            weighted += float(line.get("confidence", 0.0)) * content
            weight += content
        return weighted / weight if weight > 0 else 0.0

    @staticmethod
    def _result_score(lines: list[dict[str, Any]]) -> float:
        """Score a page pass by total useful coverage, not mean confidence.

        The old ``mean * sqrt(length)`` score let a single very confident word
        beat a complete line whose weaker words were still correct.  That is
        exactly how "only the first word" survived: the fragment looked better
        per word even though it lost most of the page.  Coverage-aware scoring
        rewards every plausible line and word instead.
        """
        if not lines:
            return 0.0
        score = 0.0
        for line in lines:
            if not TesseractEngine._plausible_line(line):
                # Toolbar noise and progress-bar hallucinations must not make
                # a sparse pass look better than a clean page pass.
                continue
            text = str(line.get("text", ""))
            content = max(1, _content_count(text))
            confidence = max(0.0, min(100.0, float(line.get("confidence", 0.0)))) / 100.0
            words = len(line.get("words") or [])
            score += content * (0.25 + confidence)
            score += math.log1p(content)
            score += 0.4 * min(words, 12)
        return score

    @staticmethod
    def _line_quality(line: dict[str, Any]) -> float:
        """Relative quality of one line, used when merging OCR passes."""
        text = str(line.get("text", ""))
        content = max(1, _content_count(text))
        confidence = max(0.0, min(100.0, float(line.get("confidence", 0.0)))) / 100.0
        words = len(line.get("words") or [])
        return content * (0.35 + confidence) + 0.25 * min(words, 10)

    def _parse_tsv(
        self,
        payload: str,
        transform: np.ndarray,
        original_size: tuple[int, int],
        offset: tuple[int, int] = (0, 0),
    ) -> list[dict[str, Any]]:
        """Parse Tesseract TSV into editable lines.

        Every level-5 word with non-negative confidence is kept during line
        assembly.  Tesseract often gives the first word of a line a high score
        and the remaining words much lower scores; dropping the weak words here
        was what produced partial lines.  Junk-only tokens are still rejected,
        and ``mask_boxes`` retains every plausible word box (including tokens
        that are later discarded) so the old text can be erased cleanly.

        ``offset`` is added to TSV coordinates before the inverse transform.
        It is used by the per-line recovery pass, where Tesseract sees a padded
        crop instead of the whole page.
        """
        # Tesseract TSV is tab-separated data, not RFC-4180 CSV.  In
        # particular, a word may legitimately start with a double quote
        # (``"Just`` in a quotation).  The default csv reader treats that as
        # the start of a quoted field and swallows every following row into
        # one text value, which produced output such as
        # ``Just5116112546429311896.946266the...``.  Disable quote processing
        # and normalise line endings so each TSV row is parsed independently.
        payload = payload.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
        reader = csv.reader(
            io.StringIO(payload), delimiter="\t", quoting=csv.QUOTE_NONE
        )
        required = {
            "level",
            "block_num",
            "par_num",
            "line_num",
            "word_num",
            "left",
            "top",
            "width",
            "height",
            "conf",
            "text",
        }
        # Locate the header instead of assuming the very first row.  Wrapper
        # scripts and some Tesseract builds can emit a preamble line before
        # the TSV column names.  Stripping a UTF-8 BOM also covers Windows
        # pipes without corrupting the first column name.
        index: dict[str, int] = {}
        found_header = False
        for header in reader:
            index = {name.lstrip("\ufeff"): i for i, name in enumerate(header)}
            if required.issubset(index):
                found_header = True
                break
        if not found_header:
            if not payload.strip():
                return []
            raise OcrError("Unexpected Tesseract TSV output.")

        original_width, original_height = original_size
        inverse = np.linalg.inv(transform) if transform is not None else np.eye(3)
        offset_x, offset_y = int(offset[0]), int(offset[1])
        grouped: dict[tuple[int, int, int], list[dict[str, Any]]] = {}

        for row in reader:
            if len(row) <= max(index[name] for name in required):
                continue
            if row[index["level"]] != "5":
                continue
            text = _clean_word_text(row[index["text"]])
            if not text:
                continue
            try:
                left = int(row[index["left"]]) + offset_x
                top = int(row[index["top"]]) + offset_y
                w = int(row[index["width"]])
                h = int(row[index["height"]])
                conf = float(row[index["conf"]])
            except (TypeError, ValueError):
                continue
            if not math.isfinite(conf) or conf < 0 or w < 2 or h < 2:
                continue

            points = np.array(
                [
                    [[left, top]],
                    [[left + w, top]],
                    [[left + w, top + h]],
                    [[left, top + h]],
                ],
                dtype=np.float32,
            )
            mapped = cv2.perspectiveTransform(points, inverse).reshape(-1, 2)
            x0 = int(math.floor(float(mapped[:, 0].min())))
            y0 = int(math.floor(float(mapped[:, 1].min())))
            x1 = int(math.ceil(float(mapped[:, 0].max())))
            y1 = int(math.ceil(float(mapped[:, 1].max())))
            x0 = max(0, min(original_width - 1, x0))
            y0 = max(0, min(original_height - 1, y0))
            x1 = max(x0 + 1, min(original_width, x1))
            y1 = max(y0 + 1, min(original_height, y1))
            if x1 - x0 < 2 or y1 - y0 < 2:
                continue
            key = (
                int(row[index["block_num"]]),
                int(row[index["par_num"]]),
                int(row[index["line_num"]]),
            )
            grouped.setdefault(key, []).append(
                {
                    "text": text,
                    "x": x0,
                    "y": y0,
                    "w": x1 - x0,
                    "h": y1 - y0,
                    "conf": conf,
                    "kept": _keep_word(text, conf, OCR_MIN_CONFIDENCE),
                    "processed_x": left,
                    "processed_w": w,
                }
            )

        lines: list[dict[str, Any]] = []
        for words in grouped.values():
            # Tesseract emits words in reading order.  Ascending-x sorting is
            # correct for LTR but reverses Arabic/Hebrew, whose logical order
            # moves right-to-left.  Keep TSV order for RTL lines; this also
            # preserves embedded Latin runs instead of mirroring them.
            if _text_direction(words) == "ltr":
                words.sort(key=lambda item: (item["x"], item["y"]))
            # Tesseract occasionally repeats a word box across line groups.
            unique: list[dict[str, Any]] = []
            seen: set[tuple[str, int, int]] = set()
            for word in words:
                signature = (word["text"], word["x"] // 2, word["y"] // 2)
                if signature in seen:
                    continue
                seen.add(signature)
                unique.append(word)
            # Icon/ornament tokens are kept only as split points: they must
            # never join the words on either side.  Every text segment between
            # them is handled independently, and the icon token is omitted
            # from both the editable words and the removal mask.
            split_input = [
                word for word in unique if word["kept"] or _is_icon_token(word["text"])
            ]
            for word in split_input:
                word["icon"] = _is_icon_token(word["text"])
            segments = self._split_segments(split_input)
            if not any(not word.get("icon") for word in split_input):
                continue

            for segment in segments:
                if any(word.get("icon") for word in segment):
                    continue
                before_trim = segment
                segment = self._trim_edge_noise(segment)
                kept_ids = {id(word) for word in segment}
                removed_ids = {
                    id(word) for word in before_trim if id(word) not in kept_ids
                }
                if not segment:
                    continue
                x0 = min(word["x"] for word in segment)
                y0 = min(word["y"] for word in segment)
                x1 = max(word["x"] + word["w"] for word in segment)
                y1 = max(word["y"] + word["h"] for word in segment)
                if x1 - x0 < 3 or y1 - y0 < 4:
                    continue
                text = self._join_words(segment)
                if not text.strip():
                    continue
                weight = sum(max(1, _content_count(word["text"])) for word in segment)
                confidence = sum(word["conf"] * max(1, _content_count(word["text"])) for word in segment) / max(1, weight)
                if not _keep_line(text, confidence, segment):
                    continue

                # Keep every plausible word box for the removal mask, even
                # tokens whose text was filtered out.  This is what prevents
                # weak/undetected words from surviving as grey "ghost" text.
                # Icon tokens are deliberately excluded: editing nearby text
                # must not erase a browser/OS icon.
                mask_boxes: list[dict[str, int]] = []
                for word in unique:
                    if id(word) in removed_ids:
                        continue
                    if word.get("icon") or _is_icon_token(word["text"]):
                        continue
                    if not _mask_worthy(word["text"]):
                        continue
                    word_x1 = word["x"] + word["w"]
                    if word_x1 < x0 - 2 or word["x"] > x1 + 2:
                        continue
                    mask_boxes.append(
                        {
                            "x": word["x"],
                            "y": word["y"],
                            "width": word["w"],
                            "height": word["h"],
                        }
                    )
                if not mask_boxes:
                    mask_boxes = [
                        {
                            "x": word["x"],
                            "y": word["y"],
                            "width": word["w"],
                            "height": word["h"],
                        }
                        for word in segment
                    ]

                line_box = {
                    "x": x0,
                    "y": y0,
                    "width": x1 - x0,
                    "height": y1 - y0,
                }
                if _looks_like_graphic_text_line(
                    text,
                    line_box,
                    confidence,
                    (original_width, original_height),
                ):
                    # A huge two-character box is a logo/illustration, not
                    # editable text.  It must also not reach the removal mask.
                    continue
                lines.append(
                    {
                        "text": text,
                        "box": line_box,
                        "confidence": round(confidence, 1),
                        "words": [
                            {
                                "text": word["text"],
                                "box": {
                                    "x": word["x"],
                                    "y": word["y"],
                                    "width": word["w"],
                                    "height": word["h"],
                                },
                                "confidence": round(word["conf"], 1),
                            }
                            for word in segment
                        ],
                        "mask_boxes": mask_boxes,
                        "word_count": len(segment),
                    }
                )

        # Tesseract usually emits rows in reading order, but column splitting
        # and multi-block pages can interleave.  Sorting by vertical position
        # then horizontal position gives the editor a predictable list.
        lines.sort(key=lambda item: (item["box"]["y"], item["box"]["x"]))
        for idx, line in enumerate(lines, start=1):
            line["id"] = f"L{idx:03d}"
        return lines

    @staticmethod
    def _split_segments(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        """Split a Tesseract line into independently editable segments.

        Two things start a new segment:

        * a browser/OS icon token, which must never join nearby words;
        * a horizontal gap that is clearly wider than this line's normal word
          spacing (or, for a two-word line, wider than about 1.7x the text
          height).  Widely tracked headings keep their even spacing and stay
          together because the gap/median ratio is not exceptional.

        Table columns are therefore split, while ordinary prose with a slightly
        generous space remains one selection.
        """
        if not words:
            return []
        if len(words) == 1:
            return [words]

        icon_flags = [
            bool(word.get("icon")) or _is_icon_token(str(word.get("text", "")))
            for word in words
        ]
        boundaries: set[int] = set()
        for index, is_icon in enumerate(icon_flags):
            if not is_icon:
                continue
            if index > 0:
                boundaries.add(index - 1)
            if index + 1 < len(words):
                boundaries.add(index)

        gaps = [_visual_gap(left, right) for left, right in zip(words, words[1:])]
        median_height, median_char = _robust_word_metrics(words)
        if median_height <= 0:
            median_height = float(np.median([word["h"] for word in words]))
        if median_char <= 0:
            median_char = float(
                np.median(
                    [
                        max(
                            1.0,
                            float(word["w"])
                            / max(1, len(str(word.get("text", "")))),
                        )
                        for word in words
                    ]
                )
            )
        positive = [gap for gap in gaps if gap > 1]
        if len(gaps) == 1:
            median_gap = 0.0
            threshold = max(median_height * 2.0, median_char * 3.5, 14.0)
            if gaps[0] > threshold:
                boundaries.add(0)
        elif positive:
            small_gaps = [gap for gap in positive if gap <= median_height * 1.6]
            median_gap = float(np.median(small_gaps or positive))
            threshold = max(
                median_height * 2.0, median_char * 3.5, median_gap * 4.5, 14.0
            )
            for number, gap in enumerate(gaps):
                if gap > threshold and gap > median_gap * 3.4:
                    boundaries.add(number)

        if not boundaries:
            return [words]

        segments: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = [words[0]]
        for number, word in enumerate(words[1:]):
            if number in boundaries:
                segments.append(current)
                current = [word]
            else:
                current.append(word)
        segments.append(current)
        return segments

    @staticmethod
    def _join_words(words: list[dict[str, Any]]) -> str:
        if not words:
            return ""
        words = _resolve_ambiguous_pipes(words)
        _, median_char = _robust_word_metrics(words)
        if median_char <= 0:
            widths = [
                max(
                    1.0,
                    float(word.get("processed_w", word.get("w", 1.0)))
                    / max(1, len(word.get("text", ""))),
                )
                for word in words
            ]
            median_char = float(np.median(widths)) if widths else 1.0
        result = ""
        previous: dict[str, Any] | None = None
        previous_text = ""
        quote_open = False
        for word in words:
            text = word["text"]
            if not result:
                result = text
                quote_open = text in {'"', "'"}
            else:
                gap = 0.0
                if previous is not None:
                    gap = _visual_gap(previous, word)
                if text in {'"', "'"} and previous is not None:
                    # ASCII quotes are ambiguous.  A quote separated by a word
                    # space opens a quotation; a tight quote closes the word
                    # before it (or is an apostrophe).  Tracking the open state
                    # avoids the old "' Hello'" text corruption.
                    if quote_open:
                        result += text
                        quote_open = False
                    elif gap > median_char * 0.45:
                        result += " " + text
                        quote_open = True
                    else:
                        result += text
                    previous = word
                    previous_text = text
                    continue
                if word.get("fragment_start") and not quote_open:
                    # This word begins a separate Tesseract fragment that was
                    # stitched into the same visual line.  Its box may overlap
                    # the previous fragment, so the usual tight-box rules can
                    # glue unrelated words together ("AttendanceHub").  At a
                    # fragment boundary ordinary spacing wins unless the
                    # helper recognises punctuation or a split URL.
                    separator = _fragment_separator(
                        previous_text, text, gap, median_char
                    )
                    if separator is not None:
                        result += separator + text
                        previous = word
                        previous_text = text
                        continue
                no_space = False
                if quote_open:
                    # The word just after an opening quote belongs to it.
                    no_space = True
                    quote_open = False
                elif (
                    len(previous_text) == 1
                    and previous_text.casefold() in {"a", "i"}
                    and len(text) > 1
                    and text[0].isalpha()
                ):
                    # Tesseract can give the article "a" or the pronoun "I" a
                    # box that overlaps the next word.  The tight-box rule below
                    # then glued them together ("ajournalist").  Single-letter
                    # English words are always separate words.
                    no_space = False
                elif (
                    text in {"a", "A", "i", "I"}
                    and len(previous_text) > 1
                    and previous_text[-1].isalpha()
                ):
                    # The symmetric case: "... do| feel" becomes "... do I
                    # feel" once the tall pipe is resolved.
                    no_space = False
                elif text in CLOSING_PUNCTUATION:
                    no_space = True
                elif text == "-":
                    # A standalone hyphen joins a compound only when it is tight
                    # against its neighbours.  A spaced dash ("7am - 6pm") and
                    # a bullet at the start of a line keep their space.
                    no_space = gap <= median_char * 0.45
                elif text.startswith("-") and text not in DASHES and not re.match(r"^-\d", text):
                    # Hyphenated compounds are commonly emitted as three
                    # tokens ("well", "-known").  A negative number such as
                    # "-5" is not a compound and keeps the preceding space.
                    no_space = True
                elif text == "/" or text.startswith("/"):
                    # "and/or" and URL paths are tight; a spaced separator
                    # ("7am - 6pm / 42 Market Street") is not a compound and
                    # must keep its word spaces.
                    no_space = gap <= median_char * 0.45
                elif (
                    text[0] in CLOSING_PUNCTUATION
                    or unicodedata.category(text[0]) in {"Pe", "Pf"}
                ):
                    # A split comma/period after a word is attached even when
                    # Tesseract leaves a small gap.  A token that starts a real
                    # word (".NET", "’tis") keeps the preceding word space, and
                    # a currency prefix before a decimal amount ("$.99") always
                    # joins it.
                    if _is_prefix_symbol(previous_text) and any(
                        char.isdigit() for char in text
                    ):
                        no_space = True
                    elif (
                        len(text) > 1
                        and text[1].isalnum()
                        and gap > median_char * 0.45
                    ):
                        no_space = False
                    else:
                        no_space = True
                elif result and (
                    result[-1] in OPENING_PUNCTUATION
                    or unicodedata.category(result[-1]) in {"Ps", "Pi"}
                ):
                    no_space = True
                elif _is_prefix_symbol(previous_text):
                    # "$ 5", "₹ 500", "# tag" and "@ user" are one text run.
                    no_space = text[:1].isalnum() or text.startswith(("(", "["))
                elif previous_text == "-":
                    # Match the same rule used when the hyphen is appended: a
                    # tight compound joins, a spaced dash/negative number keeps
                    # its space.
                    no_space = gap <= median_char * 0.45
                elif previous_text.endswith("/"):
                    # Same rule as a slash token; a spaced "/" separator keeps
                    # the following word separate.
                    no_space = gap <= median_char * 0.45
                elif gap <= median_char * 0.35 and (
                    _is_symbol_token(text)
                    or not text[0].isalnum()
                    or _is_symbol_token(previous_text)
                    or not previous_text[-1].isalnum()
                ):
                    # Tightly set symbols such as "R&D", "24°C", "1+2" and
                    # "Acme©" stay attached, and a closing mark that Tesseract
                    # split from its word rejoins it.  A spaced "Salt & Pepper"
                    # keeps its spaces.
                    no_space = True
                elif gap <= median_char * 0.28:
                    no_space = True
                result += text if no_space else " " + text
            previous = word
            previous_text = text
        return result.strip()

    # ------------------------------------------------------- line reconciliation

    @staticmethod
    def _clone_line(line: dict[str, Any]) -> dict[str, Any]:
        clone = {
            key: value
            for key, value in line.items()
            if key not in {"box", "words", "mask_boxes"}
        }
        clone["box"] = dict(line.get("box") or {})
        clone["words"] = [
            {**word, "box": dict(word.get("box") or {})}
            for word in (line.get("words") or [])
        ]
        clone["mask_boxes"] = [dict(box) for box in (line.get("mask_boxes") or [])]
        return clone

    @staticmethod
    def _box_values(box: dict[str, Any]) -> tuple[float, float, float, float]:
        x0 = float(box.get("x", 0.0))
        y0 = float(box.get("y", 0.0))
        return x0, y0, x0 + max(1.0, float(box.get("width", 1.0))), y0 + max(1.0, float(box.get("height", 1.0)))

    @classmethod
    def _box_intersection(
        cls, first: dict[str, Any], second: dict[str, Any]
    ) -> tuple[float, float, float, float]:
        ax0, ay0, ax1, ay1 = cls._box_values(first)
        bx0, by0, bx1, by1 = cls._box_values(second)
        ix0 = max(ax0, bx0)
        iy0 = max(ay0, by0)
        ix1 = min(ax1, bx1)
        iy1 = min(ay1, by1)
        return max(0.0, ix1 - ix0), max(0.0, iy1 - iy0), ix0, iy0

    @classmethod
    def _line_match(cls, first: dict[str, Any], second: dict[str, Any]) -> bool:
        """True when two boxes describe the same visual text line/segment."""
        iw, ih, _, _ = cls._box_intersection(first.get("box") or {}, second.get("box") or {})
        if iw <= 0 or ih <= 0:
            return False
        first_box = first.get("box") or {}
        second_box = second.get("box") or {}
        min_height = max(1.0, min(float(first_box.get("height", 1)), float(second_box.get("height", 1))))
        min_width = max(1.0, min(float(first_box.get("width", 1)), float(second_box.get("width", 1))))
        return ih / min_height >= 0.45 and iw / min_width >= 0.18

    # ------------------------------------------------ fragment reconstruction

    @staticmethod
    def _median_value(values: list[float]) -> float:
        ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
        if not ordered:
            return 0.0
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) / 2.0

    @classmethod
    def _horizontal_box_gap(
        cls, first: dict[str, Any], second: dict[str, Any]
    ) -> float:
        ax0, _, ax1, _ = cls._box_values(first)
        bx0, _, bx1, _ = cls._box_values(second)
        if bx0 >= ax1:
            return bx0 - ax1
        if ax0 >= bx1:
            return ax0 - bx1
        return 0.0

    @classmethod
    def _box_overlaps(
        cls, first: dict[str, Any], second: dict[str, Any], margin: float = 0.0
    ) -> bool:
        ax0, ay0, ax1, ay1 = cls._box_values(first)
        bx0, by0, bx1, by1 = cls._box_values(second)
        return not (
            ax1 < bx0 - margin
            or bx1 < ax0 - margin
            or ay1 < by0 - margin
            or by1 < ay0 - margin
        )

    @staticmethod
    def _is_chrome_junk_word(word: dict[str, Any]) -> bool:
        """True for a short toolbar/icon fragment beside a real URL."""
        text = str(word.get("text") or "")
        compact = re.sub(r"\s+", "", text)
        if not compact:
            return True
        if not any(char.isalpha() for char in compact):
            return True
        opening = {"[": "]", "(": ")", "{": "}", "<": ">"}
        closer = opening.get(compact[0])
        if closer and closer not in compact[1:]:
            return True
        try:
            confidence = float(word.get("confidence", word.get("conf", 0.0)))
        except (TypeError, ValueError):
            confidence = 0.0
        if len(compact) <= 3 and confidence < 45.0:
            return True
        return False

    @classmethod
    def _trim_url_prefix(cls, words: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop browser-toolbar garbage that precedes a URL token.

        Tesseract can read a row of toolbar buttons as ``€ > G& [P &`` and
        then attach the address bar text to it.  Only a prefix made entirely of
        short/low-confidence symbol fragments is removed, so a genuine
        sentence such as ``Visit https://example.com`` keeps its words.
        """
        index = next(
            (
                number
                for number, word in enumerate(words)
                if _looks_like_url(str(word.get("text") or ""))
            ),
            None,
        )
        if not index:
            return words
        if all(cls._is_chrome_junk_word(word) for word in words[:index]):
            return words[index:]
        return words

    @classmethod
    def _trim_edge_noise(
        cls, words: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Remove toolbar/rules noise from the start and end of a segment.

        Tesseract often attaches a border pipe, a half-icon bracket or a
        low-confidence toolbar fragment to the first/last real word.  Such
        tokens must not become editable text, and they must not enter the
        removal mask either: erasing them is what damaged adjacent decoration
        and icons.  Only edge tokens are touched; a symbol inside real text
        keeps its normal joining rules.

        Tiny edge fragments are only removed when they are isolated by a large
        gap.  A normal trailing comma or a leading quote sits tight against its
        word and must survive; a 4 px ``SJ`` speck or a stray quote 50 px away
        in the margin is noise.
        """
        heights = [
            _word_height(word)
            for word in words
            if _alnum_count(str(word.get("text") or "")) >= 2
        ]
        heights = [height for height in heights if height > 0]
        median_height = cls._median_value(heights)

        def values(item: dict[str, Any]) -> tuple[float, float, float, float]:
            box = item.get("box")
            if box:
                return cls._box_values(box)
            try:
                x0 = float(item.get("x", 0.0))
                y0 = float(item.get("y", 0.0))
                return (
                    x0,
                    y0,
                    x0 + max(1.0, float(item.get("w", item.get("width", 1.0)))),
                    y0 + max(1.0, float(item.get("h", item.get("height", 1.0)))),
                )
            except (TypeError, ValueError):
                return 0.0, 0.0, 1.0, 1.0

        def box_gap(left: dict[str, Any], right: dict[str, Any]) -> float:
            ax0, _, ax1, _ = values(left)
            bx0, _, bx1, _ = values(right)
            if bx0 >= ax1:
                return bx0 - ax1
            if ax0 >= bx1:
                return ax0 - bx1
            return 0.0

        def junk(index: int, from_left: bool) -> bool:
            word = words[index]
            text = str(word.get("text") or "")
            compact = re.sub(r"\s+", "", text)
            if not compact:
                return True
            try:
                confidence = float(
                    word.get("confidence", word.get("conf", 0.0))
                )
            except (TypeError, ValueError):
                confidence = 0.0
            if all(char in RULE_ONLY_CHARS for char in compact):
                return True
            if compact.casefold() in {"a", "i"}:
                # The article/pronoun can legitimately be a low-confidence
                # one-character token at the edge of a line.  A margin speck
                # can read as the same letter, so only keep it when it sits
                # at normal word spacing from its neighbour.
                if median_height > 0 and len(words) >= 2:
                    neighbour = words[1] if from_left else words[-2]
                    gap = box_gap(word, neighbour)
                    if gap >= max(12.0, median_height * 1.4):
                        return True
                return False
            opening = {"[": "]", "(": ")", "{": "}", "<": ">"}
            closer = opening.get(compact[0])
            if closer and closer not in compact[1:] and confidence < 60.0:
                return True
            alnum = sum(1 for char in compact if char.isalnum())
            if len(compact) <= 2 and alnum <= 1 and confidence < 35.0:
                return True
            if alnum == 0:
                if len(compact) <= 2 and confidence < 20.0:
                    return True
                if len(compact) > 4 and confidence < 55.0:
                    return True

            if median_height <= 0 or len(words) < 2:
                return False
            if from_left:
                if index + 1 >= len(words):
                    return False
                neighbour = words[index + 1]
            else:
                if index <= 0:
                    return False
                neighbour = words[index - 1]
            gap = box_gap(word, neighbour)
            if gap < max(12.0, median_height * 1.4) or confidence >= 85.0:
                return False
            height = _word_height(word)
            width = max(0.0, values(word)[2] - values(word)[0])
            if height <= 0:
                return False
            if height < median_height * 0.72:
                # A comma-, quote-, or speck-sized fragment in the margin.
                return True
            if (
                len(compact) <= 2
                and alnum <= 2
                and width < median_height * 0.9
                and confidence < 80.0
            ):
                # A tiny single digit/letter at the page edge, separated from
                # the real line ("4  time sheet itself.").
                return True
            return False

        start = 0
        end = len(words)
        while start < end and junk(start, True):
            start += 1
        while end > start and junk(end - 1, False):
            end -= 1
        return words[start:end]

    @staticmethod
    def _drop_leading_icon_prefix(
        words: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Remove an unmatched bracket fragment such as ``[BB`` before text."""
        if len(words) < 2:
            return words
        first = str(words[0].get("text") or "")
        if not first or first[0] not in "[({<":
            return words
        closing = {"[": "]", "(": ")", "{": "}", "<": ">"}.get(first[0])
        if not closing:
            return words
        rest = "".join(str(word.get("text") or "") for word in words[1:])
        if closing in rest:
            return words
        try:
            confidence = float(words[0].get("confidence", words[0].get("conf", 0.0)))
        except (TypeError, ValueError):
            confidence = 0.0
        return words[1:] if confidence < 60.0 else words

    @staticmethod
    def _dedupe_merge_words(
        words: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Drop the same word repeated by overlapping OCR passes."""
        kept: list[dict[str, Any]] = []
        for word in words:
            text = re.sub(r"\s+", " ", str(word.get("text") or "")).strip().casefold()
            box = word.get("box") or {}
            bx0 = float(box.get("x", 0.0))
            by0 = float(box.get("y", 0.0))
            bx1 = bx0 + max(1.0, float(box.get("width", 1.0)))
            by1 = by0 + max(1.0, float(box.get("height", 1.0)))
            area = max(1.0, (bx1 - bx0) * (by1 - by0))
            duplicate = False
            for existing in kept:
                existing_text = re.sub(
                    r"\s+", " ", str(existing.get("text") or "")
                ).strip().casefold()
                if not text or text != existing_text:
                    continue
                existing_box = existing.get("box") or {}
                ex0 = float(existing_box.get("x", 0.0))
                ey0 = float(existing_box.get("y", 0.0))
                ex1 = ex0 + max(1.0, float(existing_box.get("width", 1.0)))
                ey1 = ey0 + max(1.0, float(existing_box.get("height", 1.0)))
                overlap_x = min(bx1, ex1) - max(bx0, ex0)
                overlap_y = min(by1, ey1) - max(by0, ey0)
                if overlap_x <= 0 or overlap_y <= 0:
                    continue
                existing_area = max(1.0, (ex1 - ex0) * (ey1 - ey0))
                if overlap_x * overlap_y >= min(area, existing_area) * 0.45:
                    duplicate = True
                    break
            if not duplicate:
                kept.append(word)
        return kept

    @staticmethod
    def _is_icon_sized_text_line(
        line: dict[str, Any], median_height: float
    ) -> bool:
        """True for a tiny one/two-character line that must not join text."""
        text = re.sub(r"\s+", "", str(line.get("text") or ""))
        if not text or len(text) > 2:
            return False
        if any(char.isdigit() for char in text):
            return False
        if text.casefold() in {"a", "i"}:
            return False
        box = line.get("box") or {}
        width = max(1.0, float(box.get("width", 1.0)))
        height = max(1.0, float(box.get("height", 1.0)))
        if width > 22.0 or height > 16.0:
            return False
        return height < max(1.0, median_height) * 0.80

    @classmethod
    def _rebuild_merged_line(
        cls, fragments: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        words: list[dict[str, Any]] = []
        for fragment_index, fragment in enumerate(fragments):
            for word in fragment.get("words") or []:
                box = word.get("box") or {}
                text = str(word.get("text") or "")
                if not text or not box:
                    continue
                words.append(
                    {
                        "text": text,
                        "box": {
                            "x": int(round(float(box.get("x", 0.0)))),
                            "y": int(round(float(box.get("y", 0.0)))),
                            "width": max(1, int(round(float(box.get("width", 1.0))))),
                            "height": max(1, int(round(float(box.get("height", 1.0))))),
                        },
                        "confidence": round(
                            float(word.get("confidence") or 0.0), 1
                        ),
                        "_fragment_index": fragment_index,
                    }
                )
        if not words:
            return None
        words = cls._dedupe_merge_words(words)
        reverse = _text_direction(words) == "rtl"
        words.sort(
            key=lambda word: cls._box_values(word.get("box") or {})[0],
            reverse=reverse,
        )
        words = cls._drop_leading_icon_prefix(words)
        words = cls._trim_url_prefix(words)
        words = cls._trim_edge_noise(words)
        if not words:
            return None
        seen_fragments: set[int] = set()
        for word in words:
            fragment_index = word.pop("_fragment_index", None)
            if fragment_index is None or fragment_index in seen_fragments:
                continue
            seen_fragments.add(fragment_index)
            word["fragment_start"] = True

        proxies = [
            {
                "text": word["text"],
                "x": int(word["box"]["x"]),
                "y": int(word["box"]["y"]),
                "w": int(word["box"]["width"]),
                "h": int(word["box"]["height"]),
                "conf": float(word["confidence"]),
                "fragment_start": bool(word.pop("fragment_start", False)),
            }
            for word in words
        ]
        text = cls._join_words(proxies)
        if not text.strip():
            return None

        x0 = min(int(word["box"]["x"]) for word in words)
        y0 = min(int(word["box"]["y"]) for word in words)
        x1 = max(
            int(word["box"]["x"]) + int(word["box"]["width"]) for word in words
        )
        y1 = max(
            int(word["box"]["y"]) + int(word["box"]["height"]) for word in words
        )
        box = {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}
        weights = [max(1, _content_count(word["text"])) for word in words]
        total_weight = sum(weights)
        confidence = sum(
            float(word["confidence"]) * weight
            for word, weight in zip(words, weights)
        ) / max(1, total_weight)

        mask_candidates: list[dict[str, Any]] = []
        for fragment in fragments:
            mask_candidates.extend(fragment.get("mask_boxes") or [])
        for word in words:
            mask_candidates.append(dict(word["box"]))
        # Keep masks only inside the span of the words that survived edge
        # trimming.  Toolbar junk and unmatched brackets outside the first/last
        # real word must not be erased along with the text.
        tolerance = max(2.0, (y1 - y0) * 0.25)
        mask_boxes = []
        for candidate in mask_candidates:
            if not cls._box_overlaps(candidate, box, margin=2.0):
                continue
            try:
                cx0, _, cx1, _ = cls._box_values(candidate)
            except (TypeError, ValueError):
                continue
            centre = (cx0 + cx1) / 2.0
            if x0 - tolerance <= centre <= x1 + tolerance:
                mask_boxes.append(candidate)

        return {
            "text": text,
            "box": box,
            "confidence": round(confidence, 1),
            "words": words,
            "mask_boxes": cls._merge_mask_boxes([], mask_boxes),
            "word_count": len(words),
        }

    @classmethod
    def _merge_fragment_row(
        cls, row: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not row:
            return []
        all_words = [
            word
            for line in row
            for word in (line.get("words") or [])
            if word.get("box")
        ]
        proxies = [
            {
                "text": str(word.get("text") or ""),
                "w": max(
                    1.0, float((word.get("box") or {}).get("width", 1.0))
                ),
                "h": max(
                    1.0, float((word.get("box") or {}).get("height", 1.0))
                ),
                "processed_w": max(
                    1.0, float((word.get("box") or {}).get("width", 1.0))
                ),
            }
            for word in all_words
        ]
        median_height, median_char = _robust_word_metrics(proxies)
        if median_height <= 0:
            median_height = cls._median_value(
                [
                    float((line.get("box") or {}).get("height", 1.0))
                    for line in row
                ]
            )
        threshold = max(median_height * 1.35, median_char * 2.3, 10.0)

        reverse = _text_direction(all_words) == "rtl"
        row.sort(
            key=lambda line: (
                cls._box_values(line.get("box") or {})[2]
                if reverse
                else cls._box_values(line.get("box") or {})[0]
            ),
            reverse=reverse,
        )
        barriers = [
            cls._is_icon_sized_text_line(line, median_height)
            or cls._line_spans_rows(line)
            for line in row
        ]

        groups: list[list[int]] = [[0]]
        for index in range(1, len(row)):
            group = groups[-1]
            if barriers[index] or any(barriers[number] for number in group):
                groups.append([index])
                continue
            boxes = [row[number].get("box") or {} for number in group]
            union = {
                "x": min(cls._box_values(item)[0] for item in boxes),
                "y": min(cls._box_values(item)[1] for item in boxes),
                "width": 0,
                "height": 0,
            }
            union_x1 = max(cls._box_values(item)[2] for item in boxes)
            union_y1 = max(cls._box_values(item)[3] for item in boxes)
            union["width"] = union_x1 - union["x"]
            union["height"] = union_y1 - union["y"]
            gap = cls._horizontal_box_gap(union, row[index].get("box") or {})
            if gap <= threshold:
                group.append(index)
            else:
                groups.append([index])

        result: list[dict[str, Any]] = []
        for group in groups:
            rebuilt = cls._rebuild_merged_line([row[index] for index in group])
            if rebuilt is not None:
                result.append(rebuilt)
        return result

    @staticmethod
    def _word_centre(word: dict[str, Any]) -> float:
        box = word.get("box") or {}
        try:
            return float(box.get("y", 0.0)) + max(
                1.0, float(box.get("height", 1.0))
            ) / 2.0
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def _line_baseline(cls, line: dict[str, Any]) -> float:
        """Return the visual centre line of a fragment in image coordinates.

        Word centres are more reliable than the union box: a tall recovered
        fragment can span two physical rows even though its words belong to one
        baseline.  The median keeps one stray descender box from moving it.
        """
        centres = [
            cls._word_centre(word)
            for word in line.get("words") or []
            if word.get("box")
        ]
        if centres:
            return cls._median_value(centres)
        _, y0, _, y1 = cls._box_values(line.get("box") or {})
        return (y0 + y1) / 2.0

    @classmethod
    def _line_spans_rows(cls, line: dict[str, Any]) -> bool:
        """True when a line's words belong to two clearly separate baselines.

        ``_split_multibaseline_lines`` normally prevents this, but fragments
        from different passes can still be stitched into one box after their
        baselines drift on a skewed or clipped photo.  Such a selection cannot
        be edited without overwriting the neighbouring row.
        """
        words = [word for word in line.get("words") or [] if word.get("box")]
        if len(words) < 4:
            return False
        heights: list[float] = []
        bottoms: list[float] = []
        for word in words:
            box = word.get("box") or {}
            try:
                height = float(box.get("height", 0.0))
                bottom = float(box.get("y", 0.0)) + height
            except (TypeError, ValueError):
                continue
            if height <= 0:
                continue
            heights.append(height)
            bottoms.append(bottom)
        if len(heights) < 4:
            return False
        median_height = cls._median_value(heights)
        median_bottom = cls._median_value(bottoms)
        if median_height <= 0:
            return False
        if sum(1 for height in heights if height > median_height * 1.8) >= 2:
            # Two words much taller than their neighbours cannot share one
            # baseline.  On a clipped or warped photo Tesseract feeds glyph
            # remains from the row above into this line, producing tall boxes
            # that straddle both rows even though their bottoms still look
            # aligned with the lower row.
            return True
        low = [bottom for bottom in bottoms if bottom < median_bottom - median_height * 0.45]
        high = [bottom for bottom in bottoms if bottom > median_bottom + median_height * 0.45]
        if not low or not high:
            return False
        separation = cls._median_value(high) - cls._median_value(low)
        return separation > max(5.0, median_height * 0.8)

    @classmethod
    def _resolve_overlapping_lines(
        cls, lines: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Drop weaker selections that are a second reading of the same words.

        Comparing only the outer boxes was unsafe: neighbouring text rows on a
        tightly spaced or slightly warped photo overlap by a few pixels, and
        the pass then deleted a whole legitimate row in favour of the next one.
        A real conflict requires the same baseline *and* overlapping word
        masks.  Lines on different baselines, or same-baseline fragments that
        occupy different columns, are both kept.
        """
        kept: list[dict[str, Any]] = []
        for line in sorted(lines, key=cls._line_quality, reverse=True):
            conflict = False
            for existing in kept:
                if cls._lines_compete(line, existing):
                    conflict = True
                    break
            if not conflict:
                kept.append(line)
        return kept

    @classmethod
    def _lines_compete(cls, first: dict[str, Any], second: dict[str, Any]) -> bool:
        """True when two selections cover the same words on one baseline."""
        first_height = cls._median_word_height(first)
        second_height = cls._median_word_height(second)
        if first_height <= 0 or second_height <= 0:
            return False
        if abs(cls._line_baseline(first) - cls._line_baseline(second)) > max(
            4.0, min(first_height, second_height) * 0.6
        ):
            return False
        first_boxes = cls._word_boxes(first)
        second_boxes = cls._word_boxes(second)
        if not first_boxes or not second_boxes:
            return False
        for box in first_boxes:
            for other in second_boxes:
                if cls._boxes_overlap(box, other, 0.25):
                    return True
        return False

    @staticmethod
    def _median_word_height(line: dict[str, Any]) -> float:
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
            box = line.get("box") or {}
            try:
                return max(1.0, float(box.get("height", 1.0)))
            except (TypeError, ValueError):
                return 0.0
        return TesseractEngine._median_value(heights)

    @staticmethod
    def _word_boxes(line: dict[str, Any]) -> list[dict[str, Any]]:
        boxes = [box for box in line.get("mask_boxes") or [] if box]
        if boxes:
            return boxes
        return [
            word["box"]
            for word in line.get("words") or []
            if word.get("box")
        ]

    @staticmethod
    def _boxes_overlap(
        first: dict[str, Any], second: dict[str, Any], ratio: float
    ) -> bool:
        try:
            ax, ay = float(first.get("x", 0)), float(first.get("y", 0))
            aw = max(1.0, float(first.get("width", 1.0)))
            ah = max(1.0, float(first.get("height", 1.0)))
            bx, by = float(second.get("x", 0)), float(second.get("y", 0))
            bw = max(1.0, float(second.get("width", 1.0)))
            bh = max(1.0, float(second.get("height", 1.0)))
        except (TypeError, ValueError):
            return False
        overlap_width = min(ax + aw, bx + bw) - max(ax, bx)
        overlap_height = min(ay + ah, by + bh) - max(ay, by)
        if overlap_width <= 0 or overlap_height <= 0:
            return False
        smaller = min(aw * ah, bw * bh)
        return (overlap_width * overlap_height) / smaller >= ratio

    @classmethod
    def _split_multibaseline_lines(
        cls, lines: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Split a pass line whose words belong to separate visual rows.

        PSM 6 can glue a menu row to a ruler label a row below it, producing a
        line twice as tall as its text.  Style sampling then estimates a huge
        font size, and editing that line paints oversized text over unrelated
        UI.  Splitting on word centres keeps each real row editable at its own
        scale.  The operation is only used on whole-page passes; recovery crops
        are still rejected wholesale when they span rows.
        """
        result: list[dict[str, Any]] = []
        for line in lines:
            result.extend(cls._split_multibaseline_line(line))
        return result

    @classmethod
    def _split_multibaseline_line(
        cls, line: dict[str, Any]
    ) -> list[dict[str, Any]]:
        words = [
            word for word in (line.get("words") or []) if word.get("box")
        ]
        if len(words) < 2:
            return [line]
        heights = [
            max(1.0, float((word.get("box") or {}).get("height", 1.0)))
            for word in words
        ]
        median_height = cls._median_value(heights)
        if median_height <= 0:
            return [line]
        tolerance = max(3.0, median_height * 0.45)
        ordered = sorted(words, key=cls._word_centre)
        clusters: list[list[dict[str, Any]]] = [[ordered[0]]]
        centres = [cls._word_centre(ordered[0])]
        for word in ordered[1:]:
            centre = cls._word_centre(word)
            if abs(centre - centres[-1]) <= tolerance:
                clusters[-1].append(word)
                centres[-1] = cls._median_value(
                    [cls._word_centre(item) for item in clusters[-1]]
                )
            else:
                clusters.append([word])
                centres.append(centre)
        if len(clusters) < 2:
            return [line]

        main = max(clusters, key=len)
        parts: list[dict[str, Any]] = []
        for cluster in clusters:
            if cls._is_multibaseline_noise(cluster, main, median_height):
                continue
            boxes = [word["box"] for word in cluster]
            x0 = min(cls._box_values(box)[0] for box in boxes)
            y0 = min(cls._box_values(box)[1] for box in boxes)
            x1 = max(cls._box_values(box)[2] for box in boxes)
            y1 = max(cls._box_values(box)[3] for box in boxes)
            confidence = cls._median_value(
                [float(word.get("confidence") or 0.0) for word in cluster]
            )
            fragment = {
                "text": " ".join(str(word.get("text") or "") for word in cluster),
                "box": {
                    "x": int(round(x0)),
                    "y": int(round(y0)),
                    "width": max(1, int(round(x1 - x0))),
                    "height": max(1, int(round(y1 - y0))),
                },
                "confidence": confidence,
                "words": cluster,
                "mask_boxes": [dict(box) for box in boxes],
            }
            rebuilt = cls._rebuild_merged_line([fragment])
            if rebuilt is not None:
                parts.append(rebuilt)
        if not parts:
            return [line]
        return parts

    @staticmethod
    def _is_multibaseline_noise(
        cluster: list[dict[str, Any]],
        main: list[dict[str, Any]],
        median_height: float,
    ) -> bool:
        """True for a tiny low-confidence word that is not part of the row.

        A single ``a`` picked out of a ruler scale is not a real continuation
        of the menu line above it.  Digits are never dropped here because page
        numbers and prices are legitimate text.
        """
        if len(cluster) != 1 or len(main) < 2:
            return False
        word = cluster[0]
        text = re.sub(r"\s+", "", str(word.get("text") or ""))
        if not text or len(text) > 2 or any(char.isdigit() for char in text):
            return False
        try:
            confidence = float(word.get("confidence") or 0.0)
            height = max(1.0, float((word.get("box") or {}).get("height", 1.0)))
        except (TypeError, ValueError):
            return False
        return confidence < 70.0 and height < median_height * 0.85

    @classmethod
    def _merge_adjacent_fragments(
        cls, lines: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Stitch same-baseline OCR fragments into visual lines.

        Tesseract's sparse mode often returns several high-confidence fragments
        for one paragraph line (``The White``, ``Hat``, ``is``, ...).  Treating
        every fragment as a selection also samples style per fragment, which
        caused wrong bold/serif/alignment guesses and broken reading order.
        This pass groups fragments by their visual centre line, sorts them by
        x, and merges neighbours separated by ordinary word spacing.  Genuine
        table columns, tabs and isolated icons keep their wide gaps and stay
        split.

        Grouping by rectangle overlap was not safe: a tall recovered fragment
        in the margin (or a line box that includes a shadow) overlapped two
        adjacent text rows and bridged them into one garbled selection.  The
        centre-line distance, measured against the smaller of the two heights,
        keeps adjacent rows apart even when their boxes touch.
        """
        clones = [
            cls._clone_line(line) for line in lines if line.get("box")
        ]
        if not clones:
            return []
        rows: list[list[dict[str, Any]]] = []
        row_centres: list[float] = []
        for line in sorted(
            clones, key=lambda item: (item["box"]["y"], item["box"]["x"])
        ):
            lh = cls._median_word_height(line)
            centre = cls._line_baseline(line)
            best_row = None
            best_delta = float("inf")
            for index, row in enumerate(rows):
                row_height = cls._median_value(
                    [cls._median_word_height(member) for member in row]
                )
                tolerance = max(3.0, min(lh, row_height) * 0.42)
                delta = abs(centre - row_centres[index])
                if delta <= tolerance and delta < best_delta:
                    best_row = index
                    best_delta = delta
            if best_row is None:
                rows.append([line])
                row_centres.append(centre)
            else:
                rows[best_row].append(line)
                row_centres[best_row] = cls._median_value(
                    [cls._line_baseline(member) for member in rows[best_row]]
                )

        result: list[dict[str, Any]] = []
        for row in rows:
            result.extend(cls._merge_fragment_row(row))
        return result

    @classmethod
    def filter_graphic_lines(
        cls, image: Image.Image, lines: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Drop low-confidence selections whose pixels are not a text row.

        Tesseract can read a row of coloured toolbox icons or a patch of brush
        thumbnails as a word.  Text heuristics alone cannot catch every case,
        because the icon cluster often has a plausible letter count and a
        neatly aligned baseline.  Two conservative pixel checks therefore
        back up the text rules:

        * components scattered over several glyph heights are a texture/logo
          patch, not one line of type;
        * ink in several unrelated hues is a multicoloured icon rather than a
          text run with one foreground colour.

        Both checks only apply to lines that already have weak OCR confidence;
        confident text with a coloured background or a drop cap is untouched.
        """
        kept: list[dict[str, Any]] = []
        for line in lines:
            if not cls._plausible_line(line):
                continue
            try:
                confidence = float(line.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            crop = cls._line_crop(image, line.get("box") or {})
            if crop is not None and cls._line_pixels_look_graphic(
                crop, confidence
            ):
                continue
            if cls._border_clipped_line(image, line, lines):
                continue
            kept.append(line)
        return kept

    @classmethod
    def filter_border_clipped_lines(
        cls, image: Image.Image, lines: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Drop partial rows cut off by the top or bottom image edge.

        This runs before fragments are rejoined.  A clipped bottom row can sit
        exactly on the baseline of the complete row above it, so a later
        geometric merge would otherwise glue the two rows together.
        """
        return [
            line for line in lines if not cls._border_clipped_line(image, line, lines)
        ]

    @classmethod
    def _border_clipped_line(
        cls,
        image: Image.Image,
        line: dict[str, Any],
        lines: list[dict[str, Any]],
    ) -> bool:
        """True for a partial row cut off by the image border.

        A photo can crop the top or bottom of the page.  Tesseract then reads
        the surviving part of a row as a line: a cropped title can still be
        oversized, while a cut body row is shorter than the page's normal text.
        Editing either selection paints replacement text over a row that was
        never fully read.  The flat cut leaves a dense band of ink on the image
        border, unlike a normal line that merely starts near the edge.
        """
        box = line.get("box") or {}
        x0, y0, x1, y1 = cls._box_values(box)
        touches_top = y0 <= 1.0
        touches_bottom = y1 >= float(image.height) - 1.0
        if not touches_top and not touches_bottom:
            return False
        width = x1 - x0
        if width < 20.0 or width > image.width * 0.70:
            return False
        words = [word for word in line.get("words") or [] if word.get("box")]
        if len(words) < 2:
            return False
        try:
            confidence = float(line.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        # A clean title can sit flush with the border, but it normally reads
        # confidently.  A cut row leaves partial glyphs and a weaker reading.
        if confidence >= 80.0:
            return False
        text_height = cls._median_word_height(line)
        if text_height <= 0.0:
            return False
        page_heights = [
            cls._median_word_height(other)
            for other in lines
            if other is not line
            and len(re.sub(r"\s+", "", str(other.get("text") or ""))) >= 3
        ]
        page_height = cls._median_value(page_heights)
        if page_height <= 0.0:
            return False
        # A clipped title is still taller than body text; a clipped body row
        # loses its lower strokes and becomes noticeably shorter.  A complete
        # line that happens to sit on the edge is close to the page median and
        # is left alone.
        oversized = text_height >= page_height * 1.35
        partial = text_height <= page_height * 0.90
        if not oversized and not partial:
            return False
        crop = cls._line_crop(image, box)
        if crop is None:
            return False
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY).astype(np.float32)
        background = float(np.median(gray))
        ink = np.abs(gray - background) > 40.0
        band = max(4, min(crop.shape[0], int(round(y1 - y0) // 4)))
        edge = ink[:band, :] if touches_top else ink[-band:, :]
        # Deskewing can pad the border with a row or two of white, so look at
        # the densest row in the cut band rather than only the outer row.
        return float(edge.mean(axis=1).max()) >= 0.25

    @staticmethod
    def _line_crop(
        image: Image.Image, box: dict[str, Any]
    ) -> np.ndarray | None:
        x0, y0, x1, y1 = TesseractEngine._box_values(box)
        left = max(0, int(math.floor(x0)) - 1)
        top = max(0, int(math.floor(y0)) - 1)
        right = min(int(image.width), int(math.ceil(x1)) + 1)
        bottom = min(int(image.height), int(math.ceil(y1)) + 1)
        if right - left < 3 or bottom - top < 3:
            return None
        try:
            crop = image.crop((left, top, right, bottom)).convert("RGB")
        except (OSError, ValueError):
            return None
        array = np.asarray(crop)
        if array.ndim != 3 or array.shape[0] < 3 or array.shape[1] < 3:
            return None
        return array

    @classmethod
    def _line_pixels_look_graphic(
        cls, crop: np.ndarray, confidence: float
    ) -> bool:
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
        if confidence < 60.0 and cls._components_span_rows(gray):
            return True
        if confidence < 55.0 and cls._multicolour_ink(crop, gray):
            return True
        return False

    @staticmethod
    def _components_span_rows(gray: np.ndarray) -> bool:
        """True when components are scattered over several glyph heights."""
        try:
            _, binary = cv2.threshold(
                gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU
            )
            count, _, stats, centroids = cv2.connectedComponentsWithStats(
                binary, 8
            )
        except (AttributeError, TypeError, ValueError):
            return False
        heights: list[float] = []
        centres: list[float] = []
        for index in range(1, int(count)):
            area = float(stats[index, 4])
            height = float(stats[index, 3])
            if area < 6.0 or height < 2.0:
                continue
            heights.append(height)
            centres.append(float(centroids[index][1]))
        if len(heights) < 4:
            return False
        median_height = float(np.median(heights))
        if median_height <= 0.0:
            return False
        return (max(centres) - min(centres)) / median_height > 2.4

    @staticmethod
    def _multicolour_ink(crop: np.ndarray, gray: np.ndarray) -> bool:
        """True when the ink has several strong, unrelated hues."""
        background = float(np.median(gray.astype(np.float32)))
        ink = np.abs(gray.astype(np.float32) - background) > 35.0
        ink_count = int(ink.sum())
        if ink_count < 20:
            return False
        hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
        saturation = hsv[..., 1].astype(np.float32) / 255.0
        strong = ink & (saturation > 0.35)
        strong_count = int(strong.sum())
        if strong_count < 12 or strong_count / ink_count < 0.25:
            return False
        hues = hsv[..., 0][strong].astype(np.float32) * 2.0
        bins = np.histogram(hues, bins=12, range=(0.0, 360.0))[0]
        occupied = int((bins > max(1.0, strong_count * 0.05)).sum())
        return occupied >= 2

    @classmethod
    def _drop_graphic_lines(
        cls, lines: list[dict[str, Any]], image_size: tuple[int, int]
    ) -> list[dict[str, Any]]:
        """Drop huge short tokens that are logos/illustrations, not text."""
        kept: list[dict[str, Any]] = []
        for line in lines:
            try:
                confidence = float(line.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            if _looks_like_graphic_text_line(
                str(line.get("text") or ""),
                line.get("box") or {},
                confidence,
                image_size,
            ):
                continue
            kept.append(line)
        return kept

    @classmethod
    def _drop_isolated_glyph_lines(
        cls,
        lines: list[dict[str, Any]],
        image_size: tuple[int, int] | None = None,
    ) -> list[dict[str, Any]]:
        """Drop tiny standalone characters that are really toolbar icons.

        A short low-confidence line is filtered separately.  This pass targets
        high-confidence but geometrically tiny single glyphs (browser ``+``,
        ``x`` or close icons) that Tesseract reports as real text.  A glyph is
        only removed when it is much smaller than the document's normal text
        and has no neighbouring text on its baseline, so a checkbox mark or
        table cell next to a label survives.
        """
        if not lines:
            return []
        heights = []
        for line in lines:
            compact = re.sub(r"\s+", "", str(line.get("text") or ""))
            if len(compact) >= 3:
                _, y0, _, y1 = cls._box_values(line.get("box") or {})
                heights.append(max(1.0, y1 - y0))
        median_height = cls._median_value(heights)
        if median_height <= 0:
            return lines
        return [
            line
            for line in lines
            if not cls._is_isolated_glyph_line(
                line, lines, median_height, image_size
            )
        ]

    @classmethod
    def _is_isolated_glyph_line(
        cls,
        line: dict[str, Any],
        lines: list[dict[str, Any]],
        median_height: float,
        image_size: tuple[int, int] | None = None,
    ) -> bool:
        text = re.sub(r"\s+", "", str(line.get("text") or ""))
        if not text or len(text) > 4:
            return False
        try:
            confidence = float(line.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if len(text) > 2 and confidence >= 65.0:
            return False
        box = line.get("box") or {}
        width = max(1.0, float(box.get("width", 1.0)))
        height = max(1.0, float(box.get("height", 1.0)))
        if image_size is not None:
            image_width = int(image_size[0])
            image_height = int(image_size[1])
            x0, y0, x1, y1 = cls._box_values(box)
            margin_x = max(2.0, image_width * 0.03)
            margin_y = max(2.0, image_height * 0.02)
            at_edge = (
                x0 <= margin_x
                or x1 >= image_width - margin_x
                or y0 <= margin_y
                or y1 >= image_height - margin_y
            )
            if (
                at_edge
                and len(text) <= 2
                and height < max(6.0, median_height * 0.30)
            ):
                # A 4-pixel speck on the border cannot be a page number or a
                # price, even when Tesseract reads it as a digit.
                return True
        if any(char.isdigit() for char in text):
            # Page numbers, prices and single-digit labels are real text.
            return False
        if all(unicodedata.category(char) == "Sc" for char in text):
            return False
        if image_size is not None:
            image_width = int(image_size[0])
            image_height = int(image_size[1])
            x0, y0, x1, y1 = cls._box_values(box)
            margin_x = max(2.0, image_width * 0.03)
            margin_y = max(2.0, image_height * 0.02)
            at_edge = (
                x0 <= margin_x
                or x1 >= image_width - margin_x
                or y0 <= margin_y
                or y1 >= image_height - margin_y
            )
            letters = "".join(char for char in text if char.isalpha())
            if at_edge and len(text) <= 3 and confidence < 90.0 and letters and len(
                letters
            ) == len(text.replace(".", "")):
                # A lone letter on the page border is usually a scanning edge
                # or a rotated corner fragment, not a label.  Keep it when a
                # real word sits within about one glyph-height on the same
                # row; a list marker or diagram label has that neighbour.
                near_row_text = False
                for other in lines:
                    if other is line:
                        continue
                    other_box = other.get("box") or {}
                    _, oy0, _, oy1 = cls._box_values(other_box)
                    other_height = max(1.0, oy1 - oy0)
                    overlap = min(y1, oy1) - max(y0, oy0)
                    if overlap / max(1.0, min(height, other_height)) < 0.25:
                        continue
                    if cls._horizontal_box_gap(box, other_box) <= max(
                        height * 1.5, 24.0
                    ):
                        near_row_text = True
                        break
                if not near_row_text:
                    return True
        if text in {"I", "a", "A"}:
            return False
        if text in {"©", "®", "™", "°", "‰", "%", "&"}:
            return False
        if (
            image_size is not None
            and len(text) >= 2
            and text.isascii()
            and text.isalpha()
            and width < height * 0.60
        ):
            image_width = int(image_size[0])
            x0, _, x1, _ = cls._box_values(box)
            if x0 <= 1.0 or x1 >= image_width - 1.0:
                # Tesseract sometimes emits a phantom word over a page-edge
                # rule.  Two or more horizontal Latin characters cannot be
                # much narrower than they are tall, and the source has no ink
                # there.  Keeping it would leave an unusable margin selection.
                return True
        if width > 32.0 or height > 16.0:
            return False
        if height > median_height * 0.55:
            return False
        if len(text) > 2 and width > height * 4.5:
            return False
        for other in lines:
            if other is line:
                continue
            other_box = other.get("box") or {}
            _, oy0, _, oy1 = cls._box_values(other_box)
            other_height = max(1.0, oy1 - oy0)
            _, y0, _, y1 = cls._box_values(box)
            overlap = min(y1, oy1) - max(y0, oy0)
            if overlap / max(1.0, min(height, other_height)) < 0.30:
                continue
            gap = cls._horizontal_box_gap(box, other_box)
            if gap <= max(height * 4.0, 50.0):
                return False
        return True

    @staticmethod
    def _normalised_text(value: str) -> str:
        return re.sub(r"\s+", " ", value or "").strip().casefold()

    @staticmethod
    def _looks_repeated(text: str) -> bool:
        words = text.split()
        if len(words) >= 4 and len(words) % 2 == 0:
            half = len(words) // 2
            if words[:half] == words[half:]:
                return True
        compact = re.sub(r"\s+", "", text)
        if len(compact) >= 8 and len(compact) % 2 == 0:
            half = len(compact) // 2
            if compact[:half] == compact[half:]:
                return True
        return False

    @classmethod
    def _plausible_line(cls, line: dict[str, Any]) -> bool:
        text = str(line.get("text", "")).strip()
        box = line.get("box") or {}
        if not text or len(text) > 400:
            return False
        if float(box.get("width", 0)) < 3 or float(box.get("height", 0)) < 4:
            return False
        content = _content_count(text)
        if content < 1:
            return False
        compact = re.sub(r"\s+", "", text)
        try:
            confidence = float(line.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        width = float(box.get("width", 0.0))
        height = float(box.get("height", 0.0))
        # Very short low-confidence fragments are the usual shape of a browser
        # toolbar icon misread as text ("ix", "xy", "ie", "i").  Keep confident
        # two-character words ("Go", "OK", "42") and URLs.
        if len(compact) <= 2 and confidence < 55.0:
            return False
        # A short line needs enough confidence to reconstruct its glyphs and
        # face.  Below this level the reading is usually an icon, a texture, or
        # a shadow of a stronger line, and editing it would paint nonsense over
        # unrelated pixels.  Longer lines carry more word evidence, so they are
        # held to the lower floor below.
        if len(compact) <= 12 and confidence < 40.0:
            return False
        if len(compact) < 24 and confidence < 20.0:
            return False
        if _has_oversized_glyph_cells(line):
            return False
        # A two-letter token spread across a progress/separator bar is not text
        # even when Tesseract gives its anchor word a middling confidence.
        if (
            len(compact) <= 3
            and confidence < 35.0
            and height > 0
            and width > max(60.0, height * 6.0)
        ):
            return False
        if _alnum_count(text) == 0:
            # A line with no letters or digits must be made of real text
            # symbols.  This keeps "&", "©", "°C"-style marks, arrows and
            # mathematical signs editable while rejecting borders, repeated
            # rules and icon/ornament ranges such as stars or dingbats.
            if not compact or len(compact) > 40:
                return False
            # Single carets, tildes, vertical bars and backslashes are usually
            # border/arrow noise, even though they are Unicode symbols and may
            # legitimately occur *inside* a real line ("x^2", "a | b").
            if all(char in JUNK_ONLY for char in compact):
                return False
            if any(_is_decorative_symbol(char) for char in compact):
                return False
            if any(char in LIST_BULLET_SYMBOLS for char in compact):
                # A line with no words that mixes a list marker with symbols is
                # still a marker/decoration, not text.
                return False
            if any(not _is_safe_symbol(char) for char in compact):
                # If even one character is an icon/tech/emoji mark, the token
                # is a toolbar symbol rather than editable text.
                return False
            if (
                len(compact) >= 3
                and len(set(compact)) == 1
                and not compact[0].isalnum()
                and unicodedata.category(compact[0]) != "Sc"
            ):
                # Three or more identical non-currency symbols are a rule or
                # ornament.  Runs of currency signs such as "$$$" are kept as
                # textual emphasis, and singles/two-character runs stay text.
                return False
            if _all_rule_characters(compact):
                return False
            # A long, very thin run of symbols is a divider, not a text line.
            width = float(box.get("width", 0.0))
            height = float(box.get("height", 0.0))
            if height > 0 and width > max(24.0, height * 12.0):
                return False
        if cls._looks_repeated(text) and float(line.get("confidence", 0)) < 55.0:
            # Repeated rules/patterns are decorations, but confident real text
            # such as a poster repeating "SALE" must not disappear just
            # because a low-confidence pattern filter recognises the shape.
            return False
        return True

    @classmethod
    def _better_line(cls, existing: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        """Choose the fuller/better version of two boxes for the same line."""
        existing_text = cls._normalised_text(str(existing.get("text", "")))
        candidate_text = cls._normalised_text(str(candidate.get("text", "")))
        existing_content = _content_count(existing_text)
        candidate_content = _content_count(candidate_text)
        existing_conf = float(existing.get("confidence", 0.0))
        candidate_conf = float(candidate.get("confidence", 0.0))
        existing_width = float((existing.get("box") or {}).get("width", 0.0))
        candidate_width = float((candidate.get("box") or {}).get("width", 0.0))

        if candidate_text == existing_text:
            if candidate_width > existing_width * 1.08 and candidate_conf >= existing_conf - 10.0:
                return candidate
            return candidate if candidate_conf > existing_conf + 4.0 else existing
        if candidate_text in existing_text:
            return existing
        if existing_text in candidate_text:
            added = candidate_content - existing_content
            # Extending a clean fragment is good, but only when the longer
            # reading is not drastically less confident than the anchor text.
            if added >= 1 and candidate_conf >= max(10.0, existing_conf * 0.45):
                return candidate
            return existing
        if candidate_content >= existing_content + 2:
            # A much fuller line wins even when its mean confidence is lower.
            # Requiring a reasonable floor stops a wall of hallucinated noise
            # from replacing a clean short line.
            if candidate_conf >= max(12.0, existing_conf * 0.40) or candidate_content >= existing_content * 2.5:
                return candidate
        if (
            candidate_content > existing_content
            and candidate_width >= existing_width * 1.05
            and candidate_conf >= max(10.0, existing_conf * 0.55)
        ):
            # A wider reading can complete a truncated line, but not when it is
            # dramatically less confident than the clean fragment it replaces.
            # Adaptive-threshold passes often surround real status-bar text
            # with icon noise; content and width alone used to prefer that.
            return candidate
        if (
            candidate_content == existing_content
            and candidate_width > existing_width * 1.2
            and candidate_conf >= existing_conf - 8.0
        ):
            return candidate
        if candidate_content < existing_content:
            return existing
        return candidate if cls._line_quality(candidate) > cls._line_quality(existing) * 1.08 else existing

    @staticmethod
    def _merge_mask_boxes(
        first: list[dict[str, Any]], second: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[tuple[int, int, int, int]] = set()
        for box in [*first, *second]:
            try:
                key = (
                    int(round(float(box.get("x", 0)))),
                    int(round(float(box.get("y", 0)))),
                    int(round(float(box.get("width", 0)))),
                    int(round(float(box.get("height", 0)))),
                )
            except (TypeError, ValueError):
                continue
            if key[2] < 1 or key[3] < 1 or key in seen:
                continue
            seen.add(key)
            merged.append(
                {"x": key[0], "y": key[1], "width": key[2], "height": key[3]}
            )
        return merged[:600]

    @classmethod
    def _merge_line_sets(
        cls, base: list[dict[str, Any]], extra: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Merge OCR passes, preferring the fuller version of each line."""
        merged = [cls._clone_line(line) for line in base if cls._plausible_line(line)]
        candidates = sorted(extra, key=cls._line_quality, reverse=True)
        for raw_candidate in candidates:
            if not cls._plausible_line(raw_candidate):
                continue
            candidate = cls._clone_line(raw_candidate)
            matches = [
                index
                for index, existing in enumerate(merged)
                if cls._line_match(existing, candidate)
            ]
            if not matches:
                merged.append(candidate)
                continue

            # A recovered full line can overlap two fragments produced by a
            # page pass.  Consume both when the candidate genuinely spans them.
            if len(matches) > 1:
                matched = [merged[index] for index in matches]
                union_start = min(cls._box_values(line["box"])[0] for line in matched)
                union_end = max(cls._box_values(line["box"])[2] for line in matched)
                cand_start, _, cand_end, _ = cls._box_values(candidate["box"])
                span = max(1.0, union_end - union_start)
                covers = (min(union_end, cand_end) - max(union_start, cand_start)) / span
                fragment_words: set[str] = set()
                for line in matched:
                    for word in line.get("words") or []:
                        token = cls._normalised_text(str(word.get("text", "")))
                        if token:
                            fragment_words.add(token)
                union_content = sum(_content_count(token) for token in fragment_words)
                best_content = max(_content_count(str(line.get("text", ""))) for line in matched)
                candidate_content = _content_count(str(candidate.get("text", "")))
                required = max(best_content, int(math.ceil(union_content * 0.9)))
                matched_conf = max(
                    float(line.get("confidence", 0.0)) for line in matched
                )
                candidate_conf = float(candidate.get("confidence", 0.0))
                matched_width = max(1.0, union_end - union_start)
                candidate_width = max(1.0, cand_end - cand_start)
                # A full-width, nearly-as-confident reading can replace a
                # cluster of fragments even when one of those fragments is
                # low-confidence junk that inflates the union word count.
                # Without this, a clipped row's garbage tokens veto the only
                # clean reading that spans the whole row, and the fragments
                # survive as overlapping selections.
                authoritative = (
                    candidate_conf >= max(10.0, matched_conf * 0.90)
                    and candidate_width >= matched_width * 0.95
                )
                if covers >= 0.7 and (candidate_content >= required or authoritative):
                    masks: list[dict[str, Any]] = []
                    for index in matches:
                        masks = cls._merge_mask_boxes(masks, merged[index].get("mask_boxes") or [])
                    candidate["mask_boxes"] = cls._merge_mask_boxes(
                        masks, candidate.get("mask_boxes") or []
                    )
                    merged = [line for index, line in enumerate(merged) if index not in matches]
                    merged.append(candidate)
                    continue

            index = max(
                matches,
                key=lambda number: cls._box_intersection(
                    merged[number].get("box") or {}, candidate.get("box") or {}
                )[0]
                * cls._box_intersection(
                    merged[number].get("box") or {}, candidate.get("box") or {}
                )[1],
            )
            existing = merged[index]
            chosen = cls._better_line(existing, candidate)
            chosen["mask_boxes"] = cls._merge_mask_boxes(
                existing.get("mask_boxes") or [], candidate.get("mask_boxes") or []
            )
            merged[index] = chosen

        return cls._dedupe_lines(merged)

    @classmethod
    def _dedupe_lines(cls, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for line in sorted(lines, key=cls._line_quality, reverse=True):
            duplicate = None
            for index, existing in enumerate(result):
                if not cls._line_match(existing, line):
                    continue
                if cls._normalised_text(str(existing.get("text", ""))) == cls._normalised_text(
                    str(line.get("text", ""))
                ):
                    duplicate = index
                    break
                iw, ih, _, _ = cls._box_intersection(
                    existing.get("box") or {}, line.get("box") or {}
                )
                area = max(1.0, float((line.get("box") or {}).get("width", 1)) * float((line.get("box") or {}).get("height", 1)))
                if iw * ih / area > 0.65:
                    duplicate = index
                    break
            if duplicate is None:
                result.append(line)
                continue
            existing = result[duplicate]
            chosen = cls._better_line(existing, line)
            chosen["mask_boxes"] = cls._merge_mask_boxes(
                existing.get("mask_boxes") or [], line.get("mask_boxes") or []
            )
            result[duplicate] = chosen
        return result

    @staticmethod
    def _project_box(box: dict[str, Any], matrix: np.ndarray) -> tuple[float, float, float, float]:
        """Map a document-space box into processed OCR-image coordinates."""
        x0, y0, x1, y1 = TesseractEngine._box_values(box)
        points = np.array(
            [[[x0, y0]], [[x1, y0]], [[x1, y1]], [[x0, y1]]], dtype=np.float32
        )
        mapped = cv2.perspectiveTransform(points, matrix.astype(np.float64)).reshape(-1, 2)
        return (
            float(mapped[:, 0].min()),
            float(mapped[:, 1].min()),
            float(mapped[:, 0].max()),
            float(mapped[:, 1].max()),
        )

    @staticmethod
    def _merge_recovery_boxes(
        first: list[tuple[int, int, int, int]],
        second: list[tuple[int, int, int, int]],
    ) -> list[tuple[int, int, int, int]]:
        """Combine candidate boxes from both image polarities.

        A dark panel and the surrounding light page can expose the same line
        twice.  Near-duplicates are replaced by their union so a truncated
        detection from one polarity cannot hide the fuller box from the other.
        This also stops duplicates from consuming the limited recovery budget.
        """
        merged = list(first)
        for box in second:
            bx0, by0, bx1, by1 = box
            area_b = max(1, (bx1 - bx0) * (by1 - by0))
            duplicate = None
            for index, existing in enumerate(merged):
                ax0, ay0, ax1, ay1 = existing
                overlap_x = max(0, min(ax1, bx1) - max(ax0, bx0))
                overlap_y = max(0, min(ay1, by1) - max(ay0, by0))
                if overlap_x <= 0 or overlap_y <= 0:
                    continue
                area_a = max(1, (ax1 - ax0) * (ay1 - ay0))
                if overlap_x * overlap_y / min(area_a, area_b) >= 0.75:
                    duplicate = index
                    break
            if duplicate is None:
                merged.append(box)
            else:
                ax0, ay0, ax1, ay1 = merged[duplicate]
                merged[duplicate] = (
                    min(ax0, bx0),
                    min(ay0, by0),
                    max(ax1, bx1),
                    max(ay1, by1),
                )
        return merged

    @staticmethod
    def _detect_text_lines(
        gray: np.ndarray, invert: bool | None = None
    ) -> list[tuple[int, int, int, int]]:
        """Find likely text-line rectangles without relying on Tesseract.

        Tesseract's page segmentation can miss or truncate whole lines
        (tesseract-ocr/tesseract#1339).  OpenCV can still see the ink, so these
        boxes are used to crop each suspect line and re-run Tesseract in
        single-line mode.

        ``invert`` forces the polarity of the image before thresholding.
        ``None`` picks the polarity that matches the page's average brightness,
        which handles all-light or all-dark pages.  Passing True/False runs the
        opposite polarity as well, so a light-on-dark banner inside a light
        poster (or a dark panel inside a dark page) is not invisible to the
        recovery pass.
        """
        height, width = gray.shape[:2]
        if height < 30 or width < 60:
            return []
        longest = max(height, width)
        scale = min(1.0, 1800.0 / float(longest))
        if scale < 1.0:
            work = cv2.resize(
                gray,
                (max(1, int(width * scale)), max(1, int(height * scale))),
                interpolation=cv2.INTER_AREA,
            )
            detection_scale = scale
        else:
            work = gray
            detection_scale = 1.0
        work_height, work_width = work.shape[:2]
        if work_height < 20 or work_width < 40:
            return []
        # Work with dark ink on a light background so one threshold rule sees
        # both normal pages and light-on-dark signs.  When the caller asks for
        # a specific polarity, honour it instead of guessing from the mean.
        mean = float(np.mean(work))
        if invert is None:
            if mean < 118.0:
                work = cv2.bitwise_not(work)
        elif invert:
            work = cv2.bitwise_not(work)

        blur = cv2.GaussianBlur(work, (3, 3), 0)
        limit = min(work_height, work_width)
        if limit % 2 == 0:
            limit -= 1
        block = min(_odd(max(15, int(min(work_height, work_width) / 18))), max(3, limit))
        try:
            binary = cv2.adaptiveThreshold(
                blur,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY_INV,
                block,
                11,
            )
            binary = cv2.morphologyEx(
                binary, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8), iterations=1
            )
            # Connect letters into words and words into a line, but keep the
            # vertical kernel tiny so separate lines do not touch.
            kernel_width = max(11, int(round(work_width * 0.018)))
            kernel_height = max(1, int(round(work_height * 0.0025)))
            connected = cv2.dilate(
                binary,
                cv2.getStructuringElement(
                    cv2.MORPH_RECT, (kernel_width, kernel_height)
                ),
                iterations=1,
            )
            contours, _ = cv2.findContours(
                connected, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
        except cv2.error:
            return []

        boxes: list[tuple[int, int, int, int]] = []
        for contour in contours:
            x, y, box_width, box_height = cv2.boundingRect(contour)
            if box_width < max(10, int(work_width * 0.008)):
                continue
            if box_height < max(6, int(work_height * 0.0035)):
                continue
            if box_height > int(work_height * 0.42):
                continue
            if box_width < box_height * 1.05:
                continue
            if box_width * box_height > work_width * work_height * 0.30:
                continue
            x0 = int(round(x / detection_scale))
            y0 = int(round(y / detection_scale))
            x1 = int(round((x + box_width) / detection_scale))
            y1 = int(round((y + box_height) / detection_scale))
            x0 = max(0, min(width - 1, x0))
            y0 = max(0, min(height - 1, y0))
            x1 = max(x0 + 1, min(width, x1))
            y1 = max(y0 + 1, min(height, y1))
            if x1 - x0 >= 4 and y1 - y0 >= 4:
                boxes.append((x0, y0, x1, y1))

        # Merge fragments that belong to the same physical line.  Sorting by
        # area first and keeping a generous cap prevents a noisy photo from
        # making the quadratic merge loop expensive; the page passes still
        # cover everything outside this recovery candidate set.
        boxes.sort(key=lambda item: (item[2] - item[0]) * (item[3] - item[1]), reverse=True)
        merged = boxes[:400]
        changed = True
        while changed:
            changed = False
            for first in range(len(merged)):
                for second in range(first + 1, len(merged)):
                    ax0, ay0, ax1, ay1 = merged[first]
                    bx0, by0, bx1, by1 = merged[second]
                    overlap_y = max(0, min(ay1, by1) - max(ay0, by0))
                    min_height = max(1, min(ay1 - ay0, by1 - by0))
                    gap_x = max(0, bx0 - ax1, ax0 - bx1)
                    if (
                        overlap_y / min_height >= 0.55
                        and gap_x <= max(12, int(min_height * 3.0))
                    ):
                        merged[first] = (
                            min(ax0, bx0),
                            min(ay0, by0),
                            max(ax1, bx1),
                            max(ay1, by1),
                        )
                        del merged[second]
                        changed = True
                        break
                if changed:
                    break
        merged.sort(key=lambda box: (box[1], box[0]))
        return merged[:300]

    @staticmethod
    def _coverage_and_gap(
        spans: list[tuple[float, float]], start: float, end: float
    ) -> tuple[float, float]:
        """Union coverage and largest uncovered gap for a set of x spans."""
        width = max(1.0, end - start)
        clipped: list[tuple[float, float]] = []
        for span_start, span_end in spans:
            left = max(start, float(span_start))
            right = min(end, float(span_end))
            if right > left:
                clipped.append((left, right))
        if not clipped:
            return 0.0, width
        clipped.sort()
        covered = 0.0
        cursor = start
        largest_gap = 0.0
        for left, right in clipped:
            if left > cursor:
                largest_gap = max(largest_gap, left - cursor)
            if right > cursor:
                covered += right - max(left, cursor)
                cursor = right
        if cursor < end:
            largest_gap = max(largest_gap, end - cursor)
        return covered / width, largest_gap

    @staticmethod
    def _internal_gap(
        spans: list[tuple[float, float]], start: float, end: float
    ) -> float:
        """Largest uncovered gap *between* spans, ignoring the line ends.

        Ordinary word spaces are gaps too, so this deliberately excludes the
        leading and trailing space around the text.  It is used to detect a
        missing middle word inside an otherwise complete line.
        """
        clipped: list[tuple[float, float]] = []
        for span_start, span_end in spans:
            left = max(start, float(span_start))
            right = min(end, float(span_end))
            if right > left:
                clipped.append((left, right))
        if len(clipped) < 2:
            return 0.0
        clipped.sort()
        merged: list[list[float]] = [list(clipped[0])]
        for left, right in clipped[1:]:
            if left <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], right)
            else:
                merged.append([left, right])
        return max(
            (right[0] - left[1] for left, right in zip(merged, merged[1:])),
            default=0.0,
        )

    @staticmethod
    def _line_word_spans(
        line: dict[str, Any], transform: np.ndarray
    ) -> list[tuple[float, float]]:
        """Projected x extents of the actual word boxes on a line."""
        spans: list[tuple[float, float]] = []
        for word in line.get("words") or []:
            box = word.get("box")
            if not box:
                continue
            px0, _, px1, _ = TesseractEngine._project_box(box, transform)
            if px1 - px0 >= 1.0:
                spans.append((px0, px1))
        if not spans and line.get("box"):
            px0, _, px1, _ = TesseractEngine._project_box(line["box"], transform)
            spans.append((px0, px1))
        return spans

    @staticmethod
    def _matches_existing(
        box: tuple[int, int, int, int],
        existing: list[tuple[tuple[float, float, float, float], dict[str, Any]]],
    ) -> bool:
        """True when a detected box overlaps a line Tesseract already found."""
        x0, y0, x1, y1 = box
        height = max(1.0, y1 - y0)
        for px0, py0, px1, py1 in (item[0] for item in existing):
            overlap_x = max(0.0, min(x1, px1) - max(x0, px0))
            overlap_y = max(0.0, min(y1, py1) - max(y0, py0))
            if overlap_x <= 0 or overlap_y <= 0:
                continue
            if overlap_y / max(1.0, min(height, py1 - py0)) >= 0.35:
                return True
        return False

    @staticmethod
    def _gap_threshold(height: float) -> float:
        """Largest word gap that still looks like ordinary spacing.

        Normal word spaces are only a fraction of the line height.  A gap near
        or above one line height usually means Tesseract dropped a short token
        such as ``&`` or a one-word column, so the line deserves a re-read.
        """
        return max(9.0, float(height) * 1.30)

    @classmethod
    def _needs_recovery(
        cls,
        box: tuple[int, int, int, int],
        existing: list[tuple[tuple[float, float, float, float], dict[str, Any]]],
        transform: np.ndarray | None = None,
    ) -> bool:
        x0, y0, x1, y1 = box
        width = max(1.0, x1 - x0)
        height = max(1.0, y1 - y0)
        matrix = transform if transform is not None else np.eye(3, dtype=np.float64)
        matches: list[tuple[tuple[float, float, float, float], dict[str, Any]]] = []
        word_spans: list[tuple[float, float]] = []
        for projected, line in existing:
            px0, py0, px1, py1 = projected
            overlap_x = max(0.0, min(x1, px1) - max(x0, px0))
            overlap_y = max(0.0, min(y1, py1) - max(y0, py0))
            if overlap_x <= 0 or overlap_y <= 0:
                continue
            if overlap_y / max(1.0, min(height, py1 - py0)) < 0.35:
                continue
            matches.append((projected, line))
            word_spans.extend(cls._line_word_spans(line, matrix))

        if not matches:
            return width >= 12 and height >= 5

        # OpenCV detection boxes include the horizontal dilation used to join
        # letters into words, so a tight OCR box can look several percent
        # narrower than the detected line.  Trim that padding before judging
        # coverage; otherwise a complete single-word line is re-read forever.
        trim = min(width * 0.06, height * 0.45)
        left = x0 + trim
        right = x1 - trim
        if right - left < 4.0:
            left, right = float(x0), float(x1)
        effective_width = max(1.0, right - left)

        # Line-level union coverage catches truncation at either end and split
        # fragments.  It intentionally does not use word spans, because normal
        # inter-word gaps would otherwise make every ordinary sentence look
        # incomplete.
        coverage, _ = cls._coverage_and_gap(
            [(item[0][0], item[0][2]) for item in matches], left, right
        )
        if coverage < 0.84 and effective_width >= max(10.0, height * 1.5):
            return True

        # A line can span the full detection box while still missing a middle
        # word.  A gap much wider than a normal word space is the signal here.
        if word_spans:
            gap = cls._internal_gap(word_spans, left, right)
            if gap > cls._gap_threshold(height) and effective_width > max(
                24.0, height * 2.5
            ):
                return True

        # A complete line with very low confidence is still worth a second
        # reading.  The old expected-word-count heuristic is deliberately gone:
        # it re-read wide display lines such as "GRIND" even though one word
        # filled the whole line.
        if effective_width > max(18.0, height * 2.5):
            for _, line in matches:
                if float(line.get("confidence", 0.0)) < 22.0:
                    return True
        return False

    @classmethod
    def _lines_cover_quality(
        cls,
        lines: list[dict[str, Any]],
        transform: np.ndarray,
        box: tuple[int, int, int, int],
    ) -> tuple[float, float]:
        """Coverage of a detected line box plus its largest internal word gap."""
        x0, y0, x1, y1 = box
        spans: list[tuple[float, float]] = []
        word_spans: list[tuple[float, float]] = []
        for line in lines:
            if not line.get("box"):
                continue
            px0, py0, px1, py1 = cls._project_box(line["box"], transform)
            if py1 < y0 or py0 > y1:
                continue
            if px1 < x0 or px0 > x1:
                continue
            spans.append((px0, px1))
            word_spans.extend(cls._line_word_spans(line, transform))
        coverage, _ = cls._coverage_and_gap(spans, x0, x1)
        gap = cls._internal_gap(word_spans, x0, x1) if word_spans else 0.0
        return coverage, gap

    def _ocr_line_crop(
        self,
        variants: list[tuple[str, np.ndarray]],
        box: tuple[int, int, int, int],
        command: Path,
        tessdata: Path | None,
        lang: str,
        image_path: Path,
        transform: np.ndarray,
        original_size: tuple[int, int],
        deadline: float | None = None,
    ) -> list[dict[str, Any]]:
        x0, y0, x1, y1 = box
        height = max(1, y1 - y0)
        pad_x = max(4, int(round(height * 0.55)))
        pad_y = max(2, int(round(height * 0.30)))
        image_height, image_width = variants[0][1].shape[:2]
        crop_x0 = max(0, x0 - pad_x)
        crop_y0 = max(0, y0 - pad_y)
        crop_x1 = min(image_width, x1 + pad_x)
        crop_y1 = min(image_height, y1 + pad_y)
        if crop_x1 - crop_x0 < 4 or crop_y1 - crop_y0 < 4:
            return []

        border = max(8, int(round(height * 0.50)))
        offset = (crop_x0 - border, crop_y0 - border)
        best: list[dict[str, Any]] = []
        best_score = -1.0
        for _, variant in variants:
            remaining = deadline - time.monotonic() if deadline is not None else 120.0
            if remaining <= 0.5:
                break
            crop = variant[crop_y0:crop_y1, crop_x0:crop_x1]
            if crop.size == 0:
                continue
            border_value = 255 if float(np.mean(crop)) >= 128.0 else 0
            try:
                padded = cv2.copyMakeBorder(
                    crop,
                    border,
                    border,
                    border,
                    border,
                    cv2.BORDER_CONSTANT,
                    value=border_value,
                )
            except cv2.error:
                continue
            for psm in (7, 6):
                remaining = deadline - time.monotonic() if deadline is not None else 120.0
                if remaining <= 0.5:
                    break
                try:
                    payload = self._run_tesseract(
                        padded,
                        command,
                        tessdata,
                        lang,
                        psm,
                        image_path,
                        timeout=min(120.0, remaining),
                    )
                except OcrError:
                    continue
                lines = self._parse_tsv(
                    payload, transform, original_size, offset=offset
                )
                lines = [line for line in lines if self._plausible_line(line)]
                score = self._result_score(lines)
                if score > best_score:
                    best, best_score = lines, score
                # Tesseract's single-line mode can still stop after the first
                # word on a wide line.  Only stop early when the recovered
                # boxes cover most of the detected line and do not leave a
                # suspicious gap where a middle word should be.
                coverage, gap = self._lines_cover_quality(lines, transform, box)
                if (
                    lines
                    and self._mean_confidence(lines) >= 48.0
                    and score >= 5.0
                    and coverage >= 0.82
                    and gap <= self._gap_threshold(height)
                ):
                    break
            coverage, gap = self._lines_cover_quality(best, transform, box)
            if (
                best
                and self._mean_confidence(best) >= 48.0
                and best_score >= 5.0
                and coverage >= 0.82
                and gap <= self._gap_threshold(height)
            ):
                break
        return best

    @classmethod
    def _usable_recovered_line(
        cls,
        line: dict[str, Any],
        existing: list[tuple[tuple[float, float, float, float], dict[str, Any]]],
        image_size: tuple[int, int],
    ) -> bool:
        """Reject margin noise and multi-row crops from line recovery.

        Recovery is allowed to improve a page pass, but a crop around a stray
        mark or between two text rows can return a short confident-looking
        fragment.  Such fragments used to bridge two real baselines during
        fragment stitching and replace good text.  A recovered line is only
        trusted when it is plausibly a single visual row at the document's
        normal scale, and short fragments need real confidence.
        """
        if not cls._plausible_line(line):
            return False
        box = line.get("box") or {}
        try:
            width = max(1.0, float(box.get("width", 1.0)))
            height = max(1.0, float(box.get("height", 1.0)))
            confidence = float(line.get("confidence") or 0.0)
        except (TypeError, ValueError):
            return False

        word_centres: list[float] = []
        word_heights: list[float] = []
        for word in line.get("words") or []:
            word_box = word.get("box") or {}
            if not word_box:
                continue
            try:
                word_height = max(1.0, float(word_box.get("height", 1.0)))
                word_centres.append(
                    float(word_box.get("y", 0.0)) + word_height / 2.0
                )
                word_heights.append(word_height)
            except (TypeError, ValueError):
                continue
        if len(word_centres) >= 2:
            median_word_height = cls._median_value(word_heights)
            spread = max(word_centres) - min(word_centres)
            if spread > max(4.0, median_word_height * 0.55):
                # The crop includes two physical rows.  PSM 7 silently
                # concatenated them into one "line"; keep the page-pass result.
                return False

        existing_heights = [
            max(1.0, float((known.get("box") or {}).get("height", 1.0)))
            for _, known in existing
        ]
        median_height = cls._median_value(existing_heights)
        if median_height > 0 and not (
            median_height * 0.55 <= height <= median_height * 1.80
        ):
            # A 7 px speck or a 40 px two-row block is not a normal line here.
            return False

        content = _content_count(str(line.get("text") or ""))
        if content <= 0:
            return False
        if content <= 8 and confidence < 55.0:
            # Margin/toolbar fragments are short and only weakly confident.
            return False
        image_width, image_height = int(image_size[0]), int(image_size[1])
        x0, y0, x1, y1 = cls._box_values(box)
        if (
            x0 <= image_width * 0.02 or x1 >= image_width * 0.98
        ) and width < height * 3.0 and confidence < 70.0:
            # Narrow edge fragments are page borders, scan specks, or browser
            # chrome.  Genuine page-edge text is either wider or confident.
            return False
        return True

    def _recover_lines(
        self,
        detection_image: np.ndarray,
        variants: list[tuple[str, np.ndarray]],
        lines: list[dict[str, Any]],
        command: Path,
        tessdata: Path | None,
        lang: str,
        image_path: Path,
        transform: np.ndarray,
        original_size: tuple[int, int],
    ) -> list[dict[str, Any]]:
        if not OCR_LINE_RECOVERY:
            return lines
        max_lines = max(0, int(OCR_RECOVERY_MAX_LINES))
        if max_lines <= 0:
            return lines
        try:
            boxes = self._detect_text_lines(detection_image)
            # A mostly light page can still contain a dark banner, and a dark
            # photo can contain a light panel.  Run the opposite polarity too;
            # duplicates are folded together before the budget is applied.
            opposite = self._detect_text_lines(
                detection_image, invert=float(np.mean(detection_image)) >= 118.0
            )
            boxes = self._merge_recovery_boxes(boxes, opposite)
        except cv2.error:
            log.debug("Text-line detection failed", exc_info=True)
            return lines
        if not boxes:
            return lines

        forward = transform if transform is not None else np.eye(3, dtype=np.float64)
        existing = [
            (self._project_box(line["box"], forward), line)
            for line in lines
            if line.get("box")
        ]
        # Whole lines the page pass missed come first.  If the recovery budget
        # runs out on a dense page, re-reading an already recognised line can
        # wait; an invisible line cannot be edited at all until it is found.
        boxes.sort(
            key=lambda item: (
                1 if self._matches_existing(item, existing) else 0,
                item[1],
                item[0],
            )
        )
        recovered: list[dict[str, Any]] = []
        examined = 0
        started = time.monotonic()
        deadline = started + OCR_RECOVERY_BUDGET if OCR_RECOVERY_BUDGET > 0 else None
        for box in boxes:
            if examined >= max_lines:
                break
            if deadline is not None and time.monotonic() > deadline:
                log.info("Line recovery budget reached after %s crop(s)", examined)
                break
            if not self._needs_recovery(box, existing, forward):
                continue
            examined += 1
            found = self._ocr_line_crop(
                variants,
                box,
                command,
                tessdata,
                lang,
                image_path,
                transform,
                original_size,
                deadline=deadline,
            )
            for line in found:
                if not self._plausible_line(line):
                    continue
                if not self._usable_recovered_line(line, existing, original_size):
                    continue
                recovered.append(line)
                existing.append((self._project_box(line["box"], forward), line))

        if recovered:
            log.info(
                "Line recovery examined %s crop(s) and recovered %s line(s)",
                examined,
                len(recovered),
            )
            lines = self._merge_line_sets(lines, recovered)
        return lines

    @staticmethod
    def _clean_stderr(value: str | None) -> str:
        if not value:
            return ""
        rows = [line.strip() for line in value.splitlines() if line.strip()]
        rows = [line for line in rows if "Estimating resolution" not in line]
        return " ".join(rows[-3:])[:300]


def rectify_photo(image: Image.Image) -> Image.Image:
    """Return a straightened working copy of a photographed page.

    OCR boxes must live in the same coordinate space as the image that is
    rendered later.  Rectifying only inside the OCR pass would leave edits
    horizontal on a skewed photo, so the straightened version is stored as the
    document's working image.  If no reliable page quad or skew is found the
    original image is returned unchanged.
    """
    if not PHOTO_CORRECT:
        return image
    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    changed = False

    quad = TesseractEngine._find_document_quad(gray)
    if quad is not None:
        try:
            warped_gray, matrix = TesseractEngine._warp_document(gray, quad)
            rgb = cv2.warpPerspective(
                rgb,
                matrix,
                (warped_gray.shape[1], warped_gray.shape[0]),
                flags=cv2.INTER_CUBIC,
                borderValue=(255, 255, 255),
            )
            gray = warped_gray
            changed = True
        except cv2.error:
            log.debug("Photo perspective correction failed", exc_info=True)

    angle = TesseractEngine._estimate_skew(gray)
    if angle:
        try:
            deskewed_gray, matrix3 = TesseractEngine._deskew(gray, angle)
            rgb = cv2.warpAffine(
                rgb,
                matrix3[:2],
                (deskewed_gray.shape[1], deskewed_gray.shape[0]),
                flags=cv2.INTER_CUBIC,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=(255, 255, 255),
            )
            changed = True
        except cv2.error:
            log.debug("Photo deskew failed", exc_info=True)

    if not changed:
        return image
    return Image.fromarray(np.ascontiguousarray(rgb))


engine = TesseractEngine()
