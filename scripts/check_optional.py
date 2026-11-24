#!/usr/bin/env python3
"""Report optional Tesseract language packs and script fonts.

The guided installers call this after the Python environment is ready.  It only
prints package-manager or font-installation instructions; it never installs
anything.  Missing optional support does not make InkPatch unusable, so the
installers do not treat a nonzero exit as a failed installation.

Exit status: 0 when every documented optional language pack and script-font
group is present; 1 when one is missing or could not be checked.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]

OPTIONAL_LANGUAGES = ("deu", "chi_sim", "chi_tra", "hin", "kan")
LANGUAGE_LABELS = {
    "deu": "German",
    "chi_sim": "Chinese (Simplified)",
    "chi_tra": "Chinese (Traditional)",
    "hin": "Hindi",
    "kan": "Kannada",
}
LANGUAGE_PACKAGES = {
    "debian": {
        "deu": "tesseract-ocr-deu",
        "chi_sim": "tesseract-ocr-chi-sim",
        "chi_tra": "tesseract-ocr-chi-tra",
        "hin": "tesseract-ocr-hin",
        "kan": "tesseract-ocr-kan",
    },
    "fedora": {
        "deu": "tesseract-langpack-deu",
        "chi_sim": "tesseract-langpack-chi_sim",
        "chi_tra": "tesseract-langpack-chi_tra",
        "hin": "tesseract-langpack-hin",
        "kan": "tesseract-langpack-kan",
    },
    "arch": {
        "deu": "tesseract-data-deu",
        "chi_sim": "tesseract-data-chi_sim",
        "chi_tra": "tesseract-data-chi_tra",
        "hin": "tesseract-data-hin",
        "kan": "tesseract-data-kan",
    },
    "opensuse": {
        "deu": "tesseract-ocr-traineddata-deu",
        "chi_sim": "tesseract-ocr-traineddata-chi_sim",
        "chi_tra": "tesseract-ocr-traineddata-chi_tra",
        "hin": "tesseract-ocr-traineddata-hin",
        "kan": "tesseract-ocr-traineddata-kan",
    },
    "alpine": {
        "deu": "tesseract-ocr-data-deu",
        "chi_sim": "tesseract-ocr-data-chi_sim",
        "chi_tra": "tesseract-ocr-data-chi_tra",
        "hin": "tesseract-ocr-data-hin",
        "kan": "tesseract-ocr-data-kan",
    },
    "openbsd": {
        "deu": "tesseract-deu",
        "chi_sim": "tesseract-chi_sim",
        "chi_tra": "tesseract-chi_tra",
        "hin": "tesseract-hin",
        "kan": "tesseract-kan",
    },
}
LANGUAGE_COMMANDS = {
    "debian": "sudo apt install -y",
    "fedora": "sudo dnf install -y",
    "arch": "sudo pacman -S --needed",
    "opensuse": "sudo zypper install -y",
    "alpine": "sudo apk add",
    "openbsd": "doas pkg_add",
}
FONT_GROUPS = (
    ("CJK (Chinese/Japanese/Korean)", "CJK_FONTS"),
    ("Devanagari (Hindi)", "DEVANAGARI_FONTS"),
    ("Kannada", "KANNADA_FONTS"),
)
_LANGUAGE_LINE = re.compile(r"^[A-Za-z0-9_+-]+$")


def _candidate_tesseract(explicit: str | None) -> list[Path]:
    candidates: list[Path] = []
    raw = (explicit or "").strip() or os.environ.get("TESSERACT_CMD", "").strip()
    if raw:
        path = Path(raw).expanduser()
        if path.is_dir():
            candidates.extend(
                [
                    path / "tesseract",
                    path / "tesseract.exe",
                    path / "AppRun",
                ]
            )
        else:
            candidates.append(path)
    on_path = shutil.which("tesseract")
    if on_path:
        candidates.append(Path(on_path))
    candidates.extend(
        [
            Path("/usr/bin/tesseract"),
            Path("/usr/local/bin/tesseract"),
            Path("/opt/homebrew/bin/tesseract"),
            Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        ]
    )
    return candidates


def _find_tesseract(explicit: str | None = None) -> Path | None:
    for candidate in _candidate_tesseract(explicit):
        if candidate.is_file():
            return candidate
    raw = (explicit or "").strip() or os.environ.get("TESSERACT_CMD", "").strip()
    if raw:
        found = shutil.which(raw)
        if found:
            return Path(found)
    return None


def _parse_languages(output: str) -> set[str]:
    """Return language codes from ``tesseract --list-langs`` output."""
    return {
        line.strip()
        for line in output.splitlines()
        if _LANGUAGE_LINE.fullmatch(line.strip())
    }


def _installed_languages(command: Path | None) -> set[str] | None:
    if command is None:
        return None
    try:
        result = subprocess.run(
            [str(command), "--list-langs"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return _parse_languages(result.stdout)


def _language_report(
    family: str,
    missing: Iterable[str],
    available: set[str] | None,
) -> list[str]:
    missing = [code for code in missing if code in LANGUAGE_LABELS]
    if not missing:
        return []

    labels = ", ".join(
        f"{code} ({LANGUAGE_LABELS[code]})" for code in missing
    )
    lines = [f"Optional Tesseract language data missing: {labels}."]
    if available is None:
        lines.append("Tesseract is not installed or its language list could not be read.")
        lines.append("Install Tesseract first, then add the language packs you need:")
    else:
        lines.append("Install the language packs you need:")

    if family == "macos":
        lines.append("  brew install tesseract-lang")
    elif family in LANGUAGE_PACKAGES:
        packages = [
            LANGUAGE_PACKAGES[family][code]
            for code in missing
            if code in LANGUAGE_PACKAGES[family]
        ]
        if packages:
            command = LANGUAGE_COMMANDS[family]
            lines.append(f"  {command} {' '.join(packages)}")
    elif family == "freebsd":
        lines.extend(
            [
                "  The FreeBSD tesseract package normally installs tesseract-data",
                "  with all language models. Reinstall the data package with:",
                "  sudo pkg install -y tesseract-data",
            ]
        )
    elif family == "dragonfly":
        lines.extend(
            [
                "  DragonFly DPorts follows FreeBSD. Install or reinstall the",
                "  Tesseract data package with:",
                "  sudo pkg install -y tesseract-data",
            ]
        )
    elif family == "netbsd":
        lines.extend(
            [
                "  Install or reinstall the pkgsrc Tesseract package, then add the",
                "  missing *.traineddata files to its tessdata directory:",
                "  sudo pkgin install tesseract",
            ]
        )
    else:
        lines.extend(
            [
                "  Re-run the Tesseract installer and select the missing language models,",
                "  or copy the matching *.traineddata files into",
                r'  "C:\Program Files\Tesseract-OCR\tessdata".',
            ]
        )
    return lines


def _font_groups() -> tuple[tuple[str, tuple[str, ...]], ...]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from app.imaging import CJK_FONTS, DEVANAGARI_FONTS, KANNADA_FONTS

    faces = {
        "CJK_FONTS": CJK_FONTS,
        "DEVANAGARI_FONTS": DEVANAGARI_FONTS,
        "KANNADA_FONTS": KANNADA_FONTS,
    }
    groups: list[tuple[str, tuple[str, ...]]] = []
    for label, key in FONT_GROUPS:
        variants = faces[key]
        names = tuple(variants[False] + variants[True])
        groups.append((label, names))
    return tuple(groups)


def _missing_font_groups(
    finder: Callable[[tuple[str, ...]], object | None] | None = None,
) -> list[str]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from app.fonts import find_font_by_names

    check = finder or find_font_by_names
    return [
        label
        for label, names in _font_groups()
        if check(names) is None
    ]


def _font_report(family: str, missing: Sequence[str]) -> list[str]:
    missing = list(missing)
    if not missing:
        return []

    lines = [f"Optional script fonts missing: {', '.join(missing)}."]
    if family == "debian":
        lines.extend(
            [
                "Install the matching Noto fonts with:",
                "  sudo apt install -y fonts-noto-cjk fonts-noto-core",
            ]
        )
    elif family == "fedora":
        lines.extend(
            [
                "Install the matching Noto fonts with:",
                "  sudo dnf install -y google-noto-sans-cjk-fonts "
                "google-noto-sans-devanagari-fonts google-noto-sans-kannada-fonts",
            ]
        )
    elif family == "arch":
        lines.extend(
            [
                "Install the matching Noto fonts with:",
                "  sudo pacman -S --needed noto-fonts noto-fonts-cjk",
            ]
        )
    elif family == "opensuse":
        lines.extend(
            [
                "Install the matching Noto fonts with:",
                "  sudo zypper install -y google-noto-sans-cjk-fonts "
                "noto-sans-devanagari-fonts noto-sans-kannada-fonts",
            ]
        )
    elif family == "alpine":
        lines.extend(
            [
                "Install the matching Noto fonts with:",
                "  sudo apk add font-noto-cjk font-noto-devanagari font-noto-kannada",
            ]
        )
    elif family == "freebsd":
        lines.extend(
            [
                "Install the matching Noto fonts with:",
                "  sudo pkg install -y noto-extra noto-sc noto-tc",
            ]
        )
    elif family == "dragonfly":
        lines.extend(
            [
                "Install the matching Noto fonts from DPorts with:",
                "  sudo pkg install -y noto-extra noto-sc noto-tc",
            ]
        )
    elif family == "openbsd":
        lines.extend(
            [
                "Install the Noto CJK fonts with:",
                "  doas pkg_add noto-cjk",
                "For Devanagari/Kannada, install a Noto font from packages or ports,",
                "or add a .ttf file to a folder listed by TEXTEDIT_FONT_DIRS.",
            ]
        )
    elif family == "netbsd":
        lines.extend(
            [
                "Install the matching Noto fonts with:",
                "  sudo pkgin install noto-fonts noto-cjk-fonts",
            ]
        )
    elif family == "macos":
        lines.extend(
            [
                "Install the matching Noto fonts with:",
                "  brew install --cask font-noto-sans-cjk "
                "font-noto-sans-devanagari font-noto-sans-kannada",
            ]
        )
    else:
        lines.extend(
            [
                "Add the matching Chinese, Hindi, and Kannada language features in",
                "Windows Settings -> Time & Language -> Language & region, or put",
                "Noto CJK/Devanagari/Kannada fonts in a folder listed by",
                "TEXTEDIT_FONT_DIRS and restart InkPatch.",
            ]
        )
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report missing optional Tesseract languages and script fonts."
    )
    parser.add_argument(
        "--family",
        choices=(
            "debian",
            "fedora",
            "arch",
            "opensuse",
            "alpine",
            "freebsd",
            "openbsd",
            "netbsd",
            "dragonfly",
            "macos",
            "windows",
        ),
        required=True,
        help="Target platform used to choose package-manager instructions.",
    )
    parser.add_argument(
        "--tesseract",
        default=None,
        help="Optional path to the Tesseract binary.",
    )
    args = parser.parse_args(argv)

    command = _find_tesseract(args.tesseract)
    available = _installed_languages(command)
    missing_languages = [
        code
        for code in OPTIONAL_LANGUAGES
        if available is None or code not in available
    ]

    lines = _language_report(args.family, missing_languages, available)
    try:
        missing_fonts = _missing_font_groups()
    except Exception:
        lines.extend(
            [
                "Optional script fonts could not be checked because the Python",
                "environment is incomplete. Install the matching Noto fonts for",
                "the scripts you need; see INSTALL.md.",
            ]
        )
    else:
        lines.extend(_font_report(args.family, missing_fonts))

    if not lines:
        print("Optional language and font support is installed.")
        return 0

    print("Optional language/font support is incomplete.")
    for line in lines:
        print(line)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
