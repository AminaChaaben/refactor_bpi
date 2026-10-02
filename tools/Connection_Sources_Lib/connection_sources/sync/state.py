"""Fold a cycle's findings into `state.json` without trampling its other writer.

`state.json` has two writers. The orchestrator owns `orchestration`,
`action_history` and `errors`; this engine owns the item buckets and
`metadata.last_state_update`, and appends to `pending_actions`. So this is never a
plain write — it is always a read-modify-write under a lock, touching only the keys
it owns and leaving every other key exactly as it found it, including keys neither
side knows about.

When the state.json in question is the orchestrator's own (the normal case --
see `write_into_state`), even that read-modify-write is too much: the entity
buckets go through the orchestrator's own `update_state.py` instead, one
subprocess call per change, and this module never touches them directly. Only a
project with no orchestrator attached at all (`update_state()`, `sync.state_path`
unset) still does the read-modify-write described above -- e.g. a module under
development against its own local/mock state.json so it is not blocked waiting on
the integration to land.

The queue is the part that has to be idempotent. Every event carries a `dedupe_key`
derived from the change itself and not from when it was seen, so re-running a cycle
over an unchanged tracker enqueues nothing, and an event already dealt with (present
in `action_history`) is never handed back a second time.

`metadata.state_version` counts generations of *signal*, not of writes. It advances
only on a cycle that actually recorded a change, so a reader can compare it against
the number it last saw and know whether anything happened without diffing the whole
document — and a poller running every few minutes over a quiet tracker leaves it
alone for hours at a stretch. The same number is stamped onto each queued action and
each history line written in that cycle, which is what lets a change in the queue,
its line in the audit trail, and the state that produced it be tied back together
after the fact.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time

from pathlib import Path
from typing import Any, Iterable, Mapping

from .models import ChangeEvent, TrackedItem
from .normalize import DESCRIPTION_CRITERIA_LABEL
from .store import atomic_write_json, file_lock, now_iso, read_json

log = logging.getLogger("connection_sources.sync.state")

# Per-subprocess ceiling for each individual `update_state.py --op ...` call this
# module makes. Without one, a single stuck child (a lock it can't get, a hung
# owner-side hook) blocks the whole cycle forever with no trace of where it
# stopped -- the same failure mode `update_state.py`'s own subprocess call
# (`update_state.py:216`) already guards against with `timeout=30`.
_UPDATE_STATE_OP_TIMEOUT = 30.0

__all__ = ["OWNED_BUCKETS", "default_state", "init_state", "update_state", "write_into_state"]

# The only keys this engine writes. Everything else in the file belongs to someone
# else and is copied through untouched.
OWNED_BUCKETS = (
    "user_stories", "tests", "test_plans", "ci_pipelines",
    "bugs", "epics", "preconditions", "tasks", "test_executions", "test_sets",
    "sprints", "other",
)

# The subset of OWNED_BUCKETS that the state.json owner also recognises as an
# entity type (state_dictionary.yaml's `entities:` map, mirrored in
# `_bmad/scripts/update_state.py`'s `_ENTITY_COLLECTIONS`). "other" and "sprints"
# have no entity-type equivalent there -- an item landing in "other" is some
# issue type nobody has mapped to a bucket at all (see `sync.buckets`), a real,
# deliberate scope boundary, not an oversight. It stays visible only through the
# plain `alm-conn export` snapshot.
_BUCKET_ENTITY_TYPES = {
    "user_stories": "user_story",
    "tests": "test",
    "test_plans": "test_plan",
    "ci_pipelines": "ci_pipeline",
    "bugs": "bug",
    "epics": "epic",
    "preconditions": "precondition",
    "tasks": "task",
    "test_executions": "test_execution",
    "test_sets": "test_set",
}

SCHEMA_VERSION = "1.1"


def default_state(project_id: str = "PROJECT-001") -> dict[str, Any]:
    """The empty state document, matching the agreed schema."""
    return {
        "metadata": {
            "schema_version": SCHEMA_VERSION,
            "project_id": project_id,
            "state_manager_version": "1.0",
            "state_version": 0,
            "last_state_update": None,
        },
        "orchestration": {
            "status": "IDLE",
            "active_contract": {"id": None, "version": None, "path": None, "status": None},
            "current_workflow": None,
            "current_action": None,
            "execution_mode": "24h",
            "period": "DAY",
        },
        "user_stories": {},
        "tests": {},
        "test_plans": {},
        "ci_pipelines": {},
        "sprints": {},
        "other": {},
        "pending_actions": [],
        "action_history": [],
        "errors": [],
    }


def init_state(
    state_path: Path, *, project_id: str = "PROJECT-001", force: bool = False
) -> dict[str, Any]:
    """Construct `state.json` on disk, matching the agreed schema exactly.

    A first `sync` run would build this implicitly anyway (`update_state` falls back
    to `default_state` when the file is absent), but a project should be able to see
    and commit the initial, empty shape before any ALM has ever been read — this is
    that explicit, standalone construction. Idempotent: an existing file is left
    untouched unless `force` says to overwrite it, and either way the on-disk state
    (not a copy) is what gets returned.
    """
    lock = state_path.with_name(state_path.name + ".lock")
    with file_lock(lock):
        existing = read_json(state_path)
        if isinstance(existing, dict) and not force:
            return existing
        state = default_state(project_id)
        atomic_write_json(state_path, state)
        return state


def _as_action(event: ChangeEvent, state_version: int) -> dict[str, Any]:
    action = {
        "id": event.dedupe_key,
        "type": "ALM_CHANGE",
        "kind": event.kind,
        "source": event.source,
        "system": event.system,
        "key": event.key,
        "title": event.title,
        "bucket": event.bucket,
        "url": event.url,
        "detected_at": event.detected_at,
        "state_version": state_version,
        "status": "PENDING",
    }
    if event.field_name is not None:
        action["field"] = event.field_name
        action["before"] = event.before
        action["after"] = event.after
    return action


def _known_action_ids(state: Mapping[str, Any]) -> set[str]:
    """Ids already queued or already dealt with.

    `action_history` is read as well as `pending_actions`: once the orchestrator has
    handled an action and moved it to history, re-queueing the same change would
    make it redo work it has already completed.
    """
    ids: set[str] = set()
    for bucket in ("pending_actions", "action_history"):
        entries = state.get(bucket)
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, Mapping) and entry.get("id"):
                    ids.add(str(entry["id"]))
    return ids


def update_state(
    state_path: Path,
    *,
    items: Iterable[TrackedItem],
    events: Iterable[ChangeEvent],
    sprints: Mapping[str, dict] | None = None,
    deleted_keys: Iterable[str] = (),
    project_id: str = "PROJECT-001",
    emit_actions: bool = True,
) -> dict[str, int]:
    """Refresh the owned buckets and queue any new work. Returns a small summary."""
    items = list(items)
    events = list(events)
    deleted = set(deleted_keys)

    lock = state_path.with_name(state_path.name + ".lock")
    with file_lock(lock):
        existing = read_json(state_path)
        state: dict[str, Any] = existing if isinstance(existing, dict) else default_state(project_id)

        # Guarantee the keys we own exist without disturbing the ones we do not.
        for bucket in OWNED_BUCKETS:
            if not isinstance(state.get(bucket), dict):
                state[bucket] = {}
        if not isinstance(state.get("metadata"), dict):
            state["metadata"] = default_state(project_id)["metadata"]
        if not isinstance(state.get("pending_actions"), list):
            state["pending_actions"] = []

        # A cycle that observed nothing keeps the version it found, so the number
        # stays a count of changes rather than of runs. Read before the buckets are
        # rebuilt so a malformed value cannot take the new one with it.
        try:
            previous_version = int(state["metadata"].get("state_version") or 0)
        except (TypeError, ValueError):
            previous_version = 0
        version = previous_version + 1 if events else previous_version

        # Rebuild item buckets from what the tracker actually holds now. An item
        # that changed bucket (its type changed) must not survive in the old one,
        # so every owned item bucket is cleared before refilling.
        for bucket in OWNED_BUCKETS:
            if bucket != "sprints":
                state[bucket] = {}
        for item in items:
            bucket = item.bucket if item.bucket in OWNED_BUCKETS else "other"
            state[bucket][item.key] = item.to_dict()

        if sprints is not None:
            state["sprints"] = {
                sprint_id: {
                    "id": sprint_id,
                    "name": data.get("name"),
                    "state": data.get("state"),
                    "board_id": data.get("originBoardId"),
                    "goal": data.get("goal"),
                    "start": data.get("startDate"),
                    "end": data.get("endDate"),
                }
                for sprint_id, data in sprints.items()
            }

        for key in deleted:
            for bucket in OWNED_BUCKETS:
                state[bucket].pop(key, None)

        queued = 0
        if emit_actions:
            known = _known_action_ids(state)
            for event in events:
                if event.dedupe_key in known:
                    continue
                state["pending_actions"].append(_as_action(event, version))
                known.add(event.dedupe_key)
                queued += 1

        state["metadata"]["state_version"] = version
        state["metadata"]["last_state_update"] = now_iso()
        state["metadata"].setdefault("schema_version", SCHEMA_VERSION)
        state["metadata"].setdefault("project_id", project_id)

        atomic_write_json(state_path, state)

    return {
        "items": len(items),
        "events": len(events),
        "queued": queued,
        "deleted": len(deleted),
        "version": version,
    }


def _external_id(entity: Mapping[str, Any]) -> str | None:
    """An entity's `identity.external_id`, coerced to `str` for matching.

    `update_state.py`'s `update-artifact` op parses `--value` as JSON when
    possible (see its own docstring), so a numeric-looking key (an Azure work
    item id, a GitLab issue number) written through it round-trips as a JSON
    number, not the string it started as -- `write_into_state` guards against
    that on the way in (see the `json.dumps(..., ...)` wrapping `item.key`
    below), but this stays lenient on the way out too, so an external_id that
    is a number or a bool-free scalar for any other reason still indexes and
    matches instead of silently becoming invisible and reborn as a duplicate
    every cycle.
    """
    identity = entity.get("identity")
    if not isinstance(identity, Mapping):
        return None
    value = identity.get("external_id")
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (str, int, float)):
        text = str(value)
        return text or None
    return None


def _next_internal_id(existing_ids: Iterable[str], *, prefix: str) -> str:
    """The next unused `{prefix}-NNN`, zero-padded to at least 3 digits."""
    needle = f"{prefix}-"
    highest = 0
    for key in existing_ids:
        suffix = key[len(needle):]
        if key.startswith(needle) and suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{needle}{highest + 1:03d}"


def _safe_artifact_name(external_id: str) -> str:
    """`external_id` as a filesystem-safe basename (no extension).

    Trackers' own keys (Jira `PROJ-123`, Azure's numeric ids, ...) are safe as
    written, but this is defensive against anything a connector might hand
    back with characters a filename can't hold.
    """
    text = str(external_id).strip()
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in text) or "item"


def _is_ready(
    item: TrackedItem, *, ready_statuses: Iterable[str], ready_tags: Iterable[str]
) -> bool:
    """Whether this item qualifies for auto-creation under its bucket's policy.

    A bucket configured with no readiness gate at all (no `ready_statuses` and no
    `ready_tags` -- the norm for a type that is just tracked, e.g. a Bug or an
    Epic, rather than entering a workflow) has nothing to be ready *for*, so
    every item in it qualifies. `user_stories` also runs with this cleared today
    (every story is created unconditionally): its real readiness gate is the
    Contract's `ALM_IMPORT` step, evaluated after creation against the entity's
    `internal.state` -- this function only ever gated *creation*, not pipeline
    entry.
    """
    ready_statuses = tuple(ready_statuses)
    ready_tags = tuple(ready_tags)
    if not ready_statuses and not ready_tags:
        return True
    if item.status in ready_statuses:
        return True
    return any(tag in ready_tags for tag in item.tags)



def _normalise_tag(value: str) -> str:
    """Fold the punctuation a tracker's labels are written with.

    The same intent is entered by hand on every ticket, so it arrives spelled
    inconsistently -- `state_dictionary.yaml` records "A automatiser",
    "A-automatiser" and "A_automatiser" as all observed on this project's Jira.
    Separator and case differences are noise here; anything else is not, so this
    folds only those and leaves the rest of the string alone.
    """
    return " ".join(str(value).replace("-", " ").replace("_", " ").lower().split())


def _tag_derived_state(
    item: TrackedItem,
    rules: Iterable[Mapping[str, Any]],
    *,
    current_state: str | None,
    at_creation: bool = False,
) -> str | None:
    """The internal state this item's tags call for, or None to leave it alone.

    Tags are how a team says what should happen to a ticket, so they are read on
    every cycle rather than only at birth: re-tagging in the tracker is meant to
    move the entity, and a mapping applied once at creation could never do that.

    What that must not become is the tracker silently dragging an entity
    *backwards* through work already done -- a test picked up for execution does
    not return to "to be automated" because the label that put it there is still
    attached. `from_states` is the guard: a rule only fires when the entity is in
    one of the states it names, so each mapping declares the positions it is
    allowed to move an entity out of. A rule with no `from_states` fires from any
    state, which is the caller's choice to make explicitly.

    Rules are evaluated in order and the first match wins, so a ticket carrying
    two competing tags resolves the same way every time rather than by dict
    ordering.
    """
    tags = {_normalise_tag(tag) for tag in item.tags}
    if not tags:
        return None
    for rule in rules:
        if not isinstance(rule, Mapping):
            continue
        wanted = {_normalise_tag(t) for t in (rule.get("tags") or ())}
        if not wanted or not (wanted & tags):
            continue
        target = rule.get("state")
        if not target:
            continue
        allowed = rule.get("from_states")
        if not at_creation and allowed is not None and current_state not in set(allowed):
            continue
        if target == current_state:
            return None
        return str(target)
    return None


def _run_update_state_op(
    update_state_script: Path, state_path: Path, op_args: list[str], *, retries: int = 1
) -> tuple[bool, str]:
    """Invoke the owner's `update_state.py --op ...` once for one mutation.

    This is the only way this engine ever changes an entity in the orchestrator's
    state.json -- see `state_write_discipline.md`: "`_bmad/scripts/update_state.py`
    is the only way any workflow writes to state.json. Never read the file,
    mutate the in-memory object, and write it back directly." The script does
    its own concurrency check (it re-reads `metadata.last_state_update` before
    writing and exits `4` if another writer changed it first) rather than
    sharing a lock with us, so a conflict is retried here -- once, reloading
    nothing extra since the script itself re-reads on every invocation -- and a
    second conflict is surfaced as a failure rather than retried forever.
    """
    attempt = 0
    while True:
        started = time.monotonic()
        log.debug("update_state.py op start: %s", op_args)
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(update_state_script),
                    "--state-path",
                    str(state_path),
                    *op_args,
                ],
                capture_output=True,
                text=True,
                timeout=_UPDATE_STATE_OP_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            log.warning(
                "update_state.py op timed out after %.0fs: %s",
                _UPDATE_STATE_OP_TIMEOUT,
                op_args,
            )
            return False, f"timed out after {_UPDATE_STATE_OP_TIMEOUT:.0f}s: {op_args}"
        elapsed = time.monotonic() - started
        log.debug("update_state.py op done in %.2fs (rc=%s): %s", elapsed, proc.returncode, op_args)
        if proc.returncode == 0:
            return True, proc.stdout.strip()
        if proc.returncode == 4 and attempt < retries:
            attempt += 1
            continue
        return False, (proc.stderr or proc.stdout).strip()


def _extra_external_fields(item: TrackedItem) -> list[tuple[str, Any]]:
    """Every other field the tracker carries, beyond id/key/title/status/tags.

    Mirrored into `external.*` the same way those five are, so nothing the ALM
    holds for an item -- system, source, type, bucket, url, assignee, parent,
    sprint, updated_at, its custom-field `details`, and its precomputed
    `content_hash` -- is left off the entity just because it wasn't part of
    the original, narrower mirror set.
    """
    return [
        ("system", item.system),
        ("source", item.source),
        ("type", item.type),
        ("bucket", item.bucket),
        ("url", item.url),
        ("assignee", item.assignee),
        ("parent", item.parent),
        ("sprint", item.sprint),
        ("updated_at", item.updated_at),
        ("details", dict(item.details)),
        ("content_hash", item.content_hash),
    ]


def _description_of(item: TrackedItem) -> str:
    """The item's declared `description` detail field, or "" if none is configured."""
    return next(
        (value for label, value in item.details if label.strip().lower() == "description"),
        "",
    )


