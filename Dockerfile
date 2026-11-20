# Single-container production image for the hosted live demo (e.g. Render, Fly.io).
# FastAPI serves the API, the /ws WebSocket and the built dashboard on one port ($PORT).
# For local development use docker-compose.yml instead (separate nginx frontend).

FROM node:22-alpine AS frontend
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY backend/ ./
COPY sample_events.jsonl /data/sample_events.jsonl
COPY --from=frontend /app/dist /app/static

ENV WIKIPULSE_SAMPLE_FILE=/data/sample_events.jsonl \
    WIKIPULSE_STATIC_DIR=/app/static \
    PORT=8000

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT', '8000'), timeout=4)" || exit 1

# Hosting platforms inject PORT; proxy headers keep client info correct behind their load balancer.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
