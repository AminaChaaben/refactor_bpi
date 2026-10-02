"""Pins every Cypher statement the ALM graph sends, byte for byte.

The loader is being generalised so a second graph (the code graph) can reuse it.
This snapshot is what proves the ALM output did not move while that happened.
Regenerate only on purpose: UPDATE_GOLDEN=1 pytest tests/graph/test_statements_golden.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from connection_sources.graph import cypher, loader, queries
from connection_sources.graph.model import GraphBatch, GraphEdge, GraphNode, Namespace

GOLDEN = Path(__file__).with_name("golden_statements.json")


def _batch() -> GraphBatch:
    ns = Namespace.make("jira", "example.atlassian.net", "PROJ", platform="pid-1")
    story = ns.uid("issue", "PROJ-1")
    test = ns.uid("issue", "PROJ-2")
    user = ns.uid("user", "acc-1")
    batch = GraphBatch()
    batch.add_all(
        nodes=[
            GraphNode(story, ("Issue", "Story"), {"key": "PROJ-1", "title": "Login", "stub": False}),
            GraphNode(test, ("Issue", "Test"), {"key": "PROJ-2", "tags": ["a", "b"], "nested": {"x": 1}}),
            GraphNode(user, ("User",), {"display_name": "Ann"}),
            # Unknown label: must fall back, never reach Cypher as-is.
            GraphNode(ns.uid("issue", "PROJ-3"), ("NotALabel",), {"key": "PROJ-3"}),
            GraphNode(ns.uid("issue", "PROJ-4"), ("Issue", "Story"), {"key": "PROJ-4"}),
        ],
        edges=[
            GraphEdge("COVERS", test, story),
            GraphEdge("ASSIGNED_TO", story, user),
            GraphEdge("LINKED_TO", story, test, {"link_type": "relates"}, key_props=("link_type",)),
            # Unknown relationship type: must fall back too.
            GraphEdge("NOT_A_REL", story, user),
        ],
    )
    return batch


def _recorder(pages: list[int]):
    calls: list[dict] = []
    remaining = list(pages)

    def run(statement, params):
        calls.append({"statement": statement, "params": params})
        deleted = remaining.pop(0) if remaining else 0
        return [{"deleted": deleted}]

    return run, calls


def _snapshot() -> dict:
    batch = _batch()
    planned = [
        {"stage": stage, "statement": statement, "params": params}
        for stage, statement, params in loader.plan_statements(
            batch, version=7, seen_at="2026-01-01T00:00:00Z", batch_size=2
        )
    ]
    ns = Namespace.make("jira", "example.atlassian.net", "PROJ", platform="pid-1")
    run, prune_calls = _recorder([2, 1, 3, 0])
    pruned = loader.prune(run, ns, version=7, relationships=True, page=2)
    load_run, load_calls = _recorder([])
    result = loader.load_batch(batch, load_run, version=7, seen_at="2026-01-01T00:00:00Z", batch_size=2)
    return {
        "planned": planned,
        "prune_calls": prune_calls,
        "pruned": list(pruned),
        "load_result": result.to_dict(),
        "load_statement_count": len(load_calls),
        "batch": batch.to_dict(),
        "constraints": cypher.constraint_statements(),
        "label_counts": list(cypher.label_counts(ns.prefix)),
        "relationship_counts": list(cypher.relationship_counts(ns.prefix)),
        "delete_all": list(cypher.delete_all(ns.prefix, limit=10)),
        "queries": {name: list(queries.build(name, ns.prefix)) for name in sorted(queries.QUERIES)},
        "catalogue": queries.catalogue(),
    }


def test_alm_statements_are_unchanged():
    actual = json.loads(json.dumps(_snapshot(), sort_keys=True, default=str))
    if os.environ.get("UPDATE_GOLDEN") == "1" or not GOLDEN.exists():
        GOLDEN.write_text(json.dumps(actual, indent=1, sort_keys=True), encoding="utf-8")
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert actual == expected
