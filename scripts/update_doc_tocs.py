#!/usr/bin/env python3
"""Keep a table of contents at the top of every root Markdown document.

Run from the repository root:

    python3 scripts/update_doc_tocs.py
    python3 scripts/update_doc_tocs.py --check

Only level-two headings are listed, so the contents stay readable even in the
long instruction logs. Fenced code blocks are ignored so shell comments and
example snippets are never mistaken for headings.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOC_HEADING = "## Table of contents"

H1_RE = re.compile(r"^#\s+(.+?)\s*$")
H2_RE = re.compile(r"^##\s+(.+?)\s*$")


def _fence_marker(stripped: str) -> str:
    if stripped.startswith("```"):
        return "```"
    if stripped.startswith("~~~"):
        return "~~~"
    return ""


def _lines_outside_fences(lines: list[str]):
    """Yield (index, line) for lines that are not inside fenced code."""
    fence = ""
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if fence:
            if stripped.startswith(fence):
                fence = ""
            continue
        marker = _fence_marker(stripped)
        if marker:
            fence = marker
            continue
        yield index, line


def plain_heading(text: str) -> str:
    """Strip Markdown syntax so the heading can be used as link text."""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = text.replace("`", "")
    text = re.sub(r"[*_~]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def slugify(text: str) -> str:
    """Approximate GitHub's heading anchor algorithm."""
    text = plain_heading(text).lower()
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    return text.replace(" ", "-")


def unique_slug(text: str, seen: dict[str, int]) -> str:
    base = slugify(text)
    count = seen.get(base, 0)
    seen[base] = count + 1
    return base if count == 0 else f"{base}-{count}"


def toc_entries(lines: list[str]) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    seen: dict[str, int] = {}
    started = False
    for _, line in _lines_outside_fences(lines):
        if not started:
            if H1_RE.match(line):
                started = True
            continue
        match = H2_RE.match(line)
        if not match:
            continue
        heading = plain_heading(match.group(1))
        if heading.lower() == "table of contents":
            continue
        entries.append((heading, unique_slug(heading, seen)))
    return entries


def strip_toc(lines: list[str]) -> list[str]:
    """Remove an existing generated contents section, if present.

    Only the heading and the contiguous list are removed; any introduction that
    follows the contents must stay in place.
    """
    toc_index = None
    for index, line in _lines_outside_fences(lines):
        if line.strip() == TOC_HEADING:
            toc_index = index
            break
    if toc_index is None:
        return lines

    end = toc_index + 1
    while end < len(lines):
        stripped = lines[end].strip()
        if stripped and not stripped.startswith("- "):
            break
        end += 1
    del lines[toc_index:end]
    return lines


def upsert_toc(text: str) -> str:
    """Return *text* with a current table of contents directly below its H1."""
    trailing_newline = text.endswith("\n")
    lines = text.splitlines()
    lines = strip_toc(lines)

    h1_index = None
    for index, line in _lines_outside_fences(lines):
        if H1_RE.match(line):
            h1_index = index
            break
    if h1_index is None:
        return text

    entries = toc_entries(lines)
    if not entries:
        return text

    # Keep exactly one blank line between the title and the fresh contents.
    while h1_index + 1 < len(lines) and not lines[h1_index + 1].strip():
        del lines[h1_index + 1]

    block = ["", TOC_HEADING, ""]
    block.extend(f"- [{heading}](#{anchor})" for heading, anchor in entries)
    block.append("")
    lines[h1_index + 1 : h1_index + 1] = block

    result = "\n".join(lines)
    if trailing_newline:
        result += "\n"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report documents whose table of contents is out of date",
    )
    args = parser.parse_args()

    changed: list[str] = []
    for path in sorted(ROOT.glob("*.md")):
        original = path.read_text(encoding="utf-8")
        updated = upsert_toc(original)
        if updated == original:
            continue
        changed.append(path.name)
        if not args.check:
            path.write_text(updated, encoding="utf-8")

    if args.check:
        if changed:
            print(
                "Out-of-date table of contents: " + ", ".join(changed),
                file=sys.stderr,
            )
            return 1
        print("All document tables of contents are current.")
        return 0

    if changed:
        print("Updated: " + ", ".join(changed))
    else:
        print("All document tables of contents are current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
