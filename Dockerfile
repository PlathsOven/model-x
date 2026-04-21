# syntax=docker/dockerfile:1

# --- Stage 1: build the Vite/React frontend ---
FROM node:20-alpine AS frontend
WORKDIR /app/dashboard/frontend
COPY dashboard/frontend/package.json dashboard/frontend/package-lock.json* ./
RUN npm install
COPY dashboard/frontend/ ./
RUN npm run build

# --- Stage 2: python runtime ---
FROM python:3.11-slim
WORKDIR /app

# Keep Python logs unbuffered so they show up live in Railway's log viewer.
ENV PYTHONUNBUFFERED=1

# System deps: curl for healthchecks/debug, ca-certificates for httpx/yfinance.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . ./

# Bring in the frontend build artifacts so FastAPI can serve them.
COPY --from=frontend /app/dashboard/frontend/dist ./dashboard/frontend/dist

RUN chmod +x /app/docker-entrypoint.sh

# Defaults for Railway. PORT is overridden by Railway at runtime; the rest
# point at the mounted persistent volume (see README-DEPLOY).
ENV PORT=8000 \
    HOST=0.0.0.0 \
    DB_PATH=/data/modelx.db \
    CONTRACT_YAML=/data/contracts.yaml \
    AGENTS_YAML=/data/agents.yaml \
    TRACES_PATH=/data/episode_traces.json

EXPOSE 8000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
