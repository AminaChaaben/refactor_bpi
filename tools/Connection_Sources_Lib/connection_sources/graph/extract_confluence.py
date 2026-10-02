"""Confluence as written context: which page says what a requirement is meant to do.

The same restraint `extract_gitlab` applies to source code, applied to prose. This
module does not put page text into the graph and never should: a wiki page's body is
the wiki's job, and a property holding ten kilobytes of XHTML is not queryable, not
diffable and not something anyone would ask Cypher for. What goes in is the shape —
which spaces exist, which pages, how they nest, who wrote them, what they are
labelled — and one thing that exists nowhere else:

**which tracker issues a page names.** A specification page that writes `USN-260` in
its text, or carries a Jira issue macro pointing at it, is the only record connecting
the story to the document it was written from. Jira does not know the page exists;
Confluence knows nothing about what `USN-260` means. Recovering that link is what
turns "we have a wiki" into "this requirement has a specification, and here it is".

As in `extract_gitlab`, the link is `MENTIONS` and not `IMPLEMENTS` or `SPECIFIES`. A
key appearing in a page is evidence that the two are about each other; it is not a
claim that the page is the story's specification, and a traceability graph that
promotes evidence to certainty stops being usable as evidence.

The far end of every `MENTIONS` is emitted in the *tracker's* namespace as a stub, so
it merges with the real issue on the Jira pass rather than creating a parallel
Confluence-flavoured copy of the backlog. That is the same join `extract_gitlab` uses
and the reason both can run in any order after the tracker.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from .extract_gitlab import issue_keys_in
from .model import GraphBatch, GraphEdge, GraphNode, uid

__all__ = ["SYSTEM", "extract_confluence", "plain_text"]

SYSTEM = "confluence"

# Storage-format XHTML in, readable text out. Not a parser and not trying to be: the
# only consumer is `issue_keys_in`, which wants "is ABC-123 in here as a word", and
# the two things that would break that are tags glued to words (`<p>USN-260</p>`
# becoming `pUSN-260p`) and entity-escaped punctuation. Both are handled; everything
# else about the markup is deliberately ignored.
_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")
_ENTITIES = (
    ("&amp;", "&"),
    ("&lt;", "<"),
    ("&gt;", ">"),
    ("&quot;", '"'),
    ("&#39;", "'"),
    ("&nbsp;", " "),
)


def plain_text(storage: Any) -> str:
    """A page's storage body reduced to text, or "" for anything unusable."""
    if not isinstance(storage, str) or not storage.strip():
        return ""
    # Tags become spaces rather than nothing: `<td>USN-1</td><td>USN-2</td>` must not
    # collapse into `USN-1USN-2`, which matches neither key.
    text = _TAG.sub(" ", storage)
    for entity, char in _ENTITIES:
        text = text.replace(entity, char)
    return _WHITESPACE.sub(" ", text).strip()


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        for key in ("name", "title", "value", "displayName"):
            found = value.get(key)
            if isinstance(found, str) and found.strip():
                return found.strip()
        return ""
    return str(value).strip()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _user(tracker_system: str, site: str, person: Any) -> GraphNode | None:
    """One tracker account, keyed the way that tracker's own extractor keys it.

    Written into the tracker's namespace — `tracker_system`, not this module's own
    `SYSTEM` — so the person who reported the story and the person who wrote its
    specification are one node rather than two, whichever tracker the project uses.
    Jira accounts key on `accountId`; Azure DevOps and Server/DC Confluence have
    neither, and identify a person by a `username`/`name` instead, so that is the
    fallback identity rather than silently dropping every such author.
    """
    data = _mapping(person)
    account = _text(data.get("accountId"))
    username = _text(data.get("username") or data.get("name"))
    if not account and not username:
        return None
    return GraphNode(
        uid=uid(tracker_system, site, "user", account or f"name:{username}"),
        labels=("User",),
        props={
            "account_id": account or None,
            "username": username or None,
            "display_name": _text(data.get("displayName") or data.get("displayname")),
            "email": _text(data.get("email")),
            "active": data.get("accountType") != "deleted",
            "identified_by": "account_id" if account else "username",
            "stub": False,
        },
    )


def _space(site: str, space: Mapping[str, Any], base_url: str) -> GraphNode | None:
    key = _text(space.get("key"))
    if not key:
        return None
    return GraphNode(
        uid=uid(SYSTEM, site, "space", key),
        labels=("Space",),
        props={
            "key": key,
            "name": _text(space.get("name")),
            "type": _text(space.get("type")),
            "url": f"{base_url}/wiki/spaces/{key}" if base_url else "",
            "stub": False,
        },
    )


