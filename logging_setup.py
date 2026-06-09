#!/usr/bin/env python3
"""
Centralised logging + resource helpers for BTC SQL Explorer.

Why this module exists
----------------------
The sync process has historically "got stuck" on constrained devices with no
usable evidence left behind, because:

  * logs were bare ``print()`` with no timestamps, and
  * they went only to stdout, which is LOST when the container restarts
    (e.g. after an out-of-memory SIGKILL).

This module configures a root logger that writes timestamped, level-tagged,
thread-tagged lines to BOTH:

  * stdout (captured by ``docker logs``), and
  * a rotating file under ``/data/logs/sync.log`` (a mounted volume, so it
    SURVIVES restarts and is downloadable from the Umbrel file browser).

It also provides cheap resource readings (process RSS, cgroup memory, disk
free, file sizes) for the heartbeat thread.  Everything is read straight from
``/proc`` and the cgroup filesystem — **no third-party dependency required** —
with graceful ``None`` fallbacks if a file is unavailable.
"""

import logging
import logging.handlers
import os
import shutil
import time

_CONFIGURED = False

# Format: 2026-06-09T14:03:21.512Z [INFO] [blockchain-sync] btc_sync: message
_FORMAT = "%(asctime)s.%(msecs)03dZ [%(levelname)s] [%(threadName)s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S"


def setup_logging(log_dir=None, level=None):
    """Configure the root logger (idempotent — safe to call from every module).

    Writes to stdout and to a rotating file under *log_dir*
    (default ``$LOG_DIR`` or ``/data/logs``).
    """
    global _CONFIGURED
    if _CONFIGURED:
        return logging.getLogger()

    level_name = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    log_level = getattr(logging, level_name, logging.INFO)

    fmt = logging.Formatter(_FORMAT, datefmt=_DATEFMT)
    fmt.converter = time.gmtime  # log in UTC

    root = logging.getLogger()
    root.setLevel(log_level)
    for h in list(root.handlers):       # drop any pre-existing handlers
        root.removeHandler(h)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)

    log_dir = log_dir or os.environ.get("LOG_DIR", "/data/logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, "sync.log"),
            maxBytes=20 * 1024 * 1024,
            backupCount=5,
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception as e:                       # disk not writable yet, etc.
        root.warning("Could not open log file in %s: %s (stdout only)", log_dir, e)

    _CONFIGURED = True
    return root


def log_exc(logger, msg, *args):
    """Log *msg* at ERROR with the full current traceback attached."""
    logger.error(msg, *args, exc_info=True)


# ---------------------------------------------------------------------------
# Resource readings (no dependencies — straight from /proc and cgroup)
# ---------------------------------------------------------------------------

def proc_rss_mb():
    """Resident set size of this process, in MB (or None)."""
    try:
        with open("/proc/self/statm") as f:
            rss_pages = int(f.read().split()[1])
        return rss_pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    except Exception:
        return None


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return None


def cgroup_mem():
    """Return (used_mb, limit_mb) for this container's cgroup, or (None, None).

    This is the number the kernel checks against the container memory cap, so
    it is the authoritative signal for an impending OOM kill.  Handles both
    cgroup v2 (memory.current/memory.max) and v1
    (memory.usage_in_bytes/memory.limit_in_bytes).
    """
    used = limit = None

    v = _read("/sys/fs/cgroup/memory.current")          # cgroup v2
    if v is not None:
        try:
            used = int(v)
        except ValueError:
            used = None
        m = _read("/sys/fs/cgroup/memory.max")
        if m and m != "max":
            try:
                limit = int(m)
            except ValueError:
                limit = None
    else:                                                # cgroup v1
        v = _read("/sys/fs/cgroup/memory/memory.usage_in_bytes")
        if v is not None:
            try:
                used = int(v)
            except ValueError:
                used = None
        m = _read("/sys/fs/cgroup/memory/memory.limit_in_bytes")
        if m:
            try:
                limit = int(m)
            except ValueError:
                limit = None

    used_mb = used / (1024 * 1024) if used is not None else None
    # A v1 "unlimited" limit is a huge sentinel — treat as unknown.
    limit_mb = limit / (1024 * 1024) if (limit is not None and limit < (1 << 62)) else None
    return used_mb, limit_mb


def sys_mem_mb():
    """Return (total_mb, available_mb) from /proc/meminfo, or (None, None)."""
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, rest = line.partition(":")
                info[k.strip()] = rest.strip()
        total = int(info["MemTotal"].split()[0]) / 1024
        avail = int(info["MemAvailable"].split()[0]) / 1024 if "MemAvailable" in info else None
        return total, avail
    except Exception:
        return None, None


def disk_free_mb(path):
    try:
        return shutil.disk_usage(path).free / (1024 * 1024)
    except Exception:
        return None


def file_size_mb(path):
    try:
        return os.path.getsize(path) / (1024 * 1024)
    except Exception:
        return None


def resource_snapshot(db_path=None):
    """Return a dict of all resource metrics, used by the heartbeat + bundle."""
    used_mb, limit_mb = cgroup_mem()
    total_mb, avail_mb = sys_mem_mb()
    data_dir = (os.path.dirname(db_path) or ".") if db_path else None
    return {
        "rss_mb": proc_rss_mb(),
        "cgroup_used_mb": used_mb,
        "cgroup_limit_mb": limit_mb,
        "sys_total_mb": total_mb,
        "sys_avail_mb": avail_mb,
        "disk_free_mb": disk_free_mb(data_dir) if data_dir else None,
        "db_mb": file_size_mb(db_path) if db_path else None,
        "wal_mb": file_size_mb(db_path + ".wal") if db_path else None,
    }
