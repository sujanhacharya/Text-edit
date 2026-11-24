"""Filesystem-backed document store.

One folder per document:

    data/<uuid>/image.png   normalized source image (max 2400 px)
    data/<uuid>/preview.png last rendered preview
    data/<uuid>/doc.json    metadata, OCR lines and word boxes

No database is needed for a single-user local tool, and keeping the images on
disk means the server can be restarted without losing open work.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .auth import token_hash, token_matches
from .config import DATA_DIR, DEMO_IMAGE, DOCUMENT_TTL_HOURS, MAX_SIDE, PHOTO_CORRECT
from .imaging import (
    ImageError,
    infer_alignment,
    limit_size,
    merge_compatible_segments,
    open_image,
    refine_line_decorations,
    render_document,
    sample_line_style,
    save_png,
    split_styled_lines,
    stabilize_monospaced_document,
    stabilize_typefaces,
)
from .ocr import OcrError, OcrUnavailable, engine, rectify_photo

log = logging.getLogger(__name__)


class DocumentNotFound(KeyError):
    pass


class DocumentStore:
    def __init__(self, root: Path = DATA_DIR) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_lock = threading.Lock()

    # ----------------------------------------------------------------- helpers

    def _lock_for(self, doc_id: str) -> threading.Lock:
        with self._locks_lock:
            return self._locks.setdefault(doc_id, threading.Lock())

    def _dir(self, doc_id: str) -> Path:
        try:
            parsed = uuid.UUID(doc_id)
        except (ValueError, AttributeError) as exc:
            raise DocumentNotFound(doc_id) from exc
        if str(parsed) != doc_id:
            raise DocumentNotFound(doc_id)
        directory = self.root / doc_id
        if not directory.is_dir():
            raise DocumentNotFound(doc_id)
        return directory

    def _doc_path(self, doc_id: str) -> Path:
        return self._dir(doc_id) / "doc.json"

    def _load(self, doc_id: str) -> dict[str, Any]:
        path = self._doc_path(doc_id)
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise DocumentNotFound(doc_id) from exc

    def _save(self, doc: dict[str, Any]) -> None:
        doc["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        path = self._doc_path(doc["id"])
        tmp = path.with_suffix(".tmp.json")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(doc, handle, ensure_ascii=False, indent=2)
        tmp.replace(path)
        # Cleanup uses the folder mtime as "last changed".  Rename normally
        # updates it, but make that explicit so expiry works on every platform.
        try:
            os.utime(path.parent, None)
        except OSError:
            pass

    @staticmethod
    def _clean_name(value: str) -> str:
        name = Path(value or "image").name
        name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
        return (name or "image")[:120]

    # ------------------------------------------------------------------ public

    def create(
        self,
        raw: bytes,
        filename: str = "image.png",
        lang: str = "eng",
        psm: int = 3,
        source: str = "upload",
        access_token: str | None = None,
    ) -> dict[str, Any]:
        image = open_image(raw)
        image = limit_size(image, MAX_SIDE)
        if min(image.size) < 16:
            raise ImageError("That image is too small.")

        # Store the straightened working image, not just a rectified OCR copy.
        # Word boxes and the rendered preview then share one coordinate space.
        original_image = image
        try:
            straightened = rectify_photo(image)
        except Exception:
            # Photo correction is an enhancement.  A failure here should never
            # stop the upload from being editable.
            log.exception("Photo rectification failed; keeping the original image")
            straightened = original_image
        photo_rectified = straightened is not original_image
        # Perspective correction adds a small white margin, so enforce the
        # configured maximum again before the image is stored.
        image = limit_size(straightened, MAX_SIDE)

        doc_id = str(uuid.uuid4())
        directory = self.root / doc_id
        directory.mkdir(parents=True, exist_ok=False)
        image_path = directory / "image.png"
        try:
            save_png(image, image_path)
            lines, warning = self._ocr_lines(
                image_path, image, lang, psm, rectify=not photo_rectified
            )
            doc: dict[str, Any] = {
                "id": doc_id,
                "name": self._clean_name(filename),
                "source": source,
                "width": image.width,
                "height": image.height,
                "lang": lang,
                "psm": int(psm),
                "lines": lines,
                "photo_rectified": photo_rectified,
                "engine_version": engine.status().get("version"),
                "warning": warning,
                "preview_version": 0,
                "has_preview": False,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            if access_token:
                # Only a one-way digest is persisted.  The raw token lives in
                # the browser's HttpOnly cookie and is never returned by the API.
                doc["access_token_hash"] = token_hash(access_token)
            self._save(doc)
            return self.public(doc)
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def create_demo(self, lang: str = "eng", access_token: str | None = None) -> dict[str, Any]:
        if not DEMO_IMAGE.is_file():
            raise FileNotFoundError("Demo image is missing.")
        return self.create(
            DEMO_IMAGE.read_bytes(),
            "coffee-poster-demo.png",
            lang=lang,
            psm=3,
            source="demo",
            access_token=access_token,
        )

    def get(self, doc_id: str) -> dict[str, Any]:
        return self.public(self._load(doc_id))

    def raw(self, doc_id: str) -> dict[str, Any]:
        return self._load(doc_id)

    def verify_access(self, doc_id: str, token: str | None) -> bool:
        """Return whether ``token`` is the cookie for this document.

        Documents created before per-document cookies, or by internal corpus
        scripts without a token, have no hash and therefore deny API access.
        """
        doc = self._load(doc_id)
        return token_matches(token, doc.get("access_token_hash"))

    def image_path(self, doc_id: str) -> Path:
        path = self._dir(doc_id) / "image.png"
        if not path.is_file():
            raise DocumentNotFound(doc_id)
        return path

    def preview_path(self, doc_id: str) -> Path | None:
        path = self._dir(doc_id) / "preview.png"
        return path if path.is_file() else None

    def public(self, doc: dict[str, Any]) -> dict[str, Any]:
        lines: list[dict[str, Any]] = []
        for line in doc.get("lines", []):
            lines.append(
                {
                    "id": line["id"],
                    "text": line["text"],
                    "box": line["box"],
                    "confidence": line["confidence"],
                    "word_count": line.get("word_count", len(line.get("words", []))),
                    # Copy the style so infer_alignment does not mutate the
                    # cached JSON structure for documents from older versions.
                    "style": dict(line.get("style") or {}),
                }
            )
        # Documents created before alignment inference have no ``align`` key.
        # Fill it in for the UI without rewriting doc.json.
        width = int(doc.get("width") or 0)
        height = int(doc.get("height") or 0)
        if width > 0 and height > 0 and any(not line["style"].get("align") for line in lines):
            infer_alignment(lines, width, height)
        return {
            "id": doc["id"],
            "name": doc["name"],
            "source": doc.get("source", "upload"),
            "width": doc["width"],
            "height": doc["height"],
            "lang": doc["lang"],
            "psm": doc.get("psm", 3),
            "lines": lines,
            "line_count": len(lines),
            "engine_version": doc.get("engine_version"),
            "warning": doc.get("warning"),
            "preview_version": doc.get("preview_version", 0),
            "has_preview": bool(doc.get("has_preview")) and (self._dir(doc["id"]) / "preview.png").is_file(),
            "photo_rectified": bool(doc.get("photo_rectified", False)),
            "created_at": doc.get("created_at"),
        }

    def ocr_lines(self, doc_id: str, lang: str, psm: int) -> dict[str, Any]:
        with self._lock_for(doc_id):
            doc = self._load(doc_id)
            image_path = self.image_path(doc_id)
            image = open_image(image_path.read_bytes())

            # Documents created before photo rectification was stored may still
            # be skewed on disk.  A rescan discards the old line boxes anyway,
            # so straighten the working image once and keep OCR and rendering
            # in the same coordinate space from then on.
            rectified = False
            changed = False
            if PHOTO_CORRECT and not doc.get("photo_rectified", False):
                original = image
                try:
                    straightened = rectify_photo(image)
                except Exception:
                    log.exception("Photo rectification failed during rescan")
                    straightened = image
                rectified = straightened is not original
                image = limit_size(straightened, MAX_SIDE)
                changed = rectified or image is not straightened

            # The OCR pass reads image.png from disk, while style sampling uses
            # this in-memory copy.  Save the working image whenever either step
            # changed the pixels so both always share one coordinate space.
            if changed:
                save_png(image, image_path)

            # Commit the actual stored size before OCR runs.  A failed OCR pass
            # used to leave image.png rectified while doc.json kept the old
            # dimensions, which shifted every overlay and rendered edit.
            dimensions_changed = (image.width, image.height) != (
                int(doc.get("width") or 0),
                int(doc.get("height") or 0),
            )
            if changed or dimensions_changed:
                doc["width"], doc["height"] = image.size
                if rectified:
                    doc["photo_rectified"] = True
                if changed:
                    # The old word boxes and preview belong to the previous
                    # pixels.  Dropping them here keeps a hard OCR failure from
                    # exposing a document whose metadata disagrees with its
                    # image; the retry will fill in fresh lines.
                    doc["lines"] = []
                    doc["has_preview"] = False
                    doc["preview_version"] = int(doc.get("preview_version", 0)) + 1
                    preview = self._dir(doc_id) / "preview.png"
                    if preview.exists():
                        preview.unlink()
                self._save(doc)

            lines, warning = self._ocr_lines(
                image_path,
                image,
                lang,
                psm,
                rectify=not doc.get("photo_rectified", False),
            )
            doc.update(
                {
                    "lines": lines,
                    "lang": lang,
                    "psm": int(psm),
                    "warning": warning,
                    "engine_version": engine.status().get("version"),
                }
            )
            doc["preview_version"] = int(doc.get("preview_version", 0)) + 1
            doc["has_preview"] = False
            self._save(doc)
            preview = self._dir(doc_id) / "preview.png"
            if preview.exists():
                preview.unlink()
            return self.public(doc)

    def _ocr_lines(
        self,
        image_path: Path,
        image: Any,
        lang: str,
        psm: int,
        rectify: bool = True,
    ) -> tuple[list[dict[str, Any]], str | None]:
        try:
            lines = engine.recognize(image_path, lang, psm, rectify=rectify)
        except OcrUnavailable:
            raise
        except OcrError as exc:
            if "No text was found" in str(exc):
                return [], str(exc)
            raise
        for line in lines:
            line["style"] = sample_line_style(image, line)
        lines = split_styled_lines(image, lines)
        # Split segments have no style yet; sample them after partitioning so
        # each selection gets the colour/face/size of its own words instead of
        # inheriting the mixed line style.
        for line in lines:
            if not line.get("style"):
                line["style"] = sample_line_style(image, line)
        # A clipped row can share its baseline with the complete row above it.
        # Drop those partial selections before the rejoin pass can glue them
        # together geometrically.
        lines = engine.filter_border_clipped_lines(image, lines)
        # A per-word font near-tie can still create a boundary during the
        # cheap first pass.  Once every segment has its own sampled style,
        # rejoin adjacent fragments that are visibly the same text run.
        lines = merge_compatible_segments(lines)
        # Icon grids and photo textures can survive OCR as plausible-looking
        # short lines even after the text filters.  Check the actual pixels of
        # each weak line before it becomes an editable selection.
        lines = engine.filter_graphic_lines(image, lines)
        lines.sort(key=lambda item: (item["box"]["y"], item["box"]["x"]))
        refine_line_decorations(image, lines)
        for index, line in enumerate(lines, start=1):
            line["id"] = f"L{index:03d}"
        stabilize_typefaces(lines)
        stabilize_monospaced_document(lines)
        infer_alignment(lines, image.width, image.height)
        return lines, None

    def render(self, doc_id: str, edits: list[Any]) -> dict[str, Any]:
        with self._lock_for(doc_id):
            doc = self._load(doc_id)
            directory = self._dir(doc_id)
            output = directory / "preview.png"
            applied = render_document(directory / "image.png", doc["lines"], edits, output)
            doc["preview_version"] = int(doc.get("preview_version", 0)) + 1
            doc["has_preview"] = True
            self._save(doc)
            return {"applied": applied, "preview_version": doc["preview_version"], "has_preview": True}

    def delete(self, doc_id: str) -> None:
        directory = self._dir(doc_id)
        with self._lock_for(doc_id):
            shutil.rmtree(directory, ignore_errors=True)

    def cleanup(self, older_than_hours: int = DOCUMENT_TTL_HOURS) -> int:
        cutoff = time.time() - older_than_hours * 3600
        removed = 0
        for entry in self.root.iterdir():
            if not entry.is_dir():
                continue
            try:
                if entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
        return removed

    def export_name(self, doc_id: str) -> str:
        doc = self._load(doc_id)
        stem = Path(doc["name"]).stem or "image"
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-") or "image"
        return f"{stem}-edited.png"


store = DocumentStore()
