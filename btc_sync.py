#!/usr/bin/env python3
"""
Bitcoin Blockchain RPC Syncer

Connects to a running Bitcoin Core node via JSON-RPC and ingests blocks
into a DuckDB database. Supports incremental sync: on first run it loads
all blocks from genesis to current tip; on subsequent runs it picks up
where it left off.

Usage:
    python btc_sync.py                          # defaults (testnet4 on localhost)
    python btc_sync.py --rpc-url http://host:48332 --rpc-user user --rpc-password pass
    python btc_sync.py --batch-size 200         # larger batches for faster initial sync
"""

import argparse
import collections
import json
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import duckdb
import pandas as pd
import requests
from tqdm import tqdm

# Resource readings for flush-time memory logging (graceful no-op fallback so
# this module still imports if run outside the app image).
try:
    from logging_setup import proc_rss_mb, cgroup_mem, file_size_mb, log_exc
except Exception:                                       # pragma: no cover
    def proc_rss_mb():
        return None

    def cgroup_mem():
        return (None, None)

    def file_size_mb(path):
        try:
            return os.path.getsize(path) / (1024 * 1024)
        except Exception:
            return None

    def log_exc(logger, msg, *args):
        logger.error(msg, *args, exc_info=True)

_log = logging.getLogger("btc_sync")

# Tunable thresholds (overridable via env) used purely for log severity.
_RPC_SLOW_MS = float(os.environ.get("RPC_SLOW_MS", "5000"))
_RPC_BIG_RESP_MB = float(os.environ.get("RPC_BIG_RESP_MB", "50"))
_FETCH_MAX_ATTEMPTS = int(os.environ.get("FETCH_MAX_ATTEMPTS", "4"))
_FLUSH_SLOW_MS = float(os.environ.get("FLUSH_SLOW_MS", "5000"))
_LOCK_SLOW_MS = float(os.environ.get("LOCK_SLOW_MS", "2000"))
_CKPT_SLOW_MS = float(os.environ.get("CHECKPOINT_SLOW_MS", "10000"))
_NODE_HEALTH_EVERY_N_BATCHES = int(os.environ.get("NODE_HEALTH_EVERY_N_BATCHES", "50"))


def _mem_str():
    """Compact 'rss=... cgroup=used/limit' string for log lines."""
    rss = proc_rss_mb()
    used, limit = cgroup_mem()
    rss_s = f"{rss:.0f}" if rss is not None else "?"
    used_s = f"{used:.0f}" if used is not None else "?"
    lim_s = f"{limit:.0f}" if limit is not None else "?"
    return f"rss_mb={rss_s} cgroup_mb={used_s}/{lim_s}"


def _set_state(state, **kw):
    """Update the shared sync-state dict read by the heartbeat thread."""
    if state is not None:
        state.update(kw)


# ---------------------------------------------------------------------------
# Transaction-weight lookup table for progress estimation
# ---------------------------------------------------------------------------
# Approximate cumulative transaction counts at key Bitcoin mainnet block
# heights.  Used to give users a realistic, near-linear progress bar
# instead of a block-count-based one (which is misleading because early
# blocks are tiny and later blocks are huge).

_TX_CUMULATIVE = [
    (0,         1),
    (100_000,   2_500_000),
    (200_000,   30_000_000),
    (300_000,   80_000_000),
    (400_000,   145_000_000),
    (500_000,   320_000_000),
    (600_000,   530_000_000),
    (700_000,   720_000_000),
    (800_000,   910_000_000),
    (900_000,   1_050_000_000),
    (936_000,   1_100_000_000),
]


def estimate_cumulative_tx(height: int) -> int:
    """Linearly interpolate cumulative tx count for a given block height."""
    if height <= 0:
        return 0
    # Clamp to table range
    if height >= _TX_CUMULATIVE[-1][0]:
        # Extrapolate linearly from last two points
        h1, t1 = _TX_CUMULATIVE[-2]
        h2, t2 = _TX_CUMULATIVE[-1]
        rate = (t2 - t1) / max(h2 - h1, 1)
        return int(t2 + rate * (height - h2))
    # Find surrounding points and interpolate
    for i in range(1, len(_TX_CUMULATIVE)):
        h_hi, t_hi = _TX_CUMULATIVE[i]
        if height <= h_hi:
            h_lo, t_lo = _TX_CUMULATIVE[i - 1]
            frac = (height - h_lo) / max(h_hi - h_lo, 1)
            return int(t_lo + frac * (t_hi - t_lo))
    return _TX_CUMULATIVE[-1][1]


# ---------------------------------------------------------------------------
# Sync-status file — the web UI reads this to show progress
# ---------------------------------------------------------------------------

def _status_path(db_path: str) -> str:
    """Return the path for the sync status JSON file (next to the DB)."""
    return os.path.join(os.path.dirname(db_path) or ".", "sync_status.json")


def write_status(db_path: str, **fields):
    """Atomically write sync status so the web UI can read it."""
    path = _status_path(db_path)
    fields.setdefault("updated_at", datetime.utcnow().isoformat() + "Z")
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(fields, f)
        os.replace(tmp, path)          # atomic on POSIX
    except Exception:
        pass                           # non-fatal

# ---------------------------------------------------------------------------
# RPC helpers
# ---------------------------------------------------------------------------

