#!/usr/bin/env python3
"""
Bitcoin Blockchain SQL Explorer - Web Application

A modern, beautiful web-based SQL interface for exploring Bitcoin blockchain data.

Architecture:  Gunicorn runs this module with --workers 1 --threads 4.
Blockchain sync and mempool sync run as daemon threads inside this
process, sharing a single DuckDB connection.  DuckDB's in-process MVCC
allows concurrent readers + a single writer, eliminating file-lock
contention entirely.
"""

import atexit
import logging
import threading
import zipfile

from flask import Flask, request, jsonify, send_file, send_from_directory
import duckdb
import sqlite3
import pandas as pd
import os
import json
import time
from datetime import datetime
import io
import base64
import plotly.graph_objects as go
import plotly.express as px

from logging_setup import setup_logging, resource_snapshot
from btc_sync import ensure_schema, validate_schema, sync_thread as _blockchain_sync_thread
from btc_mempool_sync import mempool_sync_thread as _mempool_sync_thread

# Configure logging FIRST (timestamped, to stdout + rotating /data/logs file)
# so every line below — including sync-thread output — is captured.
setup_logging()
log = logging.getLogger("web")

APP_VERSION = os.environ.get("APP_VERSION", "1.8.7")
LOG_DIR = os.environ.get("LOG_DIR", "/data/logs")


def _i(x):
    """Format a possibly-None number for a log line."""
    return "?" if x is None else f"{x:.0f}"


app = Flask(__name__)

# CORS for React dev server
try:
    from flask_cors import CORS
    CORS(app)
except ImportError:
    pass

# React build directory
REACT_DIST = os.path.abspath(os.path.join(os.path.dirname(__file__), 'frontend', 'dist'))
REACT_INDEX = os.path.join(REACT_DIST, 'index.html')

DB_PATH = os.environ.get('DB_PATH', 'bitcoin_blockchain.db')
SAVED_QUERIES_PATH = os.environ.get('SAVED_QUERIES_PATH', 'saved_queries.db')


# ---------------------------------------------------------------------------
# Persistent DuckDB connection (shared by sync threads + request handlers)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Startup banner + environment sanity (logged so it's in the downloadable file)
# ---------------------------------------------------------------------------

def _check_writable(dir_path: str) -> bool:
    """Probe whether the data dir is actually writable (catches the old
    read-only /data permission bug at boot instead of via a write-error cascade)."""
    probe = os.path.join(dir_path, ".write_probe")
    try:
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except Exception as e:
        log.error("DATA_DIR_NOT_WRITABLE dir=%s err=%s — every DB write will fail", dir_path, e)
        return False


log.info("==== BTC SQL Explorer starting ====")
log.info("version=%s pid=%s duckdb=%s log_level=%s",
         APP_VERSION, os.getpid(), duckdb.__version__, os.environ.get("LOG_LEVEL", "INFO"))
log.info("config: rpc_host=%s rpc_port=%s rpc_user=%s rpc_pass=*** db_path=%s saved_queries=%s",
         os.environ.get("BITCOIN_RPC_HOST"), os.environ.get("BITCOIN_RPC_PORT"),
         os.environ.get("BITCOIN_RPC_USER"), DB_PATH, SAVED_QUERIES_PATH)
_snap = resource_snapshot(DB_PATH)
# The single most expensive past mistake was misjudging available RAM — log it plainly.
log.info("memory: host=%sMB container_cap=%sMB duckdb_limit=1536MB",
         _i(_snap["sys_total_mb"]), _i(_snap["cgroup_limit_mb"]))
log.info("disk: data_dir free=%sMB | db=%sMB wal=%sMB",
         _i(_snap["disk_free_mb"]), _i(_snap["db_mb"]), _i(_snap["wal_mb"]))
_check_writable(os.path.dirname(DB_PATH) or ".")

# Single connection — DuckDB's in-process MVCC handles concurrent reads
# while a writer holds the lock.  The write_lock serialises the two
# sync threads so only one writes at a time.
#
# memory_limit / threads are env-tunable so they can be matched to the
# container's RAM/CPU cap without a rebuild.
_DUCKDB_MEMORY_LIMIT = os.environ.get("DUCKDB_MEMORY_LIMIT", "1536MB")
_DUCKDB_THREADS = int(os.environ.get("DUCKDB_THREADS", "2"))


def _open_db() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(DB_PATH)
    con.execute(f"SET memory_limit = '{_DUCKDB_MEMORY_LIMIT}'")
    con.execute("SET preserve_insertion_order = false")     # lower memory during bulk inserts
    con.execute(f"SET threads = {_DUCKDB_THREADS}")
    return con


_db: duckdb.DuckDBPyConnection = _open_db()
_write_lock = threading.Lock()
_stop_event = threading.Event()
_blockchain_synced = threading.Event()  # set once blockchain reaches chain tip

# Shared, lock-free-enough state the heartbeat thread reads to report liveness.
_sync_state = {
    "phase": "starting",
    "height": 0,
    "tip": 0,
    "pct": 0.0,
    "last_flush_time": time.time(),
}

# Ensure all tables exist, then validate the layout.  If a pre-existing DB
# predates a schema change (e.g. the v1.8.6 removal of script_asm), the bulk
# INSERT would fail on every flush — so detect the mismatch and rebuild the
# database from genesis.  Saved queries live in a separate file and are kept.
with _write_lock:
    ensure_schema(_db)
    if not validate_schema(_db):
        log.warning("SCHEMA_UPGRADE: existing database does not match this version's "
                    "schema — rebuilding from genesis. Any partial sync is discarded "
                    "(saved queries are unaffected).")
        _db.close()
        for _p in (DB_PATH, DB_PATH + ".wal"):
            try:
                os.remove(_p)
                log.info("removed %s for clean rebuild", _p)
            except FileNotFoundError:
                pass
        _db = _open_db()
        ensure_schema(_db)
        validate_schema(_db)

# Log the DuckDB settings that actually took effect (confirm they applied).
for _s in ("memory_limit", "threads", "preserve_insertion_order", "wal_autocheckpoint"):
    try:
        _v = _db.execute(f"SELECT current_setting('{_s}')").fetchone()[0]
        log.info("duckdb setting %s=%s", _s, _v)
    except Exception:
        pass

log.info("DuckDB connection open — %s", DB_PATH)


def get_read_cursor():
    """Return a DuckDB cursor for read-only queries.

    Cursors are lightweight and can run concurrently with the sync
    writer within the same process (DuckDB MVCC).  Never returns None
    or raises "database busy".
    """
    return _db.cursor()


# ---------------------------------------------------------------------------
# Start background sync threads
# ---------------------------------------------------------------------------

_RPC_URL = "http://{}:{}".format(
    os.environ.get("BITCOIN_RPC_HOST", "127.0.0.1"),
    os.environ.get("BITCOIN_RPC_PORT", "8332"),
)
_RPC_USER = os.environ.get("BITCOIN_RPC_USER", "bitcoin")
_RPC_PASS = os.environ.get("BITCOIN_RPC_PASS", "bitcoin")

# ---------------------------------------------------------------------------
# Heartbeat / resource monitor — the OOM smoking gun.
# Emits one liveness line every HEARTBEAT_SEC with memory/disk/WAL/phase so the
# last lines before a kill survive in the file and point at the cause.
# ---------------------------------------------------------------------------

