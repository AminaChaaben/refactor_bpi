"""The code graph's named, read-only questions — the only way agents query it.

Same contract as `connection_sources.graph.queries`: every query is read-only, starts
from `:CodeNode`, and is scoped to one repository by uid prefix, so it can never
return the ALM graph's nodes sharing the database. Columns use the names the skills
expect (`complexity`, `qualified_name`, ...).
"""

from __future__ import annotations

from typing import Any, Mapping

from connection_sources.graph.queries import Query, build_from, catalogue_of

__all__ = ["CODE_QUERIES", "DEFAULTS", "build", "catalogue"]

M = "CodeNode"
_IN = "WHERE {v}.uid STARTS WITH $prefix"

_FN_COLUMNS = (
    "f.qualified_name AS qualified_name, f.name AS name, f.path AS path,\n"
    "       f.line_number AS line, f.end_line AS end_line,\n"
    "       f.cyclomatic_complexity AS complexity, f.cognitive AS cognitive,\n"
    "       f.loop_depth AS loop_depth, f.transitive_loop_depth AS transitive_loop_depth,\n"
    "       f.linear_scan_in_loop AS linear_scan_in_loop, f.alloc_in_loop AS alloc_in_loop,\n"
    "       f.param_count AS param_count, f.max_access_depth AS max_access_depth,\n"
    "       f.recursive AS recursive, f.recursion_in_loop AS recursion_in_loop,\n"
    "       f.unguarded_recursion AS unguarded_recursion"
)

_TARGET = (
    "  AND ($uid IS NULL OR t.uid = $uid)\n"
    "  AND ($qualified_name IS NULL OR t.qualified_name = $qualified_name)\n"
    "  AND ($name IS NULL OR t.name = $name)\n"
    "  AND ($path IS NULL OR t.path = $path)\n"
)


def _q(name: str, description: str, cypher: str) -> tuple[str, Query]:
    return name, Query(name, description, cypher)


def _by_axis(axis: str, prop: str) -> tuple[str, Query]:
    return _q(
        f"complexity-by-{axis}",
        f"Functions ranked by {prop} (descending).",
        f"MATCH (f:{M}:Function) WHERE f.uid STARTS WITH $prefix AND f.{prop} IS NOT NULL\n"
        f"RETURN {_FN_COLUMNS}\n"
        f"ORDER BY f.{prop} DESC, qualified_name LIMIT $limit",
    )


