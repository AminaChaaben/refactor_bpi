"""Turn an `AlmRecord` into a `TrackedItem`, filling in what diffing needs.

Each system buries the same three facts — when it last changed, what it hangs off,
which sprint it is in — in a different corner of its payload. The clients already
fetch all of it and keep it in `record.raw`; this module is the one place that knows
where to look per system, so nothing downstream ever branches on `system` again.

Sprint membership is the awkward one. Azure has a real field for it and GitLab calls
it a milestone, but in Jira it is an instance-specific custom field whose id differs
per site, so it cannot be hardcoded. The id is declared once in `sources.json` as
`sync.sprint_field` and threaded through the `extra_fields` plumbing the clients
already support.
"""

from __future__ import annotations

import html
import re
from typing import Any, Mapping

from ..models import AlmRecord
from .models import TrackedItem
from .taxonomy import bucket_for

__all__ = [
    "to_tracked_item",
    "sprint_of",
    "test_details_of",
    "links_of",
    "relations_of",
    "merge_relations",
    "xray_relations",
    "azure_relations",
    "manual_steps_text",
    "detail_text",
    "updated_at_of",
]

# Legacy Jira returns sprints as a toString() blob rather than an object.
_LEGACY_SPRINT = re.compile(r"\bname=([^,\]]+)")


def updated_at_of(record: AlmRecord) -> str | None:
    """When the tracker says this item last changed, as it reported it.

    Kept verbatim rather than parsed into a datetime: it is only ever compared for
    equality and written to history, and reformatting it would just add a way for
    two representations of the same instant to stop matching.
    """
    raw = record.raw or {}
    fields = raw.get("fields") or {}
    if record.system == "jira":
        return fields.get("updated") or None
    if record.system == "azuredevops":
        return fields.get("System.ChangedDate") or None
    if record.system == "gitlab":
        return raw.get("updated_at") or None
    return None


def parent_of(record: AlmRecord) -> str | None:
    """The item this one hangs off, as a key — epic, parent story, or None."""
    raw = record.raw or {}
    fields = raw.get("fields") or {}
    if record.system == "jira":
        parent = fields.get("parent")
        if isinstance(parent, Mapping):
            return parent.get("key") or None
        return None
    if record.system == "azuredevops":
        parent = fields.get("System.Parent")
        return str(parent) if parent else None
    if record.system == "gitlab":
        epic = raw.get("epic")
        if isinstance(epic, Mapping):
            iid = epic.get("iid")
            return f"&{iid}" if iid else None
        return None
    return None


def _jira_sprint(value: Any) -> str | None:
    """The current sprint name out of whatever shape this Jira site returns.

    Jira gives a list because an issue carries its whole sprint history; the last
    entry is the one it is in now. Modern sites return objects, older ones return a
    Java toString() blob, and both still occur in the wild.
    """
    if not value:
        return None
    entries = value if isinstance(value, list) else [value]
    last = entries[-1]
    if isinstance(last, Mapping):
        name = last.get("name")
        return str(name) if name else None
    if isinstance(last, str):
        match = _LEGACY_SPRINT.search(last)
        return match.group(1) if match else last
    return None


def sprint_of(record: AlmRecord, sprint_field: str | None = None) -> str | None:
    """Which sprint/iteration/milestone this item currently sits in."""
    raw = record.raw or {}
    fields = raw.get("fields") or {}
    if record.system == "jira":
        if not sprint_field:
            return None
        return _jira_sprint(fields.get(sprint_field))
    if record.system == "azuredevops":
        # IterationPath is a full tree path; the leaf is the sprint itself.
        path = fields.get("System.IterationPath")
        return str(path).rsplit("\\", 1)[-1] if path else None
    if record.system == "gitlab":
        milestone = raw.get("milestone")
        if isinstance(milestone, Mapping):
            title = milestone.get("title")
            return str(title) if title else None
        return None
    return None


