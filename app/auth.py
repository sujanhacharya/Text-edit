"""Per-document browser access cookies.

Every new document gets a random access token.  The browser receives the raw
token in an HttpOnly cookie scoped to that document's API path, while
``doc.json`` stores only a SHA-256 hash.  Separate uploads therefore receive
separate cookies, and one document's cookie cannot authorize another document.

This is document-capability authentication for a local single-user app, not a
user-account system.  There is deliberately no document listing endpoint.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from typing import Any

from .config import COOKIE_SECURE, DOCUMENT_TTL_HOURS

COOKIE_PREFIX = "inkpatch_doc_"
DOCUMENT_PATH_RE = re.compile(r"^/api/documents/([0-9a-fA-F-]{36})(?:/|$)")
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_]+")


def document_cookie_name(doc_id: str) -> str:
    """Return the browser cookie name dedicated to one document."""
    return COOKIE_PREFIX + _SAFE_ID_RE.sub("", str(doc_id))


def document_cookie_path(doc_id: str) -> str:
    """Scope the cookie to every API route for this document."""
    return f"/api/documents/{doc_id}"


def document_id_from_path(path: str) -> str | None:
    """Extract a document id from a protected API path, if this is one."""
    match = DOCUMENT_PATH_RE.match(path or "")
    return match.group(1) if match else None


def new_access_token() -> str:
    """Generate an unguessable cookie value for one document."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """Return the one-way value persisted in ``doc.json``."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(token: str | None, expected_hash: str | None) -> bool:
    """Compare a cookie value with a stored hash in constant time."""
    if not token or not expected_hash:
        return False
    return hmac.compare_digest(token_hash(token), str(expected_hash))


def set_document_cookie(response: Any, doc_id: str, token: str) -> None:
    """Attach or refresh the document's HttpOnly access cookie."""
    response.set_cookie(
        key=document_cookie_name(doc_id),
        value=token,
        max_age=DOCUMENT_TTL_HOURS * 3600,
        path=document_cookie_path(doc_id),
        httponly=True,
        samesite="strict",
        secure=COOKIE_SECURE,
    )


def clear_document_cookie(response: Any, doc_id: str) -> None:
    """Expire the document's cookie after deletion."""
    response.set_cookie(
        key=document_cookie_name(doc_id),
        value="",
        max_age=0,
        expires=0,
        path=document_cookie_path(doc_id),
        httponly=True,
        samesite="strict",
        secure=COOKIE_SECURE,
    )
