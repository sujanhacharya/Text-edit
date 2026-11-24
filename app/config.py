"""Application configuration and paths.

Everything is intentionally local: no database, no cloud calls, no model
downloads.  The only external dependency at runtime is the Tesseract binary.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
ASSETS_DIR = APP_DIR / "assets"
FONT_DIR = ASSETS_DIR / "fonts"
DEMO_IMAGE = ASSETS_DIR / "demo" / "demo.png"
VENDOR_DIR = BASE_DIR / "vendor"
VENDOR_TESSERACT = VENDOR_DIR / "tesseract"

DATA_DIR = Path(os.environ.get("TEXTEDIT_DATA_DIR", BASE_DIR / "data")).expanduser().resolve()

MAX_SIDE = int(os.environ.get("TEXTEDIT_MAX_SIDE", "2400"))
MAX_UPLOAD_MB = int(os.environ.get("TEXTEDIT_MAX_UPLOAD_MB", "20"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
MAX_LINES = int(os.environ.get("TEXTEDIT_MAX_LINES", "600"))
# Documents and their access cookies expire four days after the last change.
DOCUMENT_TTL_HOURS = int(os.environ.get("TEXTEDIT_TTL_HOURS", "96"))
# Backward-compatible alias for external scripts/tests that imported the old
# preview-oriented name.
PREVIEW_TTL_HOURS = DOCUMENT_TTL_HOURS
# The default deployment is plain HTTP on loopback, where a Secure cookie
# cannot be used.  Set this when serving InkPatch over HTTPS.
COOKIE_SECURE = os.environ.get("TEXTEDIT_COOKIE_SECURE", "0").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}

# OCR is deliberately run on a larger working copy.  Tesseract's LSTM engine
# likes roughly 300 DPI; upscaling small screenshots and phone photos improves
# both word recognition and the quality of the word boxes used for editing.
OCR_TARGET_SIDE = int(os.environ.get("TEXTEDIT_OCR_TARGET_SIDE", "2200"))
OCR_MIN_CONFIDENCE = float(os.environ.get("TEXTEDIT_OCR_MIN_CONFIDENCE", "30"))
# Page segmentation can miss or truncate individual lines even when the rest of
# the page is recognised well.  A second, line-by-line pass using OpenCV line
# detection and Tesseract's single-line mode recovers those lines.  It is
# limited so a large poster does not spawn hundreds of extra Tesseract calls.
OCR_LINE_RECOVERY = os.environ.get("TEXTEDIT_OCR_LINE_RECOVERY", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
OCR_RECOVERY_MAX_LINES = int(os.environ.get("TEXTEDIT_OCR_RECOVERY_MAX_LINES", "60"))
OCR_RECOVERY_BUDGET = float(os.environ.get("TEXTEDIT_OCR_RECOVERY_BUDGET", "60"))
# Straighten photographed pages before storage and OCR.  Disable this for
# scanned pages with decorative borders if automatic page detection is too
# eager; the other photo preprocessing steps still run.
PHOTO_CORRECT = os.environ.get("TEXTEDIT_PHOTO_CORRECT", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}

# Pillow refuses to open images larger than this by default; we lower the
# limit on purpose so a malformed upload cannot exhaust memory.
MAX_IMAGE_PIXELS = 40_000_000

APP_NAME = "InkPatch"
APP_VERSION = "1.0.0"
