from __future__ import annotations

import pytest

from connection_sources.graph import cypher, loader
from connection_sources.graph.model import GraphBatch, GraphEdge, GraphNode, Namespace
from connection_sources.graph.ontology import ALM_SCHEMA
from connection_sources.graph.schema import GraphSchema

OTHER = GraphSchema(
    name="code",
    marker="CodeNode",
    labels=frozenset({"Function", "File", "CodeSymbol"}),
    relationships=frozenset({"CALLS", "CONTAINS", "RELATED_TO"}),
    fallback_label="CodeSymbol",
    fallback_relationship="RELATED_TO",
    index_prefix="code_node",
    indexed_props=("name", "path"),
)


def test_second_schema_uses_its_own_marker_and_names():
    ns = Namespace.make("code", "local", "repo", platform="pid")
    f = ns.uid("function", "a.py#f@1")
    batch = GraphBatch().add_all(
        nodes=[GraphNode(f, ("Function", "Story"), {"name": "f"}, schema=OTHER)],
        edges=[GraphEdge("CALLS", f, f, schema=OTHER), GraphEdge("COVERS", f, f, schema=OTHER)],
    )
    statements = [s for _, s, _ in loader.plan_statements(batch, version=1, seen_at="t", schema=OTHER)]
    text = "\n".join(statements)
    assert "AlmNode" not in text
    assert "CREATE CONSTRAINT code_node_uid IF NOT EXISTS FOR (n:CodeNode) REQUIRE n.uid IS UNIQUE" in text
    assert "CREATE INDEX code_node_name IF NOT EXISTS" in text
    # Unknown label/relationship fall back inside the code vocabulary, not the ALM one.
    assert "SET n:CodeNode:CodeSymbol:Function" in text
    assert "[r:RELATED_TO]" in text and "[r:CALLS]" in text


def test_mixing_graphs_in_one_batch_is_refused():
    ns = Namespace.make("code", "local", "repo", platform="pid")
    batch = GraphBatch().add_all(nodes=[GraphNode(ns.uid("function", "x"), ("Function",), schema=OTHER)])
    with pytest.raises(ValueError):
        list(loader.plan_statements(batch, version=1, seen_at="t", schema=ALM_SCHEMA))


def test_merge_across_graphs_is_refused():
    a = GraphNode("u", ("Function",), schema=OTHER)
    b = GraphNode("u", ("Issue",))
    with pytest.raises(ValueError):
        a.merged_with(b)


def test_prune_is_marker_scoped():
    statement, _ = cypher.prune_nodes("code:local:pid-repo:", version=3, schema=OTHER)
    assert statement.startswith("MATCH (n:CodeNode)")


def test_unsafe_schema_identifiers_rejected():
    with pytest.raises(ValueError):
        GraphSchema("x", "Bad Marker", frozenset({"A"}), frozenset({"R"}), "A", "R", "x")


def test_unsafe_fulltext_property_rejected():
    with pytest.raises(ValueError):
        GraphSchema("x", "M", frozenset({"A"}), frozenset({"R"}), "A", "R", "x", fulltext_props=("a b",))


def test_alm_schema_creates_its_fulltext_index():
    statements = cypher.constraint_statements()
    assert (
        "CREATE FULLTEXT INDEX alm_node_text IF NOT EXISTS FOR (n:AlmNode) "
        "ON EACH [n.title, n.description, n.acceptance_criteria]"
    ) in statements
    assert ALM_SCHEMA.fulltext_index == "alm_node_text"


def test_schema_without_fulltext_props_creates_no_fulltext_index():
    assert not any("FULLTEXT" in s for s in OTHER.constraint_statements())


def test_alm_schema_creates_the_tests_fulltext_index_on_the_test_label():
    statements = cypher.constraint_statements()
    index = [s for s in statements if "alm_node_test_text" in s]
    assert index == [
        "CREATE FULLTEXT INDEX alm_node_test_text IF NOT EXISTS FOR (n:Test) "
        "ON EACH [n.title, n.gherkin, n.unstructured, n.steps_text, n.test_steps, "
        "n.manual_steps, n.cucumber_script]"
    ]
    assert ALM_SCHEMA.test_fulltext_index == "alm_node_test_text"


def test_schema_without_test_fulltext_props_creates_no_test_index():
    assert not any("_test_text" in s for s in OTHER.constraint_statements())


def test_unsafe_test_fulltext_label_rejected():
    with pytest.raises(ValueError):
        GraphSchema("x", "M", frozenset({"A"}), frozenset({"R"}), "A", "R", "x",
                    test_fulltext_label="T est", test_fulltext_props=("a",))
