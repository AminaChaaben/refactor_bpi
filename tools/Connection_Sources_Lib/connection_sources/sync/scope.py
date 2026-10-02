"""Decide, for an item that vanished from a cycle's read, whether it left the
configured scope for good.

The cycle already distinguishes "deleted at the tracker" from "still exists but
was not in the result set" (`runner._resolve_vanished`). What it could not tell
is *why* something still real is absent, so it kept it forever -- which means a
narrowed scope leaves the previous scope's items in `snapshots/` permanently,
re-fetched one by one on every cycle and re-persisted from `merged`, a loop that
never converges on its own.

This module supplies the missing judgement, and the shape of the answer is
different per backend because the identifiers are:

*Azure and GitLab scope to exactly one project by construction* -- `azure.project`,
`gitlab.project_id`. There is no expression that could span two, so comparing the
item's own project against the configured one is authoritative on its own. Their
keys are bare integers (`"1234"`, `"#42"` -- work item ids are unique per
*organization*, not per project), so the project is recovered from the stored
`url`, which embeds it, rather than from the key.

*Jira scope is arbitrary JQL* -- `project IN (A, B)`, a label, a saved filter.
A key prefix (`DJ20-123`) does encode a project, but the configured scope may
legitimately include several, so here the prefix is only ever a *selector*: it
narrows 1000 keys to the ones worth asking about, and the tracker itself settles
membership via `(<configured jql>) AND key IN (...)`. That is the only check that
cannot disagree with the fetch, since it is the same expression the fetch used.

Nothing here deletes. Callers get a partition and decide.
"""

from __future__ import annotations

import logging
from typing import Iterable, Mapping, Sequence

from ..models import SourceConfig
from .models import TrackedItem

__all__ = [
    "configured_project",
    "item_project",
    "partition_by_scope",
    "scope_is_single_project",
]

log = logging.getLogger("connection_sources.sync.scope")

# Jira's own cap on a single `key IN (...)` clause is well above this; the limit
# that matters is URL/JQL length, so keys go up in batches rather than one query.
_CONFIRM_BATCH = 100


def configured_project(source: SourceConfig) -> str | None:
    """The single project this source is scoped to, as configured.

    `None` when the backend has no single-project notion to compare against --
    for Jira that is the normal case, since its scope is an expression.
    """
    scope = source.scope or {}
    if source.server == "azuredevops":
        value = scope.get("project")
    elif source.server == "gitlab":
        value = scope.get("project_id")
    elif source.server == "jira":
        value = scope.get("project_key")
    else:
        return None
    value = str(value or "").strip()
    return value or None


def item_project(item: TrackedItem) -> str | None:
    """The project this item belongs to, recovered from what a snapshot stores.

    `None` means "cannot tell from this record" -- an Azure item whose
    `System.TeamProject` was empty stores `url == ""` (see
    `clients/azure.py:_record`), and a caller must treat that as unknown rather
    than as a mismatch.
    """
    if item.system == "jira":
        # DJ20-123 -> DJ20. Jira keys are `<PROJECT>-<number>`.
        key = (item.key or "").strip()
        prefix, _, number = key.rpartition("-")
        return prefix or None if number.isdigit() else None

    url = (item.url or "").strip()
    if not url:
        return None

    if item.system == "azuredevops":
        # {base_url}/{project}/_workitems/edit/{id}
        head, sep, _ = url.partition("/_workitems/")
        if not sep:
            return None
        return head.rsplit("/", 1)[-1] or None

    if item.system == "gitlab":
        # https://host/group/repo/-/issues/42
        head, sep, _ = url.partition("/-/")
        if not sep:
            return None
        path = head.split("://", 1)[-1]
        segments = [s for s in path.split("/")[1:] if s]  # drop host
        return "/".join(segments) or None

    return None


def scope_is_single_project(source: SourceConfig) -> bool:
    """Whether this backend's scope can only ever name one project.

    True for Azure and GitLab, whose scope *is* a project. False for Jira, whose
    JQL may span any number of them -- so a project mismatch there is a
    candidate, never a verdict.
    """
    return source.server in {"azuredevops", "gitlab"}


def partition_by_scope(
    vanished: Iterable[TrackedItem], source: SourceConfig
) -> tuple[list[TrackedItem], list[TrackedItem]]:
    """Split vanished items into (out-of-scope candidates, everything else).

    An item whose project cannot be determined is never a candidate: an
    unreadable record is a reason to keep asking, not a reason to drop.
    """
    configured = configured_project(source)
    candidates: list[TrackedItem] = []
    rest: list[TrackedItem] = []
    for item in vanished:
        project = item_project(item)
        if configured and project and project != configured:
            candidates.append(item)
        else:
            rest.append(item)
    return candidates, rest


def confirm_jira_out_of_scope(
    candidates: Sequence[TrackedItem],
    *,
    jql: str,
    open_search,
) -> tuple[list[TrackedItem], list[TrackedItem]]:
    """Ask Jira which candidates its own configured scope still returns.

    `open_search` takes a JQL string and returns the keys it matched. Anything
    the scope still returns was absent from the main fetch for some other reason
    (truncation, paging) and is handed back as "keep" -- this is what stops a
    broad JQL from being pruned by a narrow key prefix.
    """
    if not candidates or not jql:
        return [], list(candidates)

    still_in_scope: set[str] = set()
    for start in range(0, len(candidates), _CONFIRM_BATCH):
        batch = candidates[start : start + _CONFIRM_BATCH]
        keys = ", ".join(item.key for item in batch if item.key)
        if not keys:
            continue
        matched = open_search(f"({jql}) AND key IN ({keys})")
        still_in_scope.update(matched)

    out_of_scope = [item for item in candidates if item.key not in still_in_scope]
    keep = [item for item in candidates if item.key in still_in_scope]
    return out_of_scope, keep