class BitcoinRPC:
    """JSON-RPC client for Bitcoin Core with batch support."""

    def __init__(self, url: str, user: str, password: str):
        self.url = url
        self.auth = (user, password)
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self._id = 0
        self.last_resp_bytes = 0          # size of the most recent response (for fetch logging)

    def _next_id(self):
        self._id += 1
        return self._id

    def call(self, method: str, params=None):
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params or [],
        }
        t0 = time.time()
        try:
            resp = self.session.post(self.url, json=payload, auth=self.auth, timeout=120)
            resp.raise_for_status()
            n_bytes = len(resp.content)          # capture BEFORE json() in case parsing OOMs
            self.last_resp_bytes = n_bytes
            data = resp.json()
        except Exception as e:
            _log.error("RPC transport error method=%s url=%s err=%s", method, self.url, e)
            raise
        latency_ms = (time.time() - t0) * 1000
        if data.get("error"):
            _log.error("RPC method=%s returned error=%s", method, data["error"])
            raise RuntimeError(f"RPC error: {data['error']}")
        if latency_ms > _RPC_SLOW_MS:
            _log.warning("RPC SLOW method=%s latency_ms=%.0f resp_bytes=%d", method, latency_ms, n_bytes)
        else:
            _log.debug("RPC method=%s latency_ms=%.0f resp_bytes=%d", method, latency_ms, n_bytes)
        return data["result"]

    def call_batch(self, calls: list) -> list:
        """Send multiple RPC calls in a single HTTP request.

        *calls* is a list of (method, params) tuples.
        Returns a list of results in the same order.
        """
        if not calls:
            return []
        payloads = []
        ids = []
        for method, params in calls:
            rid = self._next_id()
            ids.append(rid)
            payloads.append({
                "jsonrpc": "2.0",
                "id": rid,
                "method": method,
                "params": params,
            })
        method0 = calls[0][0]
        t0 = time.time()
        try:
            resp = self.session.post(self.url, json=payloads, auth=self.auth, timeout=300)
            resp.raise_for_status()
            n_bytes = len(resp.content)          # capture BEFORE json() — a huge body can OOM the parse
            self.last_resp_bytes = n_bytes
            results_raw = resp.json()
        except Exception as e:
            _log.error("RPC batch transport error method=%s n_calls=%d url=%s err=%s",
                       method0, len(calls), self.url, e)
            raise
        latency_ms = (time.time() - t0) * 1000
        resp_mb = n_bytes / (1024 * 1024)
        if resp_mb > _RPC_BIG_RESP_MB:
            _log.warning("RPC batch LARGE RESPONSE method=%s n_calls=%d resp_mb=%.1f latency_ms=%.0f "
                         "(large responses are a direct memory-spike cause)",
                         method0, len(calls), resp_mb, latency_ms)
        elif latency_ms > _RPC_SLOW_MS:
            _log.warning("RPC batch SLOW method=%s n_calls=%d resp_mb=%.1f latency_ms=%.0f",
                         method0, len(calls), resp_mb, latency_ms)
        else:
            _log.debug("RPC batch method=%s n_calls=%d resp_mb=%.1f latency_ms=%.0f",
                       method0, len(calls), resp_mb, latency_ms)
        by_id = {r["id"]: r for r in results_raw}
        results = []
        for rid in ids:
            r = by_id[rid]
            if r.get("error"):
                _log.error("RPC batch item error method=%s err=%s", method0, r["error"])
                raise RuntimeError(f"RPC batch error: {r['error']}")
            results.append(r["result"])
        return results

    def getblockcount(self) -> int:
        return self.call("getblockcount")

    def getblockhash(self, height: int) -> str:
        return self.call("getblockhash", [height])

    def getblock(self, blockhash: str, verbosity: int = 2) -> dict:
        return self.call("getblock", [blockhash, verbosity])

    def getblockchaininfo(self) -> dict:
        return self.call("getblockchaininfo")

    def batch_getblockhash(self, heights: list) -> list:
        """Fetch multiple block hashes in one HTTP request."""
        return self.call_batch([("getblockhash", [h]) for h in heights])

    def batch_getblock(self, hashes: list, verbosity: int = 2) -> list:
        """Fetch multiple full blocks in one HTTP request."""
        return self.call_batch([("getblock", [h, verbosity]) for h in hashes])


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS blocks (
    hash            VARCHAR NOT NULL,
    number          BIGINT  NOT NULL,
    timestamp       VARCHAR,
    merkle_root     VARCHAR,
    bits            VARCHAR,
    nonce           VARCHAR,
    version         BIGINT,
    weight          BIGINT,
    size            BIGINT,
    stripped_size   BIGINT,
    transaction_count BIGINT,
    coinbase_param  VARCHAR
);

CREATE TABLE IF NOT EXISTS transactions (
    hash            VARCHAR NOT NULL,
    block_hash      VARCHAR,
    block_number    BIGINT,
    block_timestamp VARCHAR,
    is_coinbase     BOOLEAN,
    "index"         BIGINT,
    input_count     BIGINT,
    output_count    BIGINT,
    input_value     BIGINT,
    output_value    BIGINT,
    fee             BIGINT,
    size            BIGINT,
    virtual_size    BIGINT,
    version         BIGINT,
    lock_time       BIGINT
);

CREATE TABLE IF NOT EXISTS transaction_inputs (
    transaction_hash       VARCHAR,
    "index"                BIGINT,
    spent_transaction_hash VARCHAR,
    spent_output_index     BIGINT,
    script_asm             VARCHAR,
    script_hex             VARCHAR,
    sequence               BIGINT,
    required_signatures    BIGINT,
    type                   VARCHAR,
    addresses              VARCHAR[],
    value                  BIGINT
);

CREATE TABLE IF NOT EXISTS transaction_outputs (
    transaction_hash    VARCHAR,
    "index"             BIGINT,
    script_asm          VARCHAR,
    script_hex          VARCHAR,
    required_signatures BIGINT,
    type                VARCHAR,
    addresses           VARCHAR[],
    value               BIGINT
);

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