_HEARTBEAT_SEC = int(os.environ.get("HEARTBEAT_SEC", "15"))
_WAL_WARN_MB = float(os.environ.get("WAL_WARN_MB", "512"))          # 2x the 256MB autocheckpoint
_BLOAT_WARN_MB_PER_1K = float(os.environ.get("DB_BLOAT_WARN_MB_PER_1K", "100"))


def _heartbeat_loop():
    hb = logging.getLogger("heartbeat")
    while not _stop_event.wait(_HEARTBEAT_SEC):
        try:
            snap = resource_snapshot(DB_PATH)
            used, lim = snap["cgroup_used_mb"], snap["cgroup_limit_mb"]
            pct_mem = (used / lim * 100) if (used and lim) else None
            height = _sync_state.get("height") or 0
            tip = _sync_state.get("tip") or 0
            db_mb = snap["db_mb"] or 0
            wal_mb = snap["wal_mb"]
            per1k = (db_mb / height * 1000) if height else None
            age = time.time() - _sync_state.get("last_flush_time", time.time())

            hb.info("phase=%s height=%s tip=%s pct=%.1f rss_mb=%s cgroup_mem_mb=%s/%s (%s%%) "
                    "sys_avail_mb=%s db_mb=%s wal_mb=%s disk_free_mb=%s threads=%d "
                    "last_flush_age_s=%.0f db_mb_per_1k=%s",
                    _sync_state.get("phase"), f"{height:,}", f"{tip:,}",
                    _sync_state.get("pct") or 0.0,
                    _i(snap["rss_mb"]), _i(used), _i(lim),
                    (f"{pct_mem:.1f}" if pct_mem is not None else "?"),
                    _i(snap["sys_avail_mb"]), _i(db_mb), _i(wal_mb),
                    _i(snap["disk_free_mb"]), threading.active_count(), age,
                    (f"{per1k:.0f}" if per1k else "?"))

            if pct_mem is not None and pct_mem >= 90:
                hb.warning("MEMORY_CRITICAL cgroup at %.1f%% of cap (%s/%sMB) — OOM kill imminent",
                           pct_mem, _i(used), _i(lim))
            elif pct_mem is not None and pct_mem >= 80:
                hb.warning("MEMORY_HIGH cgroup at %.1f%% of cap (%s/%sMB)", pct_mem, _i(used), _i(lim))
            if wal_mb and wal_mb > _WAL_WARN_MB:
                hb.warning("WAL_NOT_CHECKPOINTING wal=%sMB exceeds %sMB — checkpointing may have "
                           "stopped (v1.7.x OOM signature)", _i(wal_mb), _i(_WAL_WARN_MB))
            if per1k and per1k > _BLOAT_WARN_MB_PER_1K:
                hb.warning("DB_BLOAT %.0fMB/1k blocks (> %s) — possible rollback bloat "
                           "(v1.8.0 47GB signature)", per1k, _i(_BLOAT_WARN_MB_PER_1K))
        except Exception:
            hb.exception("heartbeat error")


_hb_t = threading.Thread(target=_heartbeat_loop, daemon=True, name="heartbeat")
_hb_t.start()
log.info("heartbeat thread started (every %ds)", _HEARTBEAT_SEC)

_sync_t = threading.Thread(
    target=_blockchain_sync_thread,
    kwargs=dict(
        con=_db,
        write_lock=_write_lock,
        stop_event=_stop_event,
        blockchain_synced=_blockchain_synced,
        rpc_url=_RPC_URL,
        rpc_user=_RPC_USER,
        rpc_password=_RPC_PASS,
        db_path=DB_PATH,
        batch_size=200,
        poll_interval=30,
        state=_sync_state,
    ),
    daemon=True,
    name="blockchain-sync",
)
_sync_t.start()
log.info("blockchain sync thread started")

_mempool_t = threading.Thread(
    target=_mempool_sync_thread,
    kwargs=dict(
        con=_db,
        write_lock=_write_lock,
        stop_event=_stop_event,
        blockchain_synced=_blockchain_synced,
        rpc_url=_RPC_URL,
        rpc_user=_RPC_USER,
        rpc_password=_RPC_PASS,
        db_path=DB_PATH,
        interval=15,
    ),
    daemon=True,
    name="mempool-sync",
)
_mempool_t.start()
log.info("mempool sync thread started")


def _shutdown():
    """Signal sync threads to stop and close the DuckDB connection.

    The presence of this line in the logs is the key OOM tell: if the log just
    stops mid-operation with NO 'shutting down' line and then a fresh boot
    appears, the process was SIGKILLed (kernel OOM), not stopped gracefully.
    """
    log.info("shutting down — stop_event set, joining sync threads")
    _stop_event.set()
    _sync_t.join(timeout=5)
    _mempool_t.join(timeout=5)
    _hb_t.join(timeout=2)
    try:
        _db.close()
    except Exception:
        pass
    log.info("shutdown complete")

atexit.register(_shutdown)


# ---------------------------------------------------------------------------
# Saved Queries DB (SQLite, separate file — no lock conflict)
# ---------------------------------------------------------------------------

_sq_ensured = False