def detail_text(value: Any) -> str:
    """Flatten one custom-field value into a stable string.

    Determinism is the whole job here. These values are compared against the last
    cycle's, so any representation that can vary while the underlying value has not
    manufactures a change event on a test nobody touched — and once one exists it
    repeats every cycle forever. Three shapes cause that if handled naively:

    * A single-select arrives as `{"value": "Cucumber"}` on one read and, once the
      site changes its rendering, as a plain string. Both must flatten alike.
    * Rich text arrives as an Atlassian Document Format tree whose wrapper nodes
      carry ids and marks that churn on their own; only the leaf text is stable.
    * A multi-select's order is not guaranteed between reads, so it is sorted.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    if isinstance(value, Mapping):
        # Option objects, user objects and ADF documents all land here. Prefer the
        # human-facing key a tracker uses for a choice; fall back to walking the
        # tree for text nodes, which is what an ADF body reduces to. `action` and
        # `step` are last so a structured Xray step object flattens to its step
        # text instead of to nothing.
        for key in ("value", "name", "displayName", "title", "key", "action", "step"):
            found = value.get(key)
            if isinstance(found, str) and found.strip():
                return found.strip()
        collected = _adf_text(value)
        if collected:
            return collected
        return ""
    if isinstance(value, (list, tuple)):
        parts = [detail_text(entry) for entry in value]
        return " | ".join(sorted(part for part in parts if part))
    return str(value).strip()


def _adf_text(node: Any) -> str:
    """Every text leaf of an Atlassian Document Format tree, in document order."""
    if isinstance(node, Mapping):
        if node.get("type") == "text" and isinstance(node.get("text"), str):
            return node["text"]
        return " ".join(
            part for part in (_adf_text(child) for child in node.get("content") or []) if part
        )
    if isinstance(node, (list, tuple)):
        return " ".join(part for part in (_adf_text(child) for child in node) if part)
    return ""


DESCRIPTION_CRITERIA_LABEL = "acceptance_criteria_from_description"

_HTML_LINE_END = re.compile(r"(?i)<\s*(?:br\s*/?|/\s*(?:div|p|li|h[1-6]|tr))\s*>")
_HTML_TAG = re.compile(r"<[^>]*>")


def html_text(value: str) -> str:
    """Azure DevOps rich text as plain lines: block ends become line breaks, other tags drop."""
    text = _HTML_TAG.sub("", _HTML_LINE_END.sub("\n", value))
    lines = (html.unescape(line).replace("\xa0", " ").strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def test_details_of(
    record: AlmRecord, detail_fields: Mapping[str, str] | None
) -> tuple[tuple[str, str], ...]:
    """The declared test-shape fields of this item, as sorted (label, value) pairs.

    A field the project declared but this item does not carry is omitted rather than
    stored empty, so that later *populating* it reads as a change from absent to set
    instead of two empty strings comparing equal.
    """
    if not detail_fields:
        return ()
    raw = record.raw or {}
    fields = raw.get("fields") or {}
    if not isinstance(fields, Mapping):
        return ()

    found: list[tuple[str, str]] = []
    for label, field_id in detail_fields.items():
        if not isinstance(label, str) or not isinstance(field_id, str):
            continue
        value = fields.get(field_id)
        if record.system == "azuredevops" and isinstance(value, str):
            value = html_text(value)
        key = label.strip().lower()
        if key == "manual_steps":
            text = manual_steps_text(value)
        elif key == "acceptance_criteria":
            text = acceptance_criteria_text(value)
        else:
            text = detail_text(value)
        if text:
            found.append((key, text))
    if not any(key == "acceptance_criteria" for key, _ in found):
        source = fields.get(detail_fields.get("description", "description"))
        if record.system == "azuredevops" and isinstance(source, str):
            source = html_text(source)
        derived = criteria_from_description(source)
        if derived:
            found.append((DESCRIPTION_CRITERIA_LABEL, "\n".join(derived)))
    return tuple(sorted(found))


def acceptance_criteria_lines(value: Any) -> list[str]:
    """An issue's acceptance criteria, one entry per authored line or bullet.

    `detail_text` is deliberately lossy about structure -- it flattens an ADF tree to a
    single space-joined string, which is right for a one-line custom field and wrong
    here: acceptance criteria are authored as a bulleted or line-separated list, and
    collapsing them produces one giant run-on criterion. Consumers
    (`_bmad-input/user_stories/*.json`'s `acceptance_criteria`, and the test-design
    workflow that maps one scenario per criterion) need the individual items.

    Bullet/numbered markers are stripped: they are list formatting, not part of the
    criterion's text, and leaving them in means every generated test title starts "- ".
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [stripped for line in value.splitlines() if (stripped := _strip_bullet(line))]
    if isinstance(value, (list, tuple)):
        lines: list[str] = []
        for entry in value:
            lines.extend(acceptance_criteria_lines(entry))
        return lines
    if isinstance(value, Mapping):
        blocks = [block for block in _adf_blocks(value) if block]
        if blocks:
            return blocks
    text = detail_text(value)
    return [text] if text else []


