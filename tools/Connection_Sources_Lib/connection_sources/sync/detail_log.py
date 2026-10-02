"""Attaches a durable file handler so a sync cycle's narrative survives even when
the process that ran it never prints anywhere a human will see: `unified_tick.py`
calls `sync_once` in-process and never goes through the CLI's own
`logging.basicConfig`, and `host_bridge.py`/the sandbox listener run `alm-conn` as
a subprocess and only keep its stderr tail on failure (see `_log_sync_result` in
`host_bridge.py`).

Attached to the root logger, not just `connection_sources.*`: every entry point
this project has for a sync (`alm-conn sync`, `python -m
connection_sources.update_state`, `unified_tick.py`'s in-process call) already
calls or can call `configure_detail_logging` once, and doing it at the root means
a caller's own narrative logging (see `scheduler.unified_tick`'s before/after
`state_version` lines) lands in the same file without that module needing to know
this one exists.

Idempotent per resolved path, not per call: `sync_once` calls this on every single
cycle, so this must be safe to call hundreds of times in one process without
piling up duplicate handlers (and therefore duplicate lines) on the second call
onward.
"""

from __future__ import annotations

import logging
from pathlib import Path

__all__ = ["DETAIL_LOG_NAME", "configure_detail_logging"]

DETAIL_LOG_NAME = "sync-detail.log"

_configured: set[str] = set()


def configure_detail_logging(state_dir: Path, *, level: int = logging.INFO) -> Path:
    """Ensure `<state_dir>/scheduler/sync-detail.log` receives every log record
    emitted by this project's sync/scheduler code, in this process, from here on.

    Returns the log path regardless of whether this call actually attached a new
    handler, so a caller can always report where the narrative is going.
    """
    target = (Path(state_dir) / "scheduler" / DETAIL_LOG_NAME).resolve()
    key = str(target)
    root = logging.getLogger()
    if key not in _configured:
        target.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(target, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(handler)
        _configured.add(key)
    # Only raise the effective level, never lower it -- a caller that already
    # asked for DEBUG (e.g. a human running `alm-conn -v`) must not be quieted by
    # a second, unrelated caller of this function asking for its own INFO floor.
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    return target
