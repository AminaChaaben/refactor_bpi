"""Compare two generations of tracked items and say exactly what changed.

Everything here is a pure function: no network, no disk, no clock beyond the
timestamp handed in. That is deliberate — this is the only part of sync that can be
*wrong* in a way nobody notices, so it has to be exhaustively testable without a
Jira instance anywhere near it.

One asymmetry is worth knowing about. A key appearing is unambiguous: it is new. A
key *disappearing* is not — the item may have been deleted, or it may simply have
fallen out of the configured scope (the standard Jira scope excludes Done, so
closing a ticket removes it from the result set exactly as deleting it would).
Telling those apart needs a targeted lookup, which is I/O, so `diff_items` does not
guess: it reports the vanished items and lets the caller resolve them, then hands
the answer back to `vanished_event` to be turned into the right event.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from .models import (
    DEFAULT_DETAIL_EVENT_KIND,
    DETAIL_EVENT_KINDS,
    TRACKED_FIELDS,
    ChangeEvent,
    TrackedItem,
)

__all__ = ["DiffResult", "diff_items", "diff_details", "diff_sprints", "vanished_event"]


@dataclass(frozen=True, slots=True)
class DiffResult:
    """What the comparison could settle, and what it could not."""

    events: list[ChangeEvent] = field(default_factory=list)
    vanished: list[TrackedItem] = field(default_factory=list)


def _event(
    kind: str,
    item: TrackedItem,
    detected_at: str,
    *,
    field_name: str | None = None,
    before: object = None,
    after: object = None,
    **details: object,
) -> ChangeEvent:
    return ChangeEvent(
        kind=kind,
        system=item.system,
        source=item.source,
        key=item.key,
        title=item.title,
        bucket=item.bucket,
        url=item.url,
        field_name=field_name,
        before=before,
        after=after,
        item_updated_at=item.updated_at,
        detected_at=detected_at,
        details=dict(details),
    )


def diff_details(
    before: TrackedItem, after: TrackedItem, detected_at: str
) -> list[ChangeEvent]:
    """One event per test-shape field that moved, named by what moved.

    Reported per field rather than as a single "details changed": the point of
    watching a test's shape is to know that its *type* went from generic to
    cucumber, which a lump event carrying two opaque blobs cannot say. A field
    appearing or disappearing is a change like any other, so the union of both
    generations' labels is walked, not just the ones present now.
    """
    old_map, new_map = before.detail_map, after.detail_map
    events: list[ChangeEvent] = []

    for label in sorted(set(old_map) | set(new_map)):
        was, now = old_map.get(label), new_map.get(label)
        if was == now:
            continue
        events.append(
            _event(
                DETAIL_EVENT_KINDS.get(label, DEFAULT_DETAIL_EVENT_KIND),
                after,
                detected_at,
                field_name=label,
                before=was,
                after=now,
                detail=label,
            )
        )
    return events


def diff_items(
    previous: Mapping[str, TrackedItem],
    current: Mapping[str, TrackedItem],
    *,
    detected_at: str,
    incremental: bool = False,
) -> DiffResult:
    """Compare two generations, keyed by item key.

    `incremental` says the current generation came from a narrowed query (only items
    touched recently) rather than the full scope. In that mode an absent key means
    "not touched lately", not "gone", so disappearance is not reported at all —
    claiming a deletion from a partial read would be pure fiction. Deletions surface
    on the next full reconcile.
    """
    events: list[ChangeEvent] = []
    vanished: list[TrackedItem] = []

    for key, item in current.items():
        before = previous.get(key)
        if before is None:
            events.append(_event("CREATED", item, detected_at))
            continue

        if before.content_hash != item.content_hash:
            for attr, kind in TRACKED_FIELDS:
                old = getattr(before, attr)
                new = getattr(item, attr)
                if attr == "tags":
                    old, new = sorted(old or ()), sorted(new or ())
                if old != new:
                    events.append(
                        _event(kind, item, detected_at, field_name=attr, before=old, after=new)
                    )
            events.extend(diff_details(before, item, detected_at))
        elif before.updated_at != item.updated_at and item.updated_at:
            # The tracker moved its own timestamp while every field we compare stayed
            # put — a description edit, a comment, an attachment. Still a real change
            # to the item, so it belongs in the history even though we cannot name
            # the field.
            events.append(
                _event(
                    "UPDATED",
                    item,
                    detected_at,
                    field_name="updated_at",
                    before=before.updated_at,
                    after=item.updated_at,
                )
            )

    if not incremental:
        vanished = [item for key, item in previous.items() if key not in current]

    return DiffResult(events=events, vanished=vanished)


def vanished_event(
    item: TrackedItem,
    *,
    detected_at: str,
    still_exists: bool,
    current_status: str | None = None,
) -> ChangeEvent | None:
    """The event for an item that left the scope, once we know which it was.

    `still_exists` comes from a targeted lookup by the caller: absent means the
    tracker returned 404 and the item is genuinely gone; present means it is alive
    and merely no longer matches the configured query.

    A left-scope item stays in the snapshot under its refreshed status (see
    `runner._resolve_vanished`), so it vanishes from the *scoped* fetch again on
    every subsequent full reconcile even though nothing new has happened. Without
    this check that would re-report the same `LEFT_SCOPE` forever — once an item's
    last known status already matches what the lookup just returned, there is
    nothing to say, so this reports `None` rather than manufacture a repeat event.
    """
    if still_exists:
        if current_status == item.status:
            return None
        return _event(
            "LEFT_SCOPE",
            item,
            detected_at,
            field_name="status",
            before=item.status,
            after=current_status,
            reason="no longer matches the configured scope",
        )
    return _event("DELETED", item, detected_at, last_known_status=item.status)


def diff_sprints(
    previous: Mapping[str, dict],
    current: Mapping[str, dict],
    *,
    system: str,
    source: str,
    detected_at: str,
) -> list[ChangeEvent]:
    """Compare sprint objects by id and report lifecycle transitions.

    A sprint's `state` is the whole story: future -> active is a start, active ->
    closed is a completion. Both are things the project wants to know about the
    moment they happen, and neither shows up as a change on any individual issue.
    """
    events: list[ChangeEvent] = []

    def sprint_event(
        kind: str,
        sprint_id: str,
        data: Mapping,
        *,
        before: object = None,
        after: object = None,
        field_name: str | None = None,
    ) -> ChangeEvent:
        return ChangeEvent(
            kind=kind,
            system=system,
            source=source,
            key=f"sprint:{sprint_id}",
            title=str(data.get("name") or ""),
            bucket="sprints",
            field_name=field_name,
            before=before,
            after=after,
            item_updated_at=str(data.get("endDate") or data.get("startDate") or "") or None,
            detected_at=detected_at,
            details={
                "sprint_id": sprint_id,
                "state": data.get("state"),
                "board_id": data.get("originBoardId"),
                "goal": data.get("goal"),
            },
        )

    for sprint_id, data in current.items():
        was = previous.get(sprint_id)
        if was is None:
            events.append(sprint_event("SPRINT_CREATED", sprint_id, data))
            continue
        old_state = (was.get("state") or "").lower()
        new_state = (data.get("state") or "").lower()
        if old_state != new_state:
            kind = {
                ("future", "active"): "SPRINT_STARTED",
                ("active", "closed"): "SPRINT_COMPLETED",
            }.get((old_state, new_state), "SPRINT_STATE_CHANGED")
            events.append(
                sprint_event(
                    kind, sprint_id, data, field_name="state", before=old_state, after=new_state
                )
            )
        elif was.get("name") != data.get("name"):
            events.append(
                sprint_event(
                    "SPRINT_RENAMED",
                    sprint_id,
                    data,
                    field_name="name",
                    before=was.get("name"),
                    after=data.get("name"),
                )
            )

    for sprint_id, data in previous.items():
        if sprint_id not in current:
            events.append(sprint_event("SPRINT_DELETED", sprint_id, data))

    return events
