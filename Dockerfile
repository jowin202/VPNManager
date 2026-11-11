# --- Frontend build -----------------------------------------------------------
FROM node:22-alpine AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npx ng build

# --- Runtime --------------------------------------------------------------------
FROM python:3.12-slim
LABEL org.opencontainers.image.title="OPNsense VPN Manager" \
      org.opencontainers.image.description="Route individual devices through WireGuard VPNs on OPNsense" \
      org.opencontainers.image.licenses="MIT"
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/app backend/app
COPY --from=frontend /src/dist frontend/dist
RUN useradd --system --uid 10001 vpnmgr && mkdir -p /app/data && chown vpnmgr /app/data
USER vpnmgr
ENV VPNMGR_DATA_DIR=/app/data \
    PYTHONUNBUFFERED=1
WORKDIR /app/backend
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
