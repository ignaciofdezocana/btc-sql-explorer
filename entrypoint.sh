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

# ── Pre-create empty database schemas ───────────────────────────────
# Create both blockchain and mempool schemas BEFORE starting any
# background process or the web server. This guarantees the DB files
# and tables exist so the web UI can ATTACH them immediately.
MEMPOOL_DB="${MEMPOOL_DB_PATH:-$(dirname "${DB_PATH}")/mempool.db}"
gosu app python -c "
import duckdb, os, sys
sys.path.insert(0, '/app')
from btc_sync import ensure_schema as ensure_blockchain_schema
from btc_mempool_sync import ensure_schema as ensure_mempool_schema

# Blockchain DB
try:
    db_path = os.environ['DB_PATH']
    db = duckdb.connect(db_path)
    ensure_blockchain_schema(db)
    db.close()
    print('Blockchain schema ready at', db_path)
except Exception as e:
    print(f'Warning: could not pre-create blockchain schema ({e})', file=sys.stderr)

# Mempool DB
try:
    mempool_path = os.environ.get('MEMPOOL_DB_PATH', '${MEMPOOL_DB}')
    db = duckdb.connect(mempool_path)
    ensure_mempool_schema(db)
    db.close()
    print('Mempool schema ready at', mempool_path)
except Exception as e:
    print(f'Warning: could not pre-create mempool schema ({e})', file=sys.stderr)
" || echo "Schema pre-creation skipped (non-fatal)."

# ── Start blockchain sync in background (as app user) ────────────────
gosu app python btc_sync.py \
  --rpc-url "${RPC_URL}" \
  --rpc-user "${BITCOIN_RPC_USER}" \
  --rpc-password "${BITCOIN_RPC_PASS}" \
  --db-path "${DB_PATH}" \
  --loop &

SYNC_PID=$!
echo "Blockchain sync started (PID ${SYNC_PID})"

# ── Start mempool sync in background (as app user) ───────────────────
MEMPOOL_DB="${MEMPOOL_DB_PATH:-$(dirname "${DB_PATH}")/mempool.db}"
gosu app python btc_mempool_sync.py \
  --rpc-url "${RPC_URL}" \
  --rpc-user "${BITCOIN_RPC_USER}" \
  --rpc-password "${BITCOIN_RPC_PASS}" \
  --db-path "${MEMPOOL_DB}" \
  --interval 15 &

MEMPOOL_PID=$!
echo "Mempool sync started (PID ${MEMPOOL_PID})"

# Trap SIGTERM/SIGINT so all processes shut down cleanly.
cleanup() {
  echo "Shutting down..."
  kill "${SYNC_PID}" 2>/dev/null || true
  kill "${MEMPOOL_PID}" 2>/dev/null || true
  wait "${SYNC_PID}" 2>/dev/null || true
  wait "${MEMPOOL_PID}" 2>/dev/null || true
  exit 0
}
trap cleanup TERM INT

# ── Start web server in foreground (as app user) ────────────────────
exec gosu app gunicorn \
  --bind 0.0.0.0:5001 \
  --workers 2 \
  --timeout 120 \
  --access-logfile - \
  btc_web_app:app
