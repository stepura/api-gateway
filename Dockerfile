# syntax=docker/dockerfile:1

FROM python:3.12-slim AS builder
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    APP_PORT=8000
RUN groupadd --system app && useradd --system --gid app --create-home app
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app app/ ./app/
COPY --chown=app:app README.md ./README.md
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD python -c "import os, urllib.request; port = int(os.getenv('APP_PORT', '8000')); urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/health', timeout=2).read()" || exit 1
CMD ["sh", "-c", "exec uvicorn app.main:app --host ${APP_HOST:-0.0.0.0} --port ${APP_PORT:-8000} --workers 1 --no-access-log"]
