"""Writing to disk without ever leaving a half-written file behind.

`state.json` has two writers — this engine and the orchestrator — and it is read by
agents at arbitrary moments. A plain `write_text` is a window in which the file is
truncated but not yet refilled, and a reader landing in that window gets invalid
JSON. Every write here goes to a temporary file in the same directory and is then
moved into place with `os.replace`, which is atomic on both Windows and POSIX, so a
reader sees either the old file or the new one and never a partial one.

The lock is advisory and coarse: one writer at a time across the whole state
directory, taken only for the read-modify-write of `state.json`. Stale locks (a
process killed mid-cycle) expire by age rather than needing manual cleanup.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

__all__ = ["atomic_write_json", "file_lock", "now_iso", "read_json"]

LOCK_TIMEOUT = 30.0
LOCK_STALE_AFTER = 120.0


def now_iso() -> str:
    """Current UTC instant, ISO-8601 with a trailing Z."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_json(path: Path, default: Any = None) -> Any:
    """Parse a JSON file, returning `default` if it is absent or unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def atomic_write_json(path: Path, payload: Any) -> None:
    """Serialise to `path` via a temp file in the same directory, then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


@contextmanager
def file_lock(path: Path, *, timeout: float = LOCK_TIMEOUT) -> Iterator[None]:
    """Hold an advisory lock for the duration of the block.

    Uses exclusive file creation, which is atomic on every platform we run on and
    needs no third-party dependency.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    acquired = False
    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            acquired = True
            break
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > LOCK_STALE_AFTER:
                # The holder died mid-cycle. Reclaim rather than block forever.
                path.unlink(missing_ok=True)
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"could not acquire {path} within {timeout}s; another sync is running"
                )
            time.sleep(0.1)
    try:
        yield
    finally:
        if acquired:
            path.unlink(missing_ok=True)