def _acceptance_criteria_of(item: TrackedItem) -> list[str]:
    """The item's `acceptance_criteria` detail, split back into one entry per criterion.

    `normalize.acceptance_criteria_text` joined the criteria with newlines so `details`
    could stay a flat label->string map that diffs field-by-field. This is the other
    half of that round-trip.

    Empty when the project declares no `acceptance_criteria` detail field, or when the
    issue simply has none -- never fabricated. A story that reaches TEST_DESIGN with an
    empty list is a real finding the workflow should surface (bmad-tea will say the
    story is too thin to design against), not something to paper over here.
    """
    raw = next(
        (
            value
            for label, value in item.details
            if label.strip().lower() == "acceptance_criteria"
        ),
        "",
    )
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if lines:
        return lines
    derived = next(
        (value for label, value in item.details if label == DESCRIPTION_CRITERIA_LABEL), ""
    )
    return [line.strip() for line in derived.splitlines() if line.strip()]


def _acceptance_criteria_source_of(item: TrackedItem) -> str:
    """`field` when the criteria come from the AC field, `description` when derived, else ""."""
    labels = {label.strip().lower() for label, value in item.details if value}
    if "acceptance_criteria" in labels:
        return "field"
    return "description" if DESCRIPTION_CRITERIA_LABEL in labels else ""


