"""Timestamped, point-in-time archives: a full copy of `state.json` and a record of
what one sync cycle changed, each written under its own filename so every generation
is kept, not just the latest.

Distinct from the two things this could be confused with:

- `snapshot.py` -- per-item, last-seen copies used to compute the *next* diff. Those
  are overwritten in place; they answer "what does the tracker look like now", not
  "what did it look like at any point in the past".
- `history.py` -- a continuous, append-only per-UTC-day changelog, the permanent
  audit trail. It answers "what has ever changed"; finding one specific cycle's
  changes means replaying a whole day's file and filtering.

This module answers a different question: "what did state.json look like right
after the sync that ran at 14:32, and what did that one sync change" -- addressable
by filename alone, which is what restoring to a specific point or auditing one
specific run needs.

Both writes here happen strictly after `state.json` itself has been atomically
written (see `sync_once` in `runner.py`) -- a failure archiving must never be mistaken
for a failure writing the state a cycle actually depends on.

Only called when a cycle actually observed changes (see the `if result.events`
guard at the call site in `runner.py`): a poller firing every few minutes spends the
overwhelming majority of its cycles observing nothing, and an archive pair for every
one of those ticks would grow without bound for no reason -- the interesting moments
are exactly the ones where something changed, which `state_version` already marks.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable

from .models import ChangeEvent
from .store import atomic_write_json, read_json

log = logging.getLogger("connection_sources.sync.archive")

__all__ = [
    "DEFAULT_KEEP",
    "archive_state",
    "filesystem_safe_timestamp",
    "prune",
    "write_change_log",
]

#: How many generations each archive subdirectory keeps. Bounded because the
#: highest-frequency caller is not the Windows task this module was written for
#: but the in-sandbox poller (`Application/sandbox-image/listener.py`'s
#: `alm-conn sync --watch --interval 60`), which writes into a Kubernetes PVC. An
#: unpruned archive there fills the volume and takes the sandbox down with it --
#: a slower, quieter version of the failure this module exists to help diagnose.
DEFAULT_KEEP = 200


def _keep() -> int:
    """`ALM_ARCHIVE_KEEP` if it is a usable positive integer, else `DEFAULT_KEEP`.

    Read per call rather than at import so a long-lived poller picks up a change
    without a restart. `0` or a negative value disables pruning entirely, for an
    operator who genuinely wants every generation and is managing the disk
    themselves.
    """
    raw = os.environ.get("ALM_ARCHIVE_KEEP")
    if raw is None:
        return DEFAULT_KEEP
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_KEEP


def prune(directory: Path, *, keep: int | None = None) -> int:
    """Delete all but the newest `keep` files in `directory`. Returns how many went.

    Ordering is by filename, not mtime: both writers here embed a UTC timestamp in
    a fixed-width, zero-padded format, so lexical order *is* chronological order and
    needs no `stat` call per file. A copied or restored archive file therefore sorts
    by when its sync ran, which is the question the archive answers, rather than by
    when someone happened to touch it.

    Only `*.json` files not starting with `.` are considered, which is precisely the
    set this module writes. `atomic_write_json` stages every write as a dotted
    `.<name>.<pid>.tmp` sibling in this same directory, and those sort *before* every
    real archive name -- an unfiltered prune would therefore delete another process's
    in-flight temp file first, corrupting a concurrent sync's write rather than
    tidying up after it. The sandbox poller and a host sync can genuinely overlap
    here (they take different locks -- see `runner.sync_once`), so this is a real
    race, not a theoretical one.

    Never raises. Pruning is housekeeping for an archive that is itself optional --
    a file that cannot be deleted (locked by a reader, a permissions quirk on the
    PVC) must not propagate into the sync cycle that produced it.
    """
    if keep is None:
        keep = _keep()
    if keep <= 0:
        return 0
    try:
        entries = sorted(
            entry
            for entry in directory.iterdir()
            if entry.is_file()
            and entry.suffix == ".json"
            and not entry.name.startswith(".")
        )
    except OSError:
        return 0
    removed = 0
    for entry in entries[:-keep] if len(entries) > keep else []:
        try:
            entry.unlink()
            removed += 1
        except OSError:
            continue
    if removed:
        log.info("pruned %d old archive file(s) in %s (keeping newest %d)", removed, directory, keep)
    return removed


def filesystem_safe_timestamp(iso_timestamp: str) -> str:
    """`2026-09-19T02:58:00Z` -> `2026-09-19T02-58-00Z`.

    Colons are illegal in a Windows filename, and this project's primary caller of
    the sync this module archives is a Windows Task Scheduler launcher (see
    `Orchestrateur_Lib/scheduler/unified_tick.py` and the older
    `connection_sources/schedule.py`) -- a timestamp straight from `now_iso()` would
    make every archive write fail on the platform this runs on most.
    """
    return iso_timestamp.replace(":", "-")


def _archive_dir(state_dir: Path) -> Path:
    return state_dir / "archive"


def archive_state(state_dir: Path, state_path: Path, *, timestamp: str) -> Path | None:
    """Copy `state_path`'s current, already-written contents to a timestamped file
    under `<state_dir>/archive/state/`.

    Takes `state_path` rather than assuming `state_dir / "state.json"` because a
    project can declare its state.json lives elsewhere (`sync.state_path` in
    `sources.json` -- see `sync_once`'s two branches); the archive always follows
    whichever document this cycle actually wrote.

    Returns None, writing nothing, if that document cannot be read back -- a state
    write that itself failed must not also produce a phantom archive of nothing.
    """
    document = read_json(state_path)
    if document is None:
        log.warning("archive_state: could not read %s -- nothing archived", state_path)
        return None
    target = (
        _archive_dir(state_dir) / "state" / f"state_{filesystem_safe_timestamp(timestamp)}.json"
    )
    atomic_write_json(target, document)
    log.info("archived state snapshot -> %s", target)
    prune(target.parent)
    return target


def write_change_log(
    state_dir: Path,
    events: Iterable[ChangeEvent],
    *,
    state_version: int,
    timestamp: str,
) -> Path | None:
    """One file per sync cycle, named by when it ran, holding exactly the changes
    that cycle observed -- the same events `history.append_events` folds into the
    day's continuous log, restated here addressable by run rather than by day.

    Returns None and writes nothing when there is nothing to record.
    """
    events = list(events)
    if not events:
        return None
    target = (
        _archive_dir(state_dir) / "changes" / f"changes_{filesystem_safe_timestamp(timestamp)}.json"
    )
    payload = {
        "timestamp": timestamp,
        "state_version": state_version,
        "event_count": len(events),
        # `summary` is `describe()`'s prose sentence alongside every structured
        # field `to_dict()` already carries -- opening this file directly (a
        # human reading a specific run's changes, not code) should not require
        # mentally re-deriving "kind X with field Y going from A to B" into a
        # sentence; both forms are kept so nothing that read the old shape breaks.
        "events": [{"summary": event.describe(), **event.to_dict()} for event in events],
    }
    atomic_write_json(target, payload)
    log.info("wrote change log (%d event(s), state_version=%s) -> %s", len(events), state_version, target)
    prune(target.parent)
    return target
