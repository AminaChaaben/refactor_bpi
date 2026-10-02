"""Metrics CGC does not compute, attached to the batch before it is loaded.

Working-tree files that exist as `File` nodes are parsed once each; every CGC
`Function` node is matched to its syntax-tree function by (start line, name), the
metrics land on the node, call sites give each `CALLS` edge its `in_loop_depth`, and
the whole-graph passes (transitive loop depth, similarity) run last.

`cyclomatic_complexity` is overwritten for every language so one definition applies
everywhere (CGC writes 1 for every Java function).
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from connection_sources.graph.model import GraphBatch, GraphEdge

from ..schema import CODE_SCHEMA
from .functions import FnResult, analyze_source
from .lang import Lang, lang_for_path
from .similarity import FunctionTokens, similar_pairs
from .transitive import transitive_loop_depth

__all__ = ["METRICS_VERSION", "compute_metrics"]

METRICS_VERSION = 2

# Receivers under which a call to a function's own name is a call to itself.
_SELF_RECEIVERS = frozenset({"this", "self", "cls"})


def _is_self_call(full_call_name: str, qualified_name: str) -> bool:
    """Whether a CALLS self-loop from CGC is real recursion.

    CGC resolves a call by name, so `webElement.click()` inside `click` becomes a
    self-loop. Only a call with no receiver, or with `this`/`self`/`cls` or the
    method's own class as the receiver, calls itself.
    """
    if "." not in full_call_name:
        return True
    receiver = full_call_name.rsplit(".", 1)[0].rsplit(".", 1)[-1]
    owner = qualified_name.rsplit(".", 2)[-2] if qualified_name.count(".") >= 1 else ""
    return receiver in _SELF_RECEIVERS or (bool(owner) and receiver == owner)


def _qualified_name(path: str, lang: Lang, package: str | None, fn: FnResult) -> str:
    if lang.name == "java":
        prefix = package or ""
    else:
        stem = path.rsplit(".", 1)[0]
        if lang.name == "python" and stem.endswith("/__init__"):
            stem = stem[: -len("/__init__")]
        prefix = stem.replace("/", ".")
    parts = [p for p in (prefix, *fn.class_names, fn.name) if p]
    return ".".join(parts)


def _match(cgc: dict[str, Any], results: list[FnResult], used: set[int]) -> FnResult | None:
    line = cgc.get("line_number")
    name = cgc.get("name")
    if not isinstance(line, int):
        return None
    same_line = [r for r in results if r.start_line == line and id(r) not in used]
    named = [r for r in same_line if r.name == name]
    if named:
        return named[0]
    if len(same_line) == 1:
        return same_line[0]
    near = sorted(
        (r for r in results if r.name == name and abs(r.start_line - line) <= 3 and id(r) not in used),
        key=lambda r: abs(r.start_line - line),
    )
    if near:
        return near[0]
    inside = [r for r in results if r.name == name and r.start_line <= line <= r.end_line and id(r) not in used]
    return inside[0] if inside else None


def compute_metrics(batch: GraphBatch, repo_root: str | Path, *, similarity: bool = True) -> dict[str, Any]:
    root = Path(repo_root)
    functions_by_path: dict[str, list] = defaultdict(list)
    for node in batch.nodes.values():
        if node.labels and node.labels[0] == "Function" and isinstance(node.props.get("path"), str):
            functions_by_path[node.props["path"]].append(node)

    report: dict[str, Any] = {
        "files_parsed": 0,
        "functions_matched": 0,
        "functions_unmatched": 0,
        "parse_errors": [],
        "by_language": {},
        "similar_pairs": 0,
        "metrics_version": METRICS_VERSION,
    }
    call_sites: dict[str, list[tuple[int, str, int]]] = {}
    loop_depth: dict[str, int] = {}
    token_sets: list[FunctionTokens] = []

    for path, nodes in sorted(functions_by_path.items()):
        lang = lang_for_path(path)
        if lang is None:
            continue
        stats = report["by_language"].setdefault(lang.name, {"matched": 0, "unmatched": 0})
        try:
            source = (root / path).read_bytes()
            results, package = analyze_source(source, lang)
        except Exception as exc:  # noqa: BLE001 - one bad file must not sink the index
            report["parse_errors"].append({"path": path, "error": str(exc)[:200]})
            report["functions_unmatched"] += len(nodes)
            stats["unmatched"] += len(nodes)
            continue
        report["files_parsed"] += 1
        used: set[int] = set()
        for node in sorted(nodes, key=lambda n: (n.props.get("line_number") or 0, n.uid)):
            fn = _match(node.props, results, used)
            if fn is None:
                report["functions_unmatched"] += 1
                stats["unmatched"] += 1
                node.props["metrics_matched"] = False
                continue
            used.add(id(fn))
            report["functions_matched"] += 1
            stats["matched"] += 1
            node.props.update(
                {
                    "cyclomatic_complexity": fn.cyclomatic,
                    "cognitive": fn.cognitive,
                    "loop_depth": fn.loop_depth,
                    "param_count": fn.param_count,
                    "max_access_depth": fn.max_access_depth,
                    "alloc_in_loop": fn.alloc_in_loop,
                    "linear_scan_in_loop": fn.linear_scan_in_loop,
                    "recursive": fn.recursive,
                    "recursion_in_loop": fn.recursion_in_loop,
                    "unguarded_recursion": fn.unguarded_recursion,
                    "qualified_name": _qualified_name(path, lang, package, fn),
                    "end_line": node.props.get("end_line") or fn.end_line,
                    "metrics_matched": True,
                    "metrics_version": METRICS_VERSION,
                }
            )
            call_sites[node.uid] = fn.call_sites
            loop_depth[node.uid] = fn.loop_depth
            token_sets.append(FunctionTokens(node.uid, path, fn.start_line, fn.end_line, tuple(fn.tokens)))

    # Call-site loop depth on CALLS, and recursion CGC resolved but the AST pass could not.
    function_uids = {n.uid for nodes in functions_by_path.values() for n in nodes}
    weighted: list[tuple[str, str, int]] = []
    misresolved: list[tuple] = []
    for key, edge in batch.edges.items():
        if edge.type != "CALLS":
            continue
        sites = call_sites.get(edge.start, [])
        line = edge.props.get("line_number")
        tail = str(edge.props.get("full_call_name") or "").rsplit(".", 1)[-1]
        exact = [d for (l, t, d) in sites if l == line and t == tail]
        on_line = [d for (l, _, d) in sites if l == line]
        depth = exact[0] if exact else (max(on_line) if on_line else 0)
        edge.props["in_loop_depth"] = depth
        if edge.start == edge.end and edge.start in batch.nodes:
            node = batch.nodes[edge.start]
            if _is_self_call(str(edge.props.get("full_call_name") or ""), str(node.props.get("qualified_name") or "")):
                node.props["recursive"] = True
            else:
                misresolved.append(key)
                continue
        if edge.start in function_uids and edge.end in function_uids:
            weighted.append((edge.start, edge.end, depth))

    for key in misresolved:
        del batch.edges[key]
    report["dropped_self_calls"] = len(misresolved)

    all_depths = {uid: loop_depth.get(uid, 0) for uid in function_uids}
    for uid, tld in transitive_loop_depth(all_depths, weighted).items():
        batch.nodes[uid].props["transitive_loop_depth"] = tld

    if similarity:
        pairs = similar_pairs(token_sets)
        for a, b, jaccard, same_file in pairs:
            batch.add_edge(
                GraphEdge(
                    "SIMILAR_TO",
                    a,
                    b,
                    {"jaccard": jaccard, "same_file": same_file, "method": "bottomk-minhash-5"},
                    schema=CODE_SCHEMA,
                )
            )
        report["similar_pairs"] = len(pairs)
    return report