def get_saved_queries_db() -> sqlite3.Connection:
    """Open a SQLite connection for saved queries (separate file)."""
    global _sq_ensured
    con = sqlite3.connect(SAVED_QUERIES_PATH)
    con.row_factory = sqlite3.Row
    if not _sq_ensured:
        con.execute("""
            CREATE TABLE IF NOT EXISTS saved_queries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                query TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        con.commit()
        _sq_ensured = True
    return con

@app.route('/')
def index():
    """Serve the React frontend."""
    if os.path.isfile(REACT_INDEX):
        return send_from_directory(REACT_DIST, 'index.html')
    return jsonify({'error': 'Frontend not built. Run npm run build in frontend/.'}), 500

@app.route('/api/execute', methods=['POST'])
def execute_query():
    """Execute SQL query and return results"""
    try:
        data = request.get_json()
        query = data.get('query', '').strip()
        
        if not query:
            return jsonify({'error': 'No query provided'}), 400
        
        cur = get_read_cursor()

        # Execute query
        start_time = datetime.now()
        result = cur.execute(query).fetchdf()
        execution_time = (datetime.now() - start_time).total_seconds()
        if execution_time > 10:
            log.warning("SLOW_QUERY %.1fs rows=%d sql=%s",
                        execution_time, len(result), query[:200].replace("\n", " "))

        # Convert to JSON-serializable format
        if result.empty:
            return jsonify({
                'success': True,
                'data': [],
                'columns': list(result.columns) if len(result.columns) else [],
                'row_count': 0,
                'column_count': len(result.columns),
                'execution_time': execution_time,
                'message': 'Query executed successfully (no results)'
            })
        
        # Convert DataFrame to list of dictionaries
        data_list = []
        for _, row in result.iterrows():
            row_dict = {}
            for col in result.columns:
                value = row[col]
                if pd.isna(value):
                    row_dict[col] = None
                elif isinstance(value, (int, float)):
                    row_dict[col] = value
                else:
                    row_dict[col] = str(value)
            data_list.append(row_dict)
        
        return jsonify({
            'success': True,
            'data': data_list,
            'columns': list(result.columns),
            'row_count': len(result),
            'column_count': len(result.columns),
            'execution_time': execution_time,
            'message': 'Query executed successfully'
        })

    except Exception as e:
        log.warning("query error: %s | sql=%s", e,
                    (request.get_json(silent=True) or {}).get('query', '')[:200].replace("\n", " "))
        return jsonify({'error': str(e)}), 500

@app.route('/api/sync-status')
def sync_status():
    """Return detailed sync progress so the UI can show a progress screen."""
    # Read the status file written by the sync thread
    status_file = os.path.join(os.path.dirname(DB_PATH) or ".", "sync_status.json")
    file_status = {}
    try:
        if os.path.exists(status_file):
            with open(status_file, "r") as f:
                file_status = json.load(f)
    except Exception:
        pass

    # Also get the DB row count (the file_status might be stale)
    db_blocks = 0
    db_last_block = None
    try:
        cur = get_read_cursor()
        db_blocks = cur.execute("SELECT COUNT(*) FROM blocks").fetchone()[0]
        db_last_block = cur.execute("SELECT MAX(number) FROM blocks").fetchone()[0]
    except Exception:
        pass

    state = file_status.get("state", "unknown")
    tip = file_status.get("tip_height") or file_status.get("node_headers") or 0

    # Read mempool status if available
    mempool_status_file = os.path.join(os.path.dirname(DB_PATH) or ".", "mempool_status.json")
    mempool_status = {}
    try:
        if os.path.exists(mempool_status_file):
            with open(mempool_status_file, "r") as f:
                mempool_status = json.load(f)
    except Exception:
        pass

    return jsonify({
        # Core fields the UI needs
        "state": state,
        "message": file_status.get("message", "Initializing..."),
        "current_height": db_last_block or file_status.get("current_height", 0),
        "tip_height": tip,
        "db_blocks": db_blocks,
        "progress_pct": file_status.get("progress_pct", 0),
        "blocks_per_sec": file_status.get("blocks_per_sec", 0),
        "eta_sec": file_status.get("eta_sec", 0),
        "elapsed_sec": file_status.get("elapsed_sec", 0),
        # Transaction-weighted progress (more accurate than block-based)
        "tx_progress_pct": file_status.get("tx_progress_pct", 0),
        "tx_per_sec": file_status.get("tx_per_sec", 0),
        "tx_eta_sec": file_status.get("tx_eta_sec", 0),
        "tx_synced": file_status.get("tx_synced", 0),
        # Node IBD info (when applicable)
        "node_progress_pct": file_status.get("node_progress_pct"),
        "node_blocks": file_status.get("node_blocks"),
        "node_headers": file_status.get("node_headers"),
        # Convenience
        "syncing": state not in ("synced",),
        "updated_at": file_status.get("updated_at"),
        # Mempool info
        "mempool": {
            "state": mempool_status.get("state", "unknown"),
            "tx_count": mempool_status.get("tx_count", 0),
            "total_fee_sat": mempool_status.get("total_fee_sat", 0),
            "min_fee_rate": mempool_status.get("min_fee_rate", 0),
            "message": mempool_status.get("message", ""),
            "updated_at": mempool_status.get("updated_at"),
        },
    })

@app.route('/api/schema')
def get_schema():
    """Get database schema (all tables in the unified database)"""
    try:
        cur = get_read_cursor()
        
        result = cur.execute("""
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'main'
            ORDER BY table_name, ordinal_position
        """).fetchdf()
        
        # Group by table
        schema = {}
        for _, row in result.iterrows():
            table_name = row['table_name']
            if table_name not in schema:
                schema[table_name] = []
            schema[table_name].append({
                'column': row['column_name'],
                'type': row['data_type']
            })
        
        return jsonify({'success': True, 'schema': schema})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stats')
def get_stats():
    """Get database statistics"""
    try:
        cur = get_read_cursor()
        
        stats_query = """
        SELECT 
            (SELECT COUNT(*) FROM blocks) as total_blocks,
            (SELECT COUNT(*) FROM transactions) as total_transactions,
            (SELECT COUNT(*) FROM transaction_inputs) as total_inputs,
            (SELECT COUNT(*) FROM transaction_outputs) as total_outputs,
            (SELECT MIN(number) FROM blocks) as first_block,
            (SELECT MAX(number) FROM blocks) as last_block
        """
        
        result = cur.execute(stats_query).fetchdf()
        stats = result.iloc[0]
        
        return jsonify({
            'success': True,
            'stats': {
                'total_blocks': int(stats['total_blocks']),
                'total_transactions': int(stats['total_transactions']),
                'total_inputs': int(stats['total_inputs']),
                'total_outputs': int(stats['total_outputs']),
                'first_block': int(stats['first_block']),
                'last_block': int(stats['last_block'])
            }
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/export', methods=['POST'])
def export_results():
    """Export results to CSV"""
    try:
        data = request.get_json()
        results_data = data.get('data', [])
        filename = data.get('filename', 'query_results.csv')
        
        if not results_data:
            return jsonify({'error': 'No data to export'}), 400
        
        # Create DataFrame
        df = pd.DataFrame(results_data)
        
        # Create CSV in memory
        csv_buffer = io.StringIO()
        df.to_csv(csv_buffer, index=False)
        csv_buffer.seek(0)
        
        # Return CSV as downloadable file
        return send_file(
            io.BytesIO(csv_buffer.getvalue().encode('utf-8')),
            mimetype='text/csv',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/chart', methods=['POST'])
def create_chart():
    """Create a chart from query results using Plotly"""
    try:
        data = request.get_json()
        results_data = data.get('data', [])
        chart_type = data.get('chart_type', 'bar')
        x_column = data.get('x_column', '')
        y_column = data.get('y_column', '')
        
        if not results_data:
            return jsonify({'error': 'No data provided for chart'}), 400
        
        if not x_column or not y_column:
            return jsonify({'error': 'X and Y columns must be specified'}), 400
        
        # Create DataFrame from results
        df = pd.DataFrame(results_data)
        
        # Validate columns exist
        if x_column not in df.columns:
            return jsonify({'error': f'Column "{x_column}" not found in data'}), 400
        if y_column not in df.columns and chart_type != 'pie':
            return jsonify({'error': f'Column "{y_column}" not found in data'}), 400
        
        # Clean and prepare data
        if chart_type == 'pie':
            # For pie charts, use x_column as labels and y_column as values (or count)
            if y_column and y_column in df.columns:
                df_clean = df.dropna(subset=[x_column, y_column])
                labels = df_clean[x_column].astype(str)
                values = pd.to_numeric(df_clean[y_column], errors='coerce').fillna(0)
            else:
                # If no y column specified, count occurrences of x_column
                df_clean = df.dropna(subset=[x_column])
                value_counts = df_clean[x_column].value_counts()
                labels = value_counts.index.astype(str)
                values = value_counts.values
        else:
            # For other charts, clean both x and y columns
            df_clean = df.dropna(subset=[x_column, y_column])
            x_data = df_clean[x_column]
            y_data = pd.to_numeric(df_clean[y_column], errors='coerce').fillna(0)
        
        # Limit data points for performance (max 1000 points)
        if chart_type != 'pie' and len(df_clean) > 1000:
            df_clean = df_clean.head(1000)
            x_data = x_data.head(1000)
            y_data = y_data.head(1000)
        elif chart_type == 'pie' and len(labels) > 20:
            # For pie charts, show top 20 categories and group the rest
            if y_column:
                top_data = df_clean.nlargest(19, y_column)
                other_sum = df_clean.drop(top_data.index)[y_column].sum()
                labels = list(top_data[x_column].astype(str)) + ['Others']
                values = list(top_data[y_column]) + [other_sum]
            else:
                labels = labels[:19].tolist() + ['Others']
                values = values[:19].tolist() + [sum(values[19:])]
        
        # Create chart based on type
        fig = None
        
        if chart_type == 'bar':
            fig = px.bar(
                x=x_data, 
                y=y_data,
                labels={'x': x_column, 'y': y_column},
                title=f'{y_column} by {x_column}'
            )
            
        elif chart_type == 'line':
            fig = px.line(
                x=x_data, 
                y=y_data,
                labels={'x': x_column, 'y': y_column},
                title=f'{y_column} over {x_column}'
            )
            
        elif chart_type == 'scatter':
            fig = px.scatter(
                x=x_data, 
                y=y_data,
                labels={'x': x_column, 'y': y_column},
                title=f'{y_column} vs {x_column}'
            )
            
        elif chart_type == 'area':
            fig = px.area(
                x=x_data, 
                y=y_data,
                labels={'x': x_column, 'y': y_column},
                title=f'{y_column} over {x_column}'
            )
            
        elif chart_type == 'pie':
            fig = px.pie(
                values=values,
                names=labels,
                title=f'Distribution of {x_column}'
            )
        
        else:
            return jsonify({'error': f'Unsupported chart type: {chart_type}'}), 400
        
        # Apply consistent styling to match the app theme
        fig.update_layout(
            font=dict(family="Inter, system-ui, sans-serif", size=12),
            plot_bgcolor='#ffffff',
            paper_bgcolor='#ffffff',
            title=dict(font=dict(size=16, weight='bold'), x=0.5),
            margin=dict(l=60, r=60, t=80, b=60),
            height=550,
            width=None,  # Let it be responsive
            autosize=True
        )
        
        # Apply color scheme consistent with the app
        if chart_type == 'bar':
            # Bar charts only have marker_color (fill color)
            fig.update_traces(marker_color='#3b82f6')
        elif chart_type in ['line', 'scatter']:
            # Line and scatter charts can have both marker and line colors
            fig.update_traces(
                marker_color='#3b82f6',
                line_color='#3b82f6'
            )
        elif chart_type == 'area':
            # Area charts have fill and line colors
            fig.update_traces(
                fillcolor='rgba(59, 130, 246, 0.3)',  # Semi-transparent fill
                line_color='#3b82f6'
            )
        elif chart_type == 'pie':
            # Use a nice color palette for pie charts
            colors = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4', '#84cc16', '#f97316']
            fig.update_traces(marker=dict(colors=colors))
        
        # Update grid and axes styling
        if chart_type not in ['pie']:
            fig.update_xaxes(
                gridcolor='#e7e9ee',
                linecolor='#e7e9ee',
                title_font=dict(size=12, weight='bold')
            )
            fig.update_yaxes(
                gridcolor='#e7e9ee',
                linecolor='#e7e9ee',
                title_font=dict(size=12, weight='bold')
            )
        
        # Generate chart data as JSON for frontend rendering
        chart_json = fig.to_json()
        
        return jsonify({
            'success': True,
            'chart_json': chart_json,
            'data_points': len(df_clean) if chart_type != 'pie' else len(labels),
            'chart_type': chart_type
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/examples')
def get_examples():
    """Get example queries organized by categories"""
    examples = {
        "Basic Analysis": {
            "description": "Fundamental blockchain statistics and overview queries",
            "queries": {
                "Basic Blockchain Stats": {
                    "description": "Overall blockchain statistics and health metrics",
                    "query": """
SELECT 
    COUNT(*) as total_blocks,
    MIN(number) as first_block,
    MAX(number) as last_block,
    SUM(transaction_count) as total_transactions,
    ROUND(AVG(size / 1024.0), 2) as avg_block_size_kb,
    ROUND(AVG(transaction_count), 1) as avg_tx_per_block
FROM blocks
WHERE number > 0"""
                },
                "Daily Blockchain Stats": {
                    "description": "Daily statistics showing transactions, BTC volume, fees, and mining activity",
                    "query": """
WITH daily_stats AS (
    SELECT 
        block_timestamp::DATE as date,
        COUNT(*) as tx_count,
        COUNT(CASE WHEN is_coinbase = false THEN 1 END) as regular_tx_count,
        SUM(output_value) as total_output_value,
        SUM(CASE WHEN is_coinbase = false THEN fee ELSE 0 END) as total_fees,
        AVG(CASE WHEN is_coinbase = false THEN fee END) as avg_fee,
        COUNT(DISTINCT block_hash) as blocks_mined,
        AVG(size) as avg_tx_size
    FROM v_transactions
    GROUP BY block_timestamp::DATE
)
SELECT 
    date,
    tx_count as total_transactions_per_day,
    regular_tx_count as regular_transactions_per_day,
    blocks_mined as blocks_mined_per_day,
    ROUND(total_output_value / 100000000.0, 2) as total_btc_transferred,
    ROUND(total_fees / 100000000.0, 6) as total_fees_btc,
    ROUND(avg_fee / 100000000.0, 8) as avg_fee_btc,
    ROUND(avg_tx_size, 0) as avg_tx_size_bytes,
    ROUND(total_btc_transferred / NULLIF(blocks_mined, 0), 2) as btc_per_block
FROM daily_stats 
ORDER BY date DESC
LIMIT 30"""
                }
            }
        },
        "Bitcoin Economics": {
            "description": "Explore Bitcoin's monetary policy, supply curve, and miner economics",
            "queries": {
                "Bitcoin Halving Epochs": {
                    "description": "See Bitcoin's monetary policy in action — block subsidy halving every 210,000 blocks",
                    "query": """
WITH epochs AS (
    SELECT
        FLOOR(b.number / 210000)::INT AS epoch,
        MIN(b.number) AS first_block,
        MAX(b.number) AS last_block,
        MIN(b.timestamp) AS started,
        MAX(b.timestamp) AS ended,
        COUNT(*) AS blocks_mined
    FROM blocks b
    WHERE b.number > 0
    GROUP BY epoch
),
subsidy AS (
    SELECT
        epoch,
        first_block, last_block, started, ended, blocks_mined,
        50.0 / POWER(2, epoch) AS subsidy_btc
    FROM epochs
)
SELECT
    epoch AS halving_era,
    subsidy_btc AS block_reward_btc,
    first_block, last_block,
    LEFT(started, 10) AS start_date,
    LEFT(ended, 10) AS end_date,
    blocks_mined,
    ROUND(blocks_mined * subsidy_btc, 2) AS total_btc_mined
FROM subsidy
ORDER BY epoch"""
                },
                "Bitcoin Supply Curve": {
                    "description": "Track the cumulative BTC supply over time — watch the 21M cap approach",
                    "query": """
WITH monthly_coinbase AS (
    SELECT
        STRFTIME(block_timestamp::DATE, '%Y-%m') AS month,
        SUM(output_value) AS coinbase_satoshis
    FROM v_transactions
    WHERE is_coinbase = true
    GROUP BY month
)
SELECT
    month,
    ROUND(coinbase_satoshis / 100000000.0, 2) AS btc_mined,
    ROUND(SUM(coinbase_satoshis) OVER (ORDER BY month) / 100000000.0, 2) AS cumulative_supply_btc
FROM monthly_coinbase
ORDER BY month"""
                },
                "Miner Revenue: Subsidy vs Fees": {
                    "description": "Watch the historic shift from block rewards to transaction fees as Bitcoin matures",
                    "query": """
WITH monthly AS (
    SELECT
        STRFTIME(block_timestamp::DATE, '%Y-%m') AS month,
        SUM(CASE WHEN is_coinbase THEN output_value ELSE 0 END) AS total_coinbase,
        SUM(CASE WHEN NOT is_coinbase THEN fee ELSE 0 END) AS total_fees
    FROM v_transactions
    GROUP BY month
)
SELECT
    month,
    ROUND(total_coinbase / 100000000.0, 2) AS total_miner_revenue_btc,
    ROUND((total_coinbase - total_fees) / 100000000.0, 2) AS subsidy_btc,
    ROUND(total_fees / 100000000.0, 2) AS fees_btc,
    ROUND(100.0 * total_fees / NULLIF(total_coinbase, 0), 2) AS fee_percentage
FROM monthly
ORDER BY month"""
                }
            }
        },
        "Transaction Analysis": {
            "description": "Deep dive into transaction patterns, complexity, and behavior",
            "queries": {
                "Transaction Complexity Patterns": {
                    "description": "Analyze transaction patterns and identify complex vs simple transactions",
                    "query": """
WITH tx_patterns AS (
    SELECT 
        hash,
        input_count,
        output_count,
        ROUND(output_value / 100000000.0, 4) as output_btc,
        ROUND(fee / 100000000.0, 6) as fee_btc,
        size,
        CASE 
            WHEN input_count = 1 AND output_count = 1 THEN 'Simple (1:1)'
            WHEN input_count = 1 AND output_count = 2 THEN 'Payment + Change (1:2)'
            WHEN input_count > 1 AND output_count = 1 THEN 'Consolidation (N:1)'
            WHEN input_count = 1 AND output_count > 2 THEN 'Distribution (1:N)'
            WHEN input_count > 1 AND output_count > 1 THEN 'Complex (N:M)'
            ELSE 'Other'
        END as pattern_type
    FROM transactions 
    WHERE is_coinbase = false AND input_count > 0 AND output_count > 0
)
SELECT 
    pattern_type,
    COUNT(*) as transaction_count,
    ROUND(AVG(input_count + output_count), 1) as avg_total_ios,
    ROUND(AVG(output_btc), 4) as avg_value_btc,
    ROUND(AVG(fee_btc), 6) as avg_fee_btc,
    ROUND(AVG(size), 0) as avg_size_bytes,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER(), 2) as percentage
