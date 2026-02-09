#!/usr/bin/env python3
"""
Bitcoin Mempool RPC Syncer

Polls the local Bitcoin Core node's mempool via JSON-RPC and stores a
snapshot of all unconfirmed transactions in a dedicated DuckDB database
(mempool.db).  The web UI attaches this DB alongside the blockchain DB
so users can query both with a single SQL statement.

Usage:
    python btc_mempool_sync.py                         # defaults
    python btc_mempool_sync.py --interval 10           # poll every 10 s
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

import duckdb
import requests


# ---------------------------------------------------------------------------
# Status file — the web UI reads this to show mempool state
# ---------------------------------------------------------------------------

def _status_path(db_path: str) -> str:
    return os.path.join(os.path.dirname(db_path) or ".", "mempool_status.json")


def write_status(db_path: str, **fields):
    """Atomically write mempool sync status."""
    path = _status_path(db_path)
    fields.setdefault("updated_at", datetime.utcnow().isoformat() + "Z")
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(fields, f)
        os.replace(tmp, path)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# RPC helpers (reuse same pattern as btc_sync.py)
# ---------------------------------------------------------------------------

class BitcoinRPC:
    """Minimal JSON-RPC client for Bitcoin Core."""

    def __init__(self, url: str, user: str, password: str):
        self.url = url
        self.auth = (user, password)
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self._id = 0

    def call(self, method: str, params=None):
        self._id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": method,
            "params": params or [],
        }
        resp = self.session.post(self.url, json=payload, auth=self.auth, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            raise RuntimeError(f"RPC error: {data['error']}")
        return data["result"]

    def getrawmempool(self, verbose: bool = True) -> dict:
        return self.call("getrawmempool", [verbose])

    def getmempoolinfo(self) -> dict:
        return self.call("getmempoolinfo")

    def getblockchaininfo(self) -> dict:
        return self.call("getblockchaininfo")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS mempool_transactions (
    txid                VARCHAR NOT NULL,
    size                BIGINT,
    vsize               BIGINT,
    weight              BIGINT,
    fee                 BIGINT,
    modified_fee        BIGINT,
    ancestor_count      BIGINT,
    ancestor_size       BIGINT,
    ancestor_fees       BIGINT,
    descendant_count    BIGINT,
    descendant_size     BIGINT,
    descendant_fees     BIGINT,
    time_entered        BIGINT,
    height_entered      BIGINT,
    bip125_replaceable  BOOLEAN,
    depends             VARCHAR[],
    spentby             VARCHAR[],
    snapshot_time       BIGINT
);

CREATE TABLE IF NOT EXISTS mempool_snapshots (
    snapshot_time       BIGINT NOT NULL,
    tx_count            BIGINT,
    total_bytes         BIGINT,
    total_fee           BIGINT,
    memory_usage        BIGINT,
    max_mempool         BIGINT,
    min_fee_rate        DOUBLE,
    min_relay_fee       DOUBLE
);
"""


def _sat(btc_value) -> int:
    """Convert a BTC float to satoshis (integer)."""
    if btc_value is None:
        return 0
    return int(round(float(btc_value) * 1e8))


def ensure_schema(con: duckdb.DuckDBPyConnection):
    """Create mempool tables if they don't exist."""
    for stmt in CREATE_TABLES_SQL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            con.execute(stmt)


# ---------------------------------------------------------------------------
# Mempool parsing
# ---------------------------------------------------------------------------

def parse_mempool(raw: dict, snapshot_ts: int) -> list:
    """Parse the verbose getrawmempool response into rows."""
    rows = []
    for txid, info in raw.items():
        fees = info.get("fees", {})
        rows.append({
            "txid": txid,
            "size": info.get("size", 0),
            "vsize": info.get("vsize", 0),
            "weight": info.get("weight", 0),
            "fee": _sat(fees.get("base", 0)) if fees else _sat(info.get("fee", 0)),
            "modified_fee": _sat(fees.get("modified", 0)) if fees else _sat(info.get("modifiedfee", 0)),
            "ancestor_count": info.get("ancestorcount", 0),
            "ancestor_size": info.get("ancestorsize", 0),
            "ancestor_fees": _sat(fees.get("ancestor", 0)) if fees else _sat(info.get("ancestorfees", 0)),
            "descendant_count": info.get("descendantcount", 0),
            "descendant_size": info.get("descendantsize", 0),
            "descendant_fees": _sat(fees.get("descendant", 0)) if fees else _sat(info.get("descendantfees", 0)),
            "time_entered": info.get("time", 0),
            "height_entered": info.get("height", 0),
            "bip125_replaceable": info.get("bip125-replaceable", False),
            "depends": info.get("depends", []),
            "spentby": info.get("spentby", []),
            "snapshot_time": snapshot_ts,
        })
    return rows


