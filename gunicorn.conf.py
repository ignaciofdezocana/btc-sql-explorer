"""
Gunicorn configuration — exists mainly to LOG worker lifecycle events.

A worker that blocks past ``timeout`` (e.g. inside a very long checkpoint,
index build, or heavyweight query) is killed and restarted by Gunicorn's
arbiter.  From the outside that looks identical to a kernel OOM kill — but it
is a *different* problem with a *different* fix.  These hooks make the two
distinguishable in the logs:

  * "worker ABORTED" line  -> Gunicorn timeout killed the worker.
  * no shutdown line at all, then a fresh BOOT in entrypoint -> kernel OOM.

Server settings (workers/threads/timeout/bind) are still passed on the
command line in entrypoint.sh; CLI flags override anything here.
"""

import logging

try:
    from logging_setup import setup_logging
    setup_logging()
except Exception:                       # pragma: no cover - never block startup
    pass

_log = logging.getLogger("gunicorn.hooks")


def post_fork(server, worker):
    _log.info("gunicorn worker forked pid=%s", worker.pid)


def worker_int(worker):
    _log.warning("gunicorn worker INTERRUPTED (SIGINT) pid=%s", worker.pid)


def worker_abort(worker):
    # Raised on SIGABRT, which Gunicorn sends when a worker exceeds `timeout`.
    _log.error(
        "gunicorn worker ABORTED pid=%s — likely exceeded the request timeout "
        "(a long checkpoint / index build / query). This is NOT a kernel OOM.",
        worker.pid,
    )


def worker_exit(server, worker):
    _log.warning("gunicorn worker exited pid=%s", worker.pid)


def on_exit(server):
    _log.info("gunicorn master exiting")