FROM tx_patterns 
GROUP BY pattern_type 
ORDER BY transaction_count DESC"""
                },
                "Address Reuse Analysis": {
                    "description": "Analyze Bitcoin address reuse patterns for privacy insights",
                    "query": """
WITH unnested_addresses AS (
    SELECT 
        unnest(addresses) as address,
        transaction_hash,
        value
    FROM transaction_outputs 
    WHERE addresses IS NOT NULL AND array_length(addresses, 1) > 0
),
address_usage AS (
    SELECT 
        address,
        COUNT(DISTINCT transaction_hash) as tx_count,
        SUM(value) as total_received,
        COUNT(*) as output_count
    FROM unnested_addresses
    GROUP BY address
),
usage_categories AS (
    SELECT 
        CASE 
            WHEN tx_count = 1 THEN 'One-time use'
            WHEN tx_count BETWEEN 2 AND 5 THEN 'Light reuse'
            WHEN tx_count BETWEEN 6 AND 20 THEN 'Moderate reuse'
            WHEN tx_count > 20 THEN 'Heavy reuse'
        END as usage_pattern,
        COUNT(*) as address_count,
        AVG(tx_count) as avg_transactions,
        SUM(total_received) as total_value
    FROM address_usage
    GROUP BY 1
)
SELECT 
    usage_pattern,
    address_count,
    ROUND(avg_transactions, 2) as avg_tx_per_address,
    ROUND(total_value / 100000000.0, 2) as total_btc,
    ROUND(100.0 * address_count / SUM(address_count) OVER(), 2) as percentage