def acceptance_criteria_text(value: Any) -> str:
    """`acceptance_criteria_lines` as one newline-joined string, for `details` storage.

    `details` is a flat (label, value) mapping compared field-by-field to detect
    changes, so it holds a string; `state.py` splits it back into a list when it writes
    the content artifact. Newline-joined rather than space-joined so that split is
    lossless.
    """
    return "\n".join(acceptance_criteria_lines(value))


# Leading list markers an author types by hand or that a renderer re-adds: "-", "*",
# "•", "1.", "1)", "a.". Anchored, so a hyphen inside a criterion is untouched.
_BULLET_MARKER = re.compile(r"^(?:[-*•‣●◦]|\(?(?:\d{1,3}|[a-zA-Z])[.)])\s+")

# A line that opens the acceptance-criteria section of a description, alone on its line.
_CRITERIA_HEADER = re.compile(
    r"^(?:#+\s*)?(?:acceptance criteria|criteria|crit[èe]res? d['’ ]?acceptation|crit[èe]res?)"
    r"\s*:?\s*$",
    re.IGNORECASE,
)


def _list_items(node: Any) -> list[str]:
    """Text of every ADF list item / task item, in document order."""
    if isinstance(node, (list, tuple)):
        return [item for child in node for item in _list_items(child)]
    if not isinstance(node, Mapping):
        return []
    if node.get("type") in ("listItem", "taskItem"):
        text = _strip_bullet(_adf_text(node))
        return [text] if text else []
    return _list_items(node.get("content") or [])


def criteria_from_description(value: Any) -> list[str]:
    """The criteria a description lists, deterministically; never its prose.

    1. The lines under an "Acceptance criteria" heading, up to the next heading-like line
       (short, ending with ":").
    2. Else the list items: ADF bullet/ordered/task items, or text lines that start with a
       bullet or number marker.
    A description with neither yields nothing.
    """
    lines = acceptance_criteria_lines(value)
    for index, line in enumerate(lines):
        if _CRITERIA_HEADER.match(line):
            section: list[str] = []
            for follower in lines[index + 1 :]:
                if follower.endswith(":") and len(follower) <= 60:
                    break
                section.append(follower)
            return section
    if isinstance(value, Mapping):
        return _list_items(value)
    if isinstance(value, str):
        return [
            _strip_bullet(line)
            for line in value.splitlines()
            if _BULLET_MARKER.match(line.strip())
        ]
    return []


def _strip_bullet(line: str) -> str:
    """`- criterion` / `* criterion` / `1. criterion` -> `criterion`."""
    stripped = line.strip()
    if not stripped:
        return ""
    without_marker = _BULLET_MARKER.sub("", stripped, count=1)
    return without_marker.strip() or stripped


