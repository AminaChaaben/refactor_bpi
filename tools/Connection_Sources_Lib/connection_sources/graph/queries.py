"""The questions the graph was built to answer, written once and named.

A traceability graph is only worth the load that fills it if the questions are easy
to ask, and Cypher is not what most of the people asking them write. Naming the
queries here means `alm-conn graph query uncovered` is the interface, the query
itself is reviewable in version control, and nobody has to reconstruct the traversal
from memory at the moment they need the answer.

Every query is read-only and every one is scoped by uid prefix, so running one
against a database that also holds another system's nodes cannot accidentally report
on them.
"""

from __future__ import annotations

from typing import Any, Mapping, NamedTuple

from .ontology import ALM_SCHEMA, MARKER

__all__ = ["QUERIES", "Query", "build", "build_from", "catalogue_of"]


class Query(NamedTuple):
    """One named question: what it answers, and the Cypher that answers it."""

    name: str
    description: str
    cypher: str


_REQUIREMENT = "(n:Story OR n:Epic OR n:Feature OR n:Bug OR n:Task OR n:Requirement)"


def _is_requirement(var: str) -> str:
    return _REQUIREMENT.replace("n:", f"{var}:")


# --- story-context ------------------------------------------------------------------
# Everything test design should read before it designs one story: where the story
# sits, what it is linked to, what is already tested around it and what already broke
# around it. One row per related item, every branch returning the same columns so the
# consumer never has to know which traversal produced a row.
_STORY = f"MATCH (s:{MARKER}) WHERE s.uid STARTS WITH $prefix AND s.key = $key\n"


def _row(relation: str, via: str, node: str, *, gherkin: str = "null",
         steps: str = "[]", run_status: str = "null") -> str:
    return (
        f"RETURN {relation} AS relation, {via} AS via, {node}.key AS key,\n"
        f"       coalesce({node}.type, {node}.test_type) AS type, {node}.status AS status,\n"
        f"       {node}.title AS title, {node}.description AS description,\n"
        f"       {node}.acceptance_criteria AS acceptance_criteria, {gherkin} AS gherkin,\n"
        f"       {steps} AS steps, {run_status} AS run_status,\n"
        f"       {node}.created AS created, {node}.updated AS updated"
    )


# The story plus its siblings and linked issues: the neighbourhood whose tests and
# bugs count as evidence for this story.
_NEIGHBOURHOOD = (
    _STORY
    + "OPTIONAL MATCH (s)-[:CHILD_OF]->(:AlmNode)<-[:CHILD_OF]-(sib:AlmNode)\n"
    + "  WHERE sib <> s AND " + _is_requirement("sib") + "\n"
    + "OPTIONAL MATCH (s)-[r]-(li:AlmNode:Issue) WHERE r.link_type IS NOT NULL\n"
    + "WITH s, collect(DISTINCT sib) + collect(DISTINCT li) + [s] AS scope\n"
    + "UNWIND scope AS n\n"
)

