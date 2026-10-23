# syntax=docker/dockerfile:1

# ---------- Stage 1: instala dependencias ----------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY app/requirements.txt .
RUN pip install --prefix=/install -r requirements.txt

# ---------- Stage 2: imagem final, enxuta ----------
FROM python:3.12-slim

ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Usuario sem privilegios: o container nao roda como root
RUN useradd --system --uid 10001 --no-create-home appuser

WORKDIR /srv
COPY --from=builder /install /usr/local
COPY app/ ./app/

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