CODE_QUERIES: dict[str, Query] = dict(
    [
        _q(
            "status",
            "The indexed repository: head commit, index time, metric version, totals.",
            f"MATCH (r:{M}:Repository) WHERE r.uid STARTS WITH $prefix\n"
            f"OPTIONAL MATCH (f:{M}:Function) WHERE f.uid STARTS WITH $prefix\n"
            "WITH r, count(f) AS functions\n"
            f"OPTIONAL MATCH (a:{M}:Function)-[c:CALLS]->() WHERE a.uid STARTS WITH $prefix\n"
            "RETURN r.name AS repo, r.head_sha AS head_sha, r.indexed_at AS indexed_at,\n"
            "       r.metrics_version AS metrics_version, r.cgc_version AS cgc_version,\n"
            "       functions, count(c) AS calls",
        ),
        _q(
            "summary",
            "Node and relationship totals for this repository.",
            f"MATCH (n:{M}) WHERE n.uid STARTS WITH $prefix\n"
            "WITH count(n) AS nodes\n"
            f"MATCH (a:{M})-[r]->() WHERE a.uid STARTS WITH $prefix\n"
            "RETURN nodes, count(r) AS relationships",
        ),
        _q(
            "labels",
            "How many nodes carry each label.",
            f"MATCH (n:{M}) WHERE n.uid STARTS WITH $prefix\n"
            "UNWIND labels(n) AS label\n"
            f"WITH label WHERE label <> '{M}'\n"
            "RETURN label, count(*) AS count ORDER BY count DESC, label",
        ),
        _q(
            "relationships",
            "How many relationships of each type.",
            f"MATCH (a:{M})-[r]->() WHERE a.uid STARTS WITH $prefix\n"
            "RETURN type(r) AS type, count(*) AS count ORDER BY count DESC, type",
        ),
        _q(
            "complexity-hotspots",
            "Functions over any complexity threshold (cyclomatic, cognitive, transitive loop depth, params, access depth).",
            f"MATCH (f:{M}:Function) WHERE f.uid STARTS WITH $prefix\n"
            "  AND (coalesce(f.cyclomatic_complexity, 0) >= $min_cyclomatic\n"
            "    OR coalesce(f.cognitive, 0) >= $min_cognitive\n"
            "    OR coalesce(f.transitive_loop_depth, 0) >= $min_tld\n"
            "    OR coalesce(f.param_count, 0) >= $min_params\n"
            "    OR coalesce(f.max_access_depth, 0) >= $min_access)\n"
            f"RETURN {_FN_COLUMNS}\n"
            "ORDER BY cognitive DESC, complexity DESC, qualified_name LIMIT $limit",
        ),
        _by_axis("cyclomatic", "cyclomatic_complexity"),
        _by_axis("cognitive", "cognitive"),
        _by_axis("loop-depth", "loop_depth"),
        _by_axis("tld", "transitive_loop_depth"),
        _by_axis("params", "param_count"),
        _by_axis("access-depth", "max_access_depth"),
        _q(
            "complexity-risks",
            "Hidden O(n^2) scans, unguarded or in-loop recursion, allocations in loops.",
            f"MATCH (f:{M}:Function) WHERE f.uid STARTS WITH $prefix\n"
            "  AND (coalesce(f.linear_scan_in_loop, 0) >= 1 OR f.unguarded_recursion = true\n"
            "    OR f.recursion_in_loop = true OR coalesce(f.alloc_in_loop, 0) >= $min_alloc)\n"
            f"RETURN {_FN_COLUMNS}\n"
            "ORDER BY linear_scan_in_loop DESC, qualified_name LIMIT $limit",
        ),
        _q(
            "similar-pairs",
            "Near-duplicate function pairs (SIMILAR_TO), strongest first.",
            f"MATCH (a:{M}:Function)-[s:SIMILAR_TO]->(b:{M}:Function)\n"
            "WHERE a.uid STARTS WITH $prefix AND s.jaccard >= $min_jaccard\n"
            "  AND ($same_file IS NULL OR s.same_file = $same_file)\n"
            "RETURN a.qualified_name AS a, a.path AS a_path, a.line_number AS a_line,\n"
            "       b.qualified_name AS b, b.path AS b_path, b.line_number AS b_line,\n"
            "       s.jaccard AS jaccard, s.same_file AS same_file\n"
            "ORDER BY jaccard DESC, a, b LIMIT $limit",
        ),
        _q(
            "callers",
            "Who calls a function, up to $depth hops (<=5), optionally only confident edges.",
            f"MATCH (t:{M}:Function) WHERE t.uid STARTS WITH $prefix\n{_TARGET}"
            f"MATCH p = (c:{M})-[:CALLS*1..5]->(t)\n"
            "WHERE length(p) <= $depth\n"
            "  AND all(r IN relationships(p) WHERE coalesce(r.confidence, 1.0) >= $min_confidence)\n"
            "RETURN t.qualified_name AS target, c.qualified_name AS caller, c.path AS path,\n"
            "       c.line_number AS line, min(length(p)) AS distance\n"
            "ORDER BY distance, caller LIMIT $limit",
        ),
        _q(
            "callees",
            "What a function calls, up to $depth hops (<=5).",
            f"MATCH (t:{M}:Function) WHERE t.uid STARTS WITH $prefix\n{_TARGET}"
            f"MATCH p = (t)-[:CALLS*1..5]->(c:{M})\n"
            "WHERE length(p) <= $depth\n"
            "  AND all(r IN relationships(p) WHERE coalesce(r.confidence, 1.0) >= $min_confidence)\n"
            "RETURN t.qualified_name AS source, c.qualified_name AS callee, labels(c) AS labels,\n"
            "       c.path AS path, c.line_number AS line, min(length(p)) AS distance\n"
            "ORDER BY distance, callee LIMIT $limit",
        ),
        _q(
            "caller-count",
            "Distinct direct callers per matching function (compare with how many tests failed through it).",
            f"MATCH (t:{M}:Function) WHERE t.uid STARTS WITH $prefix\n{_TARGET}"
            f"OPTIONAL MATCH (c:{M})-[r:CALLS]->(t) WHERE coalesce(r.confidence, 1.0) >= $min_confidence\n"
            "RETURN t.qualified_name AS target, t.path AS path, t.line_number AS line,\n"
            "       count(DISTINCT c) AS callers ORDER BY callers DESC LIMIT $limit",
        ),
        _q(
            "calls-matching",
            "Call sites whose callee name or call text matches the regex $pattern (case-insensitive).",
            f"MATCH (f:{M}:Function)-[c:CALLS]->(g:{M}) WHERE f.uid STARTS WITH $prefix\n"
            "  AND (g.name =~ ('(?i)' + $pattern) OR coalesce(c.full_call_name, '') =~ ('(?i)' + $pattern))\n"
            "RETURN f.qualified_name AS caller, f.path AS path, c.line_number AS line,\n"
            "       coalesce(c.full_call_name, g.name) AS call, g.qualified_name AS callee,\n"
            "       f.loop_depth AS loop_depth, c.in_loop_depth AS in_loop_depth\n"
            "ORDER BY path, line LIMIT $limit",
        ),
        _q(
            "loops",
            "Functions containing a loop, directly or through their callees (loop_depth or TLD >= $min_depth).",
            f"MATCH (f:{M}:Function) WHERE f.uid STARTS WITH $prefix\n"
            "  AND (coalesce(f.loop_depth, 0) >= $min_depth OR coalesce(f.transitive_loop_depth, 0) >= $min_depth)\n"
            f"RETURN {_FN_COLUMNS}\n"
            "ORDER BY transitive_loop_depth DESC, loop_depth DESC, qualified_name LIMIT $limit",
        ),
        _q(
            "impact",
            "What depends on a function or class within 3 hops (calls, inheritance), test code flagged.",
            f"MATCH (t:{M}) WHERE t.uid STARTS WITH $prefix AND (t:Function OR t:Class)\n{_TARGET}"
            f"MATCH p = (x:{M})-[:CALLS|INHERITS|IMPLEMENTS*1..3]->(t)\n"
            "RETURN t.qualified_name AS target, x.qualified_name AS affected, labels(x) AS labels,\n"
            "       x.path AS path, min(length(p)) AS distance,\n"
            "       x.path =~ '(?i).*(test|spec).*' AS is_test\n"
            "ORDER BY distance, path LIMIT $limit",
        ),
        _q(
            "importers",
            "Files importing a module (exact $module or $module_prefix). $module/$module_prefix "
            "match the import specifier exactly as written in source: a Java/Python package name "
            "(e.g. 'org.junit'), or for JS/TS a relative path INCLUDING its leading './' or '../' "
            "(e.g. '../config/test-environment', not 'config/test-environment') -- check `tree` or "
            "a source file's own import line first if unsure of the exact form.",
            f"MATCH (f:{M}:File)-[i:IMPORTS]->(m:{M}:Module) WHERE f.uid STARTS WITH $prefix\n"
            "  AND (($module IS NOT NULL AND m.name = $module)\n"
            "    OR ($module_prefix IS NOT NULL AND m.name STARTS WITH $module_prefix))\n"
            "RETURN m.name AS module, f.path AS file, i.line_number AS line\n"
            "ORDER BY module, file LIMIT $limit",
        ),
        _q(
            "module-usage",
            "Importing-file count per module under $module_prefix (impact of upgrading a dependency). "
            "Same exact-specifier matching as `importers` -- for JS/TS relative imports, include the "
            "leading './' or '../' in $module_prefix, e.g. '../config/' not 'config/'.",
            f"MATCH (f:{M}:File)-[:IMPORTS]->(m:{M}:Module) WHERE f.uid STARTS WITH $prefix\n"
            "  AND m.name STARTS WITH $module_prefix\n"
            "RETURN m.name AS module, count(DISTINCT f) AS files ORDER BY files DESC, module LIMIT $limit",
        ),
        _q(
            "variables",
            "Variables declared in files (module-level and class fields); filter by $path / $name.",
            f"MATCH (f:{M}:File)-[:CONTAINS]->(v:{M}:Variable) WHERE f.uid STARTS WITH $prefix\n"
            "  AND ($path IS NULL OR f.path = $path) AND ($name IS NULL OR v.name = $name)\n"
            "RETURN f.path AS path, v.name AS name, v.line_number AS line, v.context AS context,\n"
            "       v.class_context AS class_context, v.type AS type, v.value AS value\n"
            "ORDER BY path, line LIMIT $limit",
        ),
        _q(
            "by-decorator",
            "Functions/classes carrying a decorator or annotation containing $decorator (fixtures, hooks, tests).",
            f"MATCH (f:{M}) WHERE f.uid STARTS WITH $prefix AND (f:Function OR f:Class)\n"
            "  AND any(d IN coalesce(f.decorators, []) + coalesce(f.modifiers, [])\n"
            "          WHERE toLower(d) CONTAINS toLower($decorator))\n"
            "RETURN labels(f) AS labels, f.qualified_name AS qualified_name, f.path AS path,\n"
            "       f.line_number AS line, f.decorators AS decorators ORDER BY path, line LIMIT $limit",
        ),
        _q(
            "tree",
            "Directories and what they contain, optionally under $path.",
            f"MATCH (d:{M}:Directory) WHERE d.uid STARTS WITH $prefix\n"
            "  AND ($path IS NULL OR d.path = $path OR d.path STARTS WITH $path + '/')\n"
            f"OPTIONAL MATCH (d)-[:CONTAINS]->(c:{M})\n"
            "RETURN d.path AS directory, collect(c.path)[..200] AS children ORDER BY directory LIMIT $limit",
        ),
        _q(
            "files",
            "Files with how many functions and classes each holds, optionally under $path.",
            f"MATCH (f:{M}:File) WHERE f.uid STARTS WITH $prefix\n"
            "  AND ($path IS NULL OR f.path = $path OR f.path STARTS WITH $path + '/')\n"
            f"OPTIONAL MATCH (f)-[:CONTAINS]->(s:{M})\n"
            "RETURN f.path AS path, sum(CASE WHEN s:Function THEN 1 ELSE 0 END) AS functions,\n"
            "       sum(CASE WHEN s:Class THEN 1 ELSE 0 END) AS classes ORDER BY path LIMIT $limit",
        ),
        _q(
            "file-symbols",
            "Every function/class/variable declared in one file ($path).",
            f"MATCH (f:{M}:File) WHERE f.uid STARTS WITH $prefix AND f.path = $path\n"
            f"MATCH (f)-[:CONTAINS]->(s:{M})\n"
            "RETURN labels(s) AS labels, s.name AS name, s.qualified_name AS qualified_name,\n"
            "       s.line_number AS line, s.end_line AS end_line ORDER BY line LIMIT $limit",
        ),
        _q(
            "symbol",
            "One function/class by $qualified_name, or $name (+ $path): source, lines and metrics.",
            f"MATCH (f:{M}) WHERE f.uid STARTS WITH $prefix\n"
            "  AND (f:Function OR f:Class OR f:Interface OR f:Enum OR f:Struct OR f:Record OR f:Trait)\n"
            "  AND ($qualified_name IS NULL OR f.qualified_name = $qualified_name)\n"
            "  AND ($name IS NULL OR f.name = $name) AND ($path IS NULL OR f.path = $path)\n"
            f"RETURN labels(f) AS labels, {_FN_COLUMNS}, f.source AS source\n"
            "ORDER BY path, line LIMIT $limit",
        ),
        _q(
            "search-source",
            "Functions/classes whose source contains $text (case-insensitive). Use Grep for regexes.",
            f"MATCH (f:{M}) WHERE f.uid STARTS WITH $prefix AND (f:Function OR f:Class)\n"
            "  AND toLower(f.source) CONTAINS toLower($text)\n"
            "RETURN labels(f) AS labels, f.qualified_name AS qualified_name, f.path AS path,\n"
            "       f.line_number AS line, f.end_line AS end_line ORDER BY path, line LIMIT $limit",
        ),
        _q(
            "hierarchy",
            "Inheritance/implementation chains, for classes named $name (or all).",
            f"MATCH p = (c:{M}:Class)-[:INHERITS|IMPLEMENTS*1..5]->(b:{M})\n"
            "WHERE c.uid STARTS WITH $prefix AND ($name IS NULL OR c.name = $name OR b.name = $name)\n"
            "RETURN c.qualified_name AS class, c.path AS path,\n"
            "       [n IN nodes(p)[1..] | coalesce(n.qualified_name, n.name)] AS ancestors\n"
            "ORDER BY class LIMIT $limit",
        ),
        _q(
            "endpoints",
            "HTTP endpoints the code exposes (method, path, handler).",
            f"MATCH (f:{M}:Function) WHERE f.uid STARTS WITH $prefix AND f.http_path IS NOT NULL\n"
            "RETURN f.http_method AS method, f.http_path AS http_path, f.qualified_name AS handler,\n"
            "       f.path AS path, f.line_number AS line ORDER BY http_path, method LIMIT $limit",
        ),
        _q(
            "maven-deps",
            "Declared Maven dependencies and their versions.",
            f"MATCH (m:{M}:MavenModule)-[u:USES_LIBRARY]->(l:{M}:ExternalLibrary)\n"
            "WHERE m.uid STARTS WITH $prefix\n"
            "RETURN m.artifact_id AS module, l.group_id AS group_id, l.artifact_id AS artifact_id,\n"
            "       l.version AS version, u.scope AS scope ORDER BY group_id, artifact_id LIMIT $limit",
        ),
        _q(
            "uncalled",
            "Functions nothing in the graph calls — dead-code candidates, not proof (reflection, tests, entry points).",
            f"MATCH (f:{M}:Function) WHERE f.uid STARTS WITH $prefix\n"
            f"  AND NOT EXISTS {{ MATCH (:{M})-[:CALLS]->(f) }}\n"
            "RETURN f.qualified_name AS qualified_name, f.path AS path, f.line_number AS line,\n"
            "       f.decorators AS decorators ORDER BY path, line LIMIT $limit",
        ),
        _q(
            "stale",
            "Nodes the most recent index did not confirm — pruning candidates.",
            f"MATCH (n:{M}) WHERE n.uid STARTS WITH $prefix\n"
            "  AND coalesce(n.last_seen_version, -1) < $version\n"
            "RETURN labels(n) AS labels, n.uid AS uid, n.last_seen_version AS last_seen ORDER BY uid LIMIT $limit",
        ),
        _q(
            "stubs",
            "Nodes referenced by an edge but never indexed.",
            f"MATCH (n:{M}) WHERE n.uid STARTS WITH $prefix AND n.stub\n"
            "RETURN n.uid AS uid, labels(n) AS labels ORDER BY uid LIMIT $limit",
        ),
        _q(
            "explore",
            "A small connected subgraph for visualization: up to $limit matching nodes "
            "(optionally filtered by $label / $search), plus every relationship between "
            "nodes in that set. Always returns exactly one row.",
            f"MATCH (n:{M}) WHERE n.uid STARTS WITH $prefix\n"
            "  AND ($label IS NULL OR $label IN labels(n))\n"
            "  AND ($search IS NULL OR toLower(coalesce(n.name, '')) CONTAINS toLower($search)\n"
            "       OR toLower(coalesce(n.qualified_name, '')) CONTAINS toLower($search)\n"
            "       OR toLower(coalesce(n.path, '')) CONTAINS toLower($search))\n"
            "WITH n LIMIT $limit\n"
            "WITH collect(n) AS ns\n"
            "UNWIND ns AS a\n"
            "OPTIONAL MATCH (a)-[r]-(b) WHERE b IN ns\n"
            "WITH ns, collect(DISTINCT r) AS rs\n"
            f"RETURN [x IN ns | {{uid: x.uid, labels: [l IN labels(x) WHERE l <> '{M}'],\n"
            "         name: coalesce(x.name, x.qualified_name, x.path, x.uid), path: x.path,\n"
            "         line: x.line_number, complexity: x.cyclomatic_complexity,\n"
            "         cognitive: x.cognitive}] AS nodes,\n"
            "       [rr IN rs WHERE rr IS NOT NULL |\n"
            "         {type: type(rr), source: startNode(rr).uid, target: endNode(rr).uid}] AS edges",
        ),
    ]
)

