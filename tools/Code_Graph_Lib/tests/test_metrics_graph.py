from __future__ import annotations

from code_graph.metrics.similarity import FunctionTokens, similar_pairs
from code_graph.metrics.transitive import transitive_loop_depth


def test_tld_follows_calls_inside_loops():
    # f loops once and calls g inside that loop; g loops twice -> TLD(f) = 1 + 2.
    depths = {"f": 1, "g": 2, "h": 0}
    calls = [("f", "g", 1), ("h", "f", 0)]
    tld = transitive_loop_depth(depths, calls)
    assert tld == {"f": 3, "g": 2, "h": 3}


def test_tld_handles_cycles_and_caps():
    depths = {"a": 1, "b": 1}
    calls = [("a", "b", 1), ("b", "a", 1)]  # mutual recursion inside loops
    tld = transitive_loop_depth(depths, calls)
    assert tld == {"a": 2, "b": 2}
    chain = {str(i): 1 for i in range(30)}
    calls = [(str(i), str(i + 1), 1) for i in range(29)]
    assert transitive_loop_depth(chain, calls)["0"] == 10  # capped


def _fn(uid, path, start, end, body):
    return FunctionTokens(uid, path, start, end, tuple(body))


def test_similarity_finds_renamed_copies_only():
    body = ["def", "ID", "(", "ID", ")", ":", "for", "ID", "in", "ID", ":", "if", "ID", ">", "NUM", ":", "ID", ".", "ID", "(", "ID", ")"] * 3
    other = ["class", "ID", ":", "pass", "return", "STR", "while", "ID", ":", "ID", "+=", "NUM"] * 4
    fns = [
        _fn("u1", "a.py", 1, 10, body),
        _fn("u2", "b.py", 1, 10, body),           # exact renamed copy (tokens normalised)
        _fn("u3", "c.py", 1, 10, other),          # unrelated
        _fn("u4", "a.py", 2, 5, body),            # nested inside u1 -> skipped
        _fn("u5", "d.py", 1, 2, body[:10]),       # too short
    ]
    pairs = similar_pairs(fns)
    assert pairs == [("u1", "u2", 1.0, False), ("u2", "u4", 1.0, False)]
    assert similar_pairs(fns) == pairs  # deterministic


def test_self_call_needs_a_self_receiver():
    from code_graph.metrics import _is_self_call

    qn = "core.SeleniumFactory.click"
    assert _is_self_call("click", qn)
    assert _is_self_call("this.click", qn)
    assert _is_self_call("self.click", "pkg.mod.Factory.click")
    assert _is_self_call("SeleniumFactory.click", qn)
    assert not _is_self_call("webElement.click", qn)
    assert not _is_self_call("driver.findElement(By.id(x)).click", qn)