FROM usage_categories 
ORDER BY address_count DESC"""
                },
                "Whale Transaction Detection": {
                    "description": "Identify large value transactions (>1,000 BTC) that could indicate whale activity",
                    "query": """
WITH large_transactions AS (
    SELECT 
        hash,
        block_number,
        block_timestamp::DATE as date,
        ROUND(output_value / 100000000.0, 2) as output_btc,
        input_count,
        output_count,
        ROUND(fee / 100000000.0, 4) as fee_btc,
        size,
        CASE 
            WHEN output_count = 1 THEN 'Consolidation'
            WHEN input_count = 1 THEN 'Distribution' 
            ELSE 'Complex'
        END as tx_pattern
    FROM v_transactions
    WHERE output_value > 100000000000  -- More than 1000 BTC
      AND is_coinbase = false
)
SELECT 
    date,
    COUNT(*) as whale_tx_count,
    SUM(output_btc) as total_whale_volume_btc,
    AVG(output_btc) as avg_whale_size_btc,
    MAX(output_btc) as largest_tx_btc,
    SUM(fee_btc) as total_fees_paid_btc,
    ROUND(AVG(input_count), 1) as avg_inputs,
    ROUND(AVG(output_count), 1) as avg_outputs,
    COUNT(CASE WHEN tx_pattern = 'Consolidation' THEN 1 END) as consolidation_count,
    COUNT(CASE WHEN tx_pattern = 'Distribution' THEN 1 END) as distribution_count
FROM large_transactions 
GROUP BY date 
ORDER BY date DESC 
LIMIT 20"""
                },
                "Largest Transactions in History": {
                    "description": "Find the biggest BTC movements ever recorded on your node",
                    "query": """
SELECT
    t.hash AS tx_hash,
    t.block_number,
    LEFT(t.block_timestamp, 10) AS date,
    ROUND(t.output_value / 100000000.0, 2) AS value_btc,
    ROUND(t.fee / 100000000.0, 4) AS fee_btc,
    t.input_count,
    t.output_count,
    t.size AS tx_size_bytes
FROM v_transactions t
WHERE NOT t.is_coinbase
ORDER BY t.output_value DESC
LIMIT 25"""
                }
            }
        },
        "Network & Mining": {
            "description": "Network activity, mining patterns, and fee market analysis",
            "queries": {
                "Fee Market Analysis": {
                    "description": "Analyze transaction fee patterns and market dynamics (sat/vbyte)",
                    "query": """
WITH fee_stats AS (
    SELECT 
        block_timestamp::DATE as date,
        COUNT(*) as tx_count,
        AVG(fee) as avg_fee,
        MIN(fee) as min_fee,
        MAX(fee) as max_fee,
        COUNT(CASE WHEN fee = 0 THEN 1 END) as zero_fee_count,
        AVG(virtual_size) as avg_vsize
    FROM v_transactions
    WHERE is_coinbase = false AND fee IS NOT NULL
    GROUP BY block_timestamp::DATE
)
SELECT 
    date,
    tx_count,
    ROUND(avg_fee / 100000000.0, 8) as avg_fee_btc,
    ROUND(min_fee / 100000000.0, 8) as min_fee_btc,
    ROUND(max_fee / 100000000.0, 8) as max_fee_btc,
    ROUND(avg_fee / NULLIF(avg_vsize, 0), 2) as avg_sat_per_vbyte,
    ROUND(100.0 * zero_fee_count / tx_count, 2) as zero_fee_percentage
FROM fee_stats 
ORDER BY date DESC
LIMIT 10"""
                },
                "Block Mining Efficiency": {
                    "description": "Analyze block mining timing and efficiency patterns",
                    "query": """
WITH block_timing AS (
    SELECT 
        number,
        timestamp,
        size,
        transaction_count,
        LAG(timestamp) OVER (ORDER BY number) as prev_timestamp
    FROM blocks 
    WHERE number > 0
),
timing_analysis AS (
    SELECT 
        number,
        EXTRACT(EPOCH FROM (timestamp::TIMESTAMP - prev_timestamp::TIMESTAMP)) as block_interval_seconds,
        size,
        transaction_count,
        CASE 
            WHEN EXTRACT(EPOCH FROM (timestamp::TIMESTAMP - prev_timestamp::TIMESTAMP)) < 300 THEN 'Very Fast (<5min)'
            WHEN EXTRACT(EPOCH FROM (timestamp::TIMESTAMP - prev_timestamp::TIMESTAMP)) < 600 THEN 'Fast (5-10min)'
            WHEN EXTRACT(EPOCH FROM (timestamp::TIMESTAMP - prev_timestamp::TIMESTAMP)) < 900 THEN 'Normal (10-15min)'
            WHEN EXTRACT(EPOCH FROM (timestamp::TIMESTAMP - prev_timestamp::TIMESTAMP)) < 1800 THEN 'Slow (15-30min)'
            ELSE 'Very Slow (>30min)'
        END as timing_category
    FROM block_timing 
    WHERE prev_timestamp IS NOT NULL
)
SELECT 
    timing_category,
    COUNT(*) as block_count,
    ROUND(AVG(block_interval_seconds / 60.0), 2) as avg_interval_minutes,
    ROUND(MIN(block_interval_seconds / 60.0), 2) as fastest_minutes,
    ROUND(MAX(block_interval_seconds / 60.0), 2) as slowest_minutes,
    ROUND(AVG(size / 1024.0), 2) as avg_block_size_kb,
    ROUND(AVG(transaction_count), 1) as avg_tx_count,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER(), 2) as percentage
FROM timing_analysis 
GROUP BY timing_category 
ORDER BY 
    CASE timing_category 
        WHEN 'Very Fast (<5min)' THEN 1 
        WHEN 'Fast (5-10min)' THEN 2 
        WHEN 'Normal (10-15min)' THEN 3 
        WHEN 'Slow (15-30min)' THEN 4 
        ELSE 5 
    END"""
                },
                "Empty Blocks Mystery": {
                    "description": "Find blocks with zero user transactions — why do miners sometimes mine empty blocks?",
                    "query": """
SELECT
    b.number AS block_height,
    LEFT(b.hash, 16) || '...' AS block_hash,
    b.timestamp,
    b.transaction_count,
    b.size AS block_size_bytes,
    ROUND(b.size / 1024.0, 2) AS size_kb,
    ROUND(
        EXTRACT(EPOCH FROM (
            b.timestamp::TIMESTAMP -
            LAG(b.timestamp) OVER (ORDER BY b.number)::TIMESTAMP
        )) / 60.0, 1
    ) AS minutes_after_prev
FROM blocks b
WHERE b.transaction_count <= 1
  AND b.number > 0
ORDER BY b.number DESC
LIMIT 50"""
                },
                "Block Weight Utilization": {
                    "description": "Is Bitcoin's block space full? Measure how much of the 4 MWU limit miners actually use",
                    "query": """
WITH monthly_blocks AS (
    SELECT
        STRFTIME(timestamp::DATE, '%Y-%m') AS month,
        COUNT(*) AS block_count,
        AVG(weight) AS avg_weight,
        AVG(transaction_count) AS avg_tx_count,
        AVG(size / 1024.0) AS avg_size_kb,
        MAX(weight) AS max_weight
    FROM blocks
    WHERE number > 0 AND weight > 0
    GROUP BY month
)
SELECT
    month,
    block_count,
    ROUND(avg_weight) AS avg_weight_wu,
    ROUND(100.0 * avg_weight / 4000000, 2) AS avg_utilization_pct,
    ROUND(100.0 * max_weight / 4000000, 2) AS peak_utilization_pct,
    ROUND(avg_tx_count, 0) AS avg_tx_per_block,
    ROUND(avg_size_kb, 0) AS avg_block_size_kb
FROM monthly_blocks
ORDER BY month"""
                }
            }
        },
        "Forensics & Curiosities": {
            "description": "Explore hidden patterns, protocol upgrades, and blockchain curiosities",
            "queries": {
                "The Genesis Block & Satoshi's Early Mining": {
                    "description": "Explore the very first Bitcoin blocks and Satoshi Nakamoto's coinbase messages",
                    "query": """
SELECT
    b.number AS block_height,
    b.timestamp,
    b.coinbase_param AS coinbase_message,
    b.transaction_count,
    t.output_value / 100000000.0 AS reward_btc,
    b.nonce,
    LEFT(b.hash, 20) || '...' AS block_hash
FROM blocks b
JOIN transactions t ON t.block_number = b.number AND t.is_coinbase = true
WHERE b.number <= 20
ORDER BY b.number"""
                },
                "SegWit Adoption Over Time": {
                    "description": "Track the adoption of Segregated Witness — Bitcoin's biggest protocol upgrade",
                    "query": """
WITH monthly_tx AS (
    SELECT
        STRFTIME(block_timestamp::DATE, '%Y-%m') AS month,
        COUNT(*) AS total_tx,
        COUNT(CASE WHEN virtual_size < size THEN 1 END) AS segwit_tx,
        ROUND(AVG(CASE WHEN virtual_size < size THEN fee * 1.0 / virtual_size END), 2) AS avg_segwit_feerate,
        ROUND(AVG(CASE WHEN virtual_size >= size THEN fee * 1.0 / size END), 2) AS avg_legacy_feerate
    FROM v_transactions
    WHERE NOT is_coinbase
    GROUP BY month
)
SELECT
    month,
    total_tx,
    segwit_tx,
    ROUND(100.0 * segwit_tx / total_tx, 2) AS segwit_pct,
    avg_segwit_feerate AS segwit_sat_per_vbyte,
    avg_legacy_feerate AS legacy_sat_per_byte
FROM monthly_tx
ORDER BY month"""
                },
                "OP_RETURN: Data Embedded in the Blockchain": {
                    "description": "Discover the hidden data layer — timestamps, proofs, and messages stored forever on-chain",
                    "query": """
WITH monthly_opreturn AS (
    SELECT
        STRFTIME(t.block_timestamp::DATE, '%Y-%m') AS month,
        COUNT(*) AS opreturn_outputs,
        COUNT(DISTINCT t.block_number) AS blocks_with_opreturn,
        COUNT(DISTINCT o.transaction_hash) AS transactions_with_opreturn
    FROM transaction_outputs o
    JOIN v_transactions t ON o.transaction_hash = t.hash
    WHERE o.type = 'nulldata'
    GROUP BY month
)
SELECT
    month,
    opreturn_outputs,
    transactions_with_opreturn,
    blocks_with_opreturn,
    ROUND(opreturn_outputs * 1.0 / NULLIF(transactions_with_opreturn, 0), 2) AS avg_opreturn_per_tx
FROM monthly_opreturn
ORDER BY month"""
                },
                "Dust Outputs: The Unspendable BTC": {
                    "description": "How much Bitcoin is economically stuck in outputs too small to spend?",
                    "query": """
WITH dust AS (
    SELECT
        CASE
            WHEN value < 294 THEN 'Sub-294 sat (below P2WSH dust)'
            WHEN value < 546 THEN '294-545 sat (below P2PKH dust)'
            WHEN value < 1000 THEN '546-999 sat (above dust, very small)'
            WHEN value < 10000 THEN '1,000-9,999 sat'
            ELSE 'Above 10,000 sat'
        END AS category,
        value,
        type
    FROM transaction_outputs
)
SELECT
    category,
    COUNT(*) AS output_count,
    ROUND(SUM(value) / 100000000.0, 8) AS total_btc,
    ROUND(AVG(value), 0) AS avg_sats,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER(), 4) AS pct_of_all_outputs