# Parameters each query needs beyond prefix/version/limit, with their defaults.
_TARGET_DEFAULTS = {"uid": None, "qualified_name": None, "name": None, "path": None}
DEFAULTS: dict[str, dict[str, Any]] = {
    "complexity-hotspots": {"min_cyclomatic": 11, "min_cognitive": 16, "min_tld": 3, "min_params": 6, "min_access": 4},
    "complexity-risks": {"min_alloc": 3},
    "similar-pairs": {"min_jaccard": 0.7, "same_file": None},
    "callers": {**_TARGET_DEFAULTS, "depth": 1, "min_confidence": 0.0},
    "callees": {**_TARGET_DEFAULTS, "depth": 1, "min_confidence": 0.0},
    "caller-count": {**_TARGET_DEFAULTS, "min_confidence": 0.0},
    "impact": {**_TARGET_DEFAULTS},
    "calls-matching": {"pattern": ""},
    "loops": {"min_depth": 1},
    "importers": {"module": None, "module_prefix": None},
    "module-usage": {"module_prefix": ""},
    "variables": {"path": None, "name": None},
    "by-decorator": {"decorator": ""},
    "tree": {"path": None},
    "files": {"path": None},
    "file-symbols": {"path": ""},
    "symbol": {"qualified_name": None, "name": None, "path": None},
    "search-source": {"text": ""},
    "hierarchy": {"name": None},
    "explore": {"label": None, "search": None, "limit": 150},
}

