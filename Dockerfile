# syntax=docker/dockerfile:1.7

FROM python:3.12-slim-bookworm

# Space-separated Tesseract language codes, for example "eng deu fra".
# Debian package names use hyphens, so chi_sim becomes tesseract-ocr-chi-sim.
ARG TESSERACT_LANGS="eng"
# Latin document fonts are bundled in app/assets/fonts.  This optional build
# argument is for additional script fonts, for example fonts-noto-core.
ARG EXTRA_FONTS=""

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    OMP_THREAD_LIMIT=2 \
    TEXTEDIT_DATA_DIR=/data \
    TEXTEDIT_OCR_LINE_RECOVERY=1 \
    TEXTEDIT_OCR_RECOVERY_MAX_LINES=60 \
    TEXTEDIT_OCR_RECOVERY_BUDGET=60 \
    HOME=/home/inkpatch

RUN set -eux; \
    export DEBIAN_FRONTEND=noninteractive; \
    apt-get update; \
    packages="tesseract-ocr libgl1 libglib2.0-0 libgomp1"; \
    for lang in $TESSERACT_LANGS; do \
        packages="$packages tesseract-ocr-$(printf '%s' "$lang" | tr '_' '-')"; \
    done; \
    if [ -n "$EXTRA_FONTS" ]; then \
        packages="$packages $EXTRA_FONTS"; \
    fi; \
    apt-get install -y --no-install-recommends $packages; \
    rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 inkpatch \
    && useradd --uid 10001 --gid inkpatch --create-home --shell /usr/sbin/nologin inkpatch \
    && install -d -o inkpatch -g inkpatch /data

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY --chown=inkpatch:inkpatch app ./app

USER inkpatch

EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).read()"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
