"""FastAPI application: upload, OCR, render, export."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .auth import (
    clear_document_cookie,
    document_cookie_name,
    document_id_from_path,
    new_access_token,
    set_document_cookie,
)
from .config import (
    APP_NAME,
    APP_VERSION,
    DEMO_IMAGE,
    DOCUMENT_TTL_HOURS,
    MAX_LINES,
    MAX_SIDE,
    MAX_UPLOAD_BYTES,
    MAX_UPLOAD_MB,
    OCR_LINE_RECOVERY,
    OCR_MIN_CONFIDENCE,
    OCR_RECOVERY_BUDGET,
    OCR_RECOVERY_MAX_LINES,
    OCR_TARGET_SIDE,
    PHOTO_CORRECT,
    STATIC_DIR,
)
from .fonts import catalog as font_catalog
from .imaging import ImageError, RenderError
from .ocr import OcrError, OcrUnavailable, engine
from .schemas import OcrRequest, RenderRequest
from .storage import DocumentNotFound, store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("inkpatch")


async def _cleanup_expired_documents() -> None:
    try:
        removed = await asyncio.to_thread(store.cleanup)
        if removed:
            log.info("Removed %s expired document(s)", removed)
    except OSError:
        log.exception("Document cleanup failed")


async def _cleanup_loop() -> None:
    # Startup handles documents that expired while the process was stopped;
    # this hourly pass handles a long-running server without a scheduler.
    while True:
        await asyncio.sleep(3600)
        await _cleanup_expired_documents()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Keep the app from accumulating orphaned images forever.
    await _cleanup_expired_documents()
    cleanup_task = asyncio.create_task(_cleanup_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task


app = FastAPI(
    title=f"{APP_NAME} API",
    version=APP_VERSION,
    description="Local OCR and in-image text editing.",
    lifespan=lifespan,
)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def require_document_cookie(request: Request, call_next):
    """Protect every per-document API route with its own cookie.

    Each upload gets an independent token and cookie path, so possessing one
    document's cookie never grants access to another document.  There is no
    listing endpoint: the cookie is a capability for the id it names.
    """
    doc_id = document_id_from_path(request.url.path)
    if not doc_id:
        return await call_next(request)

    token = request.cookies.get(document_cookie_name(doc_id))
    try:
        allowed = await asyncio.to_thread(store.verify_access, doc_id, token)
    except DocumentNotFound:
        return JSONResponse(
            status_code=404, content={"detail": "That document no longer exists."}
        )
    if not allowed:
        return JSONResponse(
            status_code=403,
            content={"detail": "This document requires its access cookie."},
        )

    response = await call_next(request)
    # Sliding cookie lifetime: using a document keeps its cookie alive.  Skip
    # DELETE so the endpoint can clear it instead of this middleware reviving it.
    if request.method != "DELETE" and response.status_code < 400 and token:
        set_document_cookie(response, doc_id, token)
    return response


ALLOWED_PSM = {3, 4, 6, 11, 12}


# --------------------------------------------------------------------- helpers


def _http_error(status: int, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail=message)


def _validate_ocr(lang: str, psm: int) -> OcrRequest:
    if psm not in ALLOWED_PSM:
        raise _http_error(400, "Unsupported page segmentation mode.")
    try:
        return OcrRequest(lang=lang, psm=psm)  # type: ignore[arg-type]
    except ValidationError:
        raise _http_error(400, "Invalid OCR settings.") from None


def _ocr_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, OcrUnavailable):
        return _http_error(503, str(exc))
    return _http_error(422, str(exc))


async def _read_upload(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise _http_error(413, f"Images must be under {MAX_UPLOAD_MB} MB.")
        chunks.append(chunk)
    return b"".join(chunks)


# ------------------------------------------------------------------------ meta


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/favicon.svg", include_in_schema=False)
async def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


@app.get("/api/health")
async def health(refresh: bool = False) -> dict:
    status = await asyncio.to_thread(engine.status, refresh)
    return {
        "status": "ok",
        "app": APP_NAME,
        "version": APP_VERSION,
        "ocr": status,
        "limits": {
            "max_side": MAX_SIDE,
            "max_upload_mb": MAX_UPLOAD_MB,
            "max_lines": MAX_LINES,
            "ttl_hours": DOCUMENT_TTL_HOURS,
            "ocr_target_side": OCR_TARGET_SIDE,
            "ocr_min_confidence": OCR_MIN_CONFIDENCE,
            "ocr_line_recovery": OCR_LINE_RECOVERY,
            "ocr_recovery_max_lines": OCR_RECOVERY_MAX_LINES,
            "ocr_recovery_budget": OCR_RECOVERY_BUDGET,
            "photo_correct": PHOTO_CORRECT,
        },
        "demo_available": DEMO_IMAGE.is_file(),
    }


@app.get("/api/languages")
async def languages(refresh: bool = False) -> dict:
    status = await asyncio.to_thread(engine.status, refresh)
    return {"available": status["available"], "languages": status["languages"], "error": status["error"]}


@app.get("/api/fonts")
async def fonts() -> dict:
    """List families available for explicit typeface overrides."""
    return {"families": await asyncio.to_thread(font_catalog)}


# -------------------------------------------------------------------- documents


@app.post("/api/documents", status_code=201)
async def create_document(
    response: Response,
    file: UploadFile = File(...),
    lang: str = Form("eng"),
    psm: int = Form(3),
) -> dict:
    request = _validate_ocr(lang, psm)
    access_token = new_access_token()
    try:
        raw = await _read_upload(file)
        if not raw:
            raise _http_error(400, "The uploaded file is empty.")
        doc = await asyncio.to_thread(
            store.create,
            raw,
            file.filename or "image.png",
            request.lang,
            request.psm,
            access_token=access_token,
        )
    except ImageError as exc:
        raise _http_error(400, str(exc)) from None
    except (OcrUnavailable, OcrError) as exc:
        raise _ocr_exception(exc) from None
    finally:
        await file.close()
    set_document_cookie(response, doc["id"], access_token)
    return doc


@app.get("/api/documents/{doc_id}")
async def get_document(doc_id: str) -> dict:
    try:
        return await asyncio.to_thread(store.get, doc_id)
    except DocumentNotFound:
        raise _http_error(404, "That document no longer exists.") from None


@app.get("/api/documents/{doc_id}/image")
async def get_image(doc_id: str) -> FileResponse:
    try:
        path = await asyncio.to_thread(store.image_path, doc_id)
    except DocumentNotFound:
        raise _http_error(404, "That document no longer exists.") from None
    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )


@app.post("/api/documents/{doc_id}/ocr")
async def reocr(doc_id: str, request: OcrRequest) -> dict:
    try:
        return await asyncio.to_thread(store.ocr_lines, doc_id, request.lang, request.psm)
    except DocumentNotFound:
        raise _http_error(404, "That document no longer exists.") from None
    except (OcrUnavailable, OcrError) as exc:
        raise _ocr_exception(exc) from None


@app.post("/api/documents/{doc_id}/render")
async def render(doc_id: str, request: RenderRequest) -> dict:
    try:
        result = await asyncio.to_thread(store.render, doc_id, request.edits)
    except DocumentNotFound:
        raise _http_error(404, "That document no longer exists.") from None
    except (RenderError, ImageError) as exc:
        raise _http_error(400, str(exc)) from None
    result["preview_url"] = f"/api/documents/{doc_id}/preview?v={result['preview_version']}"
    return result


@app.get("/api/documents/{doc_id}/preview")
async def preview(doc_id: str) -> FileResponse:
    try:
        path = await asyncio.to_thread(
            lambda: store.preview_path(doc_id) or store.image_path(doc_id)
        )
    except DocumentNotFound:
        raise _http_error(404, "That document no longer exists.") from None
    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@app.get("/api/documents/{doc_id}/download")
async def download(doc_id: str) -> FileResponse:
    def resolve() -> tuple[Path, str]:
        path = store.preview_path(doc_id) or store.image_path(doc_id)
        return path, store.export_name(doc_id)

    try:
        path, name = await asyncio.to_thread(resolve)
    except DocumentNotFound:
        raise _http_error(404, "That document no longer exists.") from None
    return FileResponse(
        path,
        media_type="image/png",
        filename=name,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@app.delete("/api/documents/{doc_id}", status_code=204)
async def delete_document(doc_id: str, response: Response) -> Response:
    try:
        await asyncio.to_thread(store.delete, doc_id)
    except DocumentNotFound:
        raise _http_error(404, "That document no longer exists.") from None
    response.status_code = 204
    clear_document_cookie(response, doc_id)
    return response


@app.post("/api/demo", status_code=201)
async def demo(response: Response) -> dict:
    if not DEMO_IMAGE.is_file():
        raise _http_error(404, "The demo image is not installed.")
    access_token = new_access_token()
    try:
        doc = await asyncio.to_thread(store.create_demo, "eng", access_token=access_token)
    except (OcrUnavailable, OcrError) as exc:
        raise _ocr_exception(exc) from None
    set_document_cookie(response, doc["id"], access_token)
    return doc


@app.get("/api/demo/image", include_in_schema=False)
async def demo_image() -> FileResponse:
    """Serve the sample poster so the landing page can show it before OCR."""
    if not DEMO_IMAGE.is_file():
        raise _http_error(404, "The demo image is not installed.")
    return FileResponse(
        DEMO_IMAGE,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )


@app.exception_handler(DocumentNotFound)
async def _not_found_handler(_, __):  # pragma: no cover - safety net
    return JSONResponse(status_code=404, content={"detail": "That document no longer exists."})


def main() -> None:  # pragma: no cover
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":  # pragma: no cover
    main()
