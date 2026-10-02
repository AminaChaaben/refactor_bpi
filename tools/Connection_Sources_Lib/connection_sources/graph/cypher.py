"""Every Cypher statement the loader sends, built as pure text plus parameters.

Kept apart from the loader for the same reason `diff` is kept apart from `runner`:
a statement builder that returns a string can be asserted on in a test, and the
`--dry-run` path can print exactly what a real run would send without a database
existing anywhere.

Three decisions are baked into these statements.

*One constraint covers everything.* Every node also carries `:AlmNode`, so a single
uniqueness constraint on `(:AlmNode {uid})` makes every MERGE in the file
index-backed. Neo4j Community has no composite node keys and no per-label
constraints worth having here, and without an index a MERGE degenerates into a scan
of the label — which is fine at eighteen issues and unusable at fifty thousand.

*Labels and relationship types are the only interpolated fragments.* Neo4j cannot
parameterise either. `ontology.safe_label` and `safe_relationship` have already
filtered them against a closed set by the time a `GraphNode` exists, and
`safe_property` does the same for the handful of property names that appear inside a
MERGE pattern. Everything else — every value, every uid — travels as a parameter.

*Both endpoints of an edge are MERGEd, never MATCHed.* An issue link can name an
issue outside the configured scope. With MATCH, that edge silently does not appear,
and a traceability graph that quietly drops links is worse than no graph. With MERGE
the far end is created as a stub and the link survives, visibly incomplete.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

from .ontology import ALM_SCHEMA
from .schema import SEEN_AT, SEEN_VERSION, GraphSchema

__all__ = [
    "constraint_statements",
    "delete_all",
    "edge_merge",
    "label_counts",
    "node_merge",
    "prune_nodes",
    "prune_relationships",
    "relationship_counts",
    "safe_property",
]

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def safe_property(name: str) -> str:
    """A property name safe to interpolate into a MERGE pattern.

    Only reached for `key_props`, which are written by the extractors rather than
    read off a tracker — but the check is cheap and this is the one other place a
    statement is assembled from a string, so it is guarded rather than trusted.
    """
    text = str(name)
    if not _IDENT.match(text):
        raise ValueError(f"unsafe property name for a Cypher pattern: {name!r}")
    return text


def _label_clause(labels: Sequence[str], schema: GraphSchema = ALM_SCHEMA) -> str:
    """`:AlmNode:Issue:Story` — the marker first, then the node's own labels."""
    marker = schema.marker
    checked = [marker] + [schema.safe_label(l) for l in labels if l != marker]
    return "".join(f":{name}" for name in dict.fromkeys(checked))


def constraint_statements(*, schema: GraphSchema = ALM_SCHEMA) -> list[str]:
    """The schema a graph needs before anything is written into it.

    `IF NOT EXISTS` throughout, so `graph init` is safe to run on every load rather
    than being a step somebody has to remember once and then never again.
    """
    return schema.constraint_statements()


def node_merge(
    labels: Sequence[str],
    rows: Iterable[Mapping[str, Any]],
    *,
    version: int,
    seen_at: str,
    schema: GraphSchema = ALM_SCHEMA,
) -> tuple[str, dict[str, Any]]:
    """MERGE one batch of nodes that share a label set.

    Grouped by label set because `SET n:Foo:Bar` cannot take its labels from a
    parameter, so one statement can only serve nodes carrying the same ones. The
    alternative — APOC's `apoc.create.addLabels` — would work in one statement but
    puts a plugin between this package and a stock Neo4j, which is a poor trade for
    a handful of extra round trips.

    `SET n += row.props` rather than `SET n = row.props`: a node may have been
    enriched by another pass (a derived coverage status, a run summary) and a wholesale
    replace would silently drop it.
    """
    statement = (
        "UNWIND $rows AS row\n"
        f"MERGE (n:{schema.marker} {{uid: row.uid}})\n"
        "SET n += row.props\n"
        f"SET n.{SEEN_VERSION} = $version, n.{SEEN_AT} = $seen_at\n"
        f"SET n{_label_clause(labels, schema)}"
    )
    return statement, {"rows": list(rows), "version": version, "seen_at": seen_at}


