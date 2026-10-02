"""Continuous ALM change detection: fetch, diff against the last cycle, and record.

    from connection_sources.sync import sync_once, watch

Built entirely on the existing `connection_sources` read API (`api.read`, `api.get`)
and models (`AlmRecord`) — this package adds no new way of talking to a tracker, only
a way of remembering what it looked like last time and saying what changed.
"""

from __future__ import annotations

from .diff import DiffResult, diff_details, diff_items, diff_sprints, vanished_event
from .models import DETAIL_EVENT_KINDS, ChangeEvent, TrackedItem
from .normalize import detail_text, test_details_of
from .runner import SyncResult, default_state_dir, load_detail_fields, load_sync_config, sync_once, watch
from .state import default_state, init_state
from .taxonomy import bucket_for
from .watchlist import DEFAULT_WATCHED_BUCKETS, load_watched_buckets, partition_events

__all__ = [
    "DEFAULT_WATCHED_BUCKETS",
    "DETAIL_EVENT_KINDS",
    "ChangeEvent",
    "DiffResult",
    "SyncResult",
    "TrackedItem",
    "bucket_for",
    "default_state",
    "default_state_dir",
    "detail_text",
    "diff_details",
    "diff_items",
    "diff_sprints",
    "init_state",
    "load_detail_fields",
    "load_sync_config",
    "load_watched_buckets",
    "partition_events",
    "sync_once",
    "test_details_of",
    "vanished_event",
    "watch",
]
