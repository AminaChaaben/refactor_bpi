from __future__ import annotations

import pytest

from code_graph.queries import CODE_QUERIES, REQUIRED_ANY, build, catalogue

_WRITE = ("CREATE", "MERGE", "DELETE", "SET ", "REMOVE", "DROP")


def _args(name):
    return {k: "x" for k in REQUIRED_ANY.get(name, ())[:1]}


@pytest.mark.parametrize("name", sorted(CODE_QUERIES))
def test_every_query_is_scoped_read_only_and_builds(name):
    statement, params = build(name, "code:local:pid-r:", **_args(name))
    assert ":CodeNode" in statement and "AlmNode" not in statement
    assert "STARTS WITH $prefix" in statement
    assert not any(w in statement.upper() for w in _WRITE)
    for placeholder in {p for p in __import__("re").findall(r"\$([a-z_]+)", statement)}:
        assert placeholder in params, f"{name}: ${placeholder} has no value"


def test_narrowing_required():
    with pytest.raises(KeyError):
        build("callers", "p:")
    _, params = build("callers", "p:", name="f", depth=9)
    assert params["depth"] == 5


def test_catalogue_lists_params():
    rows = {r["name"]: r for r in catalogue()}
    assert rows["callers"]["requires_one_of"] == ["uid", "qualified_name", "name"]
    assert "min_cognitive" in rows["complexity-hotspots"]["params"]
