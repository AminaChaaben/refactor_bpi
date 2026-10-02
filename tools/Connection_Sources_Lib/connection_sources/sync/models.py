"""The two shapes change detection is built on: a tracked item, and a change to one.

`AlmRecord` is what the clients return and it is deliberately minimal — the fields
every system genuinely shares. Change detection needs more than that: when an item
last changed, what it hangs off, which sprint it sits in, and a hash to compare
against. Rather than widen the frozen `AlmRecord` that the CLI and the skills also
depend on, `TrackedItem` wraps it and adds exactly what diffing needs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

__all__ = ["DETAIL_EVENT_KINDS", "TRACKED_FIELDS", "ChangeEvent", "TrackedItem"]

# The fields whose change is worth an event of its own, mapped to the event kind
# it raises. Anything not named here still moves the content hash — it just does
# not get its own named event.
TRACKED_FIELDS: tuple[tuple[str, str], ...] = (
    ("title", "TITLE_CHANGED"),
    ("status", "STATUS_CHANGED"),
    ("assignee", "ASSIGNEE_CHANGED"),
    ("type", "TYPE_CHANGED"),
    ("sprint", "SPRINT_CHANGED"),
    ("parent", "PARENT_CHANGED"),
    ("tags", "TAGS_CHANGED"),
    ("relations", "RELATIONS_CHANGED"),
)

# Detail labels important enough to raise a named event rather than the generic
# one. A consumer routing on `kind` can then act on a test changing shape without
# having to also read `field_name`; every other declared detail still reports, as
# TEST_DETAIL_CHANGED carrying its label.
DETAIL_EVENT_KINDS: dict[str, str] = {
    "test_type": "TEST_TYPE_CHANGED",
    "test_steps": "TEST_STEPS_CHANGED",
    "manual_steps": "TEST_STEPS_CHANGED",
    "cucumber_script": "TEST_STEPS_CHANGED",
    "precondition": "PRECONDITION_CHANGED",
    "expected_result": "EXPECTED_RESULT_CHANGED",
}
DEFAULT_DETAIL_EVENT_KIND = "TEST_DETAIL_CHANGED"


@dataclass(frozen=True, slots=True)
class TrackedItem:
    """One tracked item, in the shape change detection compares."""

    system: str
    source: str
    key: str
    id: str
    title: str
    status: str
    type: str
    bucket: str
    url: str = ""
    assignee: str | None = None
    tags: tuple[str, ...] = ()
    parent: str | None = None
    sprint: str | None = None
    updated_at: str | None = None
    # Test-shape fields (type, steps, preconditions, …) as (label, value) pairs
    # rather than a dict, so the item stays frozen, hashable and order-stable.
    # Which fields these are is declared per project, never hardcoded, because the
    # ids differ per instance exactly as the sprint field's does.
    details: tuple[tuple[str, str], ...] = ()
    # Named relationships, one field per kind (covers, covered_by, preconditions,
    # test_plan, test_sets, executions, relates_to, blocks, bugs, parent, …). Each
    # entry carries the other issue's key, id, title and url so a reader can
    # navigate straight from state.json without re-querying the tracker.
    relations: dict[str, list[dict[str, str]]] = field(default_factory=dict)

    @property
    def detail_map(self) -> dict[str, str]:
        return dict(self.details)

    @property
    def sorted_relations(self) -> dict[str, list[dict[str, str]]]:
        """The relation block, deterministic: fields sorted, entries sorted by key.

        A tracker is free to return the same links in a different order between two
        reads, and the hash must not call that a change on an item nobody touched.
        """
        return {
            field_name: sorted(entries, key=lambda entry: entry.get("key", ""))
            for field_name, entries in sorted(self.relations.items())
            if entries
        }

    @property
    def content_hash(self) -> str:
        """A digest over the compared fields only.

        Deliberately not over `raw`: a tracker rewrites volatile parts of its own
        payload (render URLs, avatar links, expand tokens) without anything the
        project cares about having changed, and hashing those would report a change
        on every single cycle.

        `details` is sorted in rather than appended: a tracker is free to return the
        same custom fields in a different order between two reads, and an unsorted
        hash would call that a change on a test nobody touched.
        """
        payload = json.dumps(
            [
                self.title,
                self.status,
                self.type,
                self.assignee,
                sorted(self.tags),
                self.parent,
                self.sprint,
                sorted(self.details),
                self.sorted_relations,
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "source": self.source,
            "key": self.key,
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "type": self.type,
            "bucket": self.bucket,
            "url": self.url,
            "assignee": self.assignee,
            "tags": list(self.tags),
            "parent": self.parent,
            "sprint": self.sprint,
            "updated_at": self.updated_at,
            "details": dict(self.details),
            "relations": {k: list(v) for k, v in self.sorted_relations.items()},
            "content_hash": self.content_hash,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "TrackedItem":
        raw_relations = data.get("relations")
        relations: dict[str, list[dict[str, str]]] = {}
        if isinstance(raw_relations, dict):
            for field_name, entries in raw_relations.items():
                if isinstance(entries, list):
                    relations[field_name] = [
                        {str(k): str(v) for k, v in entry.items()}
                        for entry in entries
                        if isinstance(entry, dict)
                    ]
        return TrackedItem(
            system=data.get("system", ""),
            source=data.get("source", ""),
            key=data.get("key", ""),
            id=data.get("id", ""),
            title=data.get("title", ""),
            status=data.get("status", ""),
            type=data.get("type", ""),
            bucket=data.get("bucket", "other"),
            url=data.get("url", ""),
            assignee=data.get("assignee"),
            tags=tuple(data.get("tags") or ()),
            parent=data.get("parent"),
            sprint=data.get("sprint"),
            updated_at=data.get("updated_at"),
            details=tuple(sorted((data.get("details") or {}).items())),
            relations=relations,
        )


@dataclass(frozen=True, slots=True)
class ChangeEvent:
    """One observed change, and enough context to act on it without a second lookup."""

    kind: str
    system: str
    source: str
    key: str
    title: str = ""
    bucket: str = "other"
    field_name: str | None = None
    before: Any = None
    after: Any = None
    url: str = ""
    item_updated_at: str | None = None
    detected_at: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        """One prose sentence for this change -- the same information `to_dict()`
        carries structurally, read the way a person tailing a log actually wants
        it: what item, what happened, and (when there is one) the before/after.

        Deliberately not exhaustive of every `kind` this project can raise (new
        buckets/detail labels are added in `taxonomy`/`models` without this
        needing to change) -- unnamed kinds fall through to a generic sentence
        built from `field_name`/`before`/`after` when present, or just the kind
        and item otherwise, so a new event type is always readable, just not
        specially worded, until someone adds a case for it below.
        """
        who = f"{self.key} {self.title!r}" if self.title else self.key

        if self.kind == "CREATED":
            return f"{who} ({self.bucket}): new item"
        if self.kind == "DELETED":
            return f"{who}: deleted (last known status {self.before!r})"
        if self.kind == "LEFT_SCOPE":
            reason = self.details.get("reason") if self.details else None
            tail = f" -- {reason}" if reason else ""
            return f"{who}: left the configured scope (was {self.before!r}){tail}"
        if self.field_name:
            return f"{who}: {self.field_name} changed from {self.before!r} to {self.after!r}"
        return f"{who}: {self.kind}"

    @property
    def dedupe_key(self) -> str:
        """Identity of the change itself, independent of when it was noticed.

        `detected_at` is excluded on purpose: the same real-world change seen on two
        cycles must produce the same key, or a re-run would queue the orchestrator a
        second copy of work it has already been given.
        """
        payload = json.dumps(
            [
                self.system,
                self.source,
                self.key,
                self.kind,
                self.field_name,
                self.before,
                self.after,
                self.item_updated_at,
            ],
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind,
            "system": self.system,
            "source": self.source,
            "key": self.key,
            "title": self.title,
            "bucket": self.bucket,
            "url": self.url,
            "detected_at": self.detected_at,
            "item_updated_at": self.item_updated_at,
            "dedupe_key": self.dedupe_key,
        }
        if self.field_name is not None:
            out["field"] = self.field_name
            out["before"] = self.before
            out["after"] = self.after
        if self.details:
            out["details"] = self.details
        return out

    def summary(self) -> str:
        if self.field_name:
            return (
                f"{self.kind:<18} {self.key:<14} {self.field_name}: "
                f"{self.before!r} -> {self.after!r}"
            )
        return f"{self.kind:<18} {self.key:<14} {self.title}"
