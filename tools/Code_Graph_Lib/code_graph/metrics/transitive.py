"""Interprocedural loop nesting: how deep loops get once calls are followed.

TLD(f) = max(loop_depth(f), max over calls f->g of in_loop_depth(call) + TLD(g)).
Recursion makes the call graph cyclic, so it is condensed into strongly connected
components first (iterative Tarjan) and solved in reverse topological order; a call
inside a component counts the callee's own loop depth without recursing. Capped so
a pathological graph cannot report absurd depths.
"""

from __future__ import annotations

from typing import Iterable

__all__ = ["transitive_loop_depth"]


def _tarjan(nodes: list[str], adj: dict[str, list[str]]) -> dict[str, int]:
    """Node -> component index, components numbered in reverse topological order."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    comp: dict[str, int] = {}
    counter = 0
    n_comp = 0
    for root in nodes:
        if root in index:
            continue
        work = [(root, 0)]
        while work:
            node, i = work.pop()
            if i == 0:
                index[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            recursed = False
            succ = adj.get(node, [])
            while i < len(succ):
                nxt = succ[i]
                i += 1
                if nxt not in index:
                    work.append((node, i))
                    work.append((nxt, 0))
                    recursed = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if recursed:
                continue
            if low[node] == index[node]:
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp[w] = n_comp
                    if w == node:
                        break
                n_comp += 1
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
    return comp


def transitive_loop_depth(
    loop_depth: dict[str, int], calls: Iterable[tuple[str, str, int]], *, cap: int = 10
) -> dict[str, int]:
    """`calls` are (caller, callee, in_loop_depth); only functions in `loop_depth` count."""
    adj: dict[str, list[str]] = {}
    weights: dict[str, list[tuple[str, int]]] = {}
    for caller, callee, depth in calls:
        if caller in loop_depth and callee in loop_depth:
            adj.setdefault(caller, []).append(callee)
            weights.setdefault(caller, []).append((callee, depth))
    nodes = sorted(loop_depth)
    comp = _tarjan(nodes, adj)
    # Tarjan emits sinks first, so ascending component index is a valid solve order.
    by_comp: dict[int, list[str]] = {}
    for node in nodes:
        by_comp.setdefault(comp[node], []).append(node)
    result: dict[str, int] = {}
    for c in sorted(by_comp):
        members = by_comp[c]
        for node in members:
            best = loop_depth[node]
            for callee, depth in weights.get(node, []):
                if comp[callee] == c:
                    best = max(best, depth + loop_depth[callee])
                else:
                    best = max(best, depth + result[callee])
            result[node] = min(best, cap)
    return result