# Block-level ADF nodes: each one is a separate authored line, and its text must not be
# emitted a second time by an enclosing node. `listItem` wraps a `paragraph`, so taking
# the item and not descending is what keeps one bullet from yielding two entries.
_ADF_BLOCK_TYPES = frozenset(
    {"paragraph", "listItem", "heading", "blockquote", "codeBlock", "taskItem"}
)


def _adf_blocks(node: Any) -> list[str]:
    """One string per block-level node of an ADF tree, in document order."""
    if isinstance(node, (list, tuple)):
        blocks: list[str] = []
        for child in node:
            blocks.extend(_adf_blocks(child))
        return blocks
    if not isinstance(node, Mapping):
        return []
    if node.get("type") in _ADF_BLOCK_TYPES:
        text = _strip_bullet(_adf_text(node))
        return [text] if text else []
    return _adf_blocks(node.get("content") or [])


def manual_steps_text(value: Any) -> str:
    """Flatten an Xray Server manual-steps value into one stable string.

    The field arrives as `{"steps": [{"fields": {"Action":…, "Data":…,
    "Expected Result":…}}]}` — a shape `detail_text` does not know. Each step
    flattens to `Action | Data | Expected Result`, joined by newlines, so a step
    added or edited reads as a change exactly like any other detail.
    """
    steps = value.get("steps") if isinstance(value, Mapping) else value
    if not isinstance(steps, list):
        return detail_text(value)
    lines: list[str] = []
    for step in steps:
        if not isinstance(step, Mapping):
            continue
        fields = step.get("fields") if isinstance(step.get("fields"), Mapping) else step
        parts = [
            detail_text(fields.get(name))
            for name in ("Action", "Data", "Expected Result")
            if detail_text(fields.get(name))
        ]
        if parts:
            lines.append(" | ".join(parts))
    return "\n".join(lines)


def links_of(record: AlmRecord) -> tuple[tuple[str, str], ...]:
    """This item's issue links as (type, other key) pairs.

    Jira is the only system whose links are readable this way, and the only one
    where link membership *is* the test structure — a test belongs to a set, plan
    or execution through a link, so a link appearing or disappearing is a change
    worth detecting exactly like a custom field moving. Each link is folded onto
    (link type name, the key of the issue on the other end), sorted so the pair
    order never varies between reads.
    """
    raw = record.raw or {}
    fields = raw.get("fields") or {}
    if record.system != "jira" or not isinstance(fields, Mapping):
        return ()
    found: list[tuple[str, str]] = []
    for link in fields.get("issuelinks") or ():
        if not isinstance(link, Mapping):
            continue
        type_name = (link.get("type") or {}).get("name") if isinstance(link.get("type"), Mapping) else None
        other = (link.get("inwardIssue") or {}).get("key") or (link.get("outwardIssue") or {}).get("key")
        if type_name and other:
            found.append((str(type_name), str(other)))
    return tuple(sorted(set(found)))


