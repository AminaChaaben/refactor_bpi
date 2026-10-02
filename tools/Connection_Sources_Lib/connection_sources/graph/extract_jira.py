"""Every node and edge one Jira payload contains.

Pure: it takes dictionaries and returns a `GraphBatch`. Nothing here opens a socket,
which is what lets the whole relationship vocabulary be tested against saved payloads
instead of against a live site that happens to have two issue links in it.

The shape of the output is the point. A Jira issue is not one thing — it is an issue,
its type, its status, the category that status rolls up to, its priority, its
resolution, the people in its four different people-fields, its labels, its
components, two different kinds of version, its whole sprint history, its links, its
comments, its attachments, its worklogs, its remote links, and its changelog. Each of
those is a node somebody will want to start a query from, so each is a node.

Two conventions run through all of it:

*Identity is always the tracker's id.* Statuses, priorities and sprints all get
renamed; keying on the display name splits one node into two the day somebody edits
a field. Only labels are keyed on their text, because in Jira that text is all a
label is.

*A referenced issue still becomes a node.* An issue link, a parent, or a subtask can
name an issue outside the configured scope, which was never fetched. Dropping the
edge would make the graph quietly wrong in the one place traceability matters, so the
far end is emitted as a stub — see `GraphNode.is_stub` — and filled in for real if a
later pass reads it.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..models import AlmRecord
from ..sync.normalize import detail_text, manual_steps_text
from ..sync.taxonomy import bucket_for
from .model import GraphBatch, GraphEdge, GraphNode, uid
from .ontology import Ontology

__all__ = [
    "extract_changelog",
    "extract_issue",
    "extract_issues",
    "extract_remote_links",
    "extract_sprints",
    "extract_watchers",
]

SYSTEM = "jira"

# Sprint values on older sites arrive as a Java toString() blob rather than an
# object: `com.atlassian...Sprint@1f[id=2,rapidViewId=1,state=CLOSED,name=Sprint 2,…]`.
# Both shapes still occur, and the id is the part that matters, so both are parsed
# rather than one being treated as the only real one.
_LEGACY_SPRINT_PREFIX = "["


def _text(value: Any) -> str:
    """Flatten a Jira value to a stable string, ADF included.

    Reuses `sync.normalize.detail_text` rather than reimplementing the flattening:
    it is the module that already knows an Atlassian Document Format tree carries
    ids and marks that churn on their own, and having two flatteners would let a
    description read one way in the graph and another way in the change history.
    """
    return detail_text(value)


def _first_id(value: Mapping[str, Any] | None, *keys: str) -> str:
    """The first non-empty key on a Jira sub-object, as a string."""
    if not isinstance(value, Mapping):
        return ""
    for key in keys:
        found = value.get(key)
        if found not in (None, "", []):
            return str(found)
    return ""


# -- vocabulary nodes ------------------------------------------------------


def _user_node(site: str, user: Mapping[str, Any] | None) -> GraphNode | None:
    """One Jira account.

    Merged on `accountId`, falling back to the display name only when Jira gave no
    account id at all — which happens for deleted accounts and for some app users.
    The fallback is marked so a query can tell a real identity from a reconstructed
    one, because two different people really can share a display name.
    """
    if not isinstance(user, Mapping):
        return None
    account = str(user.get("accountId") or "").strip()
    name = str(user.get("displayName") or "").strip()
    if not account and not name:
        return None
    props: dict[str, Any] = {
        "account_id": account,
        "display_name": name,
        "email": user.get("emailAddress"),
        "active": user.get("active"),
        "account_type": user.get("accountType"),
        "time_zone": user.get("timeZone"),
        "identified_by": "account_id" if account else "display_name",
        "stub": False,
    }
    return GraphNode(
        uid=uid(SYSTEM, site, "user", account or f"name:{name}"),
        labels=("User",),
        props=props,
    )


def _status_nodes(site: str, status: Mapping[str, Any] | None) -> tuple[GraphNode | None, GraphNode | None]:
    """A status and the category it rolls up to.

    The category is a separate node because it is the only part of a status that is
    comparable across projects: every site invents its own workflow names, and
    "everything not yet Done" is a question about the category, not the name.
    """
    if not isinstance(status, Mapping):
        return None, None
    status_id = _first_id(status, "id")
    if not status_id:
        return None, None
    node = GraphNode(
        uid=uid(SYSTEM, site, "status", status_id),
        labels=("Status",),
        props={
            "status_id": status_id,
            "name": status.get("name"),
            "description": status.get("description"),
            "stub": False,
        },
    )
    category = status.get("statusCategory")
    cat_node = None
    if isinstance(category, Mapping) and _first_id(category, "id"):
        cat_node = GraphNode(
            uid=uid(SYSTEM, site, "statuscategory", _first_id(category, "id")),
            labels=("StatusCategory",),
            props={
                "category_id": _first_id(category, "id"),
                "key": category.get("key"),
                "name": category.get("name"),
                "color": category.get("colorName"),
                "stub": False,
            },
        )
    return node, cat_node


def _simple_node(
    site: str, kind: str, label: str, value: Mapping[str, Any] | None, **extra: Any
) -> GraphNode | None:
    """A vocabulary node keyed on its Jira id: type, priority, resolution, component."""
    if not isinstance(value, Mapping):
        return None
    ident = _first_id(value, "id")
    if not ident:
        return None
    return GraphNode(
        uid=uid(SYSTEM, site, kind, ident),
        labels=(label,),
        props={
            f"{kind}_id": ident,
            "name": value.get("name"),
            "description": value.get("description"),
            "stub": False,
            **extra,
        },
    )


def _issue_stub(site: str, issue: Mapping[str, Any] | None) -> GraphNode | None:
    """A node for an issue named by a link, parent or subtask but never fetched."""
    if not isinstance(issue, Mapping):
        return None
    key = str(issue.get("key") or "").strip()
    if not key:
        return None
    fields = issue.get("fields") if isinstance(issue.get("fields"), Mapping) else {}
    type_name = str((fields.get("issuetype") or {}).get("name") or "")
    return GraphNode(
        uid=uid(SYSTEM, site, "issue", key),
        labels=("Issue",),
        props={
            "key": key,
            "issue_id": str(issue.get("id") or ""),
            "title": fields.get("summary"),
            "status": (fields.get("status") or {}).get("name"),
            "type": type_name or None,
            "stub": True,
        },
    )


def _parse_sprint(value: Any) -> dict[str, Any] | None:
    """One sprint entry, from either the object form or the legacy toString() blob."""
    if isinstance(value, Mapping):
        if not value.get("id"):
            return None
        return dict(value)
    if isinstance(value, str) and _LEGACY_SPRINT_PREFIX in value:
        inner = value[value.index(_LEGACY_SPRINT_PREFIX) + 1 :].rstrip()
        if inner.endswith("]"):
            inner = inner[:-1]
        parsed: dict[str, Any] = {}
        for chunk in inner.split(","):
            if "=" not in chunk:
                continue
            name, _, raw = chunk.partition("=")
            text = raw.strip()
            parsed[name.strip()] = None if text in ("", "<null>") else text
        if not parsed.get("id"):
            return None
        if parsed.get("rapidViewId"):
            parsed.setdefault("boardId", parsed["rapidViewId"])
        return parsed
    return None


def _sprint_node(site: str, sprint: Mapping[str, Any]) -> GraphNode:
    return GraphNode(
        uid=uid(SYSTEM, site, "sprint", sprint.get("id")),
        labels=("Sprint",),
        props={
            "sprint_id": str(sprint.get("id")),
            "name": sprint.get("name"),
            "state": str(sprint.get("state") or "").lower() or None,
            "goal": sprint.get("goal"),
            "start_date": sprint.get("startDate"),
            "end_date": sprint.get("endDate"),
            "complete_date": sprint.get("completeDate"),
            "board_id": str(sprint.get("boardId") or sprint.get("originBoardId") or "") or None,
            "stub": False,
        },
    )


# -- the issue itself ------------------------------------------------------


def _issue_props(
    issue: Mapping[str, Any],
    fields: Mapping[str, Any],
    *,
    source: str,
    buckets: Mapping[str, str] | None,
) -> dict[str, Any]:
    """The scalars that live on the Issue node itself.

    Denormalised on purpose, even though status, type and priority are also nodes.
    A list view wants `n.status` without three hops, and a "which statuses exist"
    question wants the node; storing both costs a string per issue and saves every
    reader from choosing.
    """
    status = fields.get("status") or {}
    category = status.get("statusCategory") if isinstance(status, Mapping) else {}
    type_name = str((fields.get("issuetype") or {}).get("name") or "")
    votes = fields.get("votes") if isinstance(fields.get("votes"), Mapping) else {}
    watches = fields.get("watches") if isinstance(fields.get("watches"), Mapping) else {}
    tracking = (
        fields.get("timetracking") if isinstance(fields.get("timetracking"), Mapping) else {}
    )
    return {
        "key": str(issue.get("key") or ""),
        "issue_id": str(issue.get("id") or ""),
        "title": fields.get("summary"),
        "description": _text(fields.get("description")) or None,
        "status": status.get("name") if isinstance(status, Mapping) else None,
        "status_category": category.get("name") if isinstance(category, Mapping) else None,
        "type": type_name or None,
        "bucket": bucket_for(type_name, buckets),
        "priority": (fields.get("priority") or {}).get("name"),
        "resolution": (fields.get("resolution") or {}).get("name"),
        "project_key": (fields.get("project") or {}).get("key"),
        "created": fields.get("created"),
        "updated": fields.get("updated"),
        "resolved_at": fields.get("resolutiondate"),
        "due_date": fields.get("duedate"),
        "status_changed_at": fields.get("statuscategorychangedate"),
        "environment": _text(fields.get("environment")) or None,
        "labels": list(fields.get("labels") or ()),
        "vote_count": votes.get("votes"),
        "watch_count": watches.get("watchCount"),
        "original_estimate": tracking.get("originalEstimate"),
        "remaining_estimate": tracking.get("remainingEstimate"),
        "time_spent": tracking.get("timeSpent"),
        "source": source,
        "system": SYSTEM,
        "stub": False,
    }


def _add_people(batch: GraphBatch, site: str, issue_uid: str, fields: Mapping[str, Any]) -> None:
    """The four different ways Jira attaches a person to an issue.

    They are four relationships rather than one because they answer different
    questions: who is doing it, who asked for it, who filed it, and — via comments
    and worklogs elsewhere — who touched it.
    """
    for field_name, rel in (
        ("assignee", "ASSIGNED_TO"),
        ("reporter", "REPORTED_BY"),
        ("creator", "CREATED_BY"),
    ):
        node = _user_node(site, fields.get(field_name))
        if node is None:
            continue
        batch.add_node(node)
        batch.add_edge(GraphEdge(rel, issue_uid, node.uid))


def _add_classification(
    batch: GraphBatch, site: str, issue_uid: str, fields: Mapping[str, Any]
) -> None:
    """Labels, components and the two kinds of version.

    `fixVersions` and `versions` are separate relationships because they mean
    opposite things: the release a bug is *fixed in* versus the release it *affects*.
    Collapsing them into one edge would make every "what shipped broken" query wrong.
    """
    for name in fields.get("labels") or ():
        text = str(name).strip()
        if not text:
            continue
        node = GraphNode(
            uid=uid(SYSTEM, site, "label", text),
            labels=("Label",),
            props={"name": text, "stub": False},
        )
        batch.add_node(node)
        batch.add_edge(GraphEdge("HAS_LABEL", issue_uid, node.uid))

    for component in fields.get("components") or ():
        node = _simple_node(site, "component", "Component", component)
        if node is None:
            continue
        batch.add_node(node)
        batch.add_edge(GraphEdge("HAS_COMPONENT", issue_uid, node.uid))

    for field_name, rel in (("fixVersions", "FIXED_IN"), ("versions", "AFFECTS")):
        for version in fields.get(field_name) or ():
            node = _simple_node(
                site,
                "version",
                "Version",
                version,
                released=(version or {}).get("released"),
                archived=(version or {}).get("archived"),
                release_date=(version or {}).get("releaseDate"),
            )
            if node is None:
                continue
            batch.add_node(node)
            batch.add_edge(GraphEdge(rel, issue_uid, node.uid))


def _add_hierarchy(
    batch: GraphBatch, site: str, issue_uid: str, fields: Mapping[str, Any]
) -> None:
    """Parent and subtasks — the same relationship read from both ends.

    Jira reports the hierarchy twice: a child names its `parent`, and a parent lists
    its `subtasks`. Both are normalised onto one CHILD_OF pointing from child to
    parent, so the pair produces one edge and not two facing each other.
    """
    parent = fields.get("parent")
    parent_stub = _issue_stub(site, parent)
    if parent_stub is not None:
        batch.add_node(parent_stub)
        batch.add_edge(GraphEdge("CHILD_OF", issue_uid, parent_stub.uid))

    for subtask in fields.get("subtasks") or ():
        child = _issue_stub(site, subtask)
        if child is None:
            continue
        batch.add_node(child)
        batch.add_edge(GraphEdge("CHILD_OF", child.uid, issue_uid))


def _add_links(
    batch: GraphBatch,
    site: str,
    issue_uid: str,
    fields: Mapping[str, Any],
    ontology: Ontology,
) -> None:
    """Issue links, normalised onto one direction each.

    Jira returns each link from whichever end you asked, as `outwardIssue` or
    `inwardIssue`, which means reading both ends of a link produces the same fact
    twice pointing opposite ways. Fixing the stored direction to the outward sense —
    "A blocks B", never "B is blocked by A" — makes those two readings collapse onto
    a single edge, and `link_type` stays on the edge so a mapping that folds several
    Jira link types onto one relationship can still be told apart afterwards.

    A COVERS edge is only kept when neither end is a test container. A test linked
    into a set, plan or execution is that container's member, which the Xray passes
    record as CONTAINS, PLANS and HAS_RUN — emitting COVERS as well would make every
    coverage query count sets and plans as requirements.
    """
    self_type = str((fields.get("issuetype") or {}).get("name") or "")

    def is_container(type_name: str) -> bool:
        labels = set(ontology.labels_for_type(type_name))
        return bool(labels & {"TestSet", "TestPlan", "TestExecution", "Precondition"})

    for link in fields.get("issuelinks") or ():
        if not isinstance(link, Mapping):
            continue
        link_type = link.get("type") if isinstance(link.get("type"), Mapping) else {}
        name = str(link_type.get("name") or "")
        rel, reverse = ontology.link_rel(name)

        outward = _issue_stub(site, link.get("outwardIssue"))
        inward = _issue_stub(site, link.get("inwardIssue"))
        if outward is not None:
            start, end = issue_uid, outward.uid
            batch.add_node(outward)
        elif inward is not None:
            start, end = inward.uid, issue_uid
            batch.add_node(inward)
        else:
            continue
        if reverse:
            start, end = end, start
        far = outward if outward is not None else inward
        if rel == "COVERS" and (is_container(self_type) or is_container(str(far.props.get("type") or ""))):
            continue

        batch.add_edge(
            GraphEdge(
                rel,
                start,
                end,
                props={
                    "link_type": name,
                    "link_id": str(link.get("id") or ""),
                    "outward": link_type.get("outward"),
                    "inward": link_type.get("inward"),
                },
                key_props=("link_type",),
            )
        )


def _add_sprints(
    batch: GraphBatch,
    site: str,
    issue_uid: str,
    fields: Mapping[str, Any],
    sprint_field: str | None,
    project_uid: str | None,
) -> None:
    """Every sprint this issue has ever been in, with the current one marked.

    Jira's sprint field is a *history*, not a value: an issue carried over three
    times lists three sprints, oldest first. Keeping all of them is what makes
    "which stories keep slipping" answerable at all, and `current` marks the last
    entry so the ordinary "what is in this sprint" question stays a one-hop filter.
    """
    if not sprint_field:
        return
    entries = fields.get(sprint_field)
    if not entries:
        return
    parsed = [
        sprint
        for sprint in (_parse_sprint(entry) for entry in (entries if isinstance(entries, list) else [entries]))
        if sprint
    ]
    for position, sprint in enumerate(parsed):
        node = _sprint_node(site, sprint)
        batch.add_node(node)
        batch.add_edge(
            GraphEdge(
                "IN_SPRINT",
                issue_uid,
                node.uid,
                props={"current": position == len(parsed) - 1, "position": position},
            )
        )
        board_id = sprint.get("boardId") or sprint.get("originBoardId")
        if board_id:
            board = GraphNode(
                uid=uid(SYSTEM, site, "board", board_id),
                labels=("Board",),
                props={"board_id": str(board_id), "stub": True},
            )
            batch.add_node(board)
            batch.add_edge(GraphEdge("ON_BOARD", node.uid, board.uid))
            if project_uid:
                batch.add_edge(GraphEdge("FOR_PROJECT", board.uid, project_uid))


def _add_comments(batch: GraphBatch, site: str, issue_uid: str, fields: Mapping[str, Any]) -> None:
    block = fields.get("comment")
    comments = block.get("comments") if isinstance(block, Mapping) else block
    for comment in comments or ():
        if not isinstance(comment, Mapping) or not comment.get("id"):
            continue
        node = GraphNode(
            uid=uid(SYSTEM, site, "comment", comment.get("id")),
            labels=("Comment",),
            props={
                "comment_id": str(comment.get("id")),
                "body": _text(comment.get("body")) or None,
                "created": comment.get("created"),
                "updated": comment.get("updated"),
                "public": (comment.get("jsdPublic") if "jsdPublic" in comment else None),
                "stub": False,
            },
        )
        batch.add_node(node)
        batch.add_edge(GraphEdge("HAS_COMMENT", issue_uid, node.uid))
        author = _user_node(site, comment.get("author"))
        if author is not None:
            batch.add_node(author)
            batch.add_edge(GraphEdge("AUTHORED", author.uid, node.uid))


def _add_attachments(
    batch: GraphBatch, site: str, issue_uid: str, fields: Mapping[str, Any]
) -> None:
    for attachment in fields.get("attachment") or ():
        if not isinstance(attachment, Mapping) or not attachment.get("id"):
            continue
        node = GraphNode(
            uid=uid(SYSTEM, site, "attachment", attachment.get("id")),
            labels=("Attachment",),
            props={
                "attachment_id": str(attachment.get("id")),
                "filename": attachment.get("filename"),
                "mime_type": attachment.get("mimeType"),
                "size": attachment.get("size"),
                "created": attachment.get("created"),
                "url": attachment.get("content"),
                "stub": False,
            },
        )
        batch.add_node(node)
        batch.add_edge(GraphEdge("HAS_ATTACHMENT", issue_uid, node.uid))
        author = _user_node(site, attachment.get("author"))
        if author is not None:
            batch.add_node(author)
            batch.add_edge(GraphEdge("UPLOADED", author.uid, node.uid))


def _add_worklogs(batch: GraphBatch, site: str, issue_uid: str, fields: Mapping[str, Any]) -> None:
    block = fields.get("worklog")
    entries = block.get("worklogs") if isinstance(block, Mapping) else block
    for entry in entries or ():
        if not isinstance(entry, Mapping) or not entry.get("id"):
            continue
        node = GraphNode(
            uid=uid(SYSTEM, site, "worklog", entry.get("id")),
            labels=("Worklog",),
            props={
                "worklog_id": str(entry.get("id")),
                "comment": _text(entry.get("comment")) or None,
                "started": entry.get("started"),
                "created": entry.get("created"),
                "updated": entry.get("updated"),
                "time_spent": entry.get("timeSpent"),
                "time_spent_seconds": entry.get("timeSpentSeconds"),
                "stub": False,
            },
        )
        batch.add_node(node)
        batch.add_edge(GraphEdge("HAS_WORKLOG", issue_uid, node.uid))
        author = _user_node(site, entry.get("author"))
        if author is not None:
            batch.add_node(author)
            batch.add_edge(GraphEdge("LOGGED", author.uid, node.uid))


def _add_test_details(
    batch: GraphBatch,
    site: str,
    issue_uid: str,
    issue_key: str,
    fields: Mapping[str, Any],
    detail_fields: Mapping[str, str] | None,
    *,
    steps_from_text: bool = True,
) -> None:
    """The test-shape custom fields a site uses to stand in for Xray.

    A site without Xray installed models a test's steps, preconditions and expected
    result as ordinary custom fields, whose ids differ per instance and so are
    declared in `sync.test_detail_fields` — the same declaration change detection
    already reads, so the two never disagree about which field is which.

    Steps become their own nodes because a step is what a run result attaches to. A
    structured value (Xray's own list of step objects) is used as given; a plain
    textarea is split on lines, which is how people actually write them when the
    field is free text.
    """
    if not detail_fields:
        return
    for label, field_id in detail_fields.items():
        raw_value = fields.get(field_id)
        if raw_value in (None, "", [], {}):
            continue
        text = _text(raw_value)

        if label == "test_steps":
            batch.add_node(
                GraphNode(uid=issue_uid, labels=("Issue",), props={"test_steps": text, "stub": False})
            )
            for index, step in enumerate(_step_values(raw_value, steps_from_text), start=1):
                node = GraphNode(
                    uid=uid(SYSTEM, site, "teststep", f"{issue_key}#{index}"),
                    labels=("TestStep",),
                    props={"index": index, "stub": False, **step},
                )
                batch.add_node(node)
                batch.add_edge(
                    GraphEdge("HAS_STEP", issue_uid, node.uid, props={"index": index})
                )
            continue

        if label == "manual_steps":
            flattened = manual_steps_text(raw_value)
            batch.add_node(
                GraphNode(
                    uid=issue_uid,
                    labels=("Issue",),
                    props={"manual_steps": flattened or None, "stub": False},
                )
            )
            for index, step in enumerate(_manual_step_values(raw_value), start=1):
                node = GraphNode(
                    uid=uid(SYSTEM, site, "teststep", f"{issue_key}#{index}"),
                    labels=("TestStep",),
                    props={"index": index, "stub": False, **step},
                )
                batch.add_node(node)
                batch.add_edge(
                    GraphEdge("HAS_STEP", issue_uid, node.uid, props={"index": index})
                )
            continue

        if label == "cucumber_script":
            steps, is_gherkin = _gherkin_step_values(text)
            props: dict[str, Any] = {"cucumber_script": text, "stub": False}
            if is_gherkin:
                props["gherkin"] = text
            batch.add_node(GraphNode(uid=issue_uid, labels=("Issue",), props=props))
            for index, step in enumerate(steps, start=1):
                node = GraphNode(
                    uid=uid(SYSTEM, site, "teststep", f"{issue_key}#{index}"),
                    labels=("TestStep",),
                    props={"index": index, "stub": False, **step},
                )
                batch.add_node(node)
                batch.add_edge(
                    GraphEdge("HAS_STEP", issue_uid, node.uid, props={"index": index})
                )
            continue

        if label == "precondition":
            node = GraphNode(
                uid=uid(SYSTEM, site, "precondition", issue_key),
                labels=("Precondition",),
                props={"definition": text, "inline": True, "source_issue": issue_key, "stub": False},
            )
            batch.add_node(node)
            batch.add_edge(GraphEdge("REQUIRES", issue_uid, node.uid))
            continue

        if label == "test_type":
            node = GraphNode(
                uid=uid(SYSTEM, site, "testtype", text),
                labels=("TestType",),
                props={"name": text, "stub": False},
            )
            batch.add_node(node)
            batch.add_edge(GraphEdge("HAS_TEST_TYPE", issue_uid, node.uid))

        # Every declared detail also lands on the issue as a plain property, so a
        # field nobody has modelled yet is still queryable rather than lost.
        batch.add_node(
            GraphNode(uid=issue_uid, labels=("Issue",), props={label: text, "stub": False})
        )


def _step_values(raw_value: Any, steps_from_text: bool) -> list[dict[str, Any]]:
    """Test steps out of whichever shape the field holds."""
    if isinstance(raw_value, list) and any(isinstance(entry, Mapping) for entry in raw_value):
        steps = []
        for entry in raw_value:
            if not isinstance(entry, Mapping):
                continue
            steps.append(
                {
                    "action": _text(entry.get("action") or entry.get("step")) or None,
                    "data": _text(entry.get("data")) or None,
                    "expected": _text(entry.get("result") or entry.get("expectedResult")) or None,
                }
            )
        return steps
    if not steps_from_text:
        return []
    text = _text(raw_value)
    return [
        {"action": line.strip(), "data": None, "expected": None}
        for line in text.replace("|", "\n").splitlines()
        if line.strip()
    ]


def _manual_step_values(raw_value: Any) -> list[dict[str, Any]]:
    """Steps out of an Xray Server manual-steps field.

    The field carries `{"steps": [{"fields": {"Action":…, "Data":…,
    "Expected Result":…}}]}`; each entry unwraps onto the same
    `{action, data, expected}` shape the other step sources use.
    """
    steps = raw_value.get("steps") if isinstance(raw_value, Mapping) else None
    if not isinstance(steps, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in steps:
        if not isinstance(entry, Mapping):
            continue
        fields = entry.get("fields") if isinstance(entry.get("fields"), Mapping) else entry
        out.append(
            {
                "action": _text(fields.get("Action")) or None,
                "data": _text(fields.get("Data")) or None,
                "expected": _text(fields.get("Expected Result")) or None,
            }
        )
    return out


def _gherkin_step_values(text: str) -> tuple[list[dict[str, Any]], bool]:
    """Steps out of a Gherkin script field, using the plain tier's line parser."""
    from .plain import gherkin_steps

    return gherkin_steps(text)


# -- entry points ----------------------------------------------------------


def extract_issue(
    issue: Mapping[str, Any],
    *,
    site: str,
    ontology: Ontology,
    source: str = "jira",
    base_url: str = "",
    buckets: Mapping[str, str] | None = None,
    sprint_field: str | None = None,
    detail_fields: Mapping[str, str] | None = None,
    include: Iterable[str] = (),
    steps_from_text: bool = True,
) -> GraphBatch:
    """One raw Jira issue payload as nodes and edges."""
    batch = GraphBatch()
    fields = issue.get("fields") if isinstance(issue.get("fields"), Mapping) else {}
    key = str(issue.get("key") or "").strip()
    if not key:
        return batch
    wanted = set(include)

    issue_uid = uid(SYSTEM, site, "issue", key)
    type_name = str((fields.get("issuetype") or {}).get("name") or "")
    props = _issue_props(issue, fields, source=source, buckets=buckets)
    if base_url:
        props["url"] = f"{base_url.rstrip('/')}/browse/{key}"
    batch.add_node(
        GraphNode(uid=issue_uid, labels=ontology.labels_for_type(type_name), props=props)
    )

    project = fields.get("project")
    project_uid = None
    if isinstance(project, Mapping) and project.get("key"):
        project_node = GraphNode(
            uid=uid(SYSTEM, site, "project", project.get("key")),
            labels=("Project",),
            props={
                "key": str(project.get("key")),
                "project_id": str(project.get("id") or ""),
                "name": project.get("name"),
                "project_type": project.get("projectTypeKey"),
                "stub": False,
            },
        )
        project_uid = project_node.uid
        batch.add_node(project_node)
        batch.add_edge(GraphEdge("IN_PROJECT", issue_uid, project_uid))

    type_node = _simple_node(
        site,
        "issuetype",
        "IssueType",
        fields.get("issuetype"),
        subtask=(fields.get("issuetype") or {}).get("subtask"),
        hierarchy_level=(fields.get("issuetype") or {}).get("hierarchyLevel"),
    )
    if type_node is not None:
        batch.add_node(type_node)
        batch.add_edge(GraphEdge("HAS_TYPE", issue_uid, type_node.uid))

    status_node, category_node = _status_nodes(site, fields.get("status"))
    if status_node is not None:
        batch.add_node(status_node)
        batch.add_edge(GraphEdge("HAS_STATUS", issue_uid, status_node.uid))
        if category_node is not None:
            batch.add_node(category_node)
            batch.add_edge(GraphEdge("IN_CATEGORY", status_node.uid, category_node.uid))

    priority_node = _simple_node(site, "priority", "Priority", fields.get("priority"))
    if priority_node is not None:
        batch.add_node(priority_node)
        batch.add_edge(GraphEdge("HAS_PRIORITY", issue_uid, priority_node.uid))

    resolution_node = _simple_node(site, "resolution", "Resolution", fields.get("resolution"))
    if resolution_node is not None:
        batch.add_node(resolution_node)
        batch.add_edge(
            GraphEdge(
                "RESOLVED_AS",
                issue_uid,
                resolution_node.uid,
                props={"resolved_at": fields.get("resolutiondate")},
            )
        )

    _add_people(batch, site, issue_uid, fields)
    _add_classification(batch, site, issue_uid, fields)
    _add_hierarchy(batch, site, issue_uid, fields)
    _add_links(batch, site, issue_uid, fields, ontology)
    if "sprints" in wanted or sprint_field:
        _add_sprints(batch, site, issue_uid, fields, sprint_field, project_uid)
    if "comments" in wanted:
        _add_comments(batch, site, issue_uid, fields)
    if "attachments" in wanted:
        _add_attachments(batch, site, issue_uid, fields)
    if "worklogs" in wanted:
        _add_worklogs(batch, site, issue_uid, fields)
    _add_test_details(
        batch, site, issue_uid, key, fields, detail_fields, steps_from_text=steps_from_text
    )
    return batch


def extract_issues(
    records: Iterable[AlmRecord | Mapping[str, Any]],
    *,
    site: str,
    ontology: Ontology,
    **kwargs: Any,
) -> GraphBatch:
    """A whole read, as one de-duplicated batch.

    Accepts `AlmRecord`s as well as raw payloads so callers can hand it straight
    through from `api.read` — the record already carries the untouched issue in
    `raw`, which is the only part the graph needs.
    """
    batch = GraphBatch()
    for record in records:
        payload = record.raw if isinstance(record, AlmRecord) else record
        if isinstance(payload, Mapping):
            batch.extend(extract_issue(payload, site=site, ontology=ontology, **kwargs))
    return batch


def extract_changelog(
    issue_key: str, histories: Iterable[Mapping[str, Any]], *, site: str
) -> GraphBatch:
    """An issue's field-change history as a chain of dated events.

    Each history entry becomes a `:ChangeEvent` and each field it touched becomes a
    `:FieldChange` hanging off it, because one Jira transition routinely changes
    three fields at once and flattening them would lose which ones moved together.
    The entries are chained with NEXT so "how long did this sit in review" is a walk
    rather than a sort over every event in the database.
    """
    batch = GraphBatch()
    issue_uid = uid(SYSTEM, site, "issue", issue_key)
    ordered = [h for h in histories if isinstance(h, Mapping) and h.get("id")]
    ordered.sort(key=lambda h: (str(h.get("created") or ""), str(h.get("id"))))

    previous_uid: str | None = None
    for history in ordered:
        event_uid = uid(SYSTEM, site, "history", history.get("id"))
        batch.add_node(
            GraphNode(
                uid=event_uid,
                labels=("ChangeEvent",),
                props={
                    "history_id": str(history.get("id")),
                    "created": history.get("created"),
                    "issue_key": issue_key,
                    "stub": False,
                },
            )
        )
        batch.add_edge(GraphEdge("ON", event_uid, issue_uid))

        author = _user_node(site, history.get("author"))
        if author is not None:
            batch.add_node(author)
            batch.add_edge(GraphEdge("BY", event_uid, author.uid))

        for index, item in enumerate(history.get("items") or ()):
            if not isinstance(item, Mapping):
                continue
            change_uid = uid(SYSTEM, site, "fieldchange", f"{history.get('id')}#{index}")
            batch.add_node(
                GraphNode(
                    uid=change_uid,
                    labels=("FieldChange",),
                    props={
                        "field": item.get("field"),
                        "field_id": item.get("fieldId"),
                        "field_type": item.get("fieldtype"),
                        "from": item.get("fromString"),
                        "to": item.get("toString"),
                        "from_id": item.get("from"),
                        "to_id": item.get("to"),
                        "created": history.get("created"),
                        "stub": False,
                    },
                )
            )
            batch.add_edge(GraphEdge("CHANGED", event_uid, change_uid))

        if previous_uid:
            batch.add_edge(GraphEdge("NEXT", previous_uid, event_uid))
        previous_uid = event_uid
    return batch


def extract_sprints(
    boards: Iterable[Mapping[str, Any]],
    sprints_by_board: Mapping[Any, Iterable[Mapping[str, Any]]],
    *,
    site: str,
    project_key: str | None = None,
) -> GraphBatch:
    """Boards and their sprints, read from the Agile API rather than off an issue.

    Worth its own pass because an issue only ever mentions the sprints it was in: a
    sprint that is planned but still empty, or one whose issues have all left, exists
    on the board and nowhere in any issue payload. Those are exactly the sprints a
    capacity question is about.
    """
    batch = GraphBatch()
    project_uid = uid(SYSTEM, site, "project", project_key) if project_key else None
    if project_uid:
        batch.add_node(
            GraphNode(
                uid=project_uid,
                labels=("Project",),
                props={"key": str(project_key), "stub": True},
            )
        )

    for board in boards:
        if not isinstance(board, Mapping) or not board.get("id"):
            continue
        board_uid = uid(SYSTEM, site, "board", board.get("id"))
        batch.add_node(
            GraphNode(
                uid=board_uid,
                labels=("Board",),
                props={
                    "board_id": str(board.get("id")),
                    "name": board.get("name"),
                    "board_type": board.get("type"),
                    "stub": False,
                },
            )
        )
        if project_uid:
            batch.add_edge(GraphEdge("FOR_PROJECT", board_uid, project_uid))

        for sprint in sprints_by_board.get(board.get("id")) or ():
            if not isinstance(sprint, Mapping) or not sprint.get("id"):
                continue
            node = _sprint_node(site, sprint)
            batch.add_node(node)
            batch.add_edge(GraphEdge("ON_BOARD", node.uid, board_uid))
    return batch


def extract_remote_links(
    issue_key: str, links: Iterable[Mapping[str, Any]], *, site: str
) -> GraphBatch:
    """An issue's links out to systems Jira does not own — its own pass because,
    unlike comments or worklogs, Jira never includes these in the issue payload;
    `JiraClient.remote_links()` reads them from their own resource, one call per
    issue, same as watchers and the changelog.
    """
    batch = GraphBatch()
    issue_uid = uid(SYSTEM, site, "issue", issue_key)
    for link in links:
        if not isinstance(link, Mapping) or not link.get("id"):
            continue
        obj = link.get("object") if isinstance(link.get("object"), Mapping) else {}
        status = obj.get("status") if isinstance(obj.get("status"), Mapping) else {}
        application = link.get("application") if isinstance(link.get("application"), Mapping) else {}
        node = GraphNode(
            uid=uid(SYSTEM, site, "remotelink", link.get("id")),
            labels=("RemoteLink",),
            props={
                "remote_link_id": str(link.get("id")),
                "url": obj.get("url"),
                "title": obj.get("title"),
                "summary": obj.get("summary"),
                "relationship": link.get("relationship"),
                "application_type": application.get("type"),
                "application_name": application.get("name"),
                "resolved": status.get("resolved"),
                "stub": False,
            },
        )
        batch.add_node(node)
        batch.add_edge(GraphEdge("HAS_REMOTE_LINK", issue_uid, node.uid))
    return batch


def extract_watchers(
    issue_key: str, watchers: Iterable[Mapping[str, Any]], *, site: str
) -> GraphBatch:
    """Who is watching an issue — its own pass because it is its own API call."""
    batch = GraphBatch()
    issue_uid = uid(SYSTEM, site, "issue", issue_key)
    for watcher in watchers:
        node = _user_node(site, watcher)
        if node is None:
            continue
        batch.add_node(node)
        batch.add_edge(GraphEdge("WATCHED_BY", issue_uid, node.uid))
    return batch