FROM dust
WHERE value < 10000
GROUP BY category
ORDER BY MIN(value)"""
                },
                "Script Type Distribution": {
                    "description": "Analyze the distribution of different Bitcoin script types (P2PKH, P2SH, P2WPKH, etc.)",
                    "query": """
WITH script_analysis AS (
    SELECT 
        type,
        COUNT(*) as output_count,
        SUM(value) as total_value,
        AVG(value) as avg_value,
        COUNT(CASE WHEN array_length(addresses, 1) > 0 THEN 1 END) as outputs_with_addresses
    FROM transaction_outputs 
    WHERE type IS NOT NULL
    GROUP BY type
)
SELECT 
    type,
    output_count,
    ROUND(total_value / 100000000.0, 2) as total_value_btc,
    ROUND(avg_value / 100000000.0, 6) as avg_value_btc,
    outputs_with_addresses,
    ROUND(100.0 * outputs_with_addresses / output_count, 2) as address_coverage_pct,
    ROUND(100.0 * output_count / SUM(output_count) OVER(), 2) as percentage_of_outputs
FROM script_analysis 
ORDER BY output_count DESC"""
                }
            }
        },
        "Mempool Analysis": {
            "description": "Real-time analysis of unconfirmed transactions waiting in the mempool",
            "queries": {
                "Mempool Overview": {
                    "description": "Current state of the mempool — transaction count, total fees, and size",
                    "query": """
SELECT
    COUNT(*) AS pending_tx_count,
    ROUND(SUM(fee) / 100000000.0, 4) AS total_fees_btc,
    ROUND(SUM(vsize) / 1000000.0, 2) AS total_size_mvb,
    ROUND(AVG(fee * 1.0 / NULLIF(vsize, 0)), 2) AS avg_fee_rate_sat_vb,
    ROUND(MIN(fee * 1.0 / NULLIF(vsize, 0)), 2) AS min_fee_rate_sat_vb,
    ROUND(MAX(fee * 1.0 / NULLIF(vsize, 0)), 2) AS max_fee_rate_sat_vb,
    MIN(time_entered) AS oldest_entry_unix,
    ROUND((EXTRACT(EPOCH FROM NOW()) - MIN(time_entered)) / 60.0, 1) AS oldest_waiting_min,
    COUNT(CASE WHEN bip125_replaceable THEN 1 END) AS rbf_eligible_count
FROM mempool_transactions"""
                },
                "Fee Rate Distribution": {
                    "description": "Histogram of fee rates — see where your transaction sits in the queue",
                    "query": """
WITH fee_buckets AS (
    SELECT
        CASE
            WHEN fee * 1.0 / NULLIF(vsize, 0) < 1   THEN '< 1 sat/vB'
            WHEN fee * 1.0 / NULLIF(vsize, 0) < 2   THEN '1-2 sat/vB'
            WHEN fee * 1.0 / NULLIF(vsize, 0) < 5   THEN '2-5 sat/vB'
            WHEN fee * 1.0 / NULLIF(vsize, 0) < 10  THEN '5-10 sat/vB'
            WHEN fee * 1.0 / NULLIF(vsize, 0) < 20  THEN '10-20 sat/vB'
            WHEN fee * 1.0 / NULLIF(vsize, 0) < 50  THEN '20-50 sat/vB'
            WHEN fee * 1.0 / NULLIF(vsize, 0) < 100 THEN '50-100 sat/vB'
            ELSE '100+ sat/vB'
        END AS fee_bucket,
        fee,
        vsize
    FROM mempool_transactions
)
SELECT
    fee_bucket,
    COUNT(*) AS tx_count,
    ROUND(SUM(vsize) / 1000.0, 1) AS total_kvb,
    ROUND(SUM(fee) / 100000000.0, 6) AS total_fees_btc,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER(), 2) AS pct_of_mempool
FROM fee_buckets
GROUP BY fee_bucket
ORDER BY
    CASE fee_bucket
        WHEN '< 1 sat/vB' THEN 1
        WHEN '1-2 sat/vB' THEN 2
        WHEN '2-5 sat/vB' THEN 3
        WHEN '5-10 sat/vB' THEN 4
        WHEN '10-20 sat/vB' THEN 5
        WHEN '20-50 sat/vB' THEN 6
        WHEN '50-100 sat/vB' THEN 7
        ELSE 8
    END"""
                },
                "Mempool Congestion History": {
                    "description": "Track how mempool congestion changes over time from periodic snapshots",
                    "query": """
SELECT
    snapshot_time,
    tx_count,
    ROUND(total_bytes / 1000000.0, 2) AS total_mb,
    ROUND(total_fee / 100000000.0, 4) AS total_fee_btc,
    ROUND(memory_usage / 1000000.0, 2) AS memory_mb,
    min_fee_rate AS min_fee_btc_kvb
FROM mempool_snapshots
ORDER BY snapshot_time DESC
LIMIT 100"""
                },
                "CPFP Chain Analysis": {
                    "description": "Find transactions with complex dependency chains (Child Pays for Parent)",
                    "query": """
SELECT
    txid,
    vsize,
    ROUND(fee * 1.0 / NULLIF(vsize, 0), 2) AS fee_rate_sat_vb,
    ancestor_count,
    ancestor_size,
    ROUND(ancestor_fees * 1.0 / NULLIF(ancestor_size, 0), 2) AS ancestor_fee_rate,
    descendant_count,
    descendant_size,
    ROUND(descendant_fees * 1.0 / NULLIF(descendant_size, 0), 2) AS descendant_fee_rate,
    ROUND(fee / 100000000.0, 8) AS fee_btc
FROM mempool_transactions
WHERE ancestor_count > 1 OR descendant_count > 1
ORDER BY ancestor_count + descendant_count DESC
LIMIT 50"""
                },
                "RBF-Eligible Transactions": {
                    "description": "Transactions that can be replaced with a higher fee (BIP 125 Replace-by-Fee)",
                    "query": """
SELECT
    txid,
    vsize,
    ROUND(fee * 1.0 / NULLIF(vsize, 0), 2) AS fee_rate_sat_vb,
    ROUND(fee / 100000000.0, 8) AS fee_btc,
    time_entered,
    ROUND((EXTRACT(EPOCH FROM NOW()) - time_entered) / 60.0, 1) AS waiting_minutes,
    ancestor_count,
    descendant_count
FROM mempool_transactions
WHERE bip125_replaceable = true
ORDER BY fee_rate_sat_vb ASC
LIMIT 50"""
                },
                "Oldest Pending Transactions": {
                    "description": "Transactions that have been waiting the longest — likely low fee or stuck",
                    "query": """
SELECT
    txid,
    vsize,
    ROUND(fee * 1.0 / NULLIF(vsize, 0), 2) AS fee_rate_sat_vb,
    ROUND(fee / 100000000.0, 8) AS fee_btc,
    time_entered,
    ROUND((EXTRACT(EPOCH FROM NOW()) - time_entered) / 3600.0, 1) AS waiting_hours,
    bip125_replaceable AS rbf,
    ancestor_count,
    descendant_count
