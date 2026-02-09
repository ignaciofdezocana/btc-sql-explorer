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
import json
import os
import sys
import time
from datetime import datetime

import duckdb
import requests
from tqdm import tqdm


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
        resp = self.session.post(self.url, json=payload, auth=self.auth, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            raise RuntimeError(f"RPC error: {data['error']}")
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
        resp = self.session.post(self.url, json=payloads, auth=self.auth, timeout=300)
        resp.raise_for_status()
        results_raw = resp.json()
        by_id = {r["id"]: r for r in results_raw}
        results = []
        for rid in ids:
            r = by_id[rid]
            if r.get("error"):
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
"""

CREATE_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_blocks_hash             ON blocks(hash);
CREATE INDEX IF NOT EXISTS idx_blocks_number           ON blocks(number);
CREATE INDEX IF NOT EXISTS idx_transactions_hash       ON transactions(hash);
CREATE INDEX IF NOT EXISTS idx_transactions_block_hash ON transactions(block_hash);
CREATE INDEX IF NOT EXISTS idx_inputs_tx_hash          ON transaction_inputs(transaction_hash);
CREATE INDEX IF NOT EXISTS idx_outputs_tx_hash         ON transaction_outputs(transaction_hash);
"""


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

def _insert_blocks(con, rows):
    if not rows:
        return
    con.executemany(
        """INSERT INTO blocks
           (hash, number, timestamp, merkle_root, bits, nonce, version,
            weight, size, stripped_size, transaction_count, coinbase_param)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (r["hash"], r["number"], r["timestamp"], r["merkle_root"],
             r["bits"], r["nonce"], r["version"], r["weight"], r["size"],
             r["stripped_size"], r["transaction_count"], r["coinbase_param"])
            for r in rows
        ],
    )


def _insert_transactions(con, rows):
    if not rows:
        return
    con.executemany(
        """INSERT INTO transactions
           (hash, block_hash, block_number, block_timestamp, is_coinbase,
            "index", input_count, output_count, input_value, output_value,
            fee, size, virtual_size, version, lock_time)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (r["hash"], r["block_hash"], r["block_number"], r["block_timestamp"],
             r["is_coinbase"], r["index"], r["input_count"], r["output_count"],
             r["input_value"], r["output_value"], r["fee"], r["size"],
             r["virtual_size"], r["version"], r["lock_time"])
            for r in rows
        ],
    )


def _insert_inputs(con, rows):
    if not rows:
        return
    con.executemany(
        """INSERT INTO transaction_inputs
           (transaction_hash, "index", spent_transaction_hash, spent_output_index,
            script_asm, script_hex, sequence, required_signatures, type,
            addresses, value)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (r["transaction_hash"], r["index"], r["spent_transaction_hash"],
             r["spent_output_index"], r["script_asm"], r["script_hex"],
             r["sequence"], r["required_signatures"], r["type"],
             r["addresses"], r["value"])
            for r in rows
        ],
    )


def _insert_outputs(con, rows):
    if not rows:
        return
    con.executemany(
        """INSERT INTO transaction_outputs
           (transaction_hash, "index", script_asm, script_hex,
            required_signatures, type, addresses, value)
           VALUES (?,?,?,?,?,?,?,?)""",
        [
            (r["transaction_hash"], r["index"], r["script_asm"], r["script_hex"],
             r["required_signatures"], r["type"], r["addresses"], r["value"])
            for r in rows
        ],
    )


def flush_batch(con, block_buf, tx_buf, in_buf, out_buf):
    """Write buffered rows into DuckDB inside a transaction."""
    con.execute("BEGIN TRANSACTION")
    try:
        _insert_blocks(con, block_buf)
        _insert_transactions(con, tx_buf)
        _insert_inputs(con, in_buf)
        _insert_outputs(con, out_buf)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
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
# CLI
# ---------------------------------------------------------------------------

def main():
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