def _content_artifact(item: TrackedItem, internal_id: str) -> dict[str, Any]:
    """The ticket-content shape `_bmad-input/{collection}/{internal_id}.json` expects.

    Always carries the reference artifact's own fields -- `internal_id`/
    `external_id`/`source`/`imported_at`/`title`/`description`/
    `acceptance_criteria` -- with every one of those keys present even when this
    engine has nothing to offer for it (`acceptance_criteria` defaults to an
    empty list rather than being left out), so a reader never has to
    special-case a synced item's shape or guard a missing key. This engine's
    own detail is appended on top rather than replacing anything: `status`/
    `url`/`tags`/`type`/`assignee`/`sprint`/`parent`/`updated_at`/`relations`,
    for whichever of our own modules wants it.
    """
    description = _description_of(item)
    return {
        "internal_id": internal_id,
        "external_id": item.key,
        "id": item.id,
        "source": (item.source or item.system or "").upper(),
        "imported_at": now_iso(),
        "title": item.title,
        "description": description,
        # Populated from the project's declared `acceptance_criteria` detail field
        # (sources.json -> sync.test_detail_fields), one entry per authored bullet or
        # line. Still present unconditionally, and still an empty list when the project
        # declares no such field or the issue carries none, because a reader (e.g.
        # `PRE_TEST_DESIGN_GATE`) must be able to rely on the key existing without
        # special-casing a synced item.
        "acceptance_criteria": _acceptance_criteria_of(item),
        "acceptance_criteria_source": _acceptance_criteria_source_of(item),
        "status": item.status,
        "url": item.url,
        "tags": list(item.tags),
        "type": item.type,
        "assignee": item.assignee,
        "sprint": item.sprint,
        "parent": item.parent,
        "updated_at": item.updated_at,
        "relations": item.sorted_relations,
    }