_STORY_CONTEXT = "\nUNION ALL\n".join(
    [
        _STORY + _row("'self'", "null", "s"),
        _STORY
        + "MATCH (s)-[:CHILD_OF*1..2]->(p:AlmNode)\n"
        + "WITH DISTINCT p LIMIT $limit\n"
        + _row("'ancestor'", "null", "p"),
        _STORY
        + "MATCH (s)-[:CHILD_OF]->(p:AlmNode)<-[:CHILD_OF]-(sib:AlmNode)\n"
        + "WHERE sib <> s AND " + _is_requirement("sib") + "\n"
        + "WITH p, sib ORDER BY sib.updated DESC LIMIT $limit\n"
        + _row("'sibling'", "p.key", "sib"),
        _STORY
        + "MATCH (s)-[r]-(li:AlmNode:Issue) WHERE r.link_type IS NOT NULL\n"
        + "WITH r, li LIMIT $limit\n"
        + _row("'link:' + type(r)", "r.link_type", "li"),
        _STORY
        + "MATCH (s)-[:HAS_COMPONENT|HAS_LABEL]->(tag)<-[:HAS_COMPONENT|HAS_LABEL]-(peer:AlmNode)\n"
        + "WHERE peer <> s AND " + _is_requirement("peer") + "\n"
        + "WITH peer, collect(DISTINCT coalesce(tag.name, tag.path)) AS tags\n"
        + "ORDER BY size(tags) DESC, peer.updated DESC LIMIT $limit\n"
        + _row(
            "'shared-tag'",
            "reduce(acc = '', t IN tags | acc + CASE acc WHEN '' THEN '' ELSE ', ' END + t)",
            "peer",
        ),
        _NEIGHBOURHOOD
        + "MATCH (t:AlmNode:Test)-[:COVERS]->(n)\n"
        + "WITH DISTINCT t, n LIMIT $limit\n"
        + "OPTIONAL MATCH (t)-[hs:HAS_STEP]->(st)\n"
        + "WITH t, n, hs, st ORDER BY hs.index\n"
        + "WITH t, n, collect(CASE WHEN st IS NULL THEN null ELSE\n"
        + "  {index: hs.index, action: st.action, data: st.data, expected: st.expected} END) AS steps\n"
        + "OPTIONAL MATCH (t)-[:LATEST_RUN]->(run)\n"
        + _row(
            "'test'", "n.key", "t",
            gherkin="t.gherkin", steps="steps", run_status="coalesce(run.status, t.run_status)",
        ),
        _NEIGHBOURHOOD
        + "MATCH (b:AlmNode:Bug)-[r]-(n)\n"
        + "WHERE b <> n AND (r.link_type IS NOT NULL OR type(r) = 'CHILD_OF')\n"
        + "WITH DISTINCT b, n LIMIT $limit\n"
        + _row("'bug'", "n.key", "b"),
        _STORY
        + "MATCH (s)-[:HAS_COMMENT]->(c:AlmNode)\n"
        + "WITH c ORDER BY c.created DESC LIMIT $limit\n"
        + "RETURN 'comment' AS relation, c.created AS via, null AS key, null AS type,\n"
        + "       null AS status, null AS title, c.body AS description,\n"
        + "       null AS acceptance_criteria, null AS gherkin, [] AS steps, null AS run_status,\n"
        + "       c.created AS created, null AS updated",
        _STORY
        + "MATCH (pg:AlmNode:Page)-[:MENTIONS]->(s)\n"
        + "WITH pg LIMIT $limit\n"
        + "RETURN 'page' AS relation, pg.url AS via, 'page:' + pg.page_id AS key,\n"
        + "       'page' AS type, pg.status AS status, pg.title AS title, null AS description,\n"
        + "       null AS acceptance_criteria, null AS gherkin, [] AS steps, null AS run_status,\n"
        + "       null AS created, null AS updated",
    ]
)

_SIMILAR_ISSUES = (
    f"CALL db.index.fulltext.queryNodes('{ALM_SCHEMA.fulltext_index}', $q) YIELD node, score\n"
    "WITH node AS n, score\n"
    f"WHERE n.uid STARTS WITH $prefix AND n.key <> coalesce($key, '') AND {_REQUIREMENT}\n"
    "WITH n, score ORDER BY score DESC LIMIT $limit\n"
    "RETURN 'similar' AS relation, toString(score) AS via, n.key AS key, n.type AS type,\n"
    "       n.status AS status, n.title AS title, n.description AS description,\n"
    "       n.acceptance_criteria AS acceptance_criteria, null AS gherkin, [] AS steps,\n"
    "       null AS run_status, n.created AS created, n.updated AS updated, score"
)