def relations_of(record: AlmRecord) -> dict[str, list[dict[str, str]]]:
    """This item's relationships, as named fields, each entry fully navigable.

    Jira is the only system whose links are readable this way, so anything else
    yields an empty block. Every link is folded onto a named field derived from
    the link type and the other issue's type — `covers`/`covered_by` for a test
    and its story, `test_plan`/`test_sets`/`executions`/`preconditions` for the
    containers a test belongs to, `relates_to`, `blocks`/`blocked_by`, `bugs`/
    `raised_by`, and `parent`/`children` from the hierarchy. Each entry carries
    the other side's key, id, title and url, so state.json is navigable on its
    own.

    The ontology is imported lazily: this module feeds change detection, which
    must stay importable without the graph package's optional dependencies.
    """
    raw = record.raw or {}
    fields = raw.get("fields") or {}
    if record.system != "jira" or not isinstance(fields, Mapping):
        return {}

    from ..graph.ontology import load_ontology

    ontology = load_ontology()
    out: dict[str, list[dict[str, str]]] = {}

    def add(field_name: str, ref: dict[str, str]) -> None:
        bucket = out.setdefault(field_name, [])
        if not any(e.get("key") == ref.get("key") for e in bucket):
            bucket.append(ref)

    base = record.url.rsplit("/browse/", 1)[0] if "/browse/" in record.url else ""

    def entry(other: Mapping[str, Any], link: Mapping[str, Any]) -> dict[str, str]:
        other_fields = other.get("fields") if isinstance(other.get("fields"), Mapping) else {}
        link_type = link.get("type") if isinstance(link.get("type"), Mapping) else {}
        return {
            "key": str(other.get("key") or ""),
            "id": str(other.get("id") or ""),
            "title": str(other_fields.get("summary") or ""),
            "url": f"{base}/browse/{other.get('key')}" if base and other.get("key") else "",
            "type": str(other_fields.get("issuetype", {}).get("name") or "")
            if isinstance(other_fields.get("issuetype"), Mapping)
            else "",
            "link_type": str(link_type.get("name") or ""),
        }

    own_labels = set(ontology.labels_for_type(record.type or ""))

    for link in fields.get("issuelinks") or ():
        if not isinstance(link, Mapping):
            continue
        link_type = link.get("type") if isinstance(link.get("type"), Mapping) else {}
        type_name = str(link_type.get("name") or "")
        rel, _ = ontology.link_rel(type_name)

        inward = link.get("inwardIssue")
        outward = link.get("outwardIssue")
        if isinstance(inward, Mapping) and inward.get("key"):
            other, direction = inward, "outward"
        elif isinstance(outward, Mapping) and outward.get("key"):
            other, direction = outward, "inward"
        else:
            continue
        ref = entry(other, link)
        other_labels = set(ontology.labels_for_type(ref["type"]))

        if rel == "COVERS":
            if direction == "outward":
                if "TestPlan" in other_labels:
                    add("test_plan", ref)
                elif "TestSet" in other_labels:
                    add("test_sets", ref)
                elif "TestExecution" in other_labels:
                    add("executions", ref)
                elif "Precondition" in other_labels:
                    add("preconditions", ref)
                elif "Bug" in other_labels:
                    add("bugs", ref)
                else:
                    add("covers", ref)
            else:
                if "TestPlan" in own_labels or "TestSet" in own_labels or "TestExecution" in own_labels:
                    add("tests", ref)
                elif "Precondition" in own_labels:
                    add("used_by", ref)
                elif "Bug" in own_labels:
                    add("raised_by", ref)
                else:
                    add("covered_by", ref)
        elif rel == "RELATES_TO":
            add("relates_to", ref)
        elif rel == "BLOCKS":
            add("blocks" if direction == "outward" else "blocked_by", ref)
        elif rel == "FOUND_DEFECT":
            add("bugs" if direction == "outward" else "raised_by", ref)
        else:
            add("linked_to", ref)

    parent = fields.get("parent")
    if isinstance(parent, Mapping) and parent.get("key"):
        parent_fields = parent.get("fields") if isinstance(parent.get("fields"), Mapping) else {}
        add(
            "parent",
            {
                "key": str(parent.get("key") or ""),
                "id": str(parent.get("id") or ""),
                "title": str(parent_fields.get("summary") or ""),
                "url": f"{base}/browse/{parent.get('key')}" if base and parent.get("key") else "",
                "type": str((parent_fields.get("issuetype") or {}).get("name") or "")
                if isinstance(parent_fields.get("issuetype"), Mapping)
                else "",
                "link_type": "parent",
            },
        )

    for child in fields.get("subtasks") or ():
        if not isinstance(child, Mapping) or not child.get("key"):
            continue
        child_fields = child.get("fields") if isinstance(child.get("fields"), Mapping) else {}
        add(
            "children",
            {
                "key": str(child.get("key") or ""),
                "id": str(child.get("id") or ""),
                "title": str(child_fields.get("summary") or ""),
                "url": f"{base}/browse/{child.get('key')}" if base and child.get("key") else "",
                "type": str((child_fields.get("issuetype") or {}).get("name") or "")
                if isinstance(child_fields.get("issuetype"), Mapping)
                else "",
                "link_type": "parent",
            },
        )

    return {name: entries for name, entries in out.items() if entries}