def _labels_of(page: Mapping[str, Any]) -> list[str]:
    metadata = _mapping(page.get("metadata"))
    labels = _mapping(metadata.get("labels"))
    results = labels.get("results")
    if not isinstance(results, list):
        return []
    return [_text(entry.get("name")) for entry in results if isinstance(entry, Mapping)]


def _page_node(
    site: str, page: Mapping[str, Any], base_url: str, *, stub: bool = False
) -> GraphNode:
    page_id = _text(page.get("id"))
    links = _mapping(page.get("_links"))
    webui = _text(links.get("webui"))
    version = _mapping(page.get("version"))
    history = _mapping(page.get("history"))
    props: dict[str, Any] = {
        "page_id": page_id,
        "title": _text(page.get("title")),
        "type": _text(page.get("type")) or "page",
        "status": _text(page.get("status")) or "current",
        "url": f"{base_url}/wiki{webui}" if (base_url and webui) else "",
        "stub": stub,
    }
    if not stub:
        props.update(
            {
                "version": version.get("number"),
                "updated": _text(version.get("when")),
                "created": _text(history.get("createdDate")),
                "latest": bool(history.get("latest", True)),
            }
        )
    return GraphNode(uid=uid(SYSTEM, site, "page", page_id), labels=("Page",), props=props)


def extract_confluence(
    *,
    site: str,
    base_url: str,
    source: str,
    tracker_system: str,
    tracker_site: str,
    pages: Iterable[Mapping[str, Any]],
) -> GraphBatch:
    """Every page's shape, plus the tracker issues each one names.

    `tracker_system`/`tracker_site` name the namespace mentioned issue keys are
    resolved into -- the Jira pass's namespace, not this one. Passing the wrong pair
    does not fail loudly; it produces a parallel set of orphan issue stubs, which is
    why the runner derives both from the tracker it just read rather than letting a
    caller guess.
    """
    batch = GraphBatch()

    for page in pages:
        if not isinstance(page, Mapping):
            continue
        page_id = _text(page.get("id"))
        if not page_id:
            continue

        node = _page_node(site, page, base_url)
        page_uid = node.uid
        batch.add_node(
            GraphNode(uid=page_uid, labels=node.labels, props={**node.props, "source": source})
        )

        space = _mapping(page.get("space"))
        space_node = _space(site, space, base_url)
        if space_node:
            batch.add_node(space_node)
            batch.add_edge(GraphEdge("IN_SPACE", page_uid, space_node.uid))

        # Ancestors come ordered root-first; the last one is the direct parent. Only
        # that edge is emitted: the grandparent relationship is derivable by walking
        # CHILD_OF, and emitting it as well would make every depth query count the
        # same page several times.
        ancestors = page.get("ancestors")
        if isinstance(ancestors, list) and ancestors:
            parent = ancestors[-1]
            if isinstance(parent, Mapping) and _text(parent.get("id")):
                parent_node = _page_node(site, parent, base_url, stub=True)
                batch.add_node(parent_node)
                batch.add_edge(GraphEdge("CHILD_OF", page_uid, parent_node.uid))

        history = _mapping(page.get("history"))
        author = _user(tracker_system, tracker_site or site, history.get("createdBy"))
        if author:
            batch.add_node(author)
            batch.add_edge(GraphEdge("AUTHORED", author.uid, page_uid))

        for label in _labels_of(page):
            if not label:
                continue
            label_uid = uid(tracker_system, tracker_site or site, "label", label)
            batch.add_node(GraphNode(uid=label_uid, labels=("Label",), props={"name": label}))
            batch.add_edge(GraphEdge("HAS_LABEL", page_uid, label_uid))

        body = _mapping(_mapping(page.get("body")).get("storage")).get("value")
        text = plain_text(body)
        # Title first: a page called "USN-260 -- payment retry spec" names its issue
        # in the one place a human would look, and a body-less build still finds it.
        for key in issue_keys_in(_text(page.get("title")), text):
            target = uid(tracker_system, tracker_site, "issue", key)
            batch.add_node(
                GraphNode(uid=target, labels=("Issue",), props={"key": key, "stub": True})
            )
            batch.add_edge(
                GraphEdge(
                    "MENTIONS",
                    page_uid,
                    target,
                    props={"found_in": "page"},
                )
            )

    return batch
