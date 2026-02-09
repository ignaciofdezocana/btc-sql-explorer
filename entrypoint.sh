#!/bin/sh
set -e

RPC_URL="http://${BITCOIN_RPC_HOST}:${BITCOIN_RPC_PORT}"

echo "=== BTC SQL Explorer ==="
echo "  RPC:      ${RPC_URL}"
echo "  DB:       ${DB_PATH}"
echo "  Network:  ${BITCOIN_NETWORK:-mainnet}"
echo ""

# ── Fix data-directory permissions ──────────────────────────────────
# Umbrel mounts a host volume at /data. The host directory is often
# owned by root, so the app user (UID 1000) cannot write to it.
# We start as root, fix ownership, then drop privileges with gosu.
DB_DIR="$(dirname "${DB_PATH}")"
mkdir -p "${DB_DIR}"
chown -R app:app "${DB_DIR}" 2>/dev/null || true
echo "Data directory ready ($(ls -ld "${DB_DIR}"))"

# ── Start web server (as app user) ─────────────────────────────────
# Gunicorn runs with a SINGLE worker process and 4 threads.
# The web app module (btc_web_app) starts blockchain sync and mempool
# sync as background daemon threads inside this worker.  Because all
# DuckDB access happens within a single OS process, DuckDB's in-process
# MVCC handles concurrent reads + writes — no file-lock contention.
exec gosu app gunicorn \
  --bind 0.0.0.0:5001 \
  --workers 1 \
  --threads 4 \
  --timeout 120 \
  --access-logfile - \
  btc_web_app:app
