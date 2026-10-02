"""One sync cycle, and the loop that repeats it.

The cycle is the only part of sync that touches the network and the clock. It is
deliberately thin: fetch, hand the comparison to `diff`, and hand the results to the
stores. All the judgement lives in the pure functions it calls.

Two properties matter more than anything else here:

*Idempotence.* Running a cycle twice over an unchanged tracker must produce zero
events and queue zero actions. Everything downstream — the history, the
orchestrator's queue — assumes this, because a scheduler that fires every five
minutes will spend almost all of its runs observing nothing at all.

*No guessing.* When a lookup fails in a way that leaves a question genuinely open —
most importantly, whether a vanished item was deleted — the cycle records that it
could not tell and moves on. A wrong `DELETED` written to an append-only history is
permanent.

Observing and signalling are separate steps here. The cycle observes every item the
configured scope returns and rebuilds the full baseline from all of them; only the
changes that pass `watchlist` go on to become history lines and queued actions. So
`state.json` stays a complete picture of the tracker while the queue stays narrow.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .. import api, operations
from ..clients.base import DEFAULT_TIMEOUT
from ..env import load_project_env
from ..errors import ConnectionSourceError, NotFoundError
from ..export import export_project
from ..health import load_project, sources_path
from ..models import AlmRecord, SourceConfig
from . import archive, history, scope, snapshot, sprints as sprint_pass
from .detail_log import configure_detail_logging
from .diff import diff_items, diff_sprints, vanished_event
from .models import ChangeEvent, TrackedItem
from .normalize import merge_relations, to_tracked_item
from .state import update_state, write_into_state
from .store import atomic_write_json, now_iso, read_json
from .taxonomy import load_buckets
from .watchlist import load_watched_buckets, partition_events
from .azure_links import azure_relations_for
from .xray_membership import xray_relations_for

__all__ = ["SyncResult", "load_sync_config", "sync_once", "watch"]

log = logging.getLogger("connection_sources.sync.runner")

DEFAULT_INTERVAL = 300
DEFAULT_FULL_RECONCILE_EVERY = 12
DEFAULT_LIMIT = 1000


@dataclass
class SyncResult:
    """What one cycle observed and wrote."""

    project: str
    started_at: str
    finished_at: str = ""
    cycle: int = 0
    full_reconcile: bool = True
    # Only the changes that passed the watchlist. What was filtered out is counted
    # in `ignored` rather than carried, so nothing downstream can act on a change
    # the project asked not to be signalled about by reaching past this field.
    events: list[ChangeEvent] = field(default_factory=list)
    ignored: int = 0
    items_seen: int = 0
    state_version: int = 0
    snapshots_written: int = 0
    history_written: int = 0
    actions_queued: int = 0
    exports_refreshed: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    # Keys whose snapshot was dropped because they left the configured scope.
    pruned: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "cycle": self.cycle,
            "full_reconcile": self.full_reconcile,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "items_seen": self.items_seen,
            "state_version": self.state_version,
            "event_count": len(self.events),
            "ignored_count": self.ignored,
            "events": [event.to_dict() for event in self.events],
            "snapshots_written": self.snapshots_written,
            "history_written": self.history_written,
            "actions_queued": self.actions_queued,
            "exports_refreshed": self.exports_refreshed,
            "unresolved": self.unresolved,
            "pruned": self.pruned,
            "errors": self.errors,
        }


def load_sync_config(project: str | Path) -> dict[str, Any]:
    """The optional `sync` block of sources.json.

    Read straight from the file rather than through `ProjectSourcesConfig`, which
    intentionally models only connection concerns. Absent block means defaults, so
    every existing project config keeps working untouched.
    """
    raw = read_json(sources_path(project), default={})
    block = raw.get("sync") if isinstance(raw, dict) else None
    return dict(block) if isinstance(block, Mapping) else {}


def default_state_dir(project: str | Path) -> Path:
    """Where snapshots/history/scheduler-meta live when the caller does not say.

    A project's state.json is normally its own, but a project can instead declare
    it is the orchestrator's (`sync.state_path` set), in which case these live
    alongside that file rather than under this project's own `_bmad_state` --
    there is no reason a cycle's snapshots and history should sit in a different
    directory tree than the state.json they describe, and every caller that does
    not pass an explicit `--state-dir` (the CLI, the scheduler launcher,
    `alm-conn export`'s default) should not have to be told the location twice. A
    project with no `state_path` configured keeps the original default untouched.
    """
    state_path = load_sync_config(project).get("state_path")
    if state_path:
        candidate = Path(state_path)
        resolved = candidate if candidate.is_absolute() else Path(project) / candidate
        return resolved.parent
    return Path(project) / "_bmad_state"


def load_detail_fields(sync_config: Mapping[str, Any] | None) -> dict[str, str]:
    """The `test_detail_fields` block: our label for a field, to its id on this site.

    Declared per project for the same reason the sprint field is — a custom field id
    is assigned by the instance that defines it, so the same "Test Type" is a
    different id on every site and can never be hardcoded here. An entry whose id is
    not a string is dropped rather than raising: a typo in one line of config must
    not stop the poller from reporting everything else it can still see.
    """
    if not isinstance(sync_config, Mapping):
        return {}
    declared = sync_config.get("test_detail_fields")
    if not isinstance(declared, Mapping):
        return {}
    return {
        label.strip().lower(): field_id.strip()
        for label, field_id in declared.items()
        if isinstance(label, str) and isinstance(field_id, str)
        and label.strip() and field_id.strip()
    }


def _meta_path(state_dir: Path) -> Path:
    return state_dir / "sync-meta.json"


def _extra_fields_for(server: str, extra_fields: list[str]) -> list[str]:
    """The extra field ids one tracker can be asked for.

    Jira takes them all. Azure DevOps takes only reference names, which always
    contain a dot (`System.Description`, `Microsoft.VSTS.Common.AcceptanceCriteria`,
    `Custom.X`); a Jira id such as `customfield_10020` never does, and naming a
    field Azure does not know fails the whole read. Other trackers take none.
    """
    if server == "jira":
        return extra_fields
    if server == "azuredevops":
        return [field_id for field_id in extra_fields if "." in field_id]
    return []


def _fetch(
    project: str | Path,
    source: SourceConfig,
    *,
    limit: int,
    timeout: float,
    extra_fields: list[str] | None,
    since_minutes: int | None,
) -> list[AlmRecord]:
    """This source's current items, narrowed to recent changes when asked.

    The narrowed read is a plain JQL refinement of the configured scope, so it stays
    honest about what the project declared it cares about — it only ever removes
    rows the full read would have returned, never adds any.
    """
    if since_minutes and source.server == "jira":
        base = source.scope.get("jql")
        if base:
            env = load_project_env(project)
            jql = f'({base}) AND updated >= "-{int(since_minutes)}m"'
            with operations.open_client(source, env, timeout=timeout) as client:
                return list(
                    client.iter_search(jql, limit=limit, extra_fields=extra_fields or None)
                )

    return api.read(
        project, source.name, limit=limit, timeout=timeout, extra_fields=extra_fields or None
    )


def _resolve_vanished(
    project: str | Path,
    source: SourceConfig,
    vanished: Iterable[TrackedItem],
    *,
    detected_at: str,
    timeout: float,
    buckets: Mapping[str, str] | None,
    sprint_field: str | None,
    detail_fields: Mapping[str, str] | None = None,
    extra_fields: list[str] | None = None,
    track_links: bool = False,
) -> tuple[list[ChangeEvent], list[str], list[str], dict[str, TrackedItem]]:
    """Decide, per vanished item, whether it was deleted or merely left the scope.

    Returns the events, the keys confirmed deleted, the keys we could not settle,
    and — for items that merely left scope — their freshly re-fetched state, so the
    snapshot that survives reflects what the item looks like *now* and not what it
    looked like the moment before it left, which is stale the instant it is written.
    """
    events: list[ChangeEvent] = []
    deleted: list[str] = []
    unresolved: list[str] = []
    refreshed: dict[str, TrackedItem] = {}

    for item in vanished:
        try:
            current = api.get(
                project, source.name, item.key, timeout=timeout, extra_fields=extra_fields
            )
        except NotFoundError:
            events.append(vanished_event(item, detected_at=detected_at, still_exists=False))
            deleted.append(item.key)
        except ConnectionSourceError:
            # Permission, transport, rate limit — the item's fate is genuinely
            # unknown. Keep the snapshot so the next cycle asks again, and say so
            # rather than inventing a deletion.
            unresolved.append(item.key)
        else:
            event = vanished_event(
                item,
                detected_at=detected_at,
                still_exists=True,
                current_status=current.status,
            )
            if event is not None:
                events.append(event)
            refreshed[item.key] = to_tracked_item(
                current,
                source=source.name,
                buckets=buckets,
                sprint_field=sprint_field,
                detail_fields=detail_fields,
                track_links=track_links,
            )
    return events, deleted, unresolved, refreshed


def _prune_out_of_scope(
    project: str | Path,
    source: SourceConfig,
    vanished: list[TrackedItem],
    *,
    timeout: float,
    result: SyncResult,
) -> tuple[list[TrackedItem], list[TrackedItem]]:
    """Split vanished items into (confirmed out of scope, still to resolve).

    The offline comparison in `scope` is authoritative where a backend's scope is
    a single project by construction (Azure, GitLab). For Jira it only nominates
    candidates, and the tracker settles them against the configured JQL itself --
    batched, so a thousand stale keys cost a handful of requests rather than a
    thousand. A confirmation that fails leaves everything to the existing
    per-item path: not pruning is always the safe direction.
    """
    candidates, rest = scope.partition_by_scope(vanished, source)
    if not candidates:
        return [], rest

    if scope.scope_is_single_project(source):
        log.info(
            "source=%s: %d vanished item(s) belong to another project than the "
            "configured scope", source.name, len(candidates),
        )
        return candidates, rest

    jql = str((source.scope or {}).get("jql") or "")
    if not jql:
        return [], vanished

    env = load_project_env(project)

    def search_keys(query: str) -> set[str]:
        with operations.open_client(source, env, timeout=timeout) as client:
            return {
                record.key
                for record in client.iter_search(query, limit=DEFAULT_LIMIT)
                if record.key
            }

    try:
        out_of_scope, keep = scope.confirm_jira_out_of_scope(
            candidates, jql=jql, open_search=search_keys
        )
    except ConnectionSourceError as exc:
        # Could not ask, so nothing is settled. Everything goes back to the
        # per-item path rather than being pruned on the offline guess alone.
        log.warning("scope confirmation failed source=%s: %s", source.name, exc)
        result.errors.append({"source": source.name, "stage": "scope_confirm", **exc.to_dict()})
        return [], vanished

    return out_of_scope, rest + keep


def _previous_xray_relations(
    previous: Mapping[str, TrackedItem]
) -> dict[str, dict[str, list[dict[str, str]]]]:
    """Each item's last-known Xray-native relation entries, keyed like
    `xray_relations_for`'s own return value so a caller can hand this straight
    to `merge_relations` in its place.

    Used only when this cycle's own Xray read failed. Filtering by `link_type
    == "xray"` (the tag `xray_relations` -- and only it -- stamps on an entry)
    keeps this to just the membership Xray itself reported; a field the
    issuelinks pass already populated this cycle is left alone rather than
    duplicated.
    """
    carried: dict[str, dict[str, list[dict[str, str]]]] = {}
    for key, prior in previous.items():
        fields = {
            field_name: [entry for entry in entries if entry.get("link_type") == "xray"]
            for field_name, entries in prior.relations.items()
        }
        fields = {name: entries for name, entries in fields.items() if entries}
        if fields:
            carried[key] = fields
    return carried


def sync_once(
    project: str | Path,
    *,
    state_dir: Path,
    source: str | None = None,
    limit: int = DEFAULT_LIMIT,
    timeout: float = DEFAULT_TIMEOUT,
    dry_run: bool = False,
    force_full: bool = False,
    emit_actions: bool = True,
    on_event: Callable[[ChangeEvent], None] | None = None,
) -> SyncResult:
    """Run one fetch/diff/record cycle over every enabled source (or just one)."""
    detail_log_path = configure_detail_logging(state_dir)
    cycle_started_at = time.monotonic()
    started = now_iso()
    config = load_project(project)
    sync_config = load_sync_config(project)
    buckets = load_buckets(sync_config)
    watched = load_watched_buckets(sync_config)
    sprint_field = sync_config.get("sprint_field")
    detail_fields = load_detail_fields(sync_config)
    # Track issue links when asked: on a plain-Jira site they are the test
    # structure, so a membership or coverage change has to surface like any other.
    track_links = bool(sync_config.get("track_links"))
    # Off unless asked for: this is the one thing a cycle does that removes a
    # record rather than adding one, and a project that narrowed its scope on
    # purpose should say so before the previous scope's snapshots are dropped.
    prune_out_of_scope = bool(sync_config.get("prune_out_of_scope"))
    # Every instance-specific id the cycle needs, asked for in the same fetch: the
    # client only returns custom fields a caller named, so a declared detail field
    # that is not requested here would read as permanently absent.
    extra_fields = [sprint_field] if sprint_field else []
    extra_fields += [fid for fid in detail_fields.values() if fid not in extra_fields]
    if track_links:
        extra_fields += [fid for fid in ("issuelinks",) if fid not in extra_fields]

    meta = read_json(_meta_path(state_dir), default={}) or {}
    cycle = int(meta.get("cycle", 0)) + 1
    every = int(sync_config.get("full_reconcile_every", DEFAULT_FULL_RECONCILE_EVERY))
    interval = int(sync_config.get("interval_seconds", DEFAULT_INTERVAL))
    # The first cycle, and every Nth, reads the full scope. An incremental read can
    # never observe a deletion — the item is simply not in a result set restricted
    # to things that changed recently — so deletions are only ever settled here.
    full = force_full or cycle == 1 or every <= 1 or (cycle % every == 1)
    since_minutes = None if full else max(2, (interval * every) // 60 + 1)

    # `is_syncable` excludes the `context` sources (a wiki, say). They are read into
    # the traceability graph and nowhere else: a specification page has no workflow
    # state and no Contract step, so syncing one into state.json would put a document
    # in the factory's backlog as work that can never be advanced. An explicit
    # `--source <name>` does not override this -- naming a context source asks for
    # something the sync cannot do, and doing it quietly would be worse than the
    # empty result.
    targets = [
        s
        for s in config.enabled_sources()
        if s.is_syncable and (source is None or s.name == source)
    ]
    log.info(
        "sync_once launched: project=%s cycle=%d full_reconcile=%s targets=%s",
        project, cycle, full, [t.name for t in targets],
    )
    result = SyncResult(
        project=str(project), started_at=started, cycle=cycle, full_reconcile=full
    )

    all_items: list[TrackedItem] = []
    all_deleted: list[str] = []

    for target in targets:
        target_extra_fields = _extra_fields_for(target.server, extra_fields)

        fetch_started = time.monotonic()
        log.info("fetching source=%s incremental=%s limit=%d", target.name, not full, limit)
        try:
            records = _fetch(
                project,
                target,
                limit=limit,
                timeout=timeout,
                extra_fields=target_extra_fields,
                since_minutes=since_minutes,
            )
        except ConnectionSourceError as exc:
            # One broken source must never hide a working one, matching how
            # `probe` already treats per-source failure.
            log.warning("fetch failed source=%s: %s", target.name, exc)
            result.errors.append({"source": target.name, **exc.to_dict()})
            continue
        log.info(
            "fetched source=%s records=%d in %.1fs",
            target.name, len(records), time.monotonic() - fetch_started,
        )

        current = {
            item.key: item
            for item in (
                to_tracked_item(
                    record,
                    source=target.name,
                    buckets=buckets,
                    sprint_field=sprint_field,
                    detail_fields=detail_fields,
                    track_links=track_links and target.server == "jira",
                )
                for record in records
            )
            if item.key
        }

        previous = snapshot.load_snapshots(state_dir, target.name)

        if track_links and target.server == "jira":
            # Issuelinks (above) are the whole membership story on a plain site;
            # once Xray is real, its own container membership is separate from
            # issuelinks entirely, so state.json needs this second read to agree
            # with the graph regardless of which tier this project is on.
            xray_by_key, xray_error = xray_relations_for(
                project,
                target,
                records=records,
                jql=str(target.scope.get("jql") or ""),
                detail_fields=detail_fields,
                timeout=timeout,
            )
            if xray_error:
                result.errors.append(xray_error)
                # A transient Xray outage (DNS blip, proxy hiccup -- `retryable:
                # true`) must never read as "the membership was removed". Left
                # alone, this cycle's `current` would simply lack every
                # Xray-native entry, `diff_items` would see each go from present
                # to gone, and -- because `full_reconcile_every` makes most
                # cycles full, so `merged` becomes exactly `current` -- that
                # false removal would be what the *next* cycle calls "previous"
                # too, only to flap back the moment Xray answers again. See
                # `_previous_xray_relations` for the "could not tell, so do not
                # touch" fallback this applies instead.
                xray_by_key = _previous_xray_relations(previous)
            for key, extra in xray_by_key.items():
                item = current.get(key)
                if item is not None:
                    current[key] = replace(item, relations=merge_relations(item.relations, extra))

        if track_links and target.server == "azuredevops":
            links_by_id, links_error = azure_relations_for(
                project, target, records=records, timeout=timeout
            )
            if links_error:
                result.errors.append(links_error)
                links_by_id = {key: dict(prior.relations) for key, prior in previous.items()}
            for key, links in links_by_id.items():
                item = current.get(key)
                if item is not None:
                    current[key] = replace(item, relations=links)

        log.info(
            "source=%s: comparing %d previous item(s) vs %d current item(s) via content-hash diff",
            target.name, len(previous), len(current),
        )
        outcome = diff_items(
            previous, current, detected_at=started, incremental=not full
        )
        events = list(outcome.events)

        deleted: list[str] = []
        vanished_items = list(outcome.vanished)
        left_scope: list[TrackedItem] = []
        if vanished_items and prune_out_of_scope:
            # Truncation is the one way a full read can be missing something that
            # is still in scope, and it is indistinguishable from a real absence
            # after the fact -- so a read that came back at the cap prunes nothing.
            truncated = len(records) >= limit
            if not full:
                log.debug("prune skipped: incremental cycle")
            elif truncated:
                log.warning(
                    "prune skipped source=%s: read hit the limit (%d), cannot tell "
                    "truncation from absence", target.name, limit,
                )
            else:
                left_scope, vanished_items = _prune_out_of_scope(
                    project, target, vanished_items, timeout=timeout, result=result
                )

        if left_scope:
            for item in left_scope:
                event = vanished_event(item, detected_at=started, still_exists=True)
                if event is not None:
                    events.append(event)
            log.info(
                "source=%s: %d item(s) left the configured scope; dropping their snapshots",
                target.name, len(left_scope),
            )

        unresolved: list[str] = []
        refreshed: dict[str, TrackedItem] = {}
        if vanished_items:
            gone_events, deleted, unresolved, refreshed = _resolve_vanished(
                project,
                target,
                vanished_items,
                detected_at=started,
                timeout=timeout,
                buckets=buckets,
                sprint_field=sprint_field,
                detail_fields=detail_fields,
                extra_fields=target_extra_fields or None,
                track_links=track_links and target.server == "jira",
            )
            events.extend(gone_events)
            all_deleted.extend(deleted)
            result.unresolved.extend(unresolved)
            # An item that left the scope is still real, so it stays in the state and
            # in the snapshots — under the status it actually has *now* (refreshed),
            # not the one it had the instant before it left. Only a confirmed
            # deletion is dropped; an unresolved one keeps its last-known snapshot
            # untouched so the next cycle asks again instead of losing the record.
            current.update(refreshed)

        # On an incremental cycle the fetch saw only recently-touched items, so the
        # full picture is the previous generation with those laid over it.
        merged = current if full else {**previous, **current}

        try:
            sprints_now = sprint_pass.collect_sprints(project, target, timeout=timeout)
        except ConnectionSourceError as exc:
            result.errors.append({"source": target.name, "stage": "sprints", **exc.to_dict()})
            sprints_now = {}

        sprints_before = (meta.get("sprints") or {}).get(target.name) or {}
        if sprints_now or sprints_before:
            events.extend(
                diff_sprints(
                    sprints_before,
                    sprints_now,
                    system=target.server,
                    source=target.name,
                    detected_at=started,
                )
            )

        # Narrow to the changes this project asked to be signalled about. The
        # baseline below is written from `merged` either way, so an ignored change
        # is absorbed into the snapshot on this cycle and never resurfaces.
        signalled, ignored = partition_events(events, watched)

        if events or outcome.vanished:
            log.info(
                "source=%s: diff complete -- %d change(s) detected (%d signalled, "
                "%d ignored by watchlist), %d vanished item(s)",
                target.name, len(events), len(signalled), len(ignored), len(outcome.vanished),
            )
            # One line per change, in plain language -- the summary above answers
            # "how many", this answers "which ones and what happened". Both halves
            # get a line, distinguished by whether this change also raises a
            # signal (a history line, a queued action) or is absorbed into the
            # snapshot silently: a bucket outside `watch_buckets` (e.g. a fresh
            # user story on a project only watching tests/test_plans) still
            # deserves to be seen here, even though it raises nothing downstream --
            # that gap is exactly what made one real case unreadable from the log
            # alone (2026-09-20: a new story read as "ignored" in the one-line
            # summary, which is about signalling, not about the separate,
            # unrelated readiness gate that actually decides state.json creation
            # -- see state.py's own new log line for that half of the story).
            for event in signalled:
                log.info("source=%s: %s", target.name, event.describe())
            for event in ignored:
                log.info(
                    "source=%s: %s [not signalled -- bucket %r is outside watch_buckets]",
                    target.name, event.describe(), event.bucket,
                )
        else:
            log.debug("source=%s: diff complete -- no changes", target.name)

        result.events.extend(signalled)
        result.ignored += len(ignored)
        result.items_seen += len(merged)
        all_items.extend(merged.values())

        if on_event:
            for event in signalled:
                on_event(event)

        if not dry_run:
            result.snapshots_written += snapshot.write_snapshots(state_dir, target.name, merged)
            for key in deleted:
                snapshot.remove_snapshot(state_dir, target.name, key)
            # An item confirmed out of scope keeps its entity in state.json -- the
            # orchestrator owns that, and a scope change is not grounds for this
            # engine to retire someone else's work -- but its snapshot goes, so the
            # next cycle stops treating it as something to re-resolve. Without this
            # the item is re-fetched and re-persisted from `merged` every cycle.
            for item in left_scope:
                snapshot.remove_snapshot(state_dir, target.name, item.key)
                result.pruned.append(item.key)
            meta.setdefault("sprints", {})[target.name] = sprints_now

    if not dry_run:
        # State first: it is what assigns the generation number, and the history
        # line and the export both have to carry the same one to be correlatable
        # afterwards. Either write can still fail on its own, and state is the
        # authority on what the current generation is, so it goes first and the
        # others quote it rather than each inventing a number of their own.
        #
        # A project can declare its state.json is actually the orchestrator's own
        # via `state_path` in sources.json's `sync` block -- the normal setup, not
        # a special case. There is only ever one state.json written per cycle --
        # this project's own `state_dir / "state.json"` is never created or
        # touched once `state_path` is set; the two branches below are mutually
        # exclusive, not a pair kept in sync.
        state_path_setting = sync_config.get("state_path")
        if state_path_setting:
            state_write = write_into_state
            candidate = Path(state_path_setting)
            target_state_path = (
                candidate if candidate.is_absolute() else Path(project) / candidate
            )
            update_state_script = sync_config.get("update_state_script")
            if not update_state_script:
                raise ConnectionSourceError(
                    "sync.state_path is set but sync.update_state_script is not -- "
                    "the owner's update_state.py path is required to write into it"
                )
            script_candidate = Path(update_state_script)
            resolved_script = (
                script_candidate if script_candidate.is_absolute() else Path(project) / script_candidate
            )
            content_root = sync_config.get("content_root")
            resolved_content_root = None
            if content_root:
                content_root_candidate = Path(content_root)
                resolved_content_root = (
                    content_root_candidate if content_root_candidate.is_absolute() else Path(project) / content_root_candidate
                )
            extra_kwargs: dict[str, Any] = {
                "update_state_script": resolved_script,
                "creation": sync_config.get("creation"),
                "input_root": sync_config.get("input_root", "_bmad-input"),
                "content_root": resolved_content_root,
                "tag_states": sync_config.get("tag_states"),
            }
        else:
            state_write = update_state
            target_state_path = state_dir / "state.json"
            extra_kwargs = {}

        summary = state_write(
            target_state_path,
            items=all_items,
            events=result.events,
            sprints=_merged_sprints(meta),
            deleted_keys=all_deleted,
            project_id=config.project,
            emit_actions=emit_actions,
            **extra_kwargs,
        )
        result.actions_queued = summary["queued"]
        result.state_version = summary["version"]
        state_write_errors = list(summary.get("error_details", ()))
        for message in state_write_errors:
            result.errors.append({"source": "state", "stage": "state_write", "message": message})
        log.info(
            "state write complete: %s -> state_version=%s queued=%d error(s)=%d",
            target_state_path, result.state_version, result.actions_queued, len(state_write_errors),
        )

        result.history_written = history.append_events(
            state_dir, result.events, state_version=result.state_version
        )
        if result.history_written:
            log.info(
                "history: wrote %d event line(s) for state_version=%s",
                result.history_written, result.state_version,
            )

        if result.events and not state_write_errors:
            # Timestamped archive pair -- a full state.json copy plus this cycle's
            # own change list, both addressable by run rather than by day. Gated
            # on result.events (state_version having actually advanced), same as
            # the day-log above: a poller firing every few minutes must not leave
            # an archive file behind for every tick that changed nothing.
            #
            # Also gated on the state write having fully succeeded: archiving a
            # generation the writer itself reported errors on would file a
            # half-applied document under a timestamp that claims it is what the
            # project looked like at that instant, which is worse than no archive.
            #
            # Wrapped, and narrowly: these two writes are auxiliary, and `watch()`
            # below only rescues `ConnectionSourceError`. An OSError here (a full
            # PVC in a sandbox, a permissions quirk, a path length limit on Windows)
            # would otherwise escape the loop and kill a poller whose actual job --
            # writing state.json -- has already succeeded at this point.
            try:
                archive.archive_state(state_dir, target_state_path, timestamp=started)
                archive.write_change_log(
                    state_dir, result.events, state_version=result.state_version, timestamp=started
                )
            except OSError as exc:
                log.error("archive failed for state_version=%s: %s", result.state_version, exc)
                result.errors.append(
                    {"source": "state", "stage": "archive", "message": str(exc)}
                )

        if result.events and sync_config.get("export_on_change"):
            _refresh_exports(
                project,
                {event.source for event in result.events},
                limit=limit,
                timeout=timeout,
                state_version=result.state_version,
                result=result,
            )

        meta.update(
            {
                "cycle": cycle,
                "last_run": started,
                "last_full_reconcile": started if full else meta.get("last_full_reconcile"),
                "project": config.project,
            }
        )
        atomic_write_json(_meta_path(state_dir), meta)

    result.finished_at = now_iso()
    log.info(
        "sync_once cycle=%d complete: items_seen=%d changes=%d ignored=%d queued=%d "
        "error(s)=%d duration=%.1fs -- narrative log: %s",
        cycle, result.items_seen, len(result.events), result.ignored, result.actions_queued,
        len(result.errors), time.monotonic() - cycle_started_at, detail_log_path,
    )
    return result


def _refresh_exports(
    project: str | Path,
    sources: Iterable[str],
    *,
    limit: int,
    timeout: float,
    state_version: int,
    result: SyncResult,
) -> None:
    """Rewrite the on-disk export for each source that just changed.

    Only the sources named by this cycle's events are touched: re-reading a source
    that did not change would cost a full fetch to produce a byte-identical folder,
    and every extra call is another chance to hit a rate limit that matters far more
    when the poller is running unattended.

    A failure here is recorded and does not propagate. The export is a derived copy;
    the state and the history are already written and correct, and losing the whole
    cycle over a stale copy would trade the authoritative record for the convenient
    one.
    """
    for name in sorted(sources):
        try:
            export_project(
                project, source=name, limit=limit, timeout=timeout, state_version=state_version
            )
        except ConnectionSourceError as exc:
            result.errors.append({"source": name, "stage": "export", **exc.to_dict()})
        else:
            result.exports_refreshed.append(name)


def _merged_sprints(meta: Mapping[str, Any]) -> dict[str, dict]:
    """Every source's sprints in one mapping, for the state document."""
    merged: dict[str, dict] = {}
    for by_source in (meta.get("sprints") or {}).values():
        if isinstance(by_source, Mapping):
            merged.update(by_source)
    return merged


def watch(
    project: str | Path,
    *,
    state_dir: Path,
    interval: int | None = None,
    max_cycles: int | None = None,
    on_result: Callable[[SyncResult], None] | None = None,
    **kwargs: Any,
) -> int:
    """Run cycles forever (or `max_cycles` times), pausing `interval` between them.

    A cycle that fails is reported and does not stop the loop: a five-minute poller
    that dies on the first network blip is worse than useless, because it looks like
    a quiet project.
    """
    if interval is None:
        interval = int(load_sync_config(project).get("interval_seconds", DEFAULT_INTERVAL))
    interval = max(1, int(interval))

    completed = 0
    while max_cycles is None or completed < max_cycles:
        try:
            result = sync_once(project, state_dir=state_dir, **kwargs)
            if on_result:
                on_result(result)
        except ConnectionSourceError as exc:
            if on_result:
                failed = SyncResult(project=str(project), started_at=now_iso())
                failed.errors.append(exc.to_dict())
                failed.finished_at = now_iso()
                on_result(failed)
        except KeyboardInterrupt:
            return completed

        completed += 1
        if max_cycles is not None and completed >= max_cycles:
            break
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            return completed
    return completed
