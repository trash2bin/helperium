# ============================================================
# WEB server: static files + reverse-proxy to API
# Core deps only — the lightest image (<100MB)
# ============================================================
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY helperium-sdk helperium-sdk/
COPY demo/web/pyproject.toml demo/web/
RUN uv sync --frozen --no-install-project --package demo-web --no-dev
COPY demo/web/ demo/web/
COPY demo/__init__.py demo/
COPY demo/settings.py demo/
RUN uv sync --frozen --package demo-web --no-dev

FROM python:3.12-slim

RUN groupadd --gid 1000 app && \
    useradd --uid 1000 --gid app --shell /bin/bash --create-home app

RUN apt-get update && \
    apt-get install --no-install-recommends -y curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/helperium-sdk /app/helperium-sdk
COPY --from=builder --chown=app:app /app/demo/web /app/demo/web
COPY --from=builder --chown=app:app /app/demo/settings.py /app/demo/settings.py
COPY --from=builder --chown=app:app /app/demo/__init__.py /app/demo/__init__.py

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/app \
    DEMO_WEB_HOST=0.0.0.0 \
    DEMO_WEB_PORT=8080 \
    DEMO_API_HOST=api \
    DEMO_API_PORT=8081

USER app
WORKDIR /app

EXPOSE 8080

CMD ["/app/.venv/bin/python", "-m", "demo.web.server"]