# --- similar-tests ------------------------------------------------------------------
# Tests ANYWHERE in the project that talk about what the story talks about, found through
# the tests' own full-text index (title, Gherkin, steps text). `story-context` only reaches
# tests through the story's siblings and links; a test written for an unrelated epic that
# exercises the same screen is reachable only this way. Tests already covering the story
# are left out (`story-context` returns them), and each row carries the keys of everything
# the test covers, so the consumer can tell a neighbour's test from a stranger's.
_SIMILAR_TESTS = (
    f"CALL db.index.fulltext.queryNodes('{ALM_SCHEMA.test_fulltext_index}', $q) YIELD node, score\n"
    "WITH node AS t, score\n"
    "WHERE t.uid STARTS WITH $prefix AND t:Test\n"
    "OPTIONAL MATCH (t)-[:COVERS]->(c:AlmNode)\n"
    "WITH t, score, collect(DISTINCT c.key) AS covers\n"
    "WHERE NOT coalesce($key, '') IN covers\n"
    "WITH t, score, covers ORDER BY score DESC LIMIT $limit\n"
    "OPTIONAL MATCH (t)-[hs:HAS_STEP]->(st)\n"
    "WITH t, score, covers, hs, st ORDER BY hs.index\n"
    "WITH t, score, covers, collect(CASE WHEN st IS NULL THEN null ELSE\n"
    "  {index: hs.index, action: st.action, data: st.data, expected: st.expected} END) AS steps\n"
    "OPTIONAL MATCH (t)-[:LATEST_RUN]->(run)\n"
    "RETURN 'similar-test' AS relation, toString(score) AS via, t.key AS key,\n"
    "       coalesce(t.type, t.test_type) AS type, t.status AS status, t.title AS title,\n"
    "       t.description AS description, null AS acceptance_criteria, t.gherkin AS gherkin,\n"
    "       steps, coalesce(run.status, t.run_status) AS run_status,\n"
    "       t.created AS created, t.updated AS updated, score, covers"
)

# --- story-changes ------------------------------------------------------------------
# What was edited on ONE story, from Jira's own changelog (the `ChangeEvent` /
# `FieldChange` nodes the loader writes): the field, the text before and after, when.
# Test design uses it to know which acceptance criteria changed after each test was
# written, without keeping a snapshot of its own. Only the fields the story's meaning
# lives in are returned: `$field_re` (a case-insensitive regex on the field's name) and
# `$field_ids` (a comma-separated list of custom field ids) pick them, so a status
# transition or an assignee change does not crowd the limit.
#
# A story that was never edited has no changelog, exactly like a story whose changelog
# was never loaded. The last branch tells them apart: it says whether ANY change event
# exists in this site's graph.
DEFAULT_CHANGE_FIELD_RE = "(?i).*(accept|crit|descr).*"

_STORY_CHANGES = "\nUNION ALL\n".join(
    [
        _STORY
        + f"MATCH (e:{MARKER}:ChangeEvent)-[:ON]->(s)\n"
        + "MATCH (e)-[:CHANGED]->(f:FieldChange)\n"
        + "WHERE f.field =~ $field_re OR f.field_id IN split($field_ids, ',')\n"
        + "OPTIONAL MATCH (e)-[:BY]->(u:User)\n"
        + "WITH e, f, u ORDER BY e.created DESC LIMIT $limit\n"
        + "RETURN 'change' AS relation, e.created AS at, u.display_name AS who,\n"
        + "       f.field AS field, f.field_id AS field_id, f.from AS before, f.to AS after,\n"
        + "       null AS loaded",
        f"OPTIONAL MATCH (e:{MARKER}:ChangeEvent) WHERE e.uid STARTS WITH $prefix\n"
        + "WITH e LIMIT 1\n"
        + "RETURN 'meta' AS relation, null AS at, null AS who, null AS field, null AS field_id,\n"
        + "       null AS before, null AS after, e IS NOT NULL AS loaded",
    ]
)


# --- test-dates ---------------------------------------------------------------------
# A diagnostic, not a question about the project: can test design trust this graph's dates?
# Teda decides which criteria changed after a test was written from the test's `created`
# and the story's change history. A test node has `created` only when that test issue was
# read by the Jira extraction (inside the JQL scope); the Xray read does not set it. One
# row of counts says whether that holds here, and whether the change history and the
# searchable steps text are loaded.
_TEST_DATES = (
    f"MATCH (t:{MARKER}:Test) WHERE t.uid STARTS WITH $prefix\n"
    "WITH count(t) AS tests, count(t.created) AS tests_with_created,\n"
    "     count(t.updated) AS tests_with_updated, count(t.steps_text) AS tests_with_steps_text\n"
    f"OPTIONAL MATCH (e:{MARKER}:ChangeEvent) WHERE e.uid STARTS WITH $prefix\n"
    "WITH tests, tests_with_created, tests_with_updated, tests_with_steps_text,\n"
    "     count(e) AS change_events\n"
    f"OPTIONAL MATCH (f:{MARKER}:FieldChange) WHERE f.uid STARTS WITH $prefix\n"
    "  AND f.field =~ '(?i).*(accept|crit|descr).*'\n"
    "RETURN tests, tests_with_created, tests_with_updated, tests_with_steps_text,\n"
    "       change_events, count(f) AS criteria_or_description_edits"
)