_EXPLORE_MAX_LIMIT = 300

# Queries that must be narrowed by the caller, and by which parameters.
REQUIRED_ANY: dict[str, tuple[str, ...]] = {
    "callers": ("uid", "qualified_name", "name"),
    "callees": ("uid", "qualified_name", "name"),
    "caller-count": ("uid", "qualified_name", "name", "path"),
    "impact": ("uid", "qualified_name", "name"),
    "calls-matching": ("pattern",),
    "importers": ("module", "module_prefix"),
    "module-usage": ("module_prefix",),
    "by-decorator": ("decorator",),
    "file-symbols": ("path",),
    "symbol": ("qualified_name", "name"),
    "search-source": ("text",),
}


def build(query: str, prefix: str, /, **params: Any) -> tuple[str, dict[str, Any]]:
    merged = {**DEFAULTS.get(query, {}), **params}
    required = REQUIRED_ANY.get(query)
    if required and not any(merged.get(k) not in (None, "") for k in required):
        raise KeyError(f"code graph query {query!r} needs one of: {', '.join(required)}")
    if "depth" in merged:
        merged["depth"] = max(1, min(int(merged["depth"]), 5))
    if query == "explore":
        try:
            limit = int(merged.get("limit") or 150)
        except (TypeError, ValueError):
            limit = 150
        merged["limit"] = max(1, min(limit, _EXPLORE_MAX_LIMIT))
    return build_from(CODE_QUERIES, query, prefix, **merged)


def catalogue() -> list[Mapping[str, Any]]:
    rows = catalogue_of(CODE_QUERIES)
    return [
        {**row, "params": DEFAULTS.get(row["name"], {}), "requires_one_of": list(REQUIRED_ANY.get(row["name"], ()))}
        for row in rows
    ]
