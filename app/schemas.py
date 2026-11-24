"""Pydantic request models and validation."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from .config import MAX_LINES

PSM = Literal[3, 4, 6, 11, 12]


class RenderBox(BaseModel):
    """Geometry for a newly created text box in working-image pixels."""

    x: float = Field(ge=-100000, le=100000)
    y: float = Field(ge=-100000, le=100000)
    width: float = Field(gt=0, le=100000)
    height: float = Field(gt=0, le=100000)


class OcrRequest(BaseModel):
    lang: str = Field(default="eng", min_length=2, max_length=64)
    psm: PSM = 3

    @field_validator("lang")
    @classmethod
    def _valid_lang(cls, value: str) -> str:
        allowed = set("abcdefghijklmnopqrstuvwxyz_+0123456789")
        value = value.strip().lower()
        if not value or any(ch not in allowed for ch in value):
            raise ValueError("invalid language code")
        return value


class RenderEdit(BaseModel):
    line_id: str = Field(min_length=1, max_length=32)
    text: str = Field(default="", max_length=2000)
    # Use typing.Optional rather than ``str | None`` here.  Pydantic evaluates
    # model annotations at runtime, and PEP 604 unions are unavailable before
    # Python 3.10 without the optional eval_type_backport package.
    color: Optional[str] = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    font: Literal["auto", "sans", "serif", "mono"] = "auto"
    # A concrete family from /api/fonts, e.g. "Liberation Sans".  When set it
    # takes precedence over the broad sans/serif/mono fallback.
    font_family: Optional[str] = Field(default=None, max_length=80)
    align: Literal["auto", "left", "center", "right"] = "auto"
    scale: float = Field(default=1.0, ge=0.4, le=3.0)
    # Absolute base size in pixels for a newly created box or an explicit
    # cross-line style copy.  ``None`` inherits the OCR-sampled size.
    font_size: Optional[float] = Field(default=None, ge=4, le=800)
    # When present, this edit is a *new* text box rather than an edit of an
    # OCR line.  Nothing is erased; the text (and any background fill) is drawn
    # over the existing pixels.
    box: Optional[RenderBox] = None
    # ``None`` means "inherit the weight/slant detected during OCR".
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    # Decoration/effect overrides.  ``None`` inherits the sampled decoration;
    # ``True``/``False`` force it on or off for this edit.  ``shadow=True``
    # keeps a detected shadow or adds a subtle user-requested drop shadow.
    underline: Optional[bool] = None
    strikethrough: Optional[bool] = None
    shadow: Optional[bool] = None
    # Optional fill behind the edited line only.  The fill is clipped to the
    # selected line box and excludes every other selection's box.
    background: Optional[str] = Field(
        default=None, pattern=r"^#[0-9a-fA-F]{6}$"
    )


class RenderRequest(BaseModel):
    edits: list[RenderEdit] = Field(default_factory=list, max_length=MAX_LINES)

    @field_validator("edits")
    @classmethod
    def _unique_lines(cls, edits: list[RenderEdit]) -> list[RenderEdit]:
        seen: set[str] = set()
        for edit in edits:
            if edit.line_id in seen:
                raise ValueError(f"duplicate line_id: {edit.line_id}")
            seen.add(edit.line_id)
        return edits