def parse_mempoolinfo(info: dict, snapshot_ts: int) -> dict:
    """Parse getmempoolinfo into a snapshot row."""
    return {
        "snapshot_time": snapshot_ts,
        "tx_count": info.get("size", 0),
        "total_bytes": info.get("bytes", 0),
        "total_fee": _sat(info.get("total_fee", 0)),
        "memory_usage": info.get("usage", 0),
        "max_mempool": info.get("maxmempool", 0),
        "min_fee_rate": info.get("mempoolminfee", 0),
        "min_relay_fee": info.get("minrelaytxfee", 0),
    }


# ---------------------------------------------------------------------------
# Write to DuckDB
# ---------------------------------------------------------------------------

def refresh_mempool(con: duckdb.DuckDBPyConnection, tx_rows: list, snapshot_row: dict):
    """Replace mempool_transactions and append a snapshot row."""
    con.execute("BEGIN TRANSACTION")
    try:
        # Full replace of current mempool state
        con.execute("DELETE FROM mempool_transactions")

        if tx_rows:
            con.executemany(
                """INSERT INTO mempool_transactions
                   (txid, size, vsize, weight, fee, modified_fee,
                    ancestor_count, ancestor_size, ancestor_fees,
                    descendant_count, descendant_size, descendant_fees,
                    time_entered, height_entered, bip125_replaceable,
                    depends, spentby, snapshot_time)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (r["txid"], r["size"], r["vsize"], r["weight"],
                     r["fee"], r["modified_fee"],
                     r["ancestor_count"], r["ancestor_size"], r["ancestor_fees"],
                     r["descendant_count"], r["descendant_size"], r["descendant_fees"],
                     r["time_entered"], r["height_entered"], r["bip125_replaceable"],
                     r["depends"], r["spentby"], r["snapshot_time"])
                    for r in tx_rows
                ],
            )

        # Append snapshot (keep history)
        con.execute(
            """INSERT INTO mempool_snapshots
               (snapshot_time, tx_count, total_bytes, total_fee,
                memory_usage, max_mempool, min_fee_rate, min_relay_fee)
               VALUES (?,?,?,?,?,?,?,?)""",
            (snapshot_row["snapshot_time"], snapshot_row["tx_count"],
             snapshot_row["total_bytes"], snapshot_row["total_fee"],
             snapshot_row["memory_usage"], snapshot_row["max_mempool"],
             snapshot_row["min_fee_rate"], snapshot_row["min_relay_fee"]),
        )

        # Prune old snapshots — keep last 7 days
        cutoff = int(time.time()) - 7 * 86400
        con.execute("DELETE FROM mempool_snapshots WHERE snapshot_time < ?", (cutoff,))

        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


# ---------------------------------------------------------------------------
# Wait for node
# ---------------------------------------------------------------------------

def wait_for_node(rpc: BitcoinRPC, db_path: str, max_wait: int = 7200):
    """Wait until Bitcoin Core is reachable and past IBD."""
    print("[mempool] Waiting for Bitcoin Core node...", flush=True)
    write_status(db_path, state="waiting_for_node",
                 message="Connecting to Bitcoin Core node...")
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            info = rpc.getblockchaininfo()
            if info.get("initialblockdownload", True):
                blocks = info.get("blocks", 0)
                headers = info.get("headers", 0)
                pct = (blocks / headers * 100) if headers else 0
                print(f"[mempool] Node IBD in progress — {pct:.1f}%", flush=True)
                write_status(db_path, state="node_ibd",
                             message=f"Waiting for node IBD ({pct:.1f}%)")
                time.sleep(15)
                continue
            print(f"[mempool] Node ready — chain={info.get('chain')}", flush=True)
            write_status(db_path, state="ready",
                         message="Node ready, starting mempool sync...")
            return True
        except requests.exceptions.ConnectionError:
            print("[mempool] Node not reachable, retrying...", flush=True)
            write_status(db_path, state="waiting_for_node",
                         message="Waiting for node...")
            time.sleep(5)
        except Exception as e:
            print(f"[mempool] Error: {e}, retrying...", flush=True)
            time.sleep(5)
    print("[mempool] Timed out waiting for node.", flush=True)
    write_status(db_path, state="error",
                 message="Timed out waiting for Bitcoin Core")
    return False


# ---------------------------------------------------------------------------
# Main poll loop
# ---------------------------------------------------------------------------

def poll_loop(rpc: BitcoinRPC, db_path: str, interval: int):
    """Continuously poll the mempool and update the DB."""

    # Ensure schema
    con = duckdb.connect(db_path)
    ensure_schema(con)
    con.close()
    del con

    print(f"[mempool] Polling every {interval}s — DB: {db_path}", flush=True)
    cycle = 0

    while True:
        t0 = time.time()
        try:
            # Fetch mempool data (2 RPC calls)
            raw = rpc.getrawmempool(verbose=True)
            info = rpc.getmempoolinfo()
            snapshot_ts = int(time.time())

            tx_rows = parse_mempool(raw, snapshot_ts)
            snapshot_row = parse_mempoolinfo(info, snapshot_ts)

            # Write to DB (brief lock)
            max_retries = 10
            for attempt in range(max_retries):
                try:
                    con = duckdb.connect(db_path)
                    try:
                        refresh_mempool(con, tx_rows, snapshot_row)
                    finally:
                        con.close()
                    break
                except duckdb.IOException:
                    if attempt < max_retries - 1:
                        time.sleep(0.3)
                    else:
                        raise

            elapsed = time.time() - t0
            cycle += 1

            if cycle % 4 == 1:  # log every ~4 cycles
                print(f"[mempool] {len(tx_rows)} txs, "
                      f"fees={snapshot_row['total_fee'] / 1e8:.4f} BTC, "
                      f"took {elapsed:.1f}s", flush=True)

            write_status(db_path,
                state="running",
                message=f"{len(tx_rows)} unconfirmed transactions",
                tx_count=len(tx_rows),
                total_bytes=snapshot_row["total_bytes"],
                total_fee_sat=snapshot_row["total_fee"],
                min_fee_rate=snapshot_row["min_fee_rate"],
                cycle=cycle,
                last_refresh_ms=round(elapsed * 1000),
            )

        except KeyboardInterrupt:
            print("\n[mempool] Stopped by user.", flush=True)
            break
        except Exception as e:
            print(f"[mempool] Error: {e}", flush=True)
            write_status(db_path, state="error", message=str(e))

        # Sleep until next cycle
        sleep_time = max(0, interval - (time.time() - t0))
        if sleep_time > 0:
            time.sleep(sleep_time)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    rpc_host = os.environ.get("BITCOIN_RPC_HOST", "127.0.0.1")
    rpc_port = os.environ.get("BITCOIN_RPC_PORT", "48332")
    default_rpc_url = f"http://{rpc_host}:{rpc_port}"
    default_rpc_user = os.environ.get("BITCOIN_RPC_USER", "bitcoin")
    default_rpc_pass = os.environ.get("BITCOIN_RPC_PASS", "bitcoin")
    default_db_path = os.environ.get("MEMPOOL_DB_PATH", "mempool.db")

    parser = argparse.ArgumentParser(
        description="Sync Bitcoin mempool into DuckDB",
    )
    parser.add_argument("--rpc-url", default=default_rpc_url,
                        help=f"Bitcoin Core RPC URL (default: {default_rpc_url})")
    parser.add_argument("--rpc-user", default=default_rpc_user, help="RPC username")
    parser.add_argument("--rpc-password", default=default_rpc_pass, help="RPC password")
    parser.add_argument("--db-path", default=default_db_path,
                        help=f"DuckDB database file (default: {default_db_path})")
    parser.add_argument("--interval", type=int, default=15,
                        help="Seconds between mempool snapshots (default: 15)")
    parser.add_argument("--no-wait", action="store_true",
                        help="Skip waiting for the node to finish IBD")
    args = parser.parse_args()

    rpc = BitcoinRPC(args.rpc_url, args.rpc_user, args.rpc_password)

    # Pre-create schema so the web UI can ATTACH mempool.db immediately
    # (tables will be empty until the node finishes IBD and polling starts)
    con = duckdb.connect(args.db_path)
    ensure_schema(con)
    con.close()
    del con
    print(f"[mempool] Schema ready at {args.db_path}", flush=True)

    # Start polling immediately; poll_loop handles errors gracefully
    # and retries every interval. During IBD the mempool is empty,
    # so it simply writes zero rows – harmless.
    poll_loop(rpc, args.db_path, args.interval)


if __name__ == "__main__":
    main()