def edge_merge(
    rel_type: str,
    key_props: Sequence[str],
    rows: Iterable[Mapping[str, Any]],
    *,
    version: int,
    schema: GraphSchema = ALM_SCHEMA,
) -> tuple[str, dict[str, Any]]:
    """MERGE one batch of relationships that share a type and matching keys.

    `key_props` go *inside* the MERGE pattern and the rest are set afterwards. That
    distinction is what lets two issues be linked twice under different link types
    without the second write overwriting the first, while a re-run of the same link
    still updates one relationship instead of adding another.
    """
    keys = [safe_property(name) for name in key_props]
    pattern_props = (
        " {" + ", ".join(f"{name}: row.keys.{name}" for name in keys) + "}" if keys else ""
    )
    marker = schema.marker
    statement = (
        "UNWIND $rows AS row\n"
        f"MERGE (a:{marker} {{uid: row.start}})\n"
        "ON CREATE SET a.stub = true\n"
        f"MERGE (b:{marker} {{uid: row.end}})\n"
        "ON CREATE SET b.stub = true\n"
        f"MERGE (a)-[r:{schema.safe_relationship(rel_type)}{pattern_props}]->(b)\n"
        "SET r += row.props\n"
        f"SET r.{SEEN_VERSION} = $version"
    )
    return statement, {"rows": list(rows), "version": version}


def prune_nodes(
    prefix: str, *, version: int, limit: int = 1000, schema: GraphSchema = ALM_SCHEMA
) -> tuple[str, dict[str, Any]]:
    """Delete nodes this run did not confirm, one bounded page at a time.

    Scoped by uid prefix (`jira:site:`) so a graph holding several systems, or
    several sites, only ever sweeps the one that was just read — an unscoped sweep
    would delete another connector's nodes simply because this run did not touch
    them.

    Bounded because `DETACH DELETE` over an unbounded match holds one transaction
    open across the whole deletion, and a sweep large enough to matter is exactly
    the one large enough to exhaust the heap.
    """
    statement = (
        f"MATCH (n:{schema.marker})\n"
        "WHERE n.uid STARTS WITH $prefix\n"
        f"  AND coalesce(n.{SEEN_VERSION}, -1) < $version\n"
        "WITH n LIMIT $limit\n"
        "DETACH DELETE n\n"
        "RETURN count(*) AS deleted"
    )
    return statement, {"prefix": prefix, "version": version, "limit": limit}


def prune_relationships(
    prefix: str, *, version: int, limit: int = 5000
) -> tuple[str, dict[str, Any]]:
    """Delete relationships this run did not confirm.

    Separate from the node sweep because a relationship disappears for two very
    different reasons — somebody removed the link, or this run simply did not read
    the issue that carries it — and only a full read of the whole scope can tell
    those apart. The graph build always reads the full scope, so the runner asks
    for the relationship sweep alongside the node sweep; a caller with a partial
    read leaves it off.
    """
    statement = (
        "MATCH (a)-[r]->()\n"
        "WHERE a.uid STARTS WITH $prefix\n"
        f"  AND coalesce(r.{SEEN_VERSION}, -1) < $version\n"
        "WITH r LIMIT $limit\n"
        "DELETE r\n"
        "RETURN count(*) AS deleted"
    )
    return statement, {"prefix": prefix, "version": version, "limit": limit}


def delete_all(
    prefix: str, *, limit: int = 1000, schema: GraphSchema = ALM_SCHEMA
) -> tuple[str, dict[str, Any]]:
    """Remove one system+site's nodes entirely — what `graph load --reset` runs."""
    statement = (
        f"MATCH (n:{schema.marker})\n"
        "WHERE n.uid STARTS WITH $prefix\n"
        "WITH n LIMIT $limit\n"
        "DETACH DELETE n\n"
        "RETURN count(*) AS deleted"
    )
    return statement, {"prefix": prefix, "limit": limit}


def label_counts(prefix: str, *, schema: GraphSchema = ALM_SCHEMA) -> tuple[str, dict[str, Any]]:
    """How many nodes of each label the graph holds for this system+site."""
    marker = schema.marker
    statement = (
        f"MATCH (n:{marker})\n"
        "WHERE n.uid STARTS WITH $prefix\n"
        "UNWIND labels(n) AS label\n"
        f"WITH label WHERE label <> '{marker}'\n"
        "RETURN label, count(*) AS count\n"
        "ORDER BY count DESC, label"
    )
    return statement, {"prefix": prefix}


def relationship_counts(
    prefix: str, *, schema: GraphSchema = ALM_SCHEMA
) -> tuple[str, dict[str, Any]]:
    """How many relationships of each type, counted from their start node."""
    statement = (
        f"MATCH (a:{schema.marker})-[r]->()\n"
        "WHERE a.uid STARTS WITH $prefix\n"
        "RETURN type(r) AS type, count(*) AS count\n"
        "ORDER BY count DESC, type"
    )
    return statement, {"prefix": prefix}
