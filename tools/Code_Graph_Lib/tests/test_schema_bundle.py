from __future__ import annotations

from connection_sources.graph.model import Namespace

from code_graph.bundle import map_bundle, relative_path
from code_graph.schema import CODE_LABELS, CODE_RELATIONSHIPS


def test_code_sets_cover_cgc_contract():
    from codegraphcontext.tools.indexing import schema_contract as contract

    assert contract.NODE_LABELS <= CODE_LABELS
    assert contract.RELATIONSHIP_TYPES <= CODE_RELATIONSHIPS


def test_relative_path():
    root = "C:/work/repo"
    assert relative_path("C:\\work\\repo\\src\\A.java", root) == "src/A.java"
    assert relative_path("C:/work/repo", root) == "."
    assert relative_path("/elsewhere/x", root) == "/elsewhere/x"


def _bundle():
    nodes = [
        {"_id": {"offset": 0, "table": 0}, "_labels": ["Repository"], "path": "/r", "name": "r", "commit_hash": "abc"},
        {"_id": {"offset": 0, "table": 1}, "_labels": ["File"], "path": "/r/a.py", "name": "a.py"},
        {"_id": {"offset": 0, "table": 4}, "_labels": ["Function"], "path": "/r/a.py", "name": "f", "line_number": 3, "occurrence_index": 0, "uid": "cgc-own", "embedding": [0.1]},
        {"_id": {"offset": 1, "table": 4}, "_labels": ["Function"], "path": "/r/a.py", "name": "f", "line_number": 3, "occurrence_index": 1},
        {"_id": {"offset": 0, "table": 9}, "_labels": ["Weird"], "path": "/r/a.py", "name": "w", "line_number": 9},
        {"_id": {"offset": 0, "table": 3}, "_labels": ["Module"], "name": "os"},
    ]
    edges = [
        {"from": {"offset": 0, "table": 1}, "to": {"offset": 0, "table": 4}, "type": "CONTAINS", "properties": {}},
        {"from": {"offset": 0, "table": 4}, "to": {"offset": 1, "table": 4}, "type": "CALLS", "properties": {"line_number": 4, "full_call_name": "f", "args_key": "[]", "confidence": 0.9}},
        {"from": {"offset": 0, "table": 4}, "to": {"offset": 1, "table": 4}, "type": "CALLS", "properties": {"line_number": 5, "full_call_name": "f", "args_key": "[]"}},
        {"from": {"offset": 0, "table": 1}, "to": {"offset": 0, "table": 3}, "type": "IMPORTS", "properties": {"line_number": 1, "imported_name": "os"}},
        {"from": {"offset": 7, "table": 7}, "to": {"offset": 0, "table": 3}, "type": "IMPORTS", "properties": {}},
    ]
    return nodes, edges


def test_map_bundle_identities_and_relinking():
    ns = Namespace.make("code", "gitlab.example", "grp/r", platform="pid")
    batch, report, _ = map_bundle(*_bundle(), namespace=ns, repo_root="/r")
    uids = set(batch.nodes)
    assert "code:gitlab.example:pid-grp/r:repo:." in uids
    assert "code:gitlab.example:pid-grp/r:file:a.py" in uids
    assert "code:gitlab.example:pid-grp/r:function:a.py#f@3" in uids
    assert "code:gitlab.example:pid-grp/r:function:a.py#f@3~1" in uids
    assert "code:gitlab.example:pid-grp/r:module:os" in uids
    fn = batch.nodes["code:gitlab.example:pid-grp/r:function:a.py#f@3"]
    assert "uid" not in fn.props  # CGC's own uid dropped; the loader writes ours
    assert "embedding" not in fn.props
    assert batch.nodes["code:gitlab.example:pid-grp/r:repo:."].props["head_sha"] == "abc"
    # Unknown label falls back inside the code vocabulary and keeps its origin.
    weird = batch.nodes["code:gitlab.example:pid-grp/r:weird:a.py#w@9"]
    assert weird.labels == ("CodeSymbol",) and weird.props["cgc_label"] == "Weird"
    # Two calls between the same functions on different lines stay two edges.
    assert sum(1 for e in batch.edges.values() if e.type == "CALLS") == 2
    assert report.dangling_edges == 1 and report.unknown_labels == {"Weird": 1}
