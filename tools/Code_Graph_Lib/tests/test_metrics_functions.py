from __future__ import annotations

from code_graph.metrics.functions import analyze_source
from code_graph.metrics.lang import LANGS

JAVA = b"""package p.q;
class C {
  int fact(int n) { if (n <= 1) { return 1; } return n * fact(n - 1); }
  int bad(int n) { return bad(n + 1); }
  void loops(List<String> xs, List<String> ys) {
    for (String x : xs) {
      for (String y : ys) {
        if (ys.contains(x) && x != null) { list.add(new Foo()); }
      }
    }
  }
  int sw(int x) { switch (x) { case 1: return 1; case 2: return 2; default: return 0; } }
  void chain() { a.b().c().d(); }
  void recInLoop(int n) { for (int i = 0; i < n; i++) { recInLoop(i); } }
  void branches(int a) { if (a > 0) { } else if (a < 0) { } else { } }
}
"""


def _by_name(results):
    return {r.name: r for r in results}


def test_java_metrics():
    results, package = analyze_source(JAVA, LANGS["java"])
    assert package == "p.q"
    f = _by_name(results)
    assert f["fact"].cyclomatic == 2 and f["fact"].cognitive == 2
    assert f["fact"].recursive and not f["fact"].unguarded_recursion
    assert f["bad"].recursive and f["bad"].unguarded_recursion and f["bad"].cognitive == 1
    lo = f["loops"]
    assert (lo.cyclomatic, lo.cognitive, lo.loop_depth) == (5, 7, 2)
    assert lo.linear_scan_in_loop == 1 and lo.alloc_in_loop == 1 and lo.param_count == 2
    assert f["sw"].cyclomatic == 3 and f["sw"].cognitive == 1
    assert f["chain"].max_access_depth == 3
    rl = f["recInLoop"]
    assert rl.recursive and rl.recursion_in_loop and not rl.unguarded_recursion and rl.loop_depth == 1
    # if / else if / else: +1 (nesting 0), +1, +1
    assert f["branches"].cyclomatic == 3 and f["branches"].cognitive == 3
    assert f["fact"].class_names == ("C",)
    assert all(t in ("ID", "NUM") or t for t in f["fact"].tokens)


PY = b"""class K:
    def f(self, a, b=1, *args, **kw):
        if a and b or kw:
            x = [i for i in a if i in b]
        elif a:
            return self.f(a)
        else:
            pass
        return 0

def g(n):
    while n:
        n = n - 1
        items = []
        Thing()
"""


def test_python_metrics():
    results, _ = analyze_source(PY, LANGS["python"])
    f = _by_name(results)["f"]
    assert f.param_count == 4
    assert f.cyclomatic == 7
    assert f.cognitive == 8
    assert f.loop_depth == 1 and f.linear_scan_in_loop == 1
    assert f.recursive and not f.unguarded_recursion
    g = _by_name(results)["g"]
    assert g.loop_depth == 1 and g.alloc_in_loop == 2  # [] literal + Thing()


TS = b"""class A {
  m(xs: number[]) {
    xs.forEach(v => { if (v) { g(v); } });
    const o = { a: 1 };
    return a?.b.c();
  }
}
function fib(n) { return n < 2 ? n : fib(n - 1) + fib(n - 2); }
const h = (z) => z ?? 1;
"""


def test_typescript_metrics():
    results, _ = analyze_source(TS, LANGS["typescript"])
    f = _by_name(results)
    assert set(f) == {"m", "fib", "h"}  # the forEach callback is part of m
    m = f["m"]
    assert m.loop_depth == 1 and m.cyclomatic == 2 and m.cognitive == 2
    assert m.max_access_depth == 2 and m.param_count == 1 and m.alloc_in_loop == 0
    fib = f["fib"]
    assert fib.recursive and not fib.unguarded_recursion
    assert fib.cyclomatic == 2 and fib.cognitive == 3
    assert f["h"].cyclomatic == 2 and f["h"].cognitive == 1
