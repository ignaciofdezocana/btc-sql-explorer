#!/usr/bin/env bash
# setup.sh — One-command setup for Bitcoin Testnet4 SQL Explorer
#
# Prerequisites:
#   - Docker and Docker Compose installed
#   - Python 3.10+ with pip
#
# What it does:
#   1. Starts the Bitcoin Core testnet4 node (Docker)
#   2. Installs Python dependencies
#   3. Waits for the node to finish initial block download
#   4. Syncs blockchain data into DuckDB
#   5. Prints instructions for starting the web UI

set -euo pipefail
cd "$(dirname "$0")"

echo "============================================"
echo " Bitcoin Testnet4 SQL Explorer — Setup"
echo "============================================"
echo ""

# --- 1. Start Bitcoin Core via Docker Compose ---
echo "[1/4] Starting Bitcoin Core testnet4 node..."
if ! command -v docker &>/dev/null; then
    echo "Error: Docker is not installed. Please install Docker first."
    echo "       https://docs.docker.com/get-docker/"
    exit 1
fi

docker compose up -d
echo "      Node container started."
echo ""

# --- 2. Install Python dependencies ---
echo "[2/4] Installing Python dependencies..."
if [ -d ".venv" ]; then
    source .venv/bin/activate 2>/dev/null || true
fi
pip install -q -r requirements.txt
echo "      Dependencies installed."
echo ""

# --- 3 & 4. Sync blockchain into DuckDB (btc_sync.py waits for IBD) ---
echo "[3/4] Syncing blockchain data into DuckDB..."
echo "      (This will wait for the node to finish initial block download,"
echo "       then sync all blocks. Testnet4 IBD takes ~10-30 minutes.)"
echo ""
python btc_sync.py
echo ""

# --- 5. Done ---
echo "============================================"
echo " Setup complete!"
echo "============================================"
echo ""
echo "To start the web explorer:"
echo "  python btc_web_app.py"
echo ""
echo "Then open: http://localhost:5001"
echo ""
echo "To re-sync new blocks later:"
echo "  python btc_sync.py"
echo ""
echo "To stop the Bitcoin node:"
echo "  docker compose down"
echo ""
