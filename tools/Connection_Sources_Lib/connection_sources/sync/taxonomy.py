"""Map a tracker's own item types onto the buckets `state.json` declares.

Every ALM names the same ideas differently: a story is a "Story" in Jira, a "User
Story" in Azure DevOps and a labelled issue in GitLab. `state.json` has one set of
buckets for all of them, so the mapping has to live somewhere — as data, not as
`if` statements spread through the diff.

The default names live in `taxonomy_defaults.json`, next to this file, keyed by
bucket rather than by name — `{"tests": ["Test", "Test Case", "Xray Test"], ...}` —
so the common type names for each bucket can be read and extended without touching
Python. Nothing is hardcoded per project on top of that: a tracker whose type names
aren't in that list gets them added in `sources.json` under `sync.buckets`, which is
merged over the defaults rather than replacing them, so an override names only what
it changes — for any bucket, not only the one it happens to mention.

Membership is deliberately narrow rather than "everything that isn't a test": a
generic work-item type (Task, Sub-task, bare "Issue") is not a requirement-level
story just because it sits in the same backlog, so it is left unmapped and falls
through to `other` — see `bucket_for` — rather than being swept into `user_stories`.
A Bug is left unmapped for the same reason: it is defect work, not a requirement a
test traces to, and a project that wants it counted as one says so in its own
`sync.buckets` override rather than that being this module's default.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

__all__ = ["DEFAULT_BUCKETS", "OTHER", "bucket_for", "load_buckets"]

OTHER = "other"

_DEFAULTS_PATH = Path(__file__).with_name("taxonomy_defaults.json")


def _load_defaults(path: Path) -> dict[str, str]:
    """Invert the file's `{bucket: [names, ...]}` into the `{name: bucket}` table
    every lookup in this module compares against."""
    declared = json.loads(path.read_text(encoding="utf-8"))
    return {
        name.strip().lower(): bucket
        for bucket, names in declared.items()
        for name in names
    }


# type name (lowercased) -> state.json bucket, built from taxonomy_defaults.json
DEFAULT_BUCKETS: dict[str, str] = _load_defaults(_DEFAULTS_PATH)

# Buckets sync is allowed to write. Anything else in state.json belongs to someone
# else and is never touched — see sync.state.
KNOWN_BUCKETS = (
    "user_stories", "tests", "test_plans", "ci_pipelines",
    "bugs", "epics", "preconditions", "tasks", "test_executions", "test_sets",
)


def load_buckets(sync_config: Mapping[str, Any] | None) -> dict[str, str]:
    """The default map, with any per-project overrides merged over it."""
    buckets = dict(DEFAULT_BUCKETS)
    overrides = (sync_config or {}).get("buckets") or {}
    if isinstance(overrides, Mapping):
        for type_name, bucket in overrides.items():
            if isinstance(type_name, str) and isinstance(bucket, str):
                buckets[type_name.strip().lower()] = bucket
    return buckets


def bucket_for(type_name: str, buckets: Mapping[str, str] | None = None) -> str:
    """Which state.json bucket an item of this type belongs in.

    An unknown type lands in `other` rather than being dropped: a type nobody
    mapped yet is still a real item whose changes we want in the history, and
    silently discarding it would make the state quietly incomplete.
    """
    table = buckets if buckets is not None else DEFAULT_BUCKETS
    return table.get((type_name or "").strip().lower(), OTHER)
