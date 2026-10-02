"""The append-only record of every change ever observed.

One JSON object per line, one file per UTC day. JSONL rather than a JSON array
because appending to an array means rewriting the whole file, which turns the one
artifact that must never lose data into the one most likely to be corrupted by an
interrupted write. A line append is atomic enough at these sizes and a truncated
final line costs one event, not the file.

Nothing here ever rewrites or deletes. History is the audit trail — if it disagrees
with `state.json`, history is what actually happened.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Iterator

from .models import ChangeEvent

__all__ = ["append_events", "history_path", "read_events"]


def history_path(state_dir: Path, day: str) -> Path:
    """The history file for one UTC day, e.g. `history/2025-01-15.jsonl`."""
    return state_dir / "history" / f"{day}.jsonl"


def append_events(
    state_dir: Path, events: Iterable[ChangeEvent], *, state_version: int | None = None
) -> int:
    """Append events to their day's file. Returns how many were written.

    `state_version` is the generation `state.json` was moved to by these same
    events. Stamping it on the line is what makes the audit trail answer the
    question it will actually be asked — not just "what changed", but "which
    version of the state does this change account for" — without a reader having
    to correlate on timestamps that two cycles a second apart could share.
    """
    events = list(events)
    if not events:
        return 0

    by_day: dict[str, list[ChangeEvent]] = {}
    for event in events:
        # detected_at is ISO-8601; the date is everything before the T.
        day = (event.detected_at or "")[:10] or "undated"
        by_day.setdefault(day, []).append(event)

    def line(event: ChangeEvent) -> str:
        payload = event.to_dict()
        if state_version is not None:
            payload["state_version"] = state_version
        return json.dumps(payload, ensure_ascii=False, default=str) + "\n"

    written = 0
    for day, day_events in by_day.items():
        path = history_path(state_dir, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = "".join(line(event) for event in day_events)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(lines)
        written += len(day_events)
    return written


def read_events(state_dir: Path, day: str) -> Iterator[dict]:
    """Replay one day's events, skipping any line an interrupted write left broken."""
    path = history_path(state_dir, day)
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue
