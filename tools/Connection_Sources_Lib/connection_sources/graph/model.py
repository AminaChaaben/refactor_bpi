"""The two shapes the graph is built from, and the naming rule that makes it idempotent.

Idempotence in a graph is entirely a question of how nodes are named. `AlmRecord` is
what the clients return and `TrackedItem` is what change detection compares; neither
can be the graph's unit, because both describe one *item* and the graph is made of
many nodes per item — its status, its type, its assignee, each of its labels.

So `GraphNode` and `GraphEdge` are deliberately dumb: a uid, some labels, a property
bag. All the judgement about what to emit lives in the extractors, and all the
judgement about how to write it lives in the loader. That split is what lets the
extractors be tested against JSON on disk with no database anywhere near them.

Two rules earn their place here rather than in either neighbour:

*Identity is the tracker's id, never a display string.* A user is merged on
`accountId`, a status on its numeric id, a sprint on its sprint id. Display names get
renamed — a sprint renamed by an admin is a common sight — and a graph keyed on them
splits one node in two the moment somebody edits a field.

*Properties are scrubbed to what Neo4j can store.* The driver rejects a nested map or
a heterogeneous list at write time, in the middle of a batch, with an error naming
neither the node nor the key. Scrubbing at construction turns that into a value that
is simply absent, which the extractor tests can see.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from .ontology import ALM_SCHEMA
from .schema import GraphSchema

__all__ = [
    "GraphBatch",
    "GraphEdge",
    "GraphNode",
    "Namespace",
    "UNSCOPED",
    "site_of",
    "uid",
]

_UNSAFE_UID = re.compile(r"\s+")

# The scope segment a `Namespace` carries when no project-level scope could be
# derived. A prefix built on it can still be used to write, but never to prune or
# query — its blast radius spans every project on the system+site, which is the
# defect a real scope exists to close.
UNSCOPED = "_"


def site_of(base_url: str) -> str:
    """The site a uid is namespaced by, from any base URL.

    Kept as the host plus whatever path the base URL carries, rather than a slug: it
    is already unique, already stable, and staying legible matters when a uid turns
    up in an error message or in the Neo4j browser.

    The path is part of it because one host is not always one tenant. A Jira site and
    a GitLab instance live at the root of their own hostnames, but every Azure DevOps
    organisation shares `dev.azure.com` and is distinguished only by the first path
    segment — dropping it would merge two organisations' work item 42 into one node.
    """
    if not base_url:
        return "unknown"
    parsed = urlparse(base_url if "://" in base_url else f"https://{base_url}")
    host = (parsed.netloc or "").strip("/").lower()
    path = (parsed.path or "").strip("/").lower()
    if not host:
        return path or "unknown"
    return f"{host}/{path}" if path else host


def uid(system: str, site: str, kind: str, natural_id: Any) -> str:
    """`system:site:kind:id` — the one name every node is merged on.

    `site` is in the key so a single Neo4j instance can serve every client in the
    factory without two projects' `PROJ-1` colliding. Whitespace is collapsed because
    some natural ids are names (a label, a test environment) and a trailing space is
    invisible in the browser but produces a second node.
    """
    ident = _UNSAFE_UID.sub(" ", str(natural_id if natural_id is not None else "")).strip()
    return f"{system}:{site}:{kind}:{ident}"


@dataclass(frozen=True, slots=True)
class Namespace:
    """A system+site+scope triple — the one unit a prune or a query is safe to sweep.

    `scope` is what closes the gap a bare `system:site` prefix leaves open: two
    projects on the same Jira site, or the same Azure DevOps organisation, share one
    `system:site` prefix, so a sweep scoped only that far deletes across projects.
    Every node this package writes is named `system:site:scope:kind:id`, and `scope`
    is ordinarily the tracker's own project identifier — a Jira project key, an
    Azure DevOps project name, a GitLab project path, a Confluence space key —
    sanitised the same way a natural id is.

    `platform` (`Namespace.make`'s keyword-only argument) closes a second gap the
    tracker's own identifier cannot: two platform-managed projects can be configured
    against the very same Jira project or GitLab repository (a duplicated demo, a
    forked client engagement), and a scope built from the tracker side alone would be
    identical for both, colliding in whatever Neo4j instance they share. When the
    caller has a platform project id, folding it in front of the tracker-derived scope
    keeps those two projects apart even when everything ALM-side is identical.
    """

    system: str
    site: str
    scope: str = UNSCOPED

    @classmethod
    def make(
        cls, system: str, site: str, scope: Any = None, *, platform: Any = None
    ) -> "Namespace":
        cleaned = _UNSAFE_UID.sub(" ", str(scope)).strip().replace(":", "-") if scope else ""
        if platform:
            platform_clean = (
                _UNSAFE_UID.sub(" ", str(platform)).strip().replace(":", "-")
            )
            cleaned = f"{platform_clean}-{cleaned}" if cleaned else platform_clean
        return cls(system=system, site=site, scope=cleaned or UNSCOPED)

    @property
    def resolved(self) -> bool:
        return self.scope != UNSCOPED

    @property
    def site_scope(self) -> str:
        """`site:scope`, the composite this package threads through as "the site"."""
        return f"{self.site}:{self.scope}"

    @property
    def prefix(self) -> str:
        """`system:site:scope:` — what every uid under this namespace starts with."""
        return f"{self.system}:{self.site_scope}:"

    def uid(self, kind: str, natural_id: Any) -> str:
        return uid(self.system, self.site_scope, kind, natural_id)


def _scrub(value: Any) -> Any:
    """One property value, reduced to something Neo4j will accept, or None.

    Neo4j stores primitives and homogeneous lists of primitives. Anything else — a
    nested object, a list of objects — is dropped rather than stringified: a JSON
    blob in a property is not queryable, so keeping it only makes the node heavier
    while still failing the question anyone would ask of it. What the extractor
    wanted from a nested object it should have pulled out as its own node.
    """
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        items = [v for v in (_scrub(entry) for entry in value) if v is not None]
        if not items or not all(isinstance(v, (str, bool, int, float)) for v in items):
            return None
        return items
    return None


def scrub_props(props: Mapping[str, Any] | None) -> dict[str, Any]:
    """A property bag with unstorable values and empty keys removed."""
    if not props:
        return {}
    out: dict[str, Any] = {}
    for key, value in props.items():
        if not isinstance(key, str) or not key.strip():
            continue
        cleaned = _scrub(value)
        if cleaned is None:
            continue
        if isinstance(cleaned, str) and not cleaned.strip():
            continue
        out[key.strip()] = cleaned.strip() if isinstance(cleaned, str) else cleaned
    return out


@dataclass(frozen=True, slots=True)
class GraphNode:
    """One node: a uid, the labels it carries, and its properties."""

    uid: str
    labels: tuple[str, ...]
    props: dict[str, Any] = field(default_factory=dict)
    # Which graph the node belongs to: its marker label and the closed label set it is
    # checked against. Not part of equality, so ALM callers never see it.
    schema: GraphSchema = field(default=ALM_SCHEMA, compare=False, repr=False)

    def __post_init__(self) -> None:
        # Labels are the only fragment interpolated into Cypher, so they are filtered
        # against the closed set here — at construction, where a test can see it —
        # rather than trusted at write time.
        schema = self.schema
        checked = tuple(
            dict.fromkeys(schema.safe_label(l) for l in self.labels if l != schema.marker)
        )
        object.__setattr__(self, "labels", checked)
        object.__setattr__(self, "props", scrub_props(self.props))

    @property
    def signature(self) -> tuple[str, ...]:
        """The label set, sorted — the key batches are grouped by before writing."""
        return tuple(sorted(self.labels))

    @property
    def is_stub(self) -> bool:
        """True when this node was only *referenced*, never fetched.

        An issue link names an issue that may sit outside the configured scope. The
        edge is real and must be kept, so the far end is emitted as a stub carrying
        whatever the link payload happened to include. `stub` is written explicitly
        as `false` on fetched nodes rather than left absent, so a later run that does
        fetch the issue clears the flag instead of leaving it set forever.
        """
        return bool(self.props.get("stub"))

    def merged_with(self, other: "GraphNode") -> "GraphNode":
        """This node and another of the same uid, combined.

        The same node is legitimately emitted more than once in one extraction pass:
        an issue produces its assignee as a `:User`, and so does every comment that
        person wrote. Combining them keeps the richest description of each — a later
        emission that knows the email must not be flattened by an earlier one that
        only knew the account id.

        When exactly one side is a stub the other side wins every shared key,
        regardless of which was added first. Otherwise a full issue extracted early
        in the pass would be overwritten by the three-field stub some later issue's
        link happens to mention, and the graph would lose the body of a node it had
        actually read.
        """
        if self.schema != other.schema:
            raise ValueError(f"cannot merge {self.uid!r} across graphs {self.schema.name!r}/{other.schema.name!r}")
        if self.is_stub != other.is_stub:
            rich, poor = (other, self) if self.is_stub else (self, other)
            props = {**poor.props, **rich.props}
        else:
            props = {**self.props, **other.props}
        return GraphNode(
            uid=self.uid,
            labels=tuple(dict.fromkeys(self.labels + other.labels)),
            props=props,
            schema=self.schema,
        )


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """One relationship, and the properties that identify or describe it."""

    type: str
    start: str
    end: str
    props: dict[str, Any] = field(default_factory=dict)
    # Property names that take part in matching the relationship rather than merely
    # describing it. Two issues can be linked twice under different link types, so
    # `LINKED_TO` merges on `link_type` while `BLOCKS` merges on the pair alone.
    key_props: tuple[str, ...] = ()
    schema: GraphSchema = field(default=ALM_SCHEMA, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "type", self.schema.safe_relationship(self.type))
        object.__setattr__(self, "props", scrub_props(self.props))
        object.__setattr__(
            self, "key_props", tuple(k for k in self.key_props if k in self.props)
        )

    @property
    def signature(self) -> tuple[str, tuple[str, ...]]:
        """Relationship type plus its matching keys — how edge batches are grouped."""
        return (self.type, tuple(sorted(self.key_props)))

    @property
    def identity(self) -> tuple[Any, ...]:
        """What makes this edge the same edge, for in-memory de-duplication."""
        return (self.type, self.start, self.end) + tuple(
            self.props.get(k) for k in sorted(self.key_props)
        )


@dataclass
class GraphBatch:
    """Everything one extraction pass produced, de-duplicated as it is collected.

    De-duplicating here rather than leaving it to `MERGE` is not about correctness —
    `MERGE` would cope — but about volume. A hundred issues in one project emit a
    hundred identical `:Project` nodes and a hundred identical `IN_PROJECT` writes,
    and the round trips are the expensive part of a load, not the statements.
    """

    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: dict[tuple, GraphEdge] = field(default_factory=dict)

    def add_node(self, node: GraphNode | None) -> None:
        if node is None or not node.uid:
            return
        existing = self.nodes.get(node.uid)
        self.nodes[node.uid] = existing.merged_with(node) if existing else node

    def add_edge(self, edge: GraphEdge | None) -> None:
        if edge is None or not edge.start or not edge.end:
            return
        self.edges[edge.identity] = edge

    def extend(self, other: "GraphBatch") -> "GraphBatch":
        for node in other.nodes.values():
            self.add_node(node)
        for edge in other.edges.values():
            self.add_edge(edge)
        return self

    def add_all(
        self, nodes: Iterable[GraphNode] = (), edges: Iterable[GraphEdge] = ()
    ) -> "GraphBatch":
        for node in nodes:
            self.add_node(node)
        for edge in edges:
            self.add_edge(edge)
        return self

    # -- reporting ---------------------------------------------------------

    def counts(self) -> dict[str, Any]:
        """A summary that reads the same in a test assertion and in the CLI."""
        by_label: dict[str, int] = {}
        for node in self.nodes.values():
            for label in node.labels:
                by_label[label] = by_label.get(label, 0) + 1
        by_type: dict[str, int] = {}
        for edge in self.edges.values():
            by_type[edge.type] = by_type.get(edge.type, 0) + 1
        return {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "by_label": dict(sorted(by_label.items())),
            "by_relationship": dict(sorted(by_type.items())),
        }

    def to_dict(self) -> dict[str, Any]:
        """The whole batch as JSON — what `graph load --dry-run --out` writes."""
        return {
            "counts": self.counts(),
            "nodes": [
                {"uid": n.uid, "labels": list(n.labels), "props": n.props}
                for n in sorted(self.nodes.values(), key=lambda n: n.uid)
            ],
            "edges": [
                {
                    "type": e.type,
                    "start": e.start,
                    "end": e.end,
                    "props": e.props,
                    "key_props": list(e.key_props),
                }
                for e in sorted(
                    self.edges.values(), key=lambda e: (e.type, e.start, e.end)
                )
            ],
        }