QUERIES: dict[str, Query] = {
    "summary": Query(
        "summary",
        "Node and relationship totals for this site.",
        f"MATCH (n:{MARKER}) WHERE n.uid STARTS WITH $prefix\n"
        "WITH count(n) AS nodes\n"
        f"MATCH (a:{MARKER})-[r]->() WHERE a.uid STARTS WITH $prefix\n"
        "RETURN nodes, count(r) AS relationships",
    ),
    "labels": Query(
        "labels",
        "How many nodes carry each label.",
        f"MATCH (n:{MARKER}) WHERE n.uid STARTS WITH $prefix\n"
        "UNWIND labels(n) AS label\n"
        f"WITH label WHERE label <> '{MARKER}'\n"
        "RETURN label, count(*) AS count ORDER BY count DESC, label",
    ),
    "relationships": Query(
        "relationships",
        "How many relationships of each type.",
        f"MATCH (a:{MARKER})-[r]->() WHERE a.uid STARTS WITH $prefix\n"
        "RETURN type(r) AS type, count(*) AS count ORDER BY count DESC, type",
    ),
    "uncovered": Query(
        "uncovered",
        "Requirements with no test covering them.",
        f"MATCH (n:{MARKER}) WHERE n.uid STARTS WITH $prefix AND {_REQUIREMENT}\n"
        "  AND coalesce(n.coverage_status, 'UNCOVERED') = 'UNCOVERED'\n"
        "RETURN n.key AS key, n.type AS type, n.status AS status, n.title AS title\n"
        "ORDER BY key",
    ),
    "coverage": Query(
        "coverage",
        "Requirement count by coverage status.",
        f"MATCH (n:{MARKER}) WHERE n.uid STARTS WITH $prefix AND {_REQUIREMENT}\n"
        "RETURN coalesce(n.coverage_status, 'UNCOVERED') AS coverage_status,\n"
        "       count(*) AS count ORDER BY count DESC",
    ),
    "coverage-gaps": Query(
        "coverage-gaps",
        "Requirements with no test covering them, grouped by type.",
        f"MATCH (n:{MARKER}) WHERE n.uid STARTS WITH $prefix AND {_REQUIREMENT}\n"
        "  AND coalesce(n.coverage_status, 'UNCOVERED') = 'UNCOVERED'\n"
        "RETURN n.type AS type, count(*) AS count, collect(n.key)[..20] AS sample\n"
        "ORDER BY count DESC, type",
    ),
    "orphan-tests": Query(
        "orphan-tests",
        "Tests that cover nothing — effort with no requirement behind it.",
        f"MATCH (t:{MARKER}:Test) WHERE t.uid STARTS WITH $prefix\n"
        "  AND coalesce(t.is_orphan, true)\n"
        "RETURN t.key AS key, t.status AS status, t.title AS title ORDER BY key",
    ),
    "traceability": Query(
        "traceability",
        "The spine: requirement -> test -> latest run, one row per pair.",
        f"MATCH (n:{MARKER}) WHERE n.uid STARTS WITH $prefix AND {_REQUIREMENT}\n"
        "OPTIONAL MATCH (n)<-[:COVERS]-(t:Test)\n"
        "OPTIONAL MATCH (t)-[:LATEST_RUN]->(run:TestRun)\n"
        "RETURN n.key AS requirement, n.title AS title,\n"
        "       coalesce(n.coverage_status, 'UNCOVERED') AS coverage,\n"
        "       t.key AS test, coalesce(run.status, t.run_status) AS run_status\n"
        "ORDER BY requirement, test",
    ),
    "hierarchy": Query(
        "hierarchy",
        "Parent/child edges, deepest first.",
        f"MATCH (c:{MARKER})-[:CHILD_OF]->(p) WHERE c.uid STARTS WITH $prefix\n"
        "RETURN c.key AS child, c.type AS child_type, p.key AS parent,\n"
        "       coalesce(c.hierarchy_depth, 0) AS depth\n"
        "ORDER BY depth DESC, parent, child",
    ),
    "links": Query(
        "links",
        "Every issue-to-issue link, with the Jira link type that produced it.",
        f"MATCH (a:{MARKER}:Issue)-[r]->(b:Issue) WHERE a.uid STARTS WITH $prefix\n"
        "  AND r.link_type IS NOT NULL\n"
        "RETURN a.key AS from, type(r) AS relationship, r.link_type AS link_type,\n"
        "       b.key AS to ORDER BY from, relationship, to",
    ),
    "workload": Query(
        "workload",
        "Open items per assignee, by bucket.",
        f"MATCH (n:{MARKER}:Issue)-[:ASSIGNED_TO]->(u:User)\n"
        "WHERE n.uid STARTS WITH $prefix AND n.status_category <> 'Done'\n"
        "RETURN u.display_name AS assignee, n.bucket AS bucket, count(*) AS count\n"
        "ORDER BY count DESC, assignee",
    ),
    "sprint-load": Query(
        "sprint-load",
        "What sits in each sprint right now.",
        f"MATCH (n:{MARKER}:Issue)-[m:IN_SPRINT]->(s:Sprint)\n"
        "WHERE n.uid STARTS WITH $prefix AND m.current\n"
        "RETURN s.name AS sprint, s.state AS state, n.bucket AS bucket,\n"
        "       count(*) AS count ORDER BY sprint, bucket",
    ),
    "stale": Query(
        "stale",
        "Nodes the most recent load did not confirm — pruning candidates.",
        f"MATCH (n:{MARKER}) WHERE n.uid STARTS WITH $prefix\n"
        "  AND coalesce(n.last_seen_version, -1) < $version\n"
        "RETURN labels(n) AS labels, n.uid AS uid, n.key AS key,\n"
        "       n.last_seen_version AS last_seen ORDER BY uid",
    ),
    "stubs": Query(
        "stubs",
        "Nodes referenced by an edge but never read — usually outside the JQL scope.",
        f"MATCH (n:{MARKER}) WHERE n.uid STARTS WITH $prefix AND n.stub\n"
        "RETURN n.uid AS uid, n.key AS key, labels(n) AS labels ORDER BY uid",
    ),
    "history": Query(
        "history",
        "Field changes over time, newest first.",
        f"MATCH (e:{MARKER}:ChangeEvent)-[:ON]->(i:Issue)\n"
        "WHERE i.uid STARTS WITH $prefix\n"
        "MATCH (e)-[:CHANGED]->(f:FieldChange)\n"
        "OPTIONAL MATCH (e)-[:BY]->(u:User)\n"
        "RETURN i.key AS key, e.created AS at, u.display_name AS who,\n"
        "       f.field AS field, f.from AS before, f.to AS after\n"
        "ORDER BY at DESC LIMIT $limit",
    ),
    "explore": Query(
        "explore",
        "A small connected subgraph for visualization: up to $limit matching nodes "
        "(optionally filtered by $label / $search), plus every relationship between "
        "nodes in that set. Always returns exactly one row.",
        f"MATCH (n:{MARKER}) WHERE n.uid STARTS WITH $prefix\n"
        "  AND ($label IS NULL OR $label IN labels(n))\n"
        "  AND ($search IS NULL OR toLower(coalesce(n.name, '')) CONTAINS toLower($search)\n"
        "       OR toLower(coalesce(n.key, '')) CONTAINS toLower($search))\n"
        "WITH n LIMIT $limit\n"
        "WITH collect(n) AS ns\n"
        "UNWIND ns AS a\n"
        "OPTIONAL MATCH (a)-[r]-(b) WHERE b IN ns\n"
        "WITH ns, collect(DISTINCT r) AS rs\n"
        f"RETURN [x IN ns | {{uid: x.uid, labels: [l IN labels(x) WHERE l <> '{MARKER}'],\n"
        "         name: coalesce(x.name, x.key, x.uid), key: x.key, status: x.status,\n"
        "         bucket: x.bucket}] AS nodes,\n"
        "       [rr IN rs WHERE rr IS NOT NULL |\n"
        "         {type: type(rr), source: startNode(rr).uid, target: endNode(rr).uid}] AS edges",
    ),
    "story-context": Query(
        "story-context",
        "Everything around one story ($key): itself, its ancestors, siblings, linked "
        "issues, stories sharing a component or label, the tests covering it or those "
        "neighbours (with steps and last result), bugs near it, its comments and the "
        "pages that mention it. One row per item, at most $limit per kind.",
        _STORY_CONTEXT,
    ),
    "similar-issues": Query(
        "similar-issues",
        "Requirements whose title, description or acceptance criteria match the "
        "full-text query $q (Lucene syntax), best first, excluding the story $key.",
        _SIMILAR_ISSUES,
    ),
    "similar-tests": Query(
        "similar-tests",
        "Tests anywhere in the project whose title, Gherkin or steps match the full-text "
        "query $q (Lucene syntax), best first, excluding the tests that already cover "
        "the story $key. Each row lists every issue the test covers.",
        _SIMILAR_TESTS,
    ),
    "story-changes": Query(
        "story-changes",
        "What was edited on the story $key, from Jira's changelog: the field, its text "
        "before and after, and when. Filtered to the fields matching $field_re or listed "
        "in $field_ids; a final `meta` row says whether any changelog is loaded at all.",
        _STORY_CHANGES,
    ),
    "test-dates": Query(
        "test-dates",
        "One row of counts: how many tests exist, how many carry `created` / `updated` / "
        "`steps_text`, how many change events are loaded and how many of them touch a "
        "criteria or description field. Tells whether test design can date its tests.",
        _TEST_DATES,
    ),
}

