"""Sending a batch to Neo4j — and the seam that lets that be tested without one.

Everything here goes through a `Runner`: a callable taking a statement and its
parameters and returning the rows. A real run adapts a driver session into one; a
test passes a recorder that keeps the statements and applies MERGE semantics to a
dictionary. That seam is the whole reason the load path can be verified with no
database on the machine.

The load is ordered — schema, then nodes, then relationships — and the order is not
cosmetic. The constraint has to exist before the first MERGE or every merge is a
label scan. Nodes have to exist before the relationships that connect them, or every
relationship statement pays to create the stubs the node pass was about to fill in
properly.

Writes are batched by *signature* rather than simply chunked. Cypher cannot take a
label or a relationship type from a parameter, so one statement can only ever serve
rows that share them; grouping first and chunking within each group is what turns
several thousand individual writes into a few dozen statements.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from ..errors import ConnectionSourceError, SourcesConfigError, TransportError
from . import cypher
from .config import GraphConfig
from .model import GraphBatch, GraphEdge, GraphNode, Namespace
from .ontology import ALM_SCHEMA
from .schema import GraphSchema

__all__ = [
    "LoadResult",
    "Runner",
    "load_batch",
    "open_session",
    "plan_statements",
    "prune",
    "session_runner",
]

# A statement, its parameters, and the rows it returned.
Runner = Callable[[str, Mapping[str, Any]], list[dict[str, Any]]]


@dataclass
class LoadResult:
    """What one load sent and what came back."""

    version: int
    dry_run: bool = False
    nodes_merged: int = 0
    relationships_merged: int = 0
    statements: int = 0
    pruned_nodes: int = 0
    pruned_relationships: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "dry_run": self.dry_run,
            "nodes_merged": self.nodes_merged,
            "relationships_merged": self.relationships_merged,
            "statements": self.statements,
            "pruned_nodes": self.pruned_nodes,
            "pruned_relationships": self.pruned_relationships,
            "errors": self.errors,
        }


def _chunks(rows: Sequence[Any], size: int) -> Iterator[list[Any]]:
    for start in range(0, len(rows), size):
        yield list(rows[start : start + size])


def _node_rows(nodes: Iterable[GraphNode]) -> dict[tuple[str, ...], list[dict[str, Any]]]:
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for node in nodes:
        grouped.setdefault(node.signature, []).append(
            {"uid": node.uid, "props": {**node.props, "uid": node.uid}}
        )
    return grouped


def _edge_rows(edges: Iterable[GraphEdge]) -> dict[tuple, list[dict[str, Any]]]:
    grouped: dict[tuple, list[dict[str, Any]]] = {}
    for edge in edges:
        grouped.setdefault(edge.signature, []).append(
            {
                "start": edge.start,
                "end": edge.end,
                "props": edge.props,
                "keys": {name: edge.props.get(name) for name in edge.key_props},
            }
        )
    return grouped


def _check_schema(batch: GraphBatch, schema: GraphSchema) -> None:
    """A batch is written under one marker; a node from another graph is a caller bug."""
    for item in (*batch.nodes.values(), *batch.edges.values()):
        if item.schema != schema:
            raise ValueError(
                f"graph: batch item of graph {item.schema.name!r} loaded as {schema.name!r}"
            )


def plan_statements(
    batch: GraphBatch,
    *,
    version: int,
    seen_at: str,
    batch_size: int = 500,
    schema: GraphSchema = ALM_SCHEMA,
) -> Iterator[tuple[str, str, dict[str, Any]]]:
    """Every statement a load would send, as (stage, statement, params).

    A generator rather than a list so `--dry-run` and the real load walk exactly the
    same code — a dry run that builds its statements a second way is a dry run that
    can disagree with the thing it is meant to predict.
    """
    _check_schema(batch, schema)
    for statement in cypher.constraint_statements(schema=schema):
        yield "schema", statement, {}

    for signature, rows in sorted(_node_rows(batch.nodes.values()).items()):
        for chunk in _chunks(rows, batch_size):
            statement, params = cypher.node_merge(
                signature, chunk, version=version, seen_at=seen_at, schema=schema
            )
            yield "nodes", statement, params

    for signature, rows in sorted(_edge_rows(batch.edges.values()).items()):
        rel_type, key_props = signature
        for chunk in _chunks(rows, batch_size):
            statement, params = cypher.edge_merge(
                rel_type, key_props, chunk, version=version, schema=schema
            )
            yield "relationships", statement, params


def load_batch(
    batch: GraphBatch,
    run: Runner,
    *,
    version: int,
    seen_at: str,
    batch_size: int = 500,
    schema: GraphSchema = ALM_SCHEMA,
) -> LoadResult:
    """Write a batch through `run`, one statement at a time."""
    result = LoadResult(version=version)
    for stage, statement, params in plan_statements(
        batch, version=version, seen_at=seen_at, batch_size=batch_size, schema=schema
    ):
        run(statement, params)
        result.statements += 1
        rows = params.get("rows") or []
        if stage == "nodes":
            result.nodes_merged += len(rows)
        elif stage == "relationships":
            result.relationships_merged += len(rows)
    return result


def prune(
    run: Runner,
    namespace: Namespace,
    *,
    version: int,
    relationships: bool = False,
    page: int = 1000,
    max_pages: int = 1000,
    schema: GraphSchema = ALM_SCHEMA,
) -> tuple[int, int]:
    """Sweep away what this run did not confirm, paging until nothing is left.

    Refuses an unscoped namespace outright: a `system:site:` sweep with no project
    scope reaches every project this Neo4j instance holds for that system and site,
    and a build that could not derive a scope is exactly the build that must not be
    trusted to know what it is deleting.

    `max_pages` is a stop, not a tuning knob: if a bug made the delete statement a
    no-op the loop would otherwise spin forever against a live database, and an
    unbounded loop inside a scheduled job is the kind of thing that is discovered
    from the disk graph rather than from the logs.
    """
    if not namespace.resolved:
        raise SourcesConfigError(
            f"graph: refusing to prune an unscoped namespace ({namespace.prefix})",
            server="neo4j",
            remediation=(
                "the source that produced this namespace has no project_key/project/"
                "project_id/space_key configured, so a sweep cannot be scoped to it "
                "without risking another project's nodes on the same system+site"
            ),
        )
    prefix = namespace.prefix
    deleted_nodes = 0
    deleted_rels = 0

    if relationships:
        for _ in range(max_pages):
            statement, params = cypher.prune_relationships(
                prefix, version=version, limit=page
            )
            rows = run(statement, params)
            count = int(rows[0].get("deleted", 0)) if rows else 0
            deleted_rels += count
            if count < page:
                break

    for _ in range(max_pages):
        statement, params = cypher.prune_nodes(prefix, version=version, limit=page, schema=schema)
        rows = run(statement, params)
        count = int(rows[0].get("deleted", 0)) if rows else 0
        deleted_nodes += count
        if count < page:
            break

    return deleted_nodes, deleted_rels


def session_runner(session: Any) -> Runner:
    """Adapt a Neo4j driver session into a `Runner`.

    A statement mid-batch can fail for reasons that have nothing to do with the
    batch — the connection drops, the server restarts, a constraint the caller never
    declared rejects a write — and the driver's own exception hierarchy is not a
    `ConnectionSourceError`. Left unwrapped, neither `run_build`'s catch nor the
    CLI's ever sees it, and a load that failed mid-write looks like a Python crash
    instead of a reported, typed error.
    """

    def run(statement: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        try:
            result = session.run(statement, dict(params))
            return [dict(record) for record in result]
        except ConnectionSourceError:
            raise
        except Exception as exc:  # noqa: BLE001 - the driver raises a wide family here
            raise TransportError(
                f"graph: Neo4j write failed: {exc}",
                server="neo4j",
                retryable=False,
            ) from exc

    return run


@contextmanager
def open_session(config: GraphConfig) -> Iterator[Any]:
    """A Neo4j session for this project, or a typed error explaining what is missing.

    The driver is imported here rather than at module import time so that every
    other part of the graph package — the extractors, the ontology, the statement
    builders and their tests — works with no `neo4j` installed at all. Only actually
    talking to a database needs it.
    """
    config.require_credentials()
    try:
        from neo4j import GraphDatabase  # noqa: PLC0415 — optional dependency
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ConnectionSourceError(
            "graph: the neo4j driver is not installed",
            server="neo4j",
            remediation="uv pip install 'neo4j>=5.20'  (or: pip install '.[graph]')",
        ) from exc

    driver = GraphDatabase.driver(config.uri, auth=(config.user, config.password))
    try:
        driver.verify_connectivity()
    except Exception as exc:  # noqa: BLE001 - the driver raises a wide family here
        driver.close()
        raise TransportError(
            f"graph: cannot reach Neo4j at {config.uri}: {exc}",
            server="neo4j",
            remediation=(
                "start Neo4j (docker run -p 7687:7687 -p 7474:7474 "
                "-e NEO4J_AUTH=neo4j/<password> neo4j:5) and check NEO4J_URI"
            ),
            retryable=True,
        ) from exc

    session = driver.session(database=config.database)
    try:
        yield session
    finally:
        session.close()
        driver.close()
