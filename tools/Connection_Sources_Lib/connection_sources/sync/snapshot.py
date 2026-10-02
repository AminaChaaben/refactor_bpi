"""The last-seen copy of every tracked item — the thing each cycle compares against.

This is close to what `export.py` produces, with one decisive difference: export
deletes the whole source folder before writing it again, because it files items under
their type and a type change would otherwise leave a ghost behind. That wipe is
exactly what a diff cannot survive — it destroys the previous generation, which is
the only record of what things looked like before.

So snapshots are stored flat, keyed by item key alone. Type then becomes an ordinary
mutable field like any other, nothing has to be deleted to keep the folder honest,
and the previous generation survives to be compared against.
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import TrackedItem
from .store import atomic_write_json, read_json

__all__ = ["load_snapshots", "remove_snapshot", "write_snapshots"]

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(value: str, *, default: str = "item") -> str:
    """Same sanitising rule `export.py` uses, so filenames match across both."""
    return _UNSAFE.sub("_", value).strip("_") or default


def _dir_for(state_dir: Path, source: str) -> Path:
    return state_dir / "snapshots" / _safe_name(source, default="source")


def load_snapshots(state_dir: Path, source: str) -> dict[str, TrackedItem]:
    """Every item as it was at the end of the previous cycle, keyed by item key."""
    source_dir = _dir_for(state_dir, source)
    if not source_dir.is_dir():
        return {}

    items: dict[str, TrackedItem] = {}
    for path in source_dir.glob("*.json"):
        data = read_json(path)
        if not isinstance(data, dict) or not data.get("key"):
            # A truncated or hand-edited file: skip it rather than crash the cycle.
            # The item simply looks new again, which is recoverable; a crash is not.
            continue
        item = TrackedItem.from_dict(data)
        items[item.key] = item
    return items


def write_snapshots(state_dir: Path, source: str, items: dict[str, TrackedItem]) -> int:
    """Persist this cycle's items. Only changed files are rewritten.

    The skip compares the whole stored document, not its `content_hash`. The hash
    deliberately excludes `updated_at` — see `TrackedItem.content_hash` — so a file
    kept because its hash matched keeps the *old* `updated_at` too, and the next
    cycle would raise the same UPDATED event with the same `dedupe_key` forever.
    Comparing the document means the file is rewritten whenever anything it stores
    differs, which is the only condition under which skipping the write is safe.
    """
    source_dir = _dir_for(state_dir, source)
    source_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    for key, item in items.items():
        path = source_dir / f"{_safe_name(key)}.json"
        payload = item.to_dict()
        existing = read_json(path)
        if isinstance(existing, dict) and existing == payload:
            continue
        atomic_write_json(path, payload)
        written += 1
    return written


def remove_snapshot(state_dir: Path, source: str, key: str) -> None:
    """Drop one item's snapshot, for an item confirmed deleted at the tracker."""
    path = _dir_for(state_dir, source) / f"{_safe_name(key)}.json"
    path.unlink(missing_ok=True)
