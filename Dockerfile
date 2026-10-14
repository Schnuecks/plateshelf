# Pinned to the digest of the multi-platform index; Dependabot keeps it current
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170

# Set to the tag by GitHub Actions on release (e.g. v0.2.0)
ARG VERSION=dev

LABEL org.opencontainers.image.title="PlateShelf" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LIBRARY_DIR=/library \
    DATA_DIR=/data \
    SCAN_INTERVAL=120 \
    APP_VERSION=${VERSION} \
    FORWARDED_ALLOW_IPS=127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"
# The visitor's address from X-Forwarded-For is only taken from proxies in FORWARDED_ALLOW_IPS
# (above: local and private networks, e.g. the reverse proxy in the Docker network); uvicorn
# then uses the last address the proxy added, not the first one the client may have made up.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
