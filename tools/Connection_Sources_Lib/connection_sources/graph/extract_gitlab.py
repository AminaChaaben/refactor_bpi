"""GitLab as delivery context: what shipped the work, never what the work is made of.

This module deliberately models almost nothing about GitLab. It does not read files,
commits, diffs or code owners, and it never will — a graph that contains the source
code is a search index, and the question this graph exists to answer is which
requirement is covered, tested and delivered. What GitLab can say about that is
narrow and valuable: this repository, these pipelines, these merge requests, these
milestones, and — the connective tissue — which tracker issues they name.

That last part is the whole reason this module exists. A merge request titled
`DEM-42: retry the payment webhook`, or a branch called `feature/DEM-42`, is the only
record anywhere that ties a Jira story to the pipeline that deployed it. Nothing in
Jira knows about it and nothing in GitLab knows what `DEM-42` means. Recovering that
link is what turns two disconnected systems into one traceable picture, and it is
done by pattern rather than by integration because the pattern is what teams actually
write, whether or not the Jira/GitLab integration was ever installed.

The link is emitted as `MENTIONS` — deliberately weaker than `IMPLEMENTS`. A key in a
title is evidence, not a guarantee, and labelling a guess as certainty is how a
traceability graph starts lying.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from .model import GraphBatch, GraphEdge, GraphNode, uid

__all__ = ["extract_gitlab", "issue_keys_in"]

SYSTEM = "gitlab"

# `ABC-123`, the shape every Jira key takes. Bounded on both sides so that a version
# string (`v2-1`), a UUID fragment or a word inside a longer token cannot match, and
# the project part is required to be at least two characters because a single letter
# followed by a number is far more often a coordinate than a key.
_ISSUE_KEY = re.compile(r"(?<![A-Za-z0-9_-])([A-Z][A-Z0-9]{1,9}-\d{1,7})(?![A-Za-z0-9_-])")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        for key in ("name", "title", "value"):
            found = value.get(key)
            if isinstance(found, str) and found.strip():
                return found.strip()
        return ""
    return str(value).strip()


def issue_keys_in(*texts: Any) -> list[str]:
    """Every tracker issue key mentioned across these strings, in order, deduplicated.

    Case-sensitive on purpose: Jira keys are upper-case, and matching case-insensitively
    turns ordinary prose — "the api-2 endpoint" — into a false reference to a project
    called API. A false edge in a traceability graph is worse than a missing one,
    because a missing one looks like a gap and a false one looks like an answer.
    """
    found: list[str] = []
    for text in texts:
        if not text:
            continue
        for match in _ISSUE_KEY.findall(str(text)):
            if match not in found:
                found.append(match)
    return found


def _user(site: str, person: Any) -> GraphNode | None:
    if not isinstance(person, Mapping):
        return None
    username = _text(person.get("username"))
    ident = _text(person.get("id"))
    if not username and not ident:
        return None
    return GraphNode(
        uid=uid(SYSTEM, site, "user", username or ident),
        labels=("User",),
        props={
            "account_id": ident,
            "username": username,
            "display_name": _text(person.get("name")),
            "identified_by": "username" if username else "account_id",
            "stub": False,
        },
    )


def _mentions(
    batch: GraphBatch,
    owner_uid: str,
    tracker_system: str,
    tracker_site: str,
    keys: Iterable[str],
    *,
    found_in: str,
) -> None:
    """Link this delivery artefact to each tracker issue it names.

    The far end is emitted as a stub in the *tracker's* namespace, not GitLab's, so
    that it merges with the real issue when the Jira or Azure pass loads it. That is
    the entire mechanism by which the two halves of the graph join: nothing here
    knows whether `DEM-42` exists, and if it does not, the stub stands as an honest
    record that something referred to it.
    """
    for key in keys:
        target = uid(tracker_system, tracker_site, "issue", key)
        batch.add_node(
            GraphNode(uid=target, labels=("Issue",), props={"key": key, "stub": True})
        )
        batch.add_edge(
            GraphEdge("MENTIONS", owner_uid, target, props={"found_in": found_in})
        )


def extract_gitlab(
    *,
    site: str,
    source: str,
    tracker_system: str = "jira",
    tracker_site: str = "",
    project: Mapping[str, Any] | None = None,
    pipelines: Iterable[Mapping[str, Any]] = (),
    merge_requests: Iterable[Mapping[str, Any]] = (),
    milestones: Iterable[Mapping[str, Any]] = (),
    issues: Iterable[Any] = (),
) -> GraphBatch:
    """One GitLab project's delivery context, as one batch.

    `tracker_site` is the site whose namespace mentioned issue keys resolve into.
    Passing it explicitly rather than deriving it keeps this module from having to
    know anything about Jira: the caller already read the tracker and knows which
    site those keys belong to, and a wrong guess here would scatter stubs into a
    namespace nothing ever merges with.
    """
    batch = GraphBatch()
    tracker_site = tracker_site or site
    project = project if isinstance(project, Mapping) else {}

    repo_id = _text(project.get("path_with_namespace")) or _text(project.get("id"))
    repo_uid = uid(SYSTEM, site, "repository", repo_id) if repo_id else ""
    if repo_uid:
        batch.add_node(
            GraphNode(
                uid=repo_uid,
                labels=("GitRepository",),
                props={
                    "key": repo_id,
                    "project_id": _text(project.get("id")),
                    "name": _text(project.get("name")),
                    "path": _text(project.get("path_with_namespace")),
                    "url": _text(project.get("web_url")),
                    "default_branch": _text(project.get("default_branch")),
                    "visibility": _text(project.get("visibility")),
                    "last_activity_at": _text(project.get("last_activity_at")),
                    "system": SYSTEM,
                    "source": source,
                    "stub": False,
                },
            )
        )

    for milestone in milestones:
        if not isinstance(milestone, Mapping) or milestone.get("id") in (None, ""):
            continue
        milestone_uid = uid(SYSTEM, site, "milestone", milestone.get("id"))
        batch.add_node(
            GraphNode(
                uid=milestone_uid,
                labels=("Milestone",),
                props={
                    "milestone_id": _text(milestone.get("id")),
                    "iid": _text(milestone.get("iid")),
                    "title": _text(milestone.get("title")),
                    "description": _text(milestone.get("description")),
                    "state": _text(milestone.get("state")),
                    "due_on": _text(milestone.get("due_date")),
                    "starts_on": _text(milestone.get("start_date")),
                    "url": _text(milestone.get("web_url")),
                    "stub": False,
                },
            )
        )
        if repo_uid:
            batch.add_edge(GraphEdge("IN_MILESTONE", milestone_uid, repo_uid))

    for pipeline in pipelines:
        if not isinstance(pipeline, Mapping) or pipeline.get("id") in (None, ""):
            continue
        pipeline_uid = uid(SYSTEM, site, "pipeline", pipeline.get("id"))
        ref = _text(pipeline.get("ref"))
        batch.add_node(
            GraphNode(
                uid=pipeline_uid,
                labels=("Pipeline",),
                props={
                    "pipeline_id": _text(pipeline.get("id")),
                    "status": _text(pipeline.get("status")).upper(),
                    "status_name": _text(pipeline.get("status")),
                    "ref": ref,
                    "sha": _text(pipeline.get("sha")),
                    "source_event": _text(pipeline.get("source")),
                    "url": _text(pipeline.get("web_url")),
                    "created": _text(pipeline.get("created_at")),
                    "updated": _text(pipeline.get("updated_at")),
                    "stub": False,
                },
            )
        )
        if repo_uid:
            batch.add_edge(GraphEdge("HAS_PIPELINE", repo_uid, pipeline_uid))
        author = _user(site, pipeline.get("user"))
        if author is not None:
            batch.add_node(author)
            batch.add_edge(GraphEdge("TRIGGERED_BY", pipeline_uid, author.uid))
        # The branch name is where a pipeline carries its issue key: a pipeline has no
        # title and its commit message is not in this payload, so `ref` is the only
        # field that can name the work it was running.
        _mentions(
            batch, pipeline_uid, tracker_system, tracker_site,
            issue_keys_in(ref), found_in="ref",
        )

    for request in merge_requests:
        if not isinstance(request, Mapping) or request.get("id") in (None, ""):
            continue
        mr_uid = uid(SYSTEM, site, "mergerequest", request.get("id"))
        title = _text(request.get("title"))
        branch = _text(request.get("source_branch"))
        batch.add_node(
            GraphNode(
                uid=mr_uid,
                labels=("MergeRequest",),
                props={
                    "merge_request_id": _text(request.get("id")),
                    "iid": _text(request.get("iid")),
                    "title": title,
                    "state": _text(request.get("state")),
                    "source_branch": branch,
                    "target_branch": _text(request.get("target_branch")),
                    "draft": bool(request.get("draft") or request.get("work_in_progress")),
                    "url": _text(request.get("web_url")),
                    "created": _text(request.get("created_at")),
                    "updated": _text(request.get("updated_at")),
                    "merged_at": _text(request.get("merged_at")),
                    "stub": False,
                },
            )
        )
        if repo_uid:
            batch.add_edge(GraphEdge("HAS_MERGE_REQUEST", repo_uid, mr_uid))
        for person, relationship in (
            (request.get("author"), "AUTHORED"),
            (request.get("assignee"), "ASSIGNED_TO"),
            (request.get("merged_by"), "MADE"),
        ):
            node = _user(site, person)
            if node is None:
                continue
            batch.add_node(node)
            start, end = (
                (node.uid, mr_uid) if relationship == "AUTHORED" else (mr_uid, node.uid)
            )
            batch.add_edge(GraphEdge(relationship, start, end))

        milestone = request.get("milestone")
        if isinstance(milestone, Mapping) and milestone.get("id"):
            batch.add_edge(
                GraphEdge(
                    "IN_MILESTONE", mr_uid, uid(SYSTEM, site, "milestone", milestone["id"])
                )
            )
        _mentions(
            batch, mr_uid, tracker_system, tracker_site,
            issue_keys_in(title, branch, request.get("description")),
            found_in="title",
        )

    for issue in issues:
        record = issue if isinstance(issue, Mapping) else _record_as_mapping(issue)
        if not record or not record.get("key"):
            continue
        issue_uid = uid(SYSTEM, site, "issue", record["key"])
        batch.add_node(
            GraphNode(
                uid=issue_uid,
                labels=("Issue",),
                props={
                    "key": _text(record.get("key")),
                    "title": _text(record.get("title")),
                    "status": _text(record.get("status")),
                    "url": _text(record.get("url")),
                    "system": SYSTEM,
                    "source": source,
                    "stub": False,
                },
            )
        )
        if repo_uid:
            batch.add_edge(GraphEdge("IN_PROJECT", issue_uid, repo_uid))
        _mentions(
            batch, issue_uid, tracker_system, tracker_site,
            issue_keys_in(record.get("title")),
            found_in="title",
        )

    return batch


def _record_as_mapping(record: Any) -> dict[str, Any]:
    """An `AlmRecord` reduced to the handful of fields this module reads."""
    key = getattr(record, "key", "") or ""
    if not key:
        return {}
    return {
        "key": key,
        "title": getattr(record, "title", "") or "",
        "status": getattr(record, "status", "") or "",
        "url": getattr(record, "url", "") or "",
    }