FROM mempool_transactions
ORDER BY time_entered ASC
LIMIT 30"""
                }
            }
        }
    }
    
    return jsonify({'success': True, 'examples': examples})

@app.route('/api/saved-queries', methods=['GET'])
def get_saved_queries():
    """Get all saved queries (from SQLite)"""
    con = None
    try:
        con = get_saved_queries_db()
        rows = con.execute("""
            SELECT id, name, description, query, created_at, updated_at
            FROM saved_queries
            ORDER BY updated_at DESC
        """).fetchall()
        
        queries = []
        for row in rows:
            queries.append({
                'id': row['id'],
                'name': row['name'],
                'description': row['description'] or '',
                'query': row['query'],
                'created_at': row['created_at'],
                'updated_at': row['updated_at'],
            })
        
        return jsonify({'success': True, 'queries': queries})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if con:
            try: con.close()
            except Exception: pass

@app.route('/api/saved-queries', methods=['POST'])
def save_query():
    """Save a new query (to SQLite)"""
    con = None
    try:
        data = request.get_json()
        name = data.get('name', '').strip()
        description = data.get('description', '').strip()
        query = data.get('query', '').strip()
        
        if not name:
            return jsonify({'error': 'Query name is required'}), 400
        if not query:
            return jsonify({'error': 'Query is required'}), 400
        
        con = get_saved_queries_db()
        
        existing = con.execute(
            "SELECT COUNT(*) FROM saved_queries WHERE name = ?", (name,)
        ).fetchone()
        if existing and existing[0] > 0:
            return jsonify({'error': 'A query with this name already exists'}), 400
        
        cur = con.execute("""
            INSERT INTO saved_queries (name, description, query)
            VALUES (?, ?, ?)
        """, (name, description, query))
        con.commit()
        
        return jsonify({
            'success': True,
            'message': f'Query "{name}" saved successfully',
            'id': cur.lastrowid,
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if con:
            try: con.close()
            except Exception: pass

@app.route('/api/saved-queries/<int:query_id>', methods=['PUT'])
def update_saved_query(query_id):
    """Update an existing saved query (in SQLite)"""
    con = None
    try:
        data = request.get_json()
        name = data.get('name', '').strip()
        description = data.get('description', '').strip()
        query = data.get('query', '').strip()
        
        if not name:
            return jsonify({'error': 'Query name is required'}), 400
        if not query:
            return jsonify({'error': 'Query is required'}), 400
        
        con = get_saved_queries_db()
        
        existing = con.execute(
            "SELECT COUNT(*) FROM saved_queries WHERE id = ?", (query_id,)
        ).fetchone()
        if not existing or existing[0] == 0:
            return jsonify({'error': 'Query not found'}), 404
        
        name_check = con.execute(
            "SELECT COUNT(*) FROM saved_queries WHERE name = ? AND id != ?",
            (name, query_id),
        ).fetchone()
        if name_check and name_check[0] > 0:
            return jsonify({'error': 'A query with this name already exists'}), 400
        
        con.execute("""
            UPDATE saved_queries 
            SET name = ?, description = ?, query = ?, updated_at = datetime('now')
            WHERE id = ?
        """, (name, description, query, query_id))
        con.commit()
        
        return jsonify({
            'success': True,
            'message': f'Query "{name}" updated successfully',
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if con:
            try: con.close()
            except Exception: pass

@app.route('/api/saved-queries/<int:query_id>', methods=['DELETE'])
def delete_saved_query(query_id):
    """Delete a saved query (from SQLite)"""
    con = None
    try:
        con = get_saved_queries_db()
        
        row = con.execute(
            "SELECT name FROM saved_queries WHERE id = ?", (query_id,)
        ).fetchone()
        if not row:
            return jsonify({'error': 'Query not found'}), 404
        
        query_name = row['name']
        con.execute("DELETE FROM saved_queries WHERE id = ?", (query_id,))
        con.commit()
        
        return jsonify({
            'success': True,
            'message': f'Query "{query_name}" deleted successfully',
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if con:
            try: con.close()
            except Exception: pass


# ---------------------------------------------------------------------------
# Diagnostics bundle — one click to download everything a model needs
# ---------------------------------------------------------------------------

def _tail_log(n: int = 200) -> str:
    path = os.path.join(LOG_DIR, "sync.log")
    try:
        with open(path, "r", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except Exception as e:
        return f"(could not read {path}: {e})\n"


def _system_snapshot_text() -> str:
    lines = ["BTC SQL Explorer — diagnostics snapshot",
             f"version={APP_VERSION} duckdb={duckdb.__version__} pid={os.getpid()}",
             f"generated_at={datetime.utcnow().isoformat()}Z",
             "--- resources ---"]
    for k, v in resource_snapshot(DB_PATH).items():
        lines.append(f"{k}={'?' if v is None else round(v, 1)}")
    lines.append("--- sync_state ---")
    for k, v in _sync_state.items():
        lines.append(f"{k}={v}")
    lines.append("--- duckdb settings ---")
    try:
        cur = get_read_cursor()
        for s in ("memory_limit", "threads", "preserve_insertion_order", "wal_autocheckpoint"):
            try:
                val = cur.execute("SELECT current_setting(?)", (s,)).fetchone()[0]
                lines.append(f"{s}={val}")
            except Exception:
                pass
    except Exception:
        pass
    try:
        with open(os.path.join(LOG_DIR, "boot_count")) as f:
            lines.append(f"boot_count={f.read().strip()}")
    except Exception:
        pass
    return "\n".join(lines) + "\n"


@app.route('/api/logs/download')
def download_logs():
    """Stream a zip of logs + status files + a fresh system snapshot."""
    try:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            if os.path.isdir(LOG_DIR):
                for fn in sorted(os.listdir(LOG_DIR)):
                    if fn.startswith("sync.log") or fn in ("boot.log", "boot_count"):
                        try:
                            z.write(os.path.join(LOG_DIR, fn), arcname=f"logs/{fn}")
                        except Exception:
                            pass
            data_dir = os.path.dirname(DB_PATH) or "."
            for sf in ("sync_status.json", "mempool_status.json"):
                p = os.path.join(data_dir, sf)
                if os.path.isfile(p):
                    try:
                        z.write(p, arcname=sf)
                    except Exception:
                        pass
            z.writestr("system_snapshot.txt", _system_snapshot_text())
            z.writestr("tail.txt", _tail_log(200))
        buf.seek(0)
        return send_file(buf, mimetype="application/zip", as_attachment=True,
                         download_name="btc-sql-explorer-diagnostics.zip")
    except Exception as e:
        log.exception("log download failed")
        return jsonify({"error": str(e)}), 500


# Serve React SPA static files (JS/CSS/images) when the build exists
if os.path.isfile(os.path.join(REACT_DIST, 'index.html')):
    @app.route('/<path:path>')
    def serve_react(path):
        file_path = os.path.join(REACT_DIST, path)
        if path and os.path.exists(file_path) and os.path.isfile(file_path):
            return send_from_directory(REACT_DIST, path)
        return send_from_directory(REACT_DIST, 'index.html')


if __name__ == '__main__':
    print(f"Database: {DB_PATH}")
    if os.path.isfile(REACT_INDEX):
        print("Serving React UI (frontend/dist)")
    else:
        print("Frontend not built. Run: cd frontend && npm run build")
    print("Bitcoin Blockchain SQL Explorer")
    print("http://localhost:5001")
    print("Press Ctrl+C to stop")
    # debug=False because debug mode uses a reloader that forks a second
    # process, which would start duplicate sync threads.
    app.run(debug=False, host='0.0.0.0', port=5001)