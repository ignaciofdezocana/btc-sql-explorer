#!/bin/sh
set -e

RPC_URL="http://${BITCOIN_RPC_HOST}:${BITCOIN_RPC_PORT}"
DB_DIR="$(dirname "${DB_PATH}")"
LOG_DIR="${LOG_DIR:-/data/logs}"
BOOT_LOG="${LOG_DIR}/boot.log"
BOOT_COUNT_FILE="${LOG_DIR}/boot_count"

# ── Logging helper ──────────────────────────────────────────────────
# Every line is UTC-timestamped and tee'd into boot.log so it ends up in
# the downloadable diagnostics bundle (stdout alone is lost on restart).
mkdir -p "${LOG_DIR}" 2>/dev/null || true
log() {
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "${ts} [entrypoint] $*" | tee -a "${BOOT_LOG}" 2>/dev/null || echo "${ts} [entrypoint] $*"
}

# ── Boot counter ────────────────────────────────────────────────────
# If this climbs by one every couple of minutes, you have a crash-restart
# loop (strongly implies an OOM kill or a worker timeout).
BOOT_N=0
if [ -f "${BOOT_COUNT_FILE}" ]; then
    BOOT_N="$(cat "${BOOT_COUNT_FILE}" 2>/dev/null || echo 0)"
fi
BOOT_N=$((BOOT_N + 1))
echo "${BOOT_N}" > "${BOOT_COUNT_FILE}" 2>/dev/null || true

log "==== BOOT #${BOOT_N} — BTC SQL Explorer ===="
log "RPC=${RPC_URL}  DB=${DB_PATH}  Network=${BITCOIN_NETWORK:-mainnet}  LOG_DIR=${LOG_DIR}"

# ── System snapshot ─────────────────────────────────────────────────
# Record what the box actually has — the most expensive past bug was
# misjudging available RAM.
TOTAL_RAM_KB="$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
log "system: cpus=$(nproc 2>/dev/null || echo '?') total_ram_mb=$((TOTAL_RAM_KB / 1024))"
if [ -f /sys/fs/cgroup/memory.max ]; then
    log "cgroup v2 memory.max=$(cat /sys/fs/cgroup/memory.max 2>/dev/null)"
elif [ -f /sys/fs/cgroup/memory/memory.limit_in_bytes ]; then
    log "cgroup v1 memory.limit_in_bytes=$(cat /sys/fs/cgroup/memory/memory.limit_in_bytes 2>/dev/null)"
fi
log "disk: $(df -h "${DB_DIR}" 2>/dev/null | awk 'NR==2 {print $4" free of "$2" ("$5" used)"}')"

# ── Fix data-directory permissions ──────────────────────────────────
# Umbrel mounts a host volume at /data, often owned by root, so the app
# user (UID 1000) cannot write to it. Start as root, fix ownership, then
# drop privileges with gosu. Report whether chown actually worked.
mkdir -p "${DB_DIR}"
if chown -R app:app "${DB_DIR}" 2>/dev/null; then
    log "chown ${DB_DIR} -> app:app OK"
else
    log "WARNING chown ${DB_DIR} FAILED (rc=$?) — writes may fail if dir is root-owned"
fi
chown -R app:app "${LOG_DIR}" 2>/dev/null || true
log "data directory: $(ls -ld "${DB_DIR}" 2>/dev/null)"

# ── Clean up stale WAL file ─────────────────────────────────────────
# Old versions accumulated a large WAL that was never checkpointed; DuckDB
# tries to replay it on open and OOMs. Deleting it is safe: the sync thread
# re-syncs any blocks that were only in the WAL.
DB_WAL="${DB_PATH}.wal"
if [ -f "$DB_WAL" ]; then
    WAL_SIZE=$(du -h "$DB_WAL" 2>/dev/null | cut -f1)
    log "removing stale WAL file (${WAL_SIZE}): ${DB_WAL}"
    rm -f "$DB_WAL"
else
    log "no WAL file found (clean state)"
fi

# ── Detect and remove bloated database ──────────────────────────────
# v1.7.x once bloated the DB to 47 GB via repeated OOM rollbacks with no
# VACUUM. If the file is >20 GB, delete it so sync rebuilds cleanly.
if [ -f "$DB_PATH" ]; then
    DB_SIZE_BYTES=$(stat -c%s "$DB_PATH" 2>/dev/null || stat -f%z "$DB_PATH" 2>/dev/null || echo 0)
    DB_SIZE_HR=$(du -h "$DB_PATH" 2>/dev/null | cut -f1)
    log "database size: ${DB_SIZE_HR} (${DB_SIZE_BYTES} bytes)"
    if [ "$DB_SIZE_BYTES" -gt 21474836480 ] 2>/dev/null; then
        log "WARNING database is bloated (${DB_SIZE_HR}) — removing to rebuild cleanly"
        rm -f "$DB_PATH"
        log "bloated database removed; sync will restart from block 0"
    fi
else
    log "no existing database — will be created on first sync"
fi

# ── Start web server (as app user) ──────────────────────────────────
# Single Gunicorn worker + 4 threads. The web app starts blockchain sync,
# mempool sync, and the heartbeat as background daemon threads inside this
# worker. gunicorn.conf.py logs worker lifecycle so a Gunicorn timeout-kill
# is distinguishable from a kernel OOM-kill.
log "starting gunicorn (1 worker, 4 threads, timeout 600) — see ${LOG_DIR}/sync.log"
exec gosu app gunicorn \
  --config gunicorn.conf.py \
  --bind 0.0.0.0:5001 \
  --workers 1 \
  --threads 4 \
  --timeout 600 \
  --access-logfile - \
  btc_web_app:app