CREATE_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_blocks_hash             ON blocks(hash);
CREATE INDEX IF NOT EXISTS idx_blocks_number           ON blocks(number);
CREATE INDEX IF NOT EXISTS idx_transactions_hash       ON transactions(hash);
CREATE INDEX IF NOT EXISTS idx_transactions_block_hash ON transactions(block_hash);
CREATE INDEX IF NOT EXISTS idx_inputs_tx_hash          ON transaction_inputs(transaction_hash);
CREATE INDEX IF NOT EXISTS idx_outputs_tx_hash         ON transaction_outputs(transaction_hash);
"""

DROP_INDEXES_SQL = """
DROP INDEX IF EXISTS idx_blocks_hash;
DROP INDEX IF EXISTS idx_blocks_number;
DROP INDEX IF EXISTS idx_transactions_hash;
DROP INDEX IF EXISTS idx_transactions_block_hash;
DROP INDEX IF EXISTS idx_inputs_tx_hash;
DROP INDEX IF EXISTS idx_outputs_tx_hash;
"""


# Expected column layout per table — used by validate_schema() to detect a
# pre-existing database whose columns drifted from what this code writes.  A
# mismatch makes every bulk INSERT ... SELECT * fail, which looks exactly like
# a "stuck" sync (the retry loop re-tries the same height forever).
EXPECTED_COLUMNS = {
    "blocks": [
        "hash", "number", "timestamp", "merkle_root", "bits", "nonce",
        "version", "weight", "size", "stripped_size", "transaction_count",
        "coinbase_param",
    ],
    "transactions": [
        "hash", "block_hash", "block_number", "block_timestamp", "is_coinbase",
        "index", "input_count", "output_count", "input_value", "output_value",
        "fee", "size", "virtual_size", "version", "lock_time",
    ],
    "transaction_inputs": [
        "transaction_hash", "index", "spent_transaction_hash", "spent_output_index",
        "script_asm", "script_hex", "sequence", "required_signatures", "type",
        "addresses", "value",
    ],
    "transaction_outputs": [
        "transaction_hash", "index", "script_asm", "script_hex",
        "required_signatures", "type", "addresses", "value",
    ],
    "mempool_transactions": [
        "txid", "size", "vsize", "weight", "fee", "modified_fee",
        "ancestor_count", "ancestor_size", "ancestor_fees", "descendant_count",
        "descendant_size", "descendant_fees", "time_entered", "height_entered",
        "bip125_replaceable", "depends", "spentby", "snapshot_time",
    ],
    "mempool_snapshots": [
        "snapshot_time", "tx_count", "total_bytes", "total_fee", "memory_usage",
        "max_mempool", "min_fee_rate", "min_relay_fee",
    ],
}


def ensure_schema(con: duckdb.DuckDBPyConnection):
    """Create tables and indexes if they do not exist."""
    for stmt in CREATE_TABLES_SQL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            con.execute(stmt)
    for stmt in CREATE_INDEXES_SQL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            con.execute(stmt)


def validate_schema(con: duckdb.DuckDBPyConnection) -> bool:
    """Compare the actual DB columns to EXPECTED_COLUMNS and log the result.

    Returns True if every table matches.  Emits a single, greppable
    ``SCHEMA_MISMATCH`` ERROR line per offending table so this failure mode is
    diagnosed instantly instead of masquerading as an OOM or a stall.
    """
    ok = True
    try:
        rows = con.execute(
            """SELECT table_name, column_name
               FROM information_schema.columns
               WHERE table_schema = 'main'
               ORDER BY table_name, ordinal_position"""
        ).fetchall()
    except Exception:
        log_exc(_log, "schema check: could not read information_schema")
        return False

    actual = {}
    for table_name, column_name in rows:
        actual.setdefault(table_name, []).append(column_name)

    for table, expected in EXPECTED_COLUMNS.items():
        got = actual.get(table)
        if got is None:
            _log.warning("schema check: %s MISSING (will be created)", table)
            continue
        if got == expected:
            _log.info("schema check: %s OK (%d cols)", table, len(expected))
        else:
            ok = False
            missing = [c for c in expected if c not in got]
            extra = [c for c in got if c not in expected]
            _log.error("SCHEMA_MISMATCH table=%s expected=%d cols found=%d cols "
                       "missing=%s extra=%s order_ok=%s",
                       table, len(expected), len(got), missing, extra,
                       got[:len(expected)] == expected)
    return ok


def drop_indexes(con: duckdb.DuckDBPyConnection):
    """Drop all indexes — used during bulk sync to speed up writes."""
    t0 = time.time()
    _log.info("drop_indexes start")
    for stmt in DROP_INDEXES_SQL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            con.execute(stmt)
    _log.info("drop_indexes done elapsed_ms=%.0f", (time.time() - t0) * 1000)


def create_indexes(con: duckdb.DuckDBPyConnection):
    """(Re-)create all indexes — called after bulk sync finishes.

    Each index is built and timed individually: rebuilding an index over
    hundreds of millions of rows is memory-heavy, so if an OOM happens here
    the per-index log tells us exactly which one.
    """
    overall = time.time()
    for stmt in CREATE_INDEXES_SQL.strip().split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        t0 = time.time()
        _log.info("create_index start: %s | %s", stmt, _mem_str())
        con.execute(stmt)
        _log.info("create_index done elapsed_ms=%.0f | %s", (time.time() - t0) * 1000, _mem_str())
    _log.info("create_indexes ALL done elapsed_ms=%.0f", (time.time() - overall) * 1000)


def get_synced_height(con: duckdb.DuckDBPyConnection) -> int:
    """Return the highest block number already in the DB, or -1."""
    try:
        row = con.execute("SELECT MAX(number) FROM blocks").fetchone()
        if row and row[0] is not None:
            return int(row[0])
    except Exception:
        pass
    return -1


# ---------------------------------------------------------------------------
# Block parsing (from RPC JSON)
# ---------------------------------------------------------------------------

def _sat(btc_value) -> int:
    """Convert a BTC float to satoshis (integer)."""
    if btc_value is None:
        return 0
    return int(round(float(btc_value) * 1e8))


def parse_rpc_block(block: dict):
    """
    Parse the JSON returned by getblock (verbosity 2) into rows
    matching the existing DuckDB schema.

    Returns (block_row, tx_rows, input_rows, output_rows).
    """

    block_hash = block["hash"]
    block_height = block["height"]
    block_time = block.get("time", 0)
    block_ts = datetime.utcfromtimestamp(block_time).strftime("%Y-%m-%d %H:%M:%S")

    # Coinbase param from first tx
    coinbase_param = ""
    if block.get("tx"):
        first_tx = block["tx"][0]
        if first_tx.get("vin") and first_tx["vin"][0].get("coinbase"):
            coinbase_param = first_tx["vin"][0]["coinbase"]

    block_row = {
        "hash": block_hash,
        "number": block_height,
        "timestamp": block_ts,
        "merkle_root": block.get("merkleroot", ""),
        "bits": block.get("bits", ""),
        "nonce": str(block.get("nonce", "")),
        "version": block.get("version", 0),
        "weight": block.get("weight", 0),
        "size": block.get("size", 0),
        "stripped_size": block.get("strippedsize", 0),
        "transaction_count": len(block.get("tx", [])),
        "coinbase_param": coinbase_param,
    }

    tx_rows = []
    input_rows = []
    output_rows = []

    for tx_idx, tx in enumerate(block.get("tx", [])):
        tx_hash = tx["txid"]
        is_coinbase = tx_idx == 0

        # Compute output_value (sum of all vout values)
        output_value = sum(_sat(vout.get("value", 0)) for vout in tx.get("vout", []))

        # Compute input_value — for coinbase there are no real inputs
        input_value = 0
        if not is_coinbase:
            for vin in tx.get("vin", []):
                prevout = vin.get("prevout", {})
                input_value += _sat(prevout.get("value", 0))

        fee = max(input_value - output_value, 0) if not is_coinbase else 0

        tx_rows.append({
            "hash": tx_hash,
            "block_hash": block_hash,
            "block_number": block_height,
            "block_timestamp": block_ts,
            "is_coinbase": is_coinbase,
            "index": tx_idx,
            "input_count": len(tx.get("vin", [])),
            "output_count": len(tx.get("vout", [])),
            "input_value": input_value,
            "output_value": output_value,
            "fee": fee,
            "size": tx.get("size", 0),
            "virtual_size": tx.get("vsize", tx.get("size", 0)),
            "version": tx.get("version", 0),
            "lock_time": tx.get("locktime", 0),
        })

        # --- inputs ---
        for in_idx, vin in enumerate(tx.get("vin", [])):
            if is_coinbase and in_idx == 0:
                # Coinbase input
                input_rows.append({
                    "transaction_hash": tx_hash,
                    "index": in_idx,
                    "spent_transaction_hash": "0" * 64,
                    "spent_output_index": 0xFFFFFFFF,
                    "script_asm": vin.get("coinbase", ""),
                    "script_hex": vin.get("coinbase", ""),
                    "sequence": vin.get("sequence", 0),
                    "required_signatures": 0,
                    "type": "coinbase",
                    "addresses": ["COINBASE"],
                    "value": 0,
                })
                continue

            script_sig = vin.get("scriptSig", {})
            prevout = vin.get("prevout", {})
            spk = prevout.get("scriptPubKey", {})

            # Address may be a single string or missing
            addr = spk.get("address", "")
            addresses = [addr] if addr else []

            input_rows.append({
                "transaction_hash": tx_hash,
                "index": in_idx,
                "spent_transaction_hash": vin.get("txid", ""),
                "spent_output_index": vin.get("vout", 0),
                "script_asm": script_sig.get("asm", ""),
                "script_hex": script_sig.get("hex", ""),
                "sequence": vin.get("sequence", 0),
                "required_signatures": 1,
                "type": spk.get("type", "unknown"),
                "addresses": addresses,
                "value": _sat(prevout.get("value", 0)),
            })

        # --- outputs ---
        for vout in tx.get("vout", []):
            spk = vout.get("scriptPubKey", {})
            addr = spk.get("address", "")
            addresses = [addr] if addr else []

            output_rows.append({
                "transaction_hash": tx_hash,
                "index": vout.get("n", 0),
                "script_asm": spk.get("asm", ""),
                "script_hex": spk.get("hex", ""),
                "required_signatures": spk.get("reqSigs", 1) if "reqSigs" in spk else 1,
                "type": spk.get("type", "unknown"),
                "addresses": addresses,
                "value": _sat(vout.get("value", 0)),
            })

    return block_row, tx_rows, input_rows, output_rows


# ---------------------------------------------------------------------------
# Batch insert helpers
# ---------------------------------------------------------------------------

_BLOCK_COLS = [
    "hash", "number", "timestamp", "merkle_root", "bits", "nonce",
    "version", "weight", "size", "stripped_size", "transaction_count",
    "coinbase_param",
]

_TX_COLS = [
    "hash", "block_hash", "block_number", "block_timestamp", "is_coinbase",
    "index", "input_count", "output_count", "input_value", "output_value",
    "fee", "size", "virtual_size", "version", "lock_time",
]

_INPUT_COLS = [
    "transaction_hash", "index", "spent_transaction_hash", "spent_output_index",
    "script_asm", "script_hex", "sequence", "required_signatures", "type",
    "addresses", "value",
]

_OUTPUT_COLS = [
    "transaction_hash", "index", "script_asm", "script_hex",
    "required_signatures", "type", "addresses", "value",
]


def _insert_blocks(con, rows):
    if not rows:
        return
    df = pd.DataFrame(rows, columns=_BLOCK_COLS)
    con.execute("INSERT INTO blocks SELECT * FROM df")


def _insert_transactions(con, rows):
    if not rows:
        return
    df = pd.DataFrame(rows, columns=_TX_COLS)
    con.execute("INSERT INTO transactions SELECT * FROM df")


def _insert_inputs(con, rows):
    if not rows:
        return
    df = pd.DataFrame(rows, columns=_INPUT_COLS)
    con.execute("INSERT INTO transaction_inputs SELECT * FROM df")


def _insert_outputs(con, rows):
    if not rows:
        return
    df = pd.DataFrame(rows, columns=_OUTPUT_COLS)
    con.execute("INSERT INTO transaction_outputs SELECT * FROM df")


def flush_batch(con, block_buf, tx_buf, in_buf, out_buf):
    """Write buffered rows into DuckDB inside a transaction.

    Each sub-insert is wrapped so that, on failure, we log WHICH table failed
    and a truncated sample row BEFORE the ROLLBACK throws the context away.
    Previously the rollback masked the real cause.
    """
    con.execute("BEGIN TRANSACTION")
    try:
        for name, fn, buf in (
            ("blocks", _insert_blocks, block_buf),
            ("transactions", _insert_transactions, tx_buf),
            ("transaction_inputs", _insert_inputs, in_buf),
            ("transaction_outputs", _insert_outputs, out_buf),
        ):
            try:
                fn(con, buf)
            except Exception:
                sample = str(buf[0])[:400] if buf else "(empty)"
                log_exc(_log, "flush_batch: INSERT into %s FAILED rows=%d sample=%s",
                        name, len(buf), sample)
                raise
        con.execute("COMMIT")
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass  # DuckDB may have already aborted the transaction
        raise
    block_buf.clear()
    tx_buf.clear()
    in_buf.clear()
    out_buf.clear()


# ---------------------------------------------------------------------------
# Main sync loop
# ---------------------------------------------------------------------------

def wait_for_node(rpc: BitcoinRPC, db_path: str, max_wait: int = 7200):
    """Wait until the Bitcoin Core node is reachable and past IBD.
    
    Default timeout is 7200 seconds (2 hours) to allow for full testnet4 IBD.
    """
    print("Waiting for Bitcoin Core node to be ready...", flush=True)
    write_status(db_path, state="waiting_for_node", message="Connecting to Bitcoin Core node...")
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            info = rpc.getblockchaininfo()
            chain = info.get("chain", "unknown")
            ibd = info.get("initialblockdownload", True)
            blocks = info.get("blocks", 0)
            headers = info.get("headers", 0)
            if ibd:
                pct = (blocks / headers * 100) if headers else 0
                print(f"  IBD in progress \u2014 {blocks}/{headers} blocks ({pct:.1f}%) [{chain}]", flush=True)
                write_status(db_path,
                    state="node_ibd",
                    message=f"Bitcoin Core is syncing the blockchain ({pct:.1f}%)",
                    node_blocks=blocks,
                    node_headers=headers,
                    node_progress_pct=round(pct, 2),
                    chain=chain,
                )
                time.sleep(10)
                continue
            print(f"  Node ready \u2014 chain={chain}, height={blocks}", flush=True)
            write_status(db_path, state="node_ready", message="Bitcoin Core ready, starting block sync...",
                         tip_height=blocks, chain=chain)
            return True
        except requests.exceptions.ConnectionError:
            print("  Node not reachable yet, retrying...", flush=True)
            write_status(db_path, state="waiting_for_node",
                         message="Waiting for Bitcoin Core node to become reachable...")
            time.sleep(3)
        except Exception as e:
            print(f"  Unexpected error: {e}, retrying...", flush=True)
            write_status(db_path, state="waiting_for_node",
                         message=f"Waiting for node... ({e})")
            time.sleep(3)
    print("Timed out waiting for node.", flush=True)
    write_status(db_path, state="error", message="Timed out waiting for Bitcoin Core node")
    return False


def sync(rpc: BitcoinRPC, db_path: str, batch_size: int):
    """Main sync: fetch blocks via RPC and insert into DuckDB.

    The DB connection is opened only to write each batch, then closed
    immediately so other processes (e.g. the web UI) can read the DB
    concurrently.
    """

    # Ensure schema exists (quick open/close)
    con = duckdb.connect(db_path)
    ensure_schema(con)
    start_height = get_synced_height(con) + 1
    con.close()
    del con

    tip_height = rpc.getblockcount()

    if start_height > tip_height:
        print(f"Database is up to date (height {tip_height}).", flush=True)
        write_status(db_path, state="synced", message="Up to date",
                     current_height=tip_height, tip_height=tip_height,
                     progress_pct=100.0, blocks_per_sec=0)
        return

    total = tip_height - start_height + 1
    print(f"Syncing blocks {start_height} to {tip_height} ({total} blocks)...", flush=True)

    block_buf = []
    tx_buf = []
    in_buf = []
    out_buf = []
    sync_t0 = time.time()
    blocks_done = 0

    def flush_and_release():
        """Open DB, write buffered rows, close DB.

        Retries on lock conflict so a concurrent read-only connection
        from the web UI does not crash the sync process.
        """
        if not block_buf:
            return
        max_retries = 30
        for attempt in range(max_retries):
            try:
                db = duckdb.connect(db_path)
                try:
                    flush_batch(db, block_buf, tx_buf, in_buf, out_buf)
                finally:
                    db.close()
                return  # success
            except duckdb.IOException:
                if attempt < max_retries - 1:
                    time.sleep(0.3)
                else:
                    raise  # give up after all retries

    def update_sync_status(current_height: int):
        nonlocal blocks_done
        blocks_done = current_height - start_height + 1
        elapsed = max(time.time() - sync_t0, 0.01)
        bps = blocks_done / elapsed
        remaining = total - blocks_done
        eta_sec = remaining / bps if bps > 0 else 0
        # Percentage relative to the full chain (0 → tip), not just this run
        pct = (current_height / tip_height * 100) if tip_height else 100
        write_status(db_path,
            state="syncing",
            message=f"Syncing block {current_height:,} of {tip_height:,}",
            current_height=current_height,
            start_height=start_height,
            tip_height=tip_height,
            blocks_synced=blocks_done,
            blocks_remaining=remaining,
            total_blocks=total,
            progress_pct=round(pct, 2),
            blocks_per_sec=round(bps, 1),
            elapsed_sec=round(elapsed),
            eta_sec=round(eta_sec),
        )

    write_status(db_path, state="syncing",
                 message=f"Starting sync of {total:,} blocks...",
                 current_height=start_height, tip_height=tip_height,
                 blocks_synced=0, total_blocks=total, progress_pct=0,
                 blocks_per_sec=0, eta_sec=0)

    with tqdm(total=total, unit="blk", desc="Syncing") as pbar:
        height = start_height
        while height <= tip_height:
            # Determine this chunk (up to batch_size blocks)
            chunk_end = min(height + batch_size, tip_height + 1)
            chunk_heights = list(range(height, chunk_end))

            try:
                # 1 HTTP request for all hashes, 1 for all blocks
                hashes = rpc.batch_getblockhash(chunk_heights)
                raw_blocks = rpc.batch_getblock(hashes, verbosity=2)
            except Exception as e:
                print(f"\nError fetching blocks {height}-{chunk_end - 1}: {e}")
                try:
                    flush_and_release()
                except Exception:
                    pass
                write_status(db_path, state="error",
                             message=f"Error fetching block {height}: {e}",
                             current_height=height, tip_height=tip_height)
                sys.exit(1)

            for block_json in raw_blocks:
                block_row, txs, ins, outs = parse_rpc_block(block_json)
                block_buf.append(block_row)
                tx_buf.extend(txs)
                in_buf.extend(ins)
                out_buf.extend(outs)

            flush_and_release()
            # Pause after releasing the write lock so the web UI can
            # acquire a read lock.  DuckDB uses exclusive file locks,
            # so readers are fully blocked while the writer is open.
            # Batch RPC makes fetching so fast that without this gap
            # the DB would be locked >80% of the time.
            time.sleep(0.25)
            update_sync_status(chunk_heights[-1])
            pbar.update(len(chunk_heights))
            height = chunk_end

    # Flush any remaining (shouldn't be any, but safety net)
    flush_and_release()

    # Print summary
    con = duckdb.connect(db_path, read_only=True)
    final = get_synced_height(con)
    row_counts = {}
    for table in ("blocks", "transactions", "transaction_inputs", "transaction_outputs"):
        cnt = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        row_counts[table] = cnt
    con.close()

    print(f"\nSync complete \u2014 DB height: {final}")
    for tbl, cnt in row_counts.items():
        print(f"  {tbl}: {cnt:,} rows")

    elapsed = max(time.time() - sync_t0, 0.01)
    write_status(db_path,
        state="synced",
        message=f"Synced to block {final:,}",
        current_height=final,
        tip_height=tip_height,
        progress_pct=100.0,
        blocks_per_sec=round(total / elapsed, 1),
        elapsed_sec=round(elapsed),
        eta_sec=0,
    )


# ---------------------------------------------------------------------------
# Thread entry point (used by btc_web_app.py for in-process sync)
# ---------------------------------------------------------------------------

def sync_thread(
    con: duckdb.DuckDBPyConnection,
    write_lock: threading.Lock,
    stop_event: threading.Event,
    rpc_url: str,
    rpc_user: str,
    rpc_password: str,
    db_path: str,
    batch_size: int = 50,
    poll_interval: int = 30,
    blockchain_synced: threading.Event | None = None,
    state: dict | None = None,
):
    """Run blockchain sync in a background thread.

    Uses *con* (a shared DuckDB connection) for all writes, acquiring
    *write_lock* for each batch so mempool writes and reader cursors
    are never blocked for long.  Loops forever (with *poll_interval*
    sleeps between tip checks) until *stop_event* is set.

    When *blockchain_synced* is provided, sets it once the chain is
    fully caught up.  The mempool thread waits on this event so it
    doesn't compete for the write lock during initial sync.

    *state* is an optional shared dict the heartbeat thread reads to report
    the live phase/height/last-flush-age.
    """
    rpc = BitcoinRPC(rpc_url, rpc_user, rpc_password)

    # Wait for node to be reachable and past IBD
    _log.info("Waiting for Bitcoin Core node at %s ...", rpc_url)
    _set_state(state, phase="waiting_for_node")
    write_status(db_path, state="waiting_for_node",
                 message="Connecting to Bitcoin Core node...")
    while not stop_event.is_set():
        try:
            info = rpc.getblockchaininfo()
            chain = info.get("chain", "unknown")
            ibd = info.get("initialblockdownload", True)
            blocks = info.get("blocks", 0)
            headers = info.get("headers", 0)
            if ibd:
                pct = (blocks / headers * 100) if headers else 0
                _log.info("node IBD %.1f%% (%d/%d) chain=%s verif=%s — waiting",
                          pct, blocks, headers, chain, info.get("verificationprogress"))
                _set_state(state, phase="node_ibd")
                write_status(db_path,
                    state="node_ibd",
                    message=f"Bitcoin Core is syncing the blockchain ({pct:.1f}%)",
                    node_blocks=blocks, node_headers=headers,
                    node_progress_pct=round(pct, 2), chain=chain)
                stop_event.wait(10)
                continue
            _log.info("node READY chain=%s height=%d headers=%d size_on_disk=%s pruned=%s",
                      chain, blocks, headers, info.get("size_on_disk"), info.get("pruned"))
            write_status(db_path, state="node_ready",
                         message="Bitcoin Core ready, starting block sync...",
                         tip_height=blocks, chain=chain)
            break
        except requests.exceptions.ConnectionError:
            _log.warning("node not reachable at %s — retrying in 3s", rpc_url)
            write_status(db_path, state="waiting_for_node",
                         message="Waiting for Bitcoin Core node...")
            stop_event.wait(3)
        except Exception:
            log_exc(_log, "node wait: unexpected error — retrying in 3s")
            stop_event.wait(3)

    # Main sync loop — runs forever until stop_event
    pass_num = 0
    while not stop_event.is_set():
        pass_num += 1
        t_pass = time.time()
        try:
            _sync_once(con, write_lock, rpc, db_path, batch_size, stop_event,
                       blockchain_synced=blockchain_synced, state=state)
        except duckdb.IOException as e:
            # If this fires, the in-process single-connection model has regressed
            # (something else holds a cross-process file lock). Name it explicitly.
            _set_state(state, phase="error")
            log_exc(_log, "DB_LOCK_CONTENTION pass #%d failed after %.0fs — duckdb.IOException "
                          "(in-process MVCC assumption violated?)", pass_num, time.time() - t_pass)
            write_status(db_path, state="error", message=f"DB lock error: {e}")
        except Exception as e:
            _set_state(state, phase="error")
            log_exc(_log, "sync pass #%d FAILED after %.0fs — retrying in %ds",
                    pass_num, time.time() - t_pass, poll_interval)
            write_status(db_path, state="error", message=f"Sync error: {e}")
        # Wait before checking for new blocks
        stop_event.wait(poll_interval)

    _log.info("sync thread stopped (stop_event set)")


def _fetch_batch(rpc: BitcoinRPC, height: int, batch_size: int, tip_height: int):
    """Fetch a batch of blocks from Bitcoin Core via RPC, with bounded retries.

    Returns (raw_blocks, chunk_heights).  Runs in a background prefetch thread.
    Retries transient RPC/network failures with exponential backoff so a brief
    node hiccup self-heals instead of bubbling up and resetting the whole pass.
    """
    chunk_end = min(height + batch_size, tip_height + 1)
    chunk_heights = list(range(height, chunk_end))
    delay = 1.0
    for attempt in range(1, _FETCH_MAX_ATTEMPTS + 1):
        t0 = time.time()
        try:
            hashes = rpc.batch_getblockhash(chunk_heights)
            raw_blocks = rpc.batch_getblock(hashes, verbosity=2)
            elapsed_ms = (time.time() - t0) * 1000
            tx = sum(len(b.get("tx", [])) for b in raw_blocks)
            resp_mb = getattr(rpc, "last_resp_bytes", 0) / (1024 * 1024)
            _log.debug("fetch ok heights=%d..%d n_blocks=%d tx=%d resp_mb=%.1f elapsed_ms=%.0f",
                       chunk_heights[0], chunk_heights[-1], len(raw_blocks), tx, resp_mb, elapsed_ms)
            return raw_blocks, chunk_heights
        except Exception as e:
            _log.warning("fetch FAIL heights=%d..%d attempt=%d/%d err=%s",
                         chunk_heights[0], chunk_heights[-1], attempt, _FETCH_MAX_ATTEMPTS, e)
            if attempt >= _FETCH_MAX_ATTEMPTS:
                log_exc(_log, "fetch GAVE UP heights=%d..%d after %d attempts (POISON RANGE?)",
                        chunk_heights[0], chunk_heights[-1], _FETCH_MAX_ATTEMPTS)
                raise
            time.sleep(delay)
            delay = min(delay * 2, 30)


def _sync_once(
    con: duckdb.DuckDBPyConnection,
    write_lock: threading.Lock,
    rpc: BitcoinRPC,
    db_path: str,
    batch_size: int,
    stop_event: threading.Event,
    blockchain_synced: threading.Event | None = None,
    state: dict | None = None,
):
    """Sync from current DB height to chain tip (one pass)."""
    with write_lock:
        start_height = get_synced_height(con) + 1

    tip_height = rpc.getblockcount()

    if start_height > tip_height:
        _set_state(state, phase="synced", height=tip_height, tip=tip_height, pct=100.0)
        write_status(db_path, state="synced", message="Up to date",
                     current_height=tip_height, tip_height=tip_height,
                     progress_pct=100.0, blocks_per_sec=0,
                     tx_progress_pct=100.0, tx_per_sec=0, tx_eta_sec=0)
        return

    total = tip_height - start_height + 1
    bulk_mode = total > 1000
    _log.info("=== sync pass start height=%d tip=%d total=%d bulk_mode=%s | %s ===",
              start_height, tip_height, total, bulk_mode, _mem_str())
    _set_state(state, phase="syncing", height=start_height, tip=tip_height, pct=0.0)

    # ------------------------------------------------------------------
    # Bulk-sync mode: drop indexes for much faster writes, lower WAL
    # checkpoint threshold so each checkpoint stalls for a shorter time.
    # ------------------------------------------------------------------
    with write_lock:
        con.execute("SET wal_autocheckpoint = '256MB'")
        if bulk_mode:
            drop_indexes(con)

    sync_t0 = time.time()
    height = start_height

    # Transaction-weighted progress: estimate total work in tx, not blocks
    total_tx_estimate = estimate_cumulative_tx(tip_height)
    base_tx = estimate_cumulative_tx(start_height)  # tx already done before this run
    tx_synced = 0  # actual tx processed in this run

    # Rolling window for tx/sec rate (last 60 seconds)
    _WINDOW_SEC = 60
    _rate_window: collections.deque = collections.deque()  # (timestamp, cumulative_tx_synced)

    write_status(db_path, state="syncing",
                 message=f"Starting sync of {total:,} blocks...",
                 current_height=start_height, tip_height=tip_height,
                 blocks_synced=0, total_blocks=total, progress_pct=0,
                 blocks_per_sec=0, eta_sec=0,
                 tx_progress_pct=0, tx_per_sec=0, tx_eta_sec=0)

    # Adaptive batch sizing: target ~5000 transactions per RPC batch so
    # early tiny blocks fly through while later large blocks stay
    # manageable.  The batch_size adjusts after every RPC batch.
    TARGET_TX = 5000
    MIN_BATCH = 5
    MAX_BATCH = 100   # capped at 100 to prevent oversized RPC responses

    batch_num = 0  # RPC batches fetched

    # ------------------------------------------------------------------
    # Accumulation: instead of writing every RPC batch (~5K tx), we
    # accumulate parsed rows and flush to DuckDB when we reach 50K tx
    # or 10 seconds — whichever comes first.  This reduces DuckDB
    # transaction overhead by ~10x.
    # ------------------------------------------------------------------
    FLUSH_TX_THRESHOLD = 50_000
    FLUSH_SEC_THRESHOLD = 10
    CHECKPOINT_EVERY_N_FLUSHES = 10  # explicit CHECKPOINT every N flushes

    acc_blocks: list = []
    acc_txs: list = []
    acc_ins: list = []
    acc_outs: list = []
    last_flush_time = time.time()
    flush_count = 0
    last_height_flushed = start_height  # track highest block in last flush

    # Pipeline: use a background thread to pre-fetch the next batch
    # while the current one is being parsed / accumulated.
    #
    # IMPORTANT: The loop is ordered as Parse → Adapt → Prefetch → Accumulate/Flush
    # so the prefetch always uses the CORRECTLY adapted batch_size.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="rpc-prefetch") as prefetch:
        # Kick off the first fetch
        future = prefetch.submit(_fetch_batch, rpc, height, batch_size, tip_height)

        while height <= tip_height and not stop_event.is_set():
            # --- Wait for the current fetch to complete ---
            t_rpc = time.time()
            raw_blocks, chunk_heights = future.result()
            rpc_ms = (time.time() - t_rpc) * 1000

            # --- Parse blocks (CPU-only, no lock) ---
            t_parse = time.time()
            batch_tx_count = 0
            for block_json in raw_blocks:
                block_row, txs, ins, outs = parse_rpc_block(block_json)
                acc_blocks.append(block_row)
                acc_txs.extend(txs)
                acc_ins.extend(ins)
                acc_outs.extend(outs)
                batch_tx_count += len(txs)
            parse_ms = (time.time() - t_parse) * 1000

            # --- Adapt batch_size BEFORE prefetch so it uses the right size ---
            if batch_tx_count > 0:
                batch_size = max(MIN_BATCH, min(MAX_BATCH,
                                                int(batch_size * TARGET_TX / batch_tx_count)))

            # --- Submit next fetch with ADAPTED batch_size (overlaps with flush) ---
            next_height = height + len(chunk_heights)
            if next_height <= tip_height and not stop_event.is_set():
                future = prefetch.submit(_fetch_batch, rpc, next_height, batch_size, tip_height)

            # --- Bookkeeping ---
            batch_num += 1
            tx_synced += batch_tx_count

            # Periodic timing log (every 10 RPC batches)
            if batch_num % 10 == 0:
                _log.info("batch %d bs=%d rpc_ms=%.0f parse_ms=%.0f batch_tx=%d acc_tx=%d | %s",
                          batch_num, batch_size, rpc_ms, parse_ms, batch_tx_count,
                          len(acc_txs), _mem_str())

            # Periodic node-health re-poll: catch a node that reorgs or falls
            # back into IBD mid-sync (otherwise invisible).
            if batch_num % _NODE_HEALTH_EVERY_N_BATCHES == 0:
                try:
                    ni = rpc.getblockchaininfo()
                    _log.info("node health: chain=%s blocks=%s headers=%s ibd=%s "
                              "verif=%s size_on_disk=%s pruned=%s",
                              ni.get("chain"), ni.get("blocks"), ni.get("headers"),
                              ni.get("initialblockdownload"), ni.get("verificationprogress"),
                              ni.get("size_on_disk"), ni.get("pruned"))
                    if ni.get("initialblockdownload"):
                        _log.warning("NODE_BACK_IN_IBD node re-entered initial block download "
                                     "mid-sync — possible reorg or resync")
                except Exception as e:
                    _log.warning("node health poll failed: %s", e)

            # --- Check if we should flush accumulated data to DuckDB ---
            now = time.time()
            is_last = next_height > tip_height
            should_flush = (
                len(acc_txs) >= FLUSH_TX_THRESHOLD
                or (now - last_flush_time) >= FLUSH_SEC_THRESHOLD
                or is_last
            )

            if should_flush and len(acc_txs) > 0:
                # Capture counts before flush_batch clears the lists
                n_flush_blk = len(acc_blocks)
                n_flush_tx = len(acc_txs)
                n_flush_in = len(acc_ins)
                n_flush_out = len(acc_outs)

                t_lock = time.time()
                with write_lock:
                    lock_wait_ms = (time.time() - t_lock) * 1000
                    t_write = time.time()
                    flush_batch(con, acc_blocks, acc_txs, acc_ins, acc_outs)
                    write_ms = (time.time() - t_write) * 1000

                flush_count += 1
                last_flush_time = now
                last_height_flushed = chunk_heights[-1]
                _set_state(state, last_flush_time=now)

                log_fn = _log.warning if (write_ms > _FLUSH_SLOW_MS or lock_wait_ms > _LOCK_SLOW_MS) else _log.info
                log_fn("flush %d: %d blk %d tx %d in %d out  lock_wait_ms=%.0f write_ms=%.0f | %s",
                       flush_count, n_flush_blk, n_flush_tx, n_flush_in, n_flush_out,
                       lock_wait_ms, write_ms, _mem_str())

                # Periodic explicit CHECKPOINT to keep WAL small and
                # prevent surprise multi-second stalls.
                if flush_count % CHECKPOINT_EVERY_N_FLUSHES == 0:
                    wal_before = file_size_mb(db_path + ".wal")
                    t_ckpt = time.time()
                    try:
                        with write_lock:
                            con.execute("CHECKPOINT")
                        ckpt_ms = (time.time() - t_ckpt) * 1000
                        wal_after = file_size_mb(db_path + ".wal")
                        ck_log = _log.warning if ckpt_ms > _CKPT_SLOW_MS else _log.info
                        ck_log("checkpoint %d done ckpt_ms=%.0f wal_mb=%s->%s | %s",
                               flush_count // CHECKPOINT_EVERY_N_FLUSHES, ckpt_ms,
                               (f"{wal_before:.0f}" if wal_before is not None else "?"),
                               (f"{wal_after:.0f}" if wal_after is not None else "?"),
                               _mem_str())
                    except Exception:
                        log_exc(_log, "periodic checkpoint failed")

            # --- Transaction-weighted progress & rolling-window ETA ---
            _rate_window.append((now, tx_synced))
            cutoff = now - _WINDOW_SEC
            while _rate_window and _rate_window[0][0] < cutoff:
                _rate_window.popleft()

            if len(_rate_window) >= 2:
                dt = _rate_window[-1][0] - _rate_window[0][0]
                dtx = _rate_window[-1][1] - _rate_window[0][1]
                tx_per_sec = dtx / dt if dt > 0 else 0
            else:
                elapsed = max(now - sync_t0, 0.01)
                tx_per_sec = tx_synced / elapsed

            tx_at_height = base_tx + tx_synced
            tx_progress_pct = (tx_at_height / total_tx_estimate * 100) if total_tx_estimate > 0 else 0

            remaining_tx = max(total_tx_estimate - tx_at_height, 0)
            tx_eta_sec = remaining_tx / tx_per_sec if tx_per_sec > 0 else 0

            # Block-based stats
            blocks_done = next_height - start_height
            elapsed = max(now - sync_t0, 0.01)
            bps = blocks_done / elapsed
            block_pct = (chunk_heights[-1] / tip_height * 100) if tip_height else 100

            # Keep the heartbeat's view of progress current (cheap, every batch).
            _set_state(state, height=chunk_heights[-1], tip=tip_height,
                       pct=round(block_pct, 2))

            # Write status every 5 RPC batches or at the end
            if batch_num % 5 == 0 or is_last:
                write_status(db_path,
                    state="syncing",
                    message=f"Syncing block {chunk_heights[-1]:,} of {tip_height:,}",
                    current_height=chunk_heights[-1],
                    start_height=start_height,
                    tip_height=tip_height,
                    blocks_synced=blocks_done,
                    blocks_remaining=total - blocks_done,
                    total_blocks=total,
                    progress_pct=round(block_pct, 2),
                    blocks_per_sec=round(bps, 1),
                    elapsed_sec=round(elapsed),
                    eta_sec=round(tx_eta_sec),
                    # Transaction-weighted fields
                    tx_synced=tx_synced,
                    tx_per_sec=round(tx_per_sec),
                    tx_progress_pct=round(tx_progress_pct, 2),
                    tx_eta_sec=round(tx_eta_sec))

            height = next_height

    # ------------------------------------------------------------------
    # Post-sync: recreate indexes (if dropped), final checkpoint.
    # Index recreation is a known memory hot-spot — the heartbeat keeps
    # ticking (separate thread) and we label the phase so its lines show
    # 'building_indexes'.
    # ------------------------------------------------------------------
    if bulk_mode and not stop_event.is_set():
        _log.info("recreating indexes (memory hot-spot — watch heartbeat) | %s", _mem_str())
        _set_state(state, phase="building_indexes")
        write_status(db_path, state="syncing",
                     message="Building indexes...",
                     current_height=tip_height, tip_height=tip_height,
                     progress_pct=100.0, tx_progress_pct=round(tx_progress_pct, 2))
        t_idx = time.time()
        try:
            with write_lock:
                create_indexes(con)
            _log.info("all indexes created total_ms=%.0f", (time.time() - t_idx) * 1000)
        except Exception:
            log_exc(_log, "index creation FAILED")

    # Final checkpoint and restore normal auto-checkpoint
    _set_state(state, phase="final_checkpoint")
    wal_before = file_size_mb(db_path + ".wal")
    t_ckpt = time.time()
    try:
        with write_lock:
            con.execute("SET wal_autocheckpoint = '256MB'")
            con.execute("CHECKPOINT")
        ckpt_ms = (time.time() - t_ckpt) * 1000
        wal_after = file_size_mb(db_path + ".wal")
        ck_log = _log.warning if ckpt_ms > _CKPT_SLOW_MS else _log.info
        ck_log("final checkpoint done ckpt_ms=%.0f wal_mb=%s->%s",
               ckpt_ms,
               (f"{wal_before:.0f}" if wal_before is not None else "?"),
               (f"{wal_after:.0f}" if wal_after is not None else "?"))
    except Exception:
        log_exc(_log, "final checkpoint failed")

    if not stop_event.is_set():
        with write_lock:
            final = get_synced_height(con)
        elapsed = max(time.time() - sync_t0, 0.01)
        _set_state(state, phase="synced", height=final, tip=tip_height, pct=100.0)
        _log.info("=== sync pass complete height=%d total=%d elapsed_s=%.0f | %s ===",
                  final, total, elapsed, _mem_str())
        write_status(db_path,
            state="synced",
            message=f"Synced to block {final:,}",
            current_height=final, tip_height=tip_height,
            progress_pct=100.0,
            blocks_per_sec=round(total / elapsed, 1),
            elapsed_sec=round(elapsed), eta_sec=0,
            tx_progress_pct=100.0, tx_per_sec=0, tx_eta_sec=0)

        # Signal mempool thread that blockchain is caught up
        if blockchain_synced is not None and not blockchain_synced.is_set():
            _log.info("blockchain caught up — signalling mempool thread")
            blockchain_synced.set()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    try:
        from logging_setup import setup_logging
        setup_logging()
    except Exception:
        logging.basicConfig(level=logging.INFO)

    # Build defaults from environment variables (Umbrel injects these)
    rpc_host = os.environ.get("BITCOIN_RPC_HOST", "127.0.0.1")
    rpc_port = os.environ.get("BITCOIN_RPC_PORT", "48332")
    default_rpc_url = f"http://{rpc_host}:{rpc_port}"
    default_rpc_user = os.environ.get("BITCOIN_RPC_USER", "bitcoin")
    default_rpc_pass = os.environ.get("BITCOIN_RPC_PASS", "bitcoin")
    default_db_path = os.environ.get("DB_PATH", "bitcoin_blockchain.db")

    parser = argparse.ArgumentParser(
        description="Sync Bitcoin blockchain from a running node into DuckDB",
    )
    parser.add_argument(
        "--rpc-url",
        default=default_rpc_url,
        help=f"Bitcoin Core RPC URL (default: {default_rpc_url})",
    )
    parser.add_argument("--rpc-user", default=default_rpc_user, help="RPC username")
    parser.add_argument("--rpc-password", default=default_rpc_pass, help="RPC password")
    parser.add_argument(
        "--db-path",
        default=default_db_path,
        help=f"DuckDB database file (default: {default_db_path})",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Number of blocks to buffer before writing to DB (default: 50)",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Skip waiting for the node to finish IBD",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run continuously, polling for new blocks every --poll-interval seconds",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=30,
        help="Seconds between new-block checks when --loop is set (default: 30)",
    )
    args = parser.parse_args()

    rpc = BitcoinRPC(args.rpc_url, args.rpc_user, args.rpc_password)

    if not args.no_wait:
        if not wait_for_node(rpc, args.db_path):
            sys.exit(1)

    sync(rpc, args.db_path, args.batch_size)

    if args.loop:
        print(f"\nLoop mode: polling for new blocks every {args.poll_interval}s...", flush=True)
        while True:
            time.sleep(args.poll_interval)
            try:
                sync(rpc, args.db_path, args.batch_size)
            except KeyboardInterrupt:
                print("\nStopped by user.")
                break
            except Exception as e:
                print(f"Sync error (will retry): {e}", flush=True)


if __name__ == "__main__":
    main()
