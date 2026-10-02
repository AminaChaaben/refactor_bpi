"""Fetch every enabled source and save each item as its own JSON file on disk.

`api.read` already returns everything `fetch --raw` prints; this module just
points that same read at a folder instead of stdout: one file per item, filed
under its type, plus a timestamp and the project path that produced it. Jira,
as this project's primary source, is filed straight under the output root;
every other source is filed under its own name first and then its type, so a
type name it shares with Jira (e.g. "Task") cannot collide with Jira's own
files. Nothing here assumes a project's identity, a system, or a type in
advance — `project` is any filesystem path, exactly like the rest of this
library, and the project name and every type folder come from whatever the
source actually returns.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import api
from .clients.base import DEFAULT_TIMEOUT
from .errors import SourcesConfigError
from .health import load_project, sources_path
from .models import SourceConfig

__all__ = ["export_project"]

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(value: str, *, default: str) -> str:
    return _UNSAFE.sub("_", value).strip("_") or default


def _export_source(
    project: str | Path,
    source: SourceConfig,
    *,
    project_dir: Path,
    fetched_at: str,
    limit: int,
    timeout: float,
    state_version: int | None = None,
) -> dict[str, Any]:
    # Imported here rather than at module scope: `sync.runner` imports this module
    # to refresh an export after a change, so a top-level import back into sync
    # would be a cycle. The cost is one dict lookup per export — the module is
    # already loaded by the time anything calls this.
    from .sync.normalize import detail_text, test_details_of
    from .sync.runner import load_detail_fields, load_sync_config

    detail_fields = load_detail_fields(load_sync_config(project))
    # Custom fields are only returned when a caller names them, and only Jira
    # accepts the argument at all; asking any other server refuses the read.
    # "comment" is a real field name (not a custom field id) so the raw export
    # also carries the issue's discussion instead of leaving it unfetched.
    extra_fields = (
        (sorted(detail_fields.values()) + ["comment"]) if source.server == "jira" else []
    )

    items = api.read(
        project, source.name, limit=limit, timeout=timeout, extra_fields=extra_fields or None
    )
    # The raw per-item mirror lives under its own `raw/` folder so it never sits
    # at the same level as the tracked-entity bucket folders (`bugs/`, `tests/`,
    # `user_stories/`, etc.) `sync.state.write_into_state` writes -- those are a
    # different kind of file (every ALM item vs. only the ones promoted into
    # state.json) and mixing both at the same folder depth is what made
    # `_bmad-input` look like a pile of near-duplicate folders. Jira is this
    # project's primary source, so its export is flattened directly under
    # `raw/` -- `<out>/raw/<type>/<key>.json` instead of
    # `<out>/raw/jira/<type>/<key>.json` -- to avoid an extra folder layer
    # nobody needs to navigate through. Every other source keeps its own
    # folder under `raw/`: a type name can repeat across sources (Jira and
    # Azure both use "Task"), and clearing a shared folder for one source
    # would destroy a different source's files.
    raw_root = project_dir / "raw"
    flatten = source.name == "jira"
    source_dir = raw_root if flatten else raw_root / source.name
    if not flatten:
        # Only once the read has succeeded, so a failed fetch cannot destroy
        # the previous export. Clearing is what keeps the folder a snapshot:
        # an item whose type changed would otherwise stay behind under its
        # old type too.
        if source_dir.exists():
            shutil.rmtree(source_dir)
        source_dir.mkdir(parents=True, exist_ok=True)
    elif source_dir.is_dir():
        # A flattened source has no single exclusive folder left to clear
        # wholesale, so a type this run's fetch has no item left under (its
        # last item was retyped away or deleted outright) would otherwise
        # never get cleared by the per-record loop below, which only ever
        # touches types the current fetch actually has. The previous run's
        # own summary says which type folders are this source's to begin
        # with, so only those -- never a sibling source's or a bucket's --
        # are candidates for this stale check.
        summary_path = source_dir / "_summary.json"
        current_types = {_safe_name(record.type, default="untyped") for record in items}
        previous_types: dict[str, Any] = {}
        if summary_path.is_file():
            try:
                previous_types = json.loads(summary_path.read_text(encoding="utf-8")).get(
                    "by_type", {}
                )
            except (OSError, ValueError):
                previous_types = {}
        for stale_type in previous_types:
            if stale_type in current_types:
                continue
            stale_dir = source_dir / stale_type
            if stale_dir.is_dir():
                shutil.rmtree(stale_dir)

    by_type: dict[str, int] = {}
    cleared_type_dirs: set[Path] = set()
    for record in items:
        type_dir = source_dir / _safe_name(record.type, default="untyped")
        if flatten and type_dir not in cleared_type_dirs:
            # Same snapshot guarantee as the non-flattened branch's rmtree
            # above, just scoped one type folder at a time -- clears out
            # files left by items this type had before that are gone now
            # (deleted, or moved to a different type this same run).
            if type_dir.exists():
                shutil.rmtree(type_dir)
            cleared_type_dirs.add(type_dir)
        type_dir.mkdir(parents=True, exist_ok=True)
        by_type[type_dir.name] = by_type.get(type_dir.name, 0) + 1

        name = _safe_name(record.key or record.id, default="item")
        payload = record.to_dict(include_raw=True)
        payload["fetched_at"] = fetched_at
        payload["project_path"] = str(project)
        payload["source"] = source.name
        # The same flattened test-shape values change detection compares, written
        # next to the raw payload they came from. An agent reading this folder to
        # author or update a test then gets the test's type and steps as plain
        # strings, instead of having to re-derive them from the instance-specific
        # custom field ids buried in `raw`.
        details = test_details_of(record, detail_fields)
        if details:
            payload["test_details"] = dict(details)
        # Simplified to id/author/created/updated/body (ADF flattened to plain
        # text via the same `detail_text` the graph's own Comment nodes use) --
        # the same "readable, not raw" choice `test_details_of` already makes.
        # Always present (even `[]`) so a reader never has to guess whether
        # "missing key" meant "none" or "never fetched".
        comment_block = ((payload.get("raw") or {}).get("fields") or {}).get("comment")
        raw_comments = comment_block.get("comments") if isinstance(comment_block, dict) else None
        payload["comments"] = [
            {
                "id": comment.get("id"),
                "author": (comment.get("author") or {}).get("displayName"),
                "created": comment.get("created"),
                "updated": comment.get("updated"),
                "body": detail_text(comment.get("body")),
            }
            for comment in raw_comments or ()
            if isinstance(comment, dict)
        ]
        if state_version is not None:
            payload["state_version"] = state_version
        (type_dir / f"{name}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    summary = {
        "source": source.name,
        "server": source.server,
        "scope": source.scope,
        "item_count": len(items),
        "by_type": by_type,
        "fetched_at": fetched_at,
        "project_path": str(project),
        "out_dir": str(source_dir),
        "state_version": state_version,
    }
    (source_dir / "_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return summary


def _default_export_root(project: str | Path) -> Path:
    """`<project>/_bmad_input`, unless `sync.content_root` says otherwise.

    A project whose state.json is the orchestrator's own (`sync.state_path` set)
    keeps its export alongside that orchestrator's own input folder via
    `content_root`, so a Bug/Epic/Precondition/Task/Test Execution/Test Set --
    the ones with no `creation` policy configured, or any item a bucket's
    readiness rule has not yet let in -- is still visible somewhere the
    orchestrator actually looks, instead of being left behind in a folder tree
    only this engine's own project ever reads.

    Read directly here (not through `connection_sources.sync`) so this module never
    depends on the `sync` package, which itself depends on this one.
    """
    try:
        raw = json.loads(sources_path(project).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    sync_block = raw.get("sync") if isinstance(raw, dict) else None
    content_root = sync_block.get("content_root") if isinstance(sync_block, dict) else None
    if content_root:
        candidate = Path(content_root)
        return candidate if candidate.is_absolute() else Path(project) / candidate
    return Path(project) / "_bmad_input"


def export_project(
    project: str | Path,
    *,
    source: str | None = None,
    out: str | Path | None = None,
    limit: int = 1000,
    timeout: float = DEFAULT_TIMEOUT,
    state_version: int | None = None,
) -> dict[str, Any]:
    """Write every item from one or every enabled source to its own JSON file.

    Layout: `<out>/raw/<type>/<item-key-or-id>.json` for Jira, and
    `<out>/raw/<source-name>/<type>/<item-key-or-id>.json` for every other
    source, plus a `_summary.json` per source (Jira's lands at
    `<out>/raw/_summary.json`) recording scope, per-type counts and when it
    last ran. Everything lives under one `raw/` folder, one level below `out`,
    so this raw per-item mirror never sits at the same depth as the
    tracked-entity bucket folders `sync.state.write_into_state` writes
    directly under `out` (`bugs/`, `tests/`, `user_stories/`, etc.) -- two
    different kinds of file, two different single places. `out` defaults to
    `<project>/_bmad_input`, so a project's exported ALM data sits alongside
    whatever else BMAD reads as input for that project. Assumes one project
    per `out` root, so no `<project-name>` subfolder is inserted -- a second
    project ever sharing the same `out` (in particular a shared
    `content_root`) would collide and needs that back. Each run replaces
    what it wrote outright -- a whole source folder for a nested source, or
    each type folder Jira's fetch touches for the flattened one -- so what
    lands on disk is that source's current state and nothing else: items
    deleted since the last run, and files left under a type an item has
    since moved away from, do not survive.

    `state_version` is recorded on every file written and in `_summary.json`. An
    export is a copy, and a copy with no way to say which generation of the truth
    it was taken from is a copy nobody can safely trust — stamping it lets a reader
    tell an export that reflects the current state from one left behind by an
    earlier run. A manual export passes nothing and records `null`, which says
    honestly that it was taken outside the change-detection cycle rather than
    claiming a version it cannot know.
    """
    config = load_project(project)
    enabled = config.enabled_sources()
    if source:
        targets = [s for s in enabled if s.name == source]
        if not targets:
            raise SourcesConfigError(
                f"no enabled source named {source!r}",
                remediation="enabled sources: " + (", ".join(s.name for s in enabled) or "none"),
            )
    else:
        targets = list(enabled)

    out_root = Path(out) if out else _default_export_root(project)
    project_dir = out_root
    fetched_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    results = {
        target.name: _export_source(
            project,
            target,
            project_dir=project_dir,
            fetched_at=fetched_at,
            limit=limit,
            timeout=timeout,
            state_version=state_version,
        )
        for target in targets
    }
    return {
        "project_path": str(project),
        "project_name": config.project,
        "out": str(project_dir),
        "fetched_at": fetched_at,
        "state_version": state_version,
        "sources": results,
    }
