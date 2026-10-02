"""Adding a project's own Xray-like custom field ids without hand-editing JSON.

`sync.test_detail_fields` in `sources.json` maps a label this package already
knows how to watch (`test_type`, `test_steps`, `precondition`, `expected_result`
raise their own named change event — see `sync.md`; any other label is still
watched, reported as `TEST_DETAIL_CHANGED`) to that field's id on this specific
Jira site (`customfield_100xx`). That id is assigned by the Jira instance itself
— the same "Test Type" field has a different id on every site, exactly the
reason `sprint_field` is per-project too — so it is never shipped as a default
here, only ever declared per project.

Despite the `test_` prefix, this map is applied to every synced record
regardless of bucket (`normalize.py`'s `test_details_of`/`make_tracked_item`
read `record.raw.fields` generically, with no bucket check) — e.g. a `bug`
entity's `ci_pipeline_link` (`state_dictionary.yaml`'s `bug.external`) is
declared here too, not in a separate `bug_detail_fields` key that nothing
reads. Renaming this to a bucket-agnostic key is a larger, deliberately
deferred cleanup; this module's contract is the key name `sources.json`
actually uses today.

This is the same read-modify-write-atomically mechanism `graph/aliases.py`
already uses for `graph.ontology.*` overrides, applied to
`sync.test_detail_fields` instead: read the whole file, add or remove one
label, write it back through a temp file and `os.replace`, under the same
advisory lock, so a crash mid-write can never leave `sources.json` truncated.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..errors import SourcesConfigError
from ..health import sources_path
from .store import atomic_write_json, file_lock, read_json

__all__ = ["add_detail_field", "list_detail_fields", "remove_detail_field"]


def _read_sources(project: str | Path) -> tuple[Path, dict[str, Any]]:
    path = sources_path(project)
    raw = read_json(path, default=None)
    if not isinstance(raw, dict):
        raise SourcesConfigError(
            f"sources config not found or invalid: {path}",
            remediation="run this from a project that already has Config_Connection_Sources/sources.json",
        )
    return path, raw


def _detail_fields_block(raw: dict[str, Any]) -> dict[str, Any]:
    block = raw.get("sync")
    if not isinstance(block, dict):
        block = {}
        raw["sync"] = block
    fields = block.get("test_detail_fields")
    if not isinstance(fields, dict):
        fields = {}
        block["test_detail_fields"] = fields
    return fields


def list_detail_fields(project: str | Path) -> dict[str, str]:
    """This project's own label -> custom-field-id map, exactly as configured."""
    _, raw = _read_sources(project)
    sync_block = raw.get("sync")
    fields = sync_block.get("test_detail_fields") if isinstance(sync_block, dict) else None
    return dict(fields) if isinstance(fields, dict) else {}


def add_detail_field(project: str | Path, label: str, field_id: str) -> dict[str, Any]:
    """Declare (or replace) which custom field id this site uses for `label`.

    `label` is never validated against a closed set — unlike a graph label, a
    detail label is just a name this project chose, and an unrecognised one
    still gets watched under the generic `TEST_DETAIL_CHANGED` event rather
    than being refused. `field_id` is the literal `customfield_XXXXX` id this
    Jira site assigned it — find it with `GET /rest/api/3/field` (see
    `jira-jql-scoping.md`), never guessed or copied from another site.
    """
    key = label.strip().lower()
    value = field_id.strip()
    if not key:
        raise SourcesConfigError("label cannot be empty")
    if not value:
        raise SourcesConfigError("field id cannot be empty")

    path = sources_path(project)
    lock_path = path.with_name(f".{path.name}.lock")
    with file_lock(lock_path):
        _, raw = _read_sources(project)
        fields = _detail_fields_block(raw)
        fields[key] = value
        atomic_write_json(path, raw)
    return {"label": key, "field_id": value}


def remove_detail_field(project: str | Path, label: str) -> dict[str, Any]:
    """Stop watching `label`. Removing one that was never declared is a no-op,
    not an error — the same discipline `graph/aliases.py` applies."""
    key = label.strip().lower()
    path = sources_path(project)
    lock_path = path.with_name(f".{path.name}.lock")
    with file_lock(lock_path):
        _, raw = _read_sources(project)
        sync_block = raw.get("sync")
        fields = sync_block.get("test_detail_fields") if isinstance(sync_block, dict) else None
        removed = isinstance(fields, dict) and fields.pop(key, None) is not None
        if removed:
            atomic_write_json(path, raw)
    return {"label": key, "removed": removed}