def merge_relations(
    primary: Mapping[str, list[dict[str, str]]], extra: Mapping[str, list[dict[str, str]]]
) -> dict[str, list[dict[str, str]]]:
    """Two relations blocks folded into one, de-duplicated by key within each field.

    `primary` wins ties: an entry `extra` repeats under the same field and key is
    dropped rather than duplicated, so merging Xray-native membership into
    issuelink-derived relations never doubles a fact both sides happen to report.
    """
    merged: dict[str, list[dict[str, str]]] = {
        name: list(entries) for name, entries in primary.items()
    }
    for field_name, entries in extra.items():
        bucket = merged.setdefault(field_name, [])
        seen = {entry.get("key") for entry in bucket}
        for entry in entries:
            if entry.get("key") not in seen:
                bucket.append(entry)
                seen.add(entry.get("key"))
    return merged


def xray_relations(
    collections: Mapping[str, list[Mapping[str, Any]]], *, base_url: str = ""
) -> dict[str, dict[str, list[dict[str, str]]]]:
    """Test/container membership, read straight from Xray's own API.

    Xray Cloud and Server both hold this fact themselves — which tests a set, plan
    or execution contains, which tests a precondition applies to — separately from
    Jira's `issuelinks`, so `relations_of` (issuelinks only) cannot see it once a
    real Xray is active: a site running Cloud or Server/DC does not have to mirror
    that membership as a plain issue link, and generally does not.

    Given the same collections the graph already reads (`tests`, `preconditions`,
    `test_sets`, `test_plans`, `executions`, in the shape `XrayCloudClient` and
    `xray_server.collect` both produce), this derives the same named fields
    `relations_of` would have, keyed by Jira key so `merge_relations` can fold the
    two together into one `TrackedItem`.
    """
    index: dict[str, tuple[str, str]] = {}
    for name in ("tests", "preconditions", "test_sets", "test_plans", "executions"):
        for entry in collections.get(name) or ():
            issue_id = str(entry.get("issueId") or "")
            jira = entry.get("jira") if isinstance(entry.get("jira"), Mapping) else {}
            key = str(jira.get("key") or "")
            if issue_id and key:
                index[issue_id] = (key, str(jira.get("summary") or ""))

    out: dict[str, dict[str, list[dict[str, str]]]] = {}

    def add(owner_key: str, field_name: str, ref_id: str, ref_type: str) -> None:
        if not owner_key or not ref_id or ref_id not in index:
            return
        ref_key, ref_title = index[ref_id]
        bucket = out.setdefault(owner_key, {}).setdefault(field_name, [])
        if any(entry.get("key") == ref_key for entry in bucket):
            return
        bucket.append(
            {
                "key": ref_key,
                "id": ref_id,
                "title": ref_title,
                "url": f"{base_url}/browse/{ref_key}" if base_url else "",
                "type": ref_type,
                "link_type": "xray",
            }
        )

    def members_of(entry: Mapping[str, Any]) -> list[str]:
        block = entry.get("tests")
        results = block.get("results") if isinstance(block, Mapping) else None
        return [
            str(t.get("issueId") or "")
            for t in results or ()
            if isinstance(t, Mapping) and t.get("issueId")
        ]

    containers = (
        ("test_sets", "Test Set", "test_sets"),
        ("test_plans", "Test Plan", "test_plan"),
        ("executions", "Test Execution", "executions"),
        ("preconditions", "Precondition", "preconditions"),
    )
    for collection_name, type_label, test_field in containers:
        for entry in collections.get(collection_name) or ():
            container_id = str(entry.get("issueId") or "")
            if not container_id or container_id not in index:
                continue
            container_key = index[container_id][0]
            reverse_field = "used_by" if collection_name == "preconditions" else "tests"
            for member_id in members_of(entry):
                member_key = index.get(member_id, ("", ""))[0]
                add(member_key, test_field, container_id, type_label)
                add(container_key, reverse_field, member_id, "Test")

    return out


