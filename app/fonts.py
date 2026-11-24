"""Font discovery and matching helpers.

InkPatch ships a small, curated set of open-source Latin faces and also looks
at fonts installed on the machine.  The OCR style sampler compares the source
glyphs against several families instead of assuming every document uses
DejaVu.  That makes edits look much closer to the original when a screenshot
was taken on Windows/macOS, or when a photo uses a common web font.

Only faces that contain basic Latin glyphs are considered for Latin matching.
Script fallback fonts (CJK, Arabic, Hebrew, Devanagari, Kannada) are handled
separately by :func:`find_font_by_names`, because those often live in ``.ttc``
files with several faces inside one file.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from PIL import ImageFont

from .config import FONT_DIR

log = logging.getLogger(__name__)

# Files whose names contain one of these words are likely Latin text faces.
# Filtering before opening files keeps startup fast on systems with hundreds of
# Noto script fonts installed.
_COMMON_MARKERS = (
    "dejavu",
    "liberation",
    "notosans",
    "notoserif",
    "notomono",
    "freesans",
    "freeserif",
    "freemono",
    "ubuntu",
    "nimbus",
    "c059",
    "p052",
    "urw",
    "arial",
    "helvetica",
    "times",
    "courier",
    "georgia",
    "verdana",
    "tahoma",
    "calibri",
    "cambria",
    "segoe",
    "consolas",
    "roboto",
    "open sans",
    "opensans",
    "lato",
    "montserrat",
    "merriweather",
    "source sans",
    "sourcesans",
    "source serif",
    "sourceserif",
    "inter",
    "ibm plex",
    "ibmplex",
    "fira",
    "pt sans",
    "ptsans",
    "pt serif",
    "ptserif",
    "bitter",
    "crimson",
    "libre",
    "cantarell",
    "noto sans display",
    "notoserifdisplay",
    "notosansdisplay",
)

# Script-specific families are useful for drawing, but they are not good
# candidates for Latin typeface matching.  They are also numerous on Linux.
_EXCLUDED_MARKERS = (
    "arabic",
    "hebrew",
    "devanagari",
    "bengali",
    "tamil",
    "telugu",
    "kannada",
    "malayalam",
    "gujarati",
    "gurmukhi",
    "oriya",
    "odia",
    "sinhala",
    "khmer",
    "lao",
    "tibetan",
    "myanmar",
    "ethiopic",
    "armenian",
    "georgian",
    "cherokee",
    "adlam",
    "syriac",
    "thaana",
    "mongolian",
    "cjk",
    "thai",
    "emoji",
    "symbol",
    "dingbat",
    "ornament",
    "music",
    "math",
    "braille",
    "signwriting",
    "pahawh",
    "warang",
    "yezidi",
)

_FAMILY_PRIORITY = {
    "dejavu sans": 0,
    "dejavu serif": 0,
    "dejavu sans mono": 0,
    "liberation sans": 1,
    "liberation serif": 1,
    "liberation mono": 1,
    "liberation sans narrow": 2,
    "noto sans": 2,
    "noto serif": 2,
    "noto sans mono": 2,
    "noto mono": 2,
    "free sans": 3,
    "free serif": 3,
    "free mono": 3,
    "ubuntu": 4,
    "ubuntu sans": 4,
    "ubuntu mono": 4,
    "nimbus sans": 5,
    "nimbus roman": 5,
    "nimbus mono ps": 5,
    "c059": 6,
    "p052": 6,
    "arial": 1,
    "helvetica": 1,
    "times new roman": 1,
    "courier new": 1,
    "georgia": 2,
    "verdana": 2,
    "tahoma": 2,
    "calibri": 2,
    "cambria": 2,
    "segoe ui": 2,
    "consolas": 2,
    "roboto": 3,
    "open sans": 3,
    "lato": 3,
    "montserrat": 3,
    "merriweather": 3,
    "source sans 3": 3,
    "source sans pro": 3,
    "source serif 4": 3,
    "source serif pro": 3,
    "inter": 3,
    "ibm plex sans": 3,
    "ibm plex serif": 3,
    "ibm plex mono": 3,
    "fira sans": 3,
    "fira mono": 3,
    "pt sans": 3,
    "pt serif": 3,
    "bitter": 4,
    "crimson text": 4,
    "libre franklin": 4,
    "cantarell": 4,
    "noto sans display": 3,
    "noto serif display": 3,
}

_SYSTEM_FONT_DIRS = [
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path.home() / ".fonts",
    Path.home() / ".local" / "share" / "fonts",
    Path("/System/Library/Fonts"),
    Path("/Library/Fonts"),
    Path.home() / "Library" / "Fonts",
    Path("/system/fonts"),  # Android/Termux
    Path(r"C:\Windows\Fonts"),
    Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
]

_VARIANT_SUFFIX = {
    (False, False): "regular",
    (True, False): "bold",
    (False, True): "italic",
    (True, True): "bold-italic",
}

_VARIANT_LABEL = {
    (False, False): "Regular",
    (True, False): "Bold",
    (False, True): "Italic",
    (True, True): "Bold Italic",
}


@dataclass(frozen=True)
class FontFace:
    font_id: str
    path: Path
    index: int
    family: str
    category: str
    bold: bool
    italic: bool
    bundled: bool

    @property
    def label(self) -> str:
        return f"{self.family} {_VARIANT_LABEL[(self.bold, self.italic)]}".strip()


@dataclass(frozen=True)
class FontFamily:
    name: str
    category: str
    priority: int
    faces: dict[tuple[bool, bool], FontFace]

    def face(self, bold: bool = False, italic: bool = False) -> FontFace | None:
        exact = self.faces.get((bold, italic))
        if exact is not None:
            return exact
        # Prefer a close weight/slant before falling back to regular.
        for key in ((bold, False), (False, italic), (False, False)):
            face = self.faces.get(key)
            if face is not None:
                return face
        return None


def _slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value or "font"


def _extra_font_dirs() -> list[Path]:
    raw = os.environ.get("TEXTEDIT_FONT_DIRS", "").strip()
    if not raw:
        return []
    return [Path(item).expanduser() for item in raw.split(os.pathsep) if item.strip()]


def _is_common_filename(name: str) -> bool:
    lower = name.lower()
    if any(marker in lower for marker in _EXCLUDED_MARKERS):
        return False
    if "[" in name or "]" in name:
        # Variable fonts expose several named instances through one file.  PIL
        # can load them, but the default instance is often Thin/ExtraLight,
        # which would poison matching and rendering.
        return False
    return any(marker in lower for marker in _COMMON_MARKERS)


def _candidate_paths() -> Iterable[Path]:
    seen: set[Path] = set()

    def emit(path: Path) -> Iterable[Path]:
        try:
            resolved = path.resolve()
        except OSError:
            return []
        if resolved in seen:
            return []
        seen.add(resolved)
        return [resolved]

    for path in sorted(FONT_DIR.glob("*")):
        if path.suffix.lower() in {".ttf", ".otf"}:
            yield from emit(path)

    for directory in [*_SYSTEM_FONT_DIRS, *_extra_font_dirs()]:
        if not directory.is_dir():
            continue
        try:
            entries = sorted(directory.rglob("*"))
        except OSError:
            continue
        for path in entries:
            if path.suffix.lower() not in {".ttf", ".otf"}:
                continue
            if not _is_common_filename(path.name):
                continue
            yield from emit(path)


def _style_flags(style: str) -> tuple[bool, bool]:
    lower = style.lower()
    bold = any(word in lower for word in ("bold", "black", "heavy", "extrabold", "semibold"))
    italic = any(word in lower for word in ("italic", "oblique"))
    return bold, italic


def _category_for(family: str, filename: str) -> str:
    lower = f"{family} {filename}".lower()
    if "mono" in lower or "courier" in lower or "consolas" in lower:
        return "mono"
    if "serif" in lower and "sans" not in lower:
        return "serif"
    if any(word in lower for word in ("roman", "times", "georgia", "cambria", "c059", "p052", "bookman")):
        return "serif"
    return "sans"


def _family_priority(family: str) -> int:
    lower = family.lower()
    for name, priority in _FAMILY_PRIORITY.items():
        if lower == name or lower.startswith(name + " "):
            return priority
    return 20


def _has_latin(font: ImageFont.FreeTypeFont) -> bool:
    try:
        bbox = font.getbbox("Aa")
    except (OSError, ValueError):
        return False
    return bbox[2] > bbox[0] and bbox[3] > bbox[1]


def _read_face(path: Path) -> tuple[str, str, bool, bool] | None:
    try:
        font = ImageFont.truetype(str(path), size=18)
        family, style = font.getname()
    except (OSError, ValueError, RuntimeError):
        return None
    if not family or not _has_latin(font):
        return None
    family = family.split(",")[0].strip()
    if not family:
        return None
    bold, italic = _style_flags(style or "")
    # Some fonts report "Book" for regular and only put the weight in the
    # filename.  Combine both sources so file names like LiberationSans-Bold
    # are classified correctly.
    file_bold, file_italic = _style_flags(path.stem)
    return family, f"{style or ''} {path.stem}", bold or file_bold, italic or file_italic


def _build_registry() -> tuple[dict[str, FontFace], dict[str, FontFamily]]:
    faces_by_key: dict[tuple[str, bool, bool], tuple[Path, str, str, bool, bool]] = {}
    for path in _candidate_paths():
        parsed = _read_face(path)
        if parsed is None:
            continue
        family, style_text, bold, italic = parsed
        category = _category_for(family, path.name)
        key = (family, bold, italic)
        bundled = FONT_DIR in path.parents
        existing = faces_by_key.get(key)
        # A bundled face wins over a system duplicate; otherwise keep the first
        # path (which is deterministic because directories are sorted).
        if existing is None or (bundled and not existing[3]):
            faces_by_key[key] = (path, category, style_text, bundled, bold)

    faces: dict[str, FontFace] = {}
    families: dict[str, FontFamily] = {}
    used_ids: set[str] = set()

    ordered = sorted(
        faces_by_key.items(),
        key=lambda item: (
            _family_priority(item[0][0]),
            0 if item[1][3] else 1,
            item[0][0].lower(),
            item[0][1],
            item[0][2],
        ),
    )

    for (family, bold, italic), (path, category, _style_text, bundled, _flag) in ordered:
        base_id = f"{_slug(family)}-{_VARIANT_SUFFIX[(bold, italic)]}"
        font_id = base_id
        counter = 2
        while font_id in used_ids:
            font_id = f"{base_id}-{counter}"
            counter += 1
        used_ids.add(font_id)
        face = FontFace(
            font_id=font_id,
            path=path,
            index=0,
            family=family,
            category=category,
            bold=bold,
            italic=italic,
            bundled=bundled,
        )
        faces[font_id] = face

        entry = families.get(family)
        if entry is None:
            entry = FontFamily(
                name=family,
                category=category,
                priority=_family_priority(family),
                faces={},
            )
            families[family] = entry
        # FontFamily is frozen, so update the mutable dict in place.
        entry.faces[(bold, italic)] = face

    # A missing/broken font directory should never leave the renderer without a
    # fallback.  The files are part of the repository, so this is only a guard
    # for unusual installations.
    if not faces:
        log.warning("No usable fonts were found in %s", FONT_DIR)
        fallback = FONT_DIR / "DejaVuSans.ttf"
        face = FontFace(
            font_id="dejavu-sans-regular",
            path=fallback,
            index=0,
            family="DejaVu Sans",
            category="sans",
            bold=False,
            italic=False,
            bundled=True,
        )
        faces[face.font_id] = face
        families[face.family] = FontFamily(
            name=face.family,
            category=face.category,
            priority=0,
            faces={(False, False): face},
        )

    return faces, families


@lru_cache(maxsize=1)
def _registry() -> tuple[dict[str, FontFace], dict[str, FontFamily]]:
    return _build_registry()


def all_faces() -> tuple[FontFace, ...]:
    faces, _ = _registry()
    return tuple(faces.values())


def face_by_id(font_id: str | None) -> FontFace | None:
    if not font_id:
        return None
    faces, _ = _registry()
    return faces.get(font_id)


def family(name: str) -> FontFamily | None:
    _, families = _registry()
    found = families.get(name)
    if found is not None:
        return found
    lower = name.strip().lower()
    for key, item in families.items():
        if key.lower() == lower:
            return item
    return None


def _families_for_category(category: str) -> list[FontFamily]:
    _, families = _registry()
    candidates = [
        item
        for item in families.values()
        if item.category == category
        and item.face(False, False) is not None
        and _catalog_family(item)
    ]
    candidates.sort(key=lambda item: (item.priority, item.name.lower()))
    return candidates


def match_faces(category: str, limit: int = 4) -> list[FontFace]:
    """Return regular faces used for the first pass of typeface matching."""
    result: list[FontFace] = []
    for item in _families_for_category(category)[:limit]:
        face = item.face(False, False)
        if face is not None:
            result.append(face)
    return result


def variants_for_face(face: FontFace) -> list[FontFace]:
    """Return the regular/bold/italic faces of the same family."""
    item = family(face.family)
    if item is None:
        return [face]
    result: list[FontFace] = []
    for key in ((False, False), (True, False), (False, True), (True, True)):
        variant = item.faces.get(key)
        if variant is not None:
            result.append(variant)
    return result or [face]


def default_face(category: str = "sans", bold: bool = False, italic: bool = False) -> FontFace:
    families = _families_for_category(category)
    # Prefer an exact variant anywhere in the category.  Otherwise a family
    # without italics (for example DejaVu Serif) would win on priority and
    # silently ignore a requested italic face.
    for item in families:
        face = item.faces.get((bold, italic))
        if face is not None:
            return face
    # No exact variant exists: accept the closest weight/slant.
    for item in families:
        face = item.face(bold, italic)
        if face is not None:
            return face
    # Absolute last resort: any face at all.
    faces = all_faces()
    if faces:
        return faces[0]
    # The registry guard above should make this unreachable.
    return FontFace(
        font_id="dejavu-sans-regular",
        path=FONT_DIR / "DejaVuSans.ttf",
        index=0,
        family="DejaVu Sans",
        category="sans",
        bold=False,
        italic=False,
        bundled=True,
    )


def _catalog_family(item: FontFamily) -> bool:
    """Keep the font picker focused on useful Latin document faces.

    Linux systems often ship dozens of Noto script faces whose family names
    start with "Noto Sans".  They are useful for drawing CJK/Arabic text but
    make a terrible Latin typeface picker, so only curated families (or
    bundled faces) are exposed here.
    """
    if any(face.bundled for face in item.faces.values()):
        return True
    lower = item.name.lower()
    return lower in _FAMILY_PRIORITY and _FAMILY_PRIORITY[lower] <= 6


def catalog() -> list[dict[str, object]]:
    """Compact list for the API and diagnostics."""
    _, families = _registry()
    items: list[dict[str, object]] = []
    for item in sorted(families.values(), key=lambda value: (value.priority, value.name.lower())):
        if not _catalog_family(item):
            continue
        items.append(
            {
                "family": item.name,
                "category": item.category,
                "priority": item.priority,
                "variants": [
                    face.font_id
                    for face in sorted(
                        item.faces.values(),
                        key=lambda face: (face.bold, face.italic),
                    )
                ],
            }
        )
    return items


@lru_cache(maxsize=32)
def find_font_by_names(names: tuple[str, ...]) -> Path | None:
    """Find a script fallback font by filename.

    The search covers the common Linux, macOS and Windows font directories.
    Only a handful of direct children are inspected for the usual Noto/Droid
    names; a recursive search is used as a fallback for package layouts that
    put fonts one level deeper.
    """
    for directory in [*_SYSTEM_FONT_DIRS, *_extra_font_dirs()]:
        if not directory.is_dir():
            continue
        for name in names:
            candidate = directory / name
            if candidate.is_file():
                return candidate
        for name in names:
            try:
                matches = list(directory.rglob(name))
            except OSError:
                continue
            for candidate in matches:
                if candidate.is_file():
                    return candidate
    return None