_EXPLORE_MAX_LIMIT = 300


def _apply_defaults(name: str, params: dict[str, Any]) -> None:
    """Fill the optional parameters a query names, so Neo4j never sees one missing."""
    if name in ("story-context", "similar-issues", "similar-tests"):
        params.setdefault("key", None)
        params.setdefault("q", None)
    elif name == "story-changes":
        params.setdefault("key", None)
        params.setdefault("field_re", DEFAULT_CHANGE_FIELD_RE)
        params.setdefault("field_ids", "")
    elif name == "explore":
        _clamp_explore(params)


def _clamp_explore(params: dict[str, Any]) -> None:
    params.setdefault("label", None)
    params.setdefault("search", None)
    try:
        limit = int(params.get("limit") or 150)
    except (TypeError, ValueError):
        limit = 150
    params["limit"] = max(1, min(limit, _EXPLORE_MAX_LIMIT))


def build_from(
    catalogue_: Mapping[str, Query], name: str, prefix: str, /, **params: Any
) -> tuple[str, dict[str, Any]]:
    """One named query from `catalogue_` and its parameters, ready for a `loader.Runner`.

    Unknown names raise rather than returning an empty result: a typo that silently
    answers "nothing found" is indistinguishable from a real empty answer, and this
    is the one place that distinction is free to make.
    """
    query = catalogue_.get(name)
    if query is None:
        raise KeyError(
            f"unknown graph query {name!r}; try one of: {', '.join(sorted(catalogue_))}"
        )
    merged = {"prefix": prefix, "version": 0, "limit": 50, **params}
    _apply_defaults(name, merged)
    return query.cypher, merged


def build(name: str, prefix: str, **params: Any) -> tuple[str, dict[str, Any]]:
    """One named ALM query and its parameters, ready for a `loader.Runner`."""
    return build_from(QUERIES, name, prefix, **params)


def catalogue_of(catalogue_: Mapping[str, Query]) -> list[Mapping[str, str]]:
    """Every query in `catalogue_` with its description, sorted by name."""
    return [
        {"name": q.name, "description": q.description}
        for q in sorted(catalogue_.values(), key=lambda q: q.name)
    ]


def catalogue() -> list[Mapping[str, str]]:
    """Every query with its description, for `graph query --list`."""
    return catalogue_of(QUERIES)