_AZURE_RELATION_FIELDS = {
    "Microsoft.VSTS.Common.TestedBy-Forward": "covered_by",
    "Microsoft.VSTS.Common.TestedBy-Reverse": "covers",
    "System.LinkTypes.Hierarchy-Reverse": "parent",
    "System.LinkTypes.Hierarchy-Forward": "children",
    "System.LinkTypes.Related": "relates_to",
    "System.LinkTypes.Dependency-Forward": "blocks",
    "System.LinkTypes.Dependency-Reverse": "blocked_by",
}


def _azure_work_item_id(url: Any) -> str:
    """The work item id at the end of a relation url, or "" for any other artifact."""
    if not isinstance(url, str) or "/_apis/wit/workItems/" not in url:
        return ""
    tail = url.rsplit("/", 1)[-1]
    return tail if tail.isdigit() else ""


def azure_relations(
    items: list[Mapping[str, Any]], *, known: Mapping[str, AlmRecord]
) -> dict[str, dict[str, list[dict[str, str]]]]:
    """Azure DevOps work item links as the named relation fields `relations_of` produces.

    `items` are relation-expanded payloads (`AzureDevOpsClient.graph_batch`);
    `known` maps work item ids to the records the same cycle already read, which
    supply the other side's title, type and url when it is in scope. Tested By
    and Tests become `covered_by`/`covers`, the hierarchy `parent`/`children`,
    Related `relates_to`, Successor/Predecessor `blocks`/`blocked_by`, and any
    other work item link `linked_to`. Hyperlinks, commits and attachments are
    not work items and are skipped. Keyed by work item id.
    """
    out: dict[str, dict[str, list[dict[str, str]]]] = {}
    for item in items:
        owner = str(item.get("id") or "")
        if not owner:
            continue
        for relation in item.get("relations") or ():
            if not isinstance(relation, Mapping):
                continue
            other_id = _azure_work_item_id(relation.get("url"))
            if not other_id:
                continue
            rel = str(relation.get("rel") or "")
            attributes = relation.get("attributes") if isinstance(relation.get("attributes"), Mapping) else {}
            other = known.get(other_id)
            bucket = out.setdefault(owner, {}).setdefault(
                _AZURE_RELATION_FIELDS.get(rel, "linked_to"), []
            )
            if any(entry.get("key") == other_id for entry in bucket):
                continue
            bucket.append(
                {
                    "key": other_id,
                    "id": other_id,
                    "title": other.title if other else "",
                    "url": other.url if other else "",
                    "type": other.type if other else "",
                    "link_type": str(attributes.get("name") or rel),
                }
            )
    return out


def to_tracked_item(
    record: AlmRecord,
    *,
    source: str,
    buckets: Mapping[str, str] | None = None,
    sprint_field: str | None = None,
    detail_fields: Mapping[str, str] | None = None,
    track_links: bool = False,
) -> TrackedItem:
    """One `AlmRecord` as the shape change detection compares.

    `track_links` controls whether the issue links are asked for in the first
    place (they are extra fetch weight for the sync path). When they are present
    in the record, the named relationship block is always derived — `relations`
    is what state.json stores, so the orchestrator reads who-covers-what
    directly instead of parsing link lists.
    """
    details = test_details_of(record, detail_fields)
    relations = relations_of(record) if track_links else {}
    return TrackedItem(
        system=record.system,
        source=source,
        key=record.key or record.id,
        id=record.id,
        title=record.title,
        status=record.status,
        type=record.type,
        bucket=bucket_for(record.type, buckets),
        url=record.url,
        assignee=record.assignee,
        tags=tuple(record.tags or ()),
        parent=parent_of(record),
        sprint=sprint_of(record, sprint_field),
        updated_at=updated_at_of(record),
        details=details,
        relations=relations,
    )
