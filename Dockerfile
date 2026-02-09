# ── Stage 1: Build React frontend ──────────────────────────────────────
FROM node:20-slim AS frontend-build

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build          # produces /build/dist/


# ── Stage 2: Python runtime ───────────────────────────────────────────
FROM python:3.11-slim

# System deps for DuckDB / pandas (numpy wheels)
RUN apt-get update && \
    apt-get install -y --no-install-recommends tini gosu && \
    rm -rf /var/lib/apt/lists/*

# Non-root user (UID 1000, expected by Umbrel)
RUN groupadd -g 1000 app && useradd -u 1000 -g app -m app

WORKDIR /app

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Application code
COPY btc_web_app.py btc_sync.py btc_mempool_sync.py ./

# Built React frontend from stage 1
COPY --from=frontend-build /build/dist/ frontend/dist/

# Entrypoint script
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

# Persistent data lives here (mounted as a volume)
RUN mkdir -p /data && chown app:app /data
VOLUME /data

# Default environment (overridden by Umbrel docker-compose)
ENV DB_PATH=/data/bitcoin_blockchain.db \
    SAVED_QUERIES_PATH=/data/saved_queries.db \
    BITCOIN_RPC_HOST=127.0.0.1 \
    BITCOIN_RPC_PORT=8332 \
    BITCOIN_RPC_USER=bitcoin \
    BITCOIN_RPC_PASS=bitcoin \
    BITCOIN_NETWORK=mainnet

# Start as root — entrypoint fixes /data permissions then drops to app user
EXPOSE 5001

# tini ensures clean signal handling for child processes
ENTRYPOINT ["tini", "--"]
CMD ["./entrypoint.sh"]
