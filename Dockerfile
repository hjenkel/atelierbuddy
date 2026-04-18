FROM python:3.12-slim

ARG APP_VERSION=dev
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

LABEL org.opencontainers.image.title="Atelier Buddy" \
      org.opencontainers.image.description="Lokale Web-App fuer Belegverwaltung, Ausgangsrechnungen und betriebliche Auswertungen." \
      org.opencontainers.image.url="https://github.com/hjenkel/atelierbuddy" \
      org.opencontainers.image.source="https://github.com/hjenkel/atelierbuddy" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-dejavu-core \
        libharfbuzz-subset0 \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        ocrmypdf \
        tesseract-ocr \
        tesseract-ocr-deu \
        tesseract-ocr-eng \
        ghostscript \
    && rm -rf /var/lib/apt/lists/*

COPY . /app

RUN pip install --upgrade pip \
    && pip install .

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

CMD ["python", "-m", "belegmanager"]