def _write_content_artifact(path: Path, content: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, content)


def write_into_state(
    state_path: Path,
    *,
    items: Iterable[TrackedItem],
    events: Iterable[ChangeEvent],
    update_state_script: Path,
    sprints: Mapping[str, dict] | None = None,
    deleted_keys: Iterable[str] = (),
    project_id: str = "PROJECT-001",
    emit_actions: bool = True,
    creation: Mapping[str, Mapping[str, Any]] | None = None,
    input_root: str = "_bmad-input",
    content_root: Path | None = None,
    tag_states: Mapping[str, Any] | None = None,
) -> dict[str, int]:
    """Fold a cycle's findings into the orchestrator's own state.json --
    through its writer script, never by reading, mutating and rewriting it.

    `update_state_script` is the orchestrator's `_bmad/scripts/update_state.py`.
    Every entity mutation below is one subprocess call into it
    (`--op create-entity` / `--op update-artifact`); this function itself only
    ever *reads* the file, to index existing entities by external_id and to
    know each one's currently mirrored status/tags so an unchanged value is
    never rewritten for nothing.

    A tracked item matching an existing entity's `identity.external_id` gets
    `external.status.value` (and `external.tags`, `external.id`, `external.key`,
    `external.title`) refreshed via `update-artifact`, the same field
    `orc-sync-state`'s own reconciliation writes (`step-03-reconcile-or-flag.md`)
    -- not an invented `"alm"` mirror key. Its `relations` (covers, preconditions, test_plan, whatever a tracker
    calls container/epic membership, …) are refreshed the same way, compared
    and written via `TrackedItem.sorted_relations` so a tracker returning the
    same links in a different order is never mistaken for a change -- this is
    the only place that block reaches this file at all; `events` carries the
    fact something changed, never the relations themselves. A bucket with no
    entity-type equivalent on the orchestrator's side
    (`other`: whatever issue type nobody has mapped to a bucket, see
    `sync.buckets`) is matched for counting purposes only; nothing is written
    for it, because there is no `--entity-type` to target.

    `creation` is a per-bucket policy map (default: none, so nothing is ever
    auto-created): `{bucket: {"id_prefix", "initial_state", "ready_statuses",
    "ready_tags"}}`. A bucket present in it, for an item matching no existing
    entity, is created once the item meets that bucket's readiness rule (see
    `_is_ready` -- a bucket with no rule configured at all is always ready,
    which is the norm for a type that is only ever tracked, never gated into a
    workflow) via `create-entity` followed by `update-artifact` calls for
    `identity.external_id`, `external.status.value`, `external.tags`,
    `external.id`, `external.key`, `external.title`, its
    `relations` if it already has any at birth, and -- when the policy names
    one -- `internal.state`, seeding the entity into
    the workflow state its type is supposed to start in (e.g. a fresh user
    story starts `READY_FOR_TEST_DESIGN`; a tracked-only type like a Bug has no
    such workflow and so no `initial_state` to seed). When `content_root` is
    given, the ticket's actual content is also materialized at
    `{content_root}/{bucket}/{internal_id}.json`, a plain filesystem write
    alongside the script call (matching how `step-03-reconcile-or-flag.md`
    refreshes an already-tracked entity's content file: "a direct filesystem
    write, it isn't state.json"). Every bucket absent from `creation` is only
    ever attached to, never created into -- those entities are born from the
    orchestrator's own workflow steps.

    This never queues into `pending_actions`: that array's shape
    (`action_id`/`entity_type`/`entity_id`/`action`/`role`/`priority`, written
    by the orchestrator's own `add-pending` op) is reserved for decisions that
    need a human, not routine ALM drift, which is applied directly above
    instead. `events`/`emit_actions` are accepted for call-site symmetry with
    `update_state()` but change nothing here; the caller's own local history
    (`history.append_events`) is still the place a cycle's events are recorded.
    """
    items = list(items)
    events = list(events)
    deleted = set(deleted_keys)
    creation = creation or {}
    tag_rules = {k: tuple(v or ()) for k, v in (tag_states or {}).items()}
    retagged = 0
    removed = 0
    script_path = Path(update_state_script)
    if not script_path.is_file():
        raise FileNotFoundError(f"update_state_script not found: {script_path}")

    log.info(
        "write_into_state launched: state_path=%s items=%d events=%d",
        state_path, len(items), len(events),
    )
    _cycle_started = time.monotonic()

    # A distinct lock file from update_state.py's own per-op lock
    # (`state.json.lock`), deliberately: this block calls that script once
    # per changed field, and if both used the same lock path, every one of
    # those subprocess calls would deadlock waiting on a lock its own parent
    # holds. This lock's job is narrower -- stop two overlapping sync cycles
    # (e.g. a slow full reconcile still running when the next scheduled tick
    # fires) from interleaving their reads of `existing` -- not to serialize
    # against the Orchestrator/Planner, which update_state.py's own lock
    # already does on every individual write this loop makes.
    lock = state_path.with_name(state_path.name + ".sync.lock")
    with file_lock(lock):
        existing = read_json(state_path)
        if not isinstance(existing, dict):
            raise FileNotFoundError(
                f"state file not found or not a JSON object: {state_path}"
            )

        try:
            previous_version = int(existing.get("metadata", {}).get("state_version") or 0)
        except (TypeError, ValueError):
            previous_version = 0
        version = previous_version + 1 if events else previous_version

        # Read-only indexes over the file as it stands: existing entities by
        # external_id (for matching) and each owned bucket's real keys (to mint
        # the next unused internal_id). Neither is ever written back directly --
        # every change from here on is a subprocess call into update_state.py.
        by_external_id: dict[str, dict[str, dict[str, Any]]] = {}
        bucket_keys: dict[str, set[str]] = {}
        for bucket in OWNED_BUCKETS:
            if bucket == "sprints":
                continue
            bucket_value = existing.get(bucket)
            bucket_value = bucket_value if isinstance(bucket_value, dict) else {}
            bucket_keys[bucket] = set(bucket_value.keys())
            index: dict[str, dict[str, Any]] = {}
            for entity in bucket_value.values():
                if isinstance(entity, dict):
                    ext_id = _external_id(entity)
                    if ext_id:
                        index[ext_id] = entity
            by_external_id[bucket] = index

        matched = 0
        created = 0
        unmatched = 0
        write_errors: list[str] = []

        for _index, item in enumerate(items, start=1):
            if _index == 1 or _index % 25 == 0 or _index == len(items):
                log.info(
                    "write_into_state progress: %d/%d items (matched=%d created=%d unmatched=%d, %.1fs elapsed)",
                    _index, len(items), matched, created, unmatched,
                    time.monotonic() - _cycle_started,
                )
            bucket = item.bucket if item.bucket in by_external_id else "other"
            entity_type = _BUCKET_ENTITY_TYPES.get(bucket)
            entity = by_external_id.get(bucket, {}).get(item.key)

            if entity is not None:
                matched += 1
                if entity_type is None:
                    continue  # e.g. "other" -- no update_state.py entity-type to target
                internal_id = entity.get("identity", {}).get("internal_id")
                if not internal_id:
                    continue
                external_block = entity.get("external") if isinstance(entity.get("external"), Mapping) else {}
                current_status = None
                status_block = external_block.get("status") if isinstance(external_block, Mapping) else None
                if isinstance(status_block, Mapping):
                    current_status = status_block.get("value")
                if current_status != item.status:
                    ok, message = _run_update_state_op(
                        script_path,
                        state_path,
                        [
                            "--op", "update-artifact",
                            "--entity-type", entity_type,
                            "--internal-id", str(internal_id),
                            "--field", "external.status.value",
                            "--value", json.dumps(item.status, ensure_ascii=False),
                        ],
                    )
                    if not ok:
                        write_errors.append(f"update-artifact external.status.value {item.key}: {message}")
                current_tags = external_block.get("tags") if isinstance(external_block, Mapping) else None
                new_tags = list(item.tags)
                if current_tags != new_tags:
                    ok, message = _run_update_state_op(
                        script_path,
                        state_path,
                        [
                            "--op", "update-artifact",
                            "--entity-type", entity_type,
                            "--internal-id", str(internal_id),
                            "--field", "external.tags",
                            "--value", json.dumps(new_tags, ensure_ascii=False),
                        ],
                    )
                    if not ok:
                        write_errors.append(f"update-artifact external.tags {item.key}: {message}")

                # Same mirror-and-refresh treatment as status/tags above, for the
                # tracker's own id/key/title -- every entity type, not just the
                # ones with a creation policy (this loop runs for any matched
                # entity regardless of bucket).
                for ext_field, new_value in (("id", item.id), ("key", item.key), ("title", item.title)):
                    if external_block.get(ext_field) != new_value:
                        ok, message = _run_update_state_op(
                            script_path,
                            state_path,
                            [
                                "--op", "update-artifact",
                                "--entity-type", entity_type,
                                "--internal-id", str(internal_id),
                                "--field", f"external.{ext_field}",
                                "--value", json.dumps(new_value, ensure_ascii=False),
                            ],
                        )
                        if not ok:
                            write_errors.append(f"update-artifact external.{ext_field} {item.key}: {message}")

                extra_changed = False
                for ext_field, new_value in _extra_external_fields(item):
                    if external_block.get(ext_field) != new_value:
                        extra_changed = True
                        ok, message = _run_update_state_op(
                            script_path,
                            state_path,
                            [
                                "--op", "update-artifact",
                                "--entity-type", entity_type,
                                "--internal-id", str(internal_id),
                                "--field", f"external.{ext_field}",
                                "--value", json.dumps(new_value, ensure_ascii=False),
                            ],
                        )
                        if not ok:
                            write_errors.append(f"update-artifact external.{ext_field} {item.key}: {message}")

                # Tags are the team's own signal for what should happen to a
                # ticket, so they are re-read every cycle: a label added in the
                # tracker after import has to be able to move the entity, which a
                # creation-time-only mapping could never do. `from_states` on each
                # rule is what keeps that from dragging in-flight work backwards.
                internal_block = entity.get("internal") if isinstance(entity.get("internal"), Mapping) else {}
                current_internal = internal_block.get("state")
                wanted_state = _tag_derived_state(
                    item, tag_rules.get(bucket, ()), current_state=current_internal
                )
                if wanted_state:
                    ok, message = _run_update_state_op(
                        script_path,
                        state_path,
                        [
                            "--op", "update-artifact",
                            "--entity-type", entity_type,
                            "--internal-id", str(internal_id),
                            "--field", "internal.state",
                            "--value", json.dumps(wanted_state, ensure_ascii=False),
                        ],
                    )
                    if ok:
                        log.info(
                            "%s: tags moved internal.state %s -> %s",
                            item.key, current_internal, wanted_state,
                        )
                        retagged += 1
                    else:
                        write_errors.append(f"update-artifact internal.state {item.key}: {message}")

                current_relations = entity.get("relations") if isinstance(entity.get("relations"), Mapping) else {}
                new_relations = item.sorted_relations
                if current_relations != new_relations:
                    ok, message = _run_update_state_op(
                        script_path,
                        state_path,
                        [
                            "--op", "update-artifact",
                            "--entity-type", entity_type,
                            "--internal-id", str(internal_id),
                            "--field", "relations",
                            "--value", json.dumps(new_relations, ensure_ascii=False),
                        ],
                    )
                    if not ok:
                        write_errors.append(f"update-artifact relations {item.key}: {message}")

                # The fields above are refreshed in state.json via update_state.py, but the
                # on-disk content artifact (`{content_root}/{bucket}/{internal_id}.json`) is
                # only ever written at creation -- without this it silently drifts out of
                # sync forever afterward for an already-tracked entity.
                #
                # The staleness test is the ARTIFACT vs what this sync would now produce --
                # deliberately not "did state.json's external block change since last
                # cycle". Those are different questions, and using the second one leaves a
                # permanently stale artifact whenever the artifact is behind but the ALM
                # itself is quiet. That is not a corner case, it is the normal shape of
                # mapping a new field: cycle N copies the newly-fetched `details` into
                # state.json (so the external block now matches), and every cycle after
                # that sees "nothing changed" and never rewrites the artifact -- so the
                # value sits in `details.<label>` forever while the top-level key everyone
                # actually reads keeps its creation-time default. Observed live: US-001
                # stuck at `acceptance_criteria: []` with the full criteria present in
                # `details.acceptance_criteria`.
                #
                # Comparing against the artifact costs one small JSON read per tracked item
                # per cycle and makes the write self-healing instead: whatever the artifact
                # is missing or has wrong, the next sync repairs.
                if content_root is not None:
                    # The entity's own identity.content_path is authoritative -- once a
                    # ticket has an external_id, its content file is named after that
                    # (see op_update_artifact's rename-on-first-external-id), not after
                    # internal_id any more. Falling back to the internal_id-named path
                    # only covers an entity somehow missing content_path entirely.
                    content_path = (entity.get("identity") or {}).get("content_path")
                    artifact_path = (
                        Path(content_root).parent / content_path
                        if content_path
                        else Path(content_root) / bucket / f"{internal_id}.json"
                    )
                    artifact = read_json(artifact_path)
                    if isinstance(artifact, dict):
                        # `description` and `acceptance_criteria` are DERIVED from
                        # `details` rather than carried in `_extra_external_fields`, so
                        # they must be recomputed here, not just copied through.
                        desired: dict[str, Any] = {
                            "status": item.status,
                            "tags": new_tags,
                            "id": item.id,
                            "external_id": item.key,
                            "title": item.title,
                            "relations": new_relations,
                            "description": _description_of(item),
                            "acceptance_criteria": _acceptance_criteria_of(item),
                            **dict(_extra_external_fields(item)),
                        }
                        if any(artifact.get(key) != value for key, value in desired.items()):
                            artifact.update(desired)
                            _write_content_artifact(artifact_path, artifact)

            elif (
                entity_type is not None
                and bucket in creation
                and _is_ready(
                    item,
                    ready_statuses=creation[bucket].get("ready_statuses", ()),
                    ready_tags=creation[bucket].get("ready_tags", ()),
                )
            ):
                policy = creation[bucket]
                internal_id = _next_internal_id(bucket_keys[bucket], prefix=policy["id_prefix"])
                bucket_keys[bucket].add(internal_id)  # reserved for the rest of this cycle

                # An ALM-synced item always already carries a real external_id
                # (item.key, the tracker's own key) at creation time -- there is
                # no "not yet published" window for it the way there is for an
                # entity born from the orchestrator's own workflow. So its
                # content file is named after that external_id from the start,
                # never after internal_id first and renamed later (see
                # update_state.py's op_update_artifact, which is what performs
                # that rename for entities that DO start out unpublished).
                artifact_name = _safe_artifact_name(item.key)
                if content_root is not None:
                    _write_content_artifact(
                        Path(content_root) / bucket / f"{artifact_name}.json",
                        _content_artifact(item, internal_id),
                    )

                ok, message = _run_update_state_op(
                    script_path,
                    state_path,
                    [
                        "--op", "create-entity",
                        "--entity-type", entity_type,
                        "--internal-id", internal_id,
                        "--content-path", f"{input_root}/{bucket}/{artifact_name}.json",
                        "--actor", "ALM_SYNC",
                    ],
                )
                if not ok:
                    write_errors.append(f"create-entity {item.key}: {message}")
                    unmatched += 1
                    continue

                fields = [
                    # Every value here is JSON-encoded before being handed to
                    # `update_state.py`, whose `update-artifact` op tries
                    # `json.loads(--value)` first (see its own docstring) -- a
                    # numeric-looking key (Azure/GitLab) or status would
                    # otherwise round-trip as a JSON number/bool instead of the
                    # string it is, making it invisible to `_external_id`'s
                    # matching and reborn as a duplicate entity every cycle.
                    ("identity.external_id", json.dumps(item.key, ensure_ascii=False)),
                    ("external.status.value", json.dumps(item.status, ensure_ascii=False)),
                    ("external.tags", json.dumps(list(item.tags), ensure_ascii=False)),
                    ("external.id", json.dumps(item.id, ensure_ascii=False)),
                    ("external.key", json.dumps(item.key, ensure_ascii=False)),
                    ("external.title", json.dumps(item.title, ensure_ascii=False)),
                ]
                for ext_field, ext_value in _extra_external_fields(item):
                    fields.append((f"external.{ext_field}", json.dumps(ext_value, ensure_ascii=False)))
                if item.sorted_relations:
                    fields.append(("relations", json.dumps(item.sorted_relations, ensure_ascii=False)))
                initial_state = _tag_derived_state(
                    item, tag_rules.get(bucket, ()), current_state=None, at_creation=True
                ) or policy.get("initial_state")
                if initial_state:
                    # Seeds the entity into the workflow state its type starts in
                    # (e.g. a fresh user story into READY_FOR_TEST_DESIGN) -- without
                    # this, `internal` stays `{}` forever and no gate that watches
                    # `internal.state` ever picks the entity up.
                    fields.append(("internal.state", json.dumps(initial_state, ensure_ascii=False)))

                for field_name, value in fields:
                    ok, message = _run_update_state_op(
                        script_path,
                        state_path,
                        [
                            "--op", "update-artifact",
                            "--entity-type", entity_type,
                            "--internal-id", internal_id,
                            "--field", field_name,
                            "--value", value,
                        ],
                    )
                    if not ok:
                        write_errors.append(f"update-artifact {field_name} {item.key}: {message}")

                by_external_id[bucket][item.key] = {"identity": {"internal_id": internal_id}}
                created += 1
            else:
                # Not matched and not (creatable and ready) -- stays out of the
                # file entirely, visible only through `alm-conn export`.
                #
                # Logged at INFO only for the genuinely actionable case -- a bucket
                # that DOES have a creation policy but this item fails its
                # readiness gate -- naming exactly what's missing, the same
                # question `_is_ready()` itself answers but never previously
                # surfaced anywhere. The other two paths here (no entity type
                # mapped for this bucket at all; bucket has no creation policy,
                # i.e. tracked-only by design) are not gate failures, just
                # "nothing to create here", so they stay at DEBUG to avoid
                # burying the actionable line under the tracked-only majority.
                if entity_type is not None and bucket in creation:
                    policy = creation[bucket]
                    ready_statuses = tuple(policy.get("ready_statuses", ()))
                    ready_tags = tuple(policy.get("ready_tags", ()))
                    log.info(
                        "not created: %s %r (bucket=%s, status=%r, tags=%r) -- needs "
                        "status in %r or a tag in %r",
                        item.key, item.title, bucket, item.status, list(item.tags),
                        ready_statuses, ready_tags,
                    )
                else:
                    log.debug(
                        "not created: %s (bucket=%s) -- tracked only, no creation policy",
                        item.key, bucket,
                    )
                unmatched += 1

        # A ticket the sync engine confirmed gone from the tracker (a re-fetch
        # that returned 404 -- `runner._resolve_vanished`, which keeps that apart
        # from an item that merely left a query's scope) has to leave state.json
        # too, or the factory keeps showing and scheduling work for something
        # nobody can open any more. Through `delete-entity` like every other
        # mutation here, never by rewriting the file.
        for key in deleted:
            for bucket, index in by_external_id.items():
                entity = index.get(key)
                if entity is None:
                    continue
                entity_type = _BUCKET_ENTITY_TYPES.get(bucket)
                internal_id = (entity.get("identity") or {}).get("internal_id")
                if entity_type is None or not internal_id:
                    continue
                ok, message = _run_update_state_op(
                    script_path,
                    state_path,
                    [
                        "--op", "delete-entity",
                        "--entity-type", entity_type,
                        "--internal-id", str(internal_id),
                        "--actor", "ALM_SYNC",
                        "--reason", f"{key} no longer exists in the tracker",
                    ],
                )
                if ok:
                    log.info("%s: deleted in tracker, retired %s", key, internal_id)
                    removed += 1
                else:
                    write_errors.append(f"delete-entity {key}: {message}")
                index.pop(key, None)

        # Sprints are a flat, non-entity bucket with no `update_state.py` op of
        # its own -- there is no `--op` to delegate this to. Narrowly scoped to
        # just this one top-level key, re-read fresh right before writing so the
        # window against a concurrent writer is as small as this function can
        # make it without a real op to call.
        if sprints is not None:
            current = read_json(state_path)
            if isinstance(current, dict):
                current["sprints"] = {
                    sprint_id: {
                        "id": sprint_id,
                        "name": data.get("name"),
                        "state": data.get("state"),
                        "board_id": data.get("originBoardId"),
                        "goal": data.get("goal"),
                        "start": data.get("startDate"),
                        "end": data.get("endDate"),
                    }
                    for sprint_id, data in sprints.items()
                }
                atomic_write_json(state_path, current)

    log.info(
        "write_into_state finished in %.1fs: items=%d matched=%d created=%d retagged=%d deleted=%d unmatched=%d errors=%d",
        time.monotonic() - _cycle_started, len(items), matched, created, retagged, removed, unmatched, len(write_errors),
    )

    return {
        "items": len(items),
        "matched": matched,
        "created": created,
        "retagged": retagged,
        "unmatched": unmatched,
        "events": len(events),
        "queued": 0,
        "deleted": removed,
        "version": version,
        "errors": len(write_errors),
        "error_details": write_errors,
    }
