"""The facts the graph computes about itself once everything is loaded.

Nothing here comes from a tracker. Coverage status, the latest run of a test, whether
a test covers anything at all — these are conclusions drawn from the shape of the
graph, and they are written back as properties for one reason: they are the questions
people ask most often and the ones that are most expensive to answer by traversal
every time.

They are recomputed wholesale rather than maintained incrementally. A story's
coverage changes when a *test* changes, not when the story does, so an incremental
update would have to chase the edge backwards from every write — and get it wrong the
first time somebody deletes a link. Recomputing is a handful of statements over an
already-indexed graph, and it cannot drift.

Every statement is scoped by uid prefix, so deriving over one Jira site never touches
another system's nodes sitting in the same database.
"""

from __future__ import annotations

from typing import Any, Iterable, Iterator, Mapping

from .ontology import MARKER

__all__ = [
    "DERIVATIONS",
    "FAILING_STATUSES",
    "PASSING_STATUSES",
    "REQUIREMENT_LABELS",
    "coverage_status",
    "derivation_statements",
    "run_derivations",
    "summarise",
]

# Statuses that mean a test ran and did not pass. Kept here rather than in the
# ontology because this is a judgement about *severity* — ABORTED counts as "not
# green" for a coverage question even though it is not a failure — while the
# ontology only normalises spelling.
FAILING_STATUSES: tuple[str, ...] = ("FAIL", "ABORTED")
PASSING_STATUSES: tuple[str, ...] = ("PASS",)

# The labels a coverage question is asked about. A precondition or a test set is not
# a requirement, so an unlinked one is not an "uncovered story".
REQUIREMENT_LABELS: tuple[str, ...] = (
    "Story",
    "Epic",
    "Feature",
    "Bug",
    "Task",
    "Requirement",
)

# The Cypher fragments below are built from the tuples above rather than written out,
# so the rule the database applies and the rule `project_state` applies in Python are
# the same rule. Two hand-maintained copies of "what counts as covered" would agree
# on the day they were written and disagree by the time anyone noticed.
_FAILING = repr(list(FAILING_STATUSES))
_PASSING = repr(list(PASSING_STATUSES))
_REQUIREMENT = "(" + " OR ".join(f"n:{label}" for label in REQUIREMENT_LABELS) + ")"


def coverage_status(statuses: Iterable[str]) -> str:
    """The four-state coverage verdict for one requirement's tests.

    Four states rather than a boolean, because "no test exists" and "a test exists
    and fails" are the opposite of interchangeable — the first is a planning gap and
    the second is a defect — and collapsing them is how coverage dashboards end up
    reassuring everybody about the wrong thing.
    """
    listed = [str(s).strip().upper() or "TODO" for s in statuses]
    if not listed:
        return "UNCOVERED"
    if any(status in FAILING_STATUSES for status in listed):
        return "FAIL"
    if all(status in PASSING_STATUSES for status in listed):
        return "OK"
    return "NOTRUN"


def _latest_run_clear() -> tuple[str, dict[str, Any]]:
    return (
        f"MATCH (t:{MARKER})-[r:LATEST_RUN]->()\n"
        "WHERE t.uid STARTS WITH $prefix\n"
        "DELETE r\n"
        "RETURN count(*) AS affected"
    ), {}


def _latest_run_mark() -> tuple[str, dict[str, Any]]:
    """Point each test at its most recent run.

    Ordered by finish time and falling back to start time, because a run that is
    still executing has no finish time and would otherwise sort as the oldest thing
    in the graph — which is exactly backwards for the run people most want to see.
    """
    return (
        f"MATCH (t:{MARKER}:Test)<-[:OF_TEST]-(run:TestRun)\n"
        "WHERE t.uid STARTS WITH $prefix\n"
        "WITH t, run\n"
        "ORDER BY coalesce(run.finished_on, run.started_on, run.created, '') DESC\n"
        "WITH t, head(collect(run)) AS latest\n"
        "MERGE (t)-[:LATEST_RUN]->(latest)\n"
        "SET t.run_status = latest.status, t.last_run_at = "
        "coalesce(latest.finished_on, latest.started_on)\n"
        "RETURN count(*) AS affected"
    ), {}


def _run_status_fallback() -> tuple[str, dict[str, Any]]:
    """Give every test a `run_status`, even on a site with no execution data.

    Without Xray there are no run nodes at all, so a test's only evidence of having
    been exercised is whatever the site records on the issue itself — a simulated
    "Test State" custom field, or failing that its workflow status. Falling back
    keeps the coverage derivation below meaningful on a plain-Jira site instead of
    reporting every story as untested.
    """
    return (
        f"MATCH (t:{MARKER}:Test)\n"
        "WHERE t.uid STARTS WITH $prefix AND t.run_status IS NULL\n"
        "SET t.run_status = toUpper(coalesce(t.test_state, t.status, 'TODO'))\n"
        "RETURN count(*) AS affected"
    ), {}


def _test_coverage_counts() -> tuple[str, dict[str, Any]]:
    return (
        f"MATCH (t:{MARKER}:Test)\n"
        "WHERE t.uid STARTS WITH $prefix\n"
        "OPTIONAL MATCH (t)-[:COVERS]->(req)\n"
        "WITH t, count(DISTINCT req) AS covered\n"
        "SET t.covers_count = covered, t.is_orphan = covered = 0\n"
        "RETURN count(*) AS affected"
    ), {}


def _requirement_coverage() -> tuple[str, dict[str, Any]]:
    """The headline number: is this requirement covered, and is its coverage green?

    The four states and the rule that picks between them are `coverage_status`; this
    is the same rule expressed as a single `SET` over the whole graph.
    """
    return (
        f"MATCH (n:{MARKER})\n"
        f"WHERE n.uid STARTS WITH $prefix AND {_REQUIREMENT}\n"
        "OPTIONAL MATCH (n)<-[:COVERS]-(t:Test)\n"
        "WITH n, collect(DISTINCT coalesce(t.run_status, 'TODO')) AS statuses,\n"
        "     count(DISTINCT t) AS test_count\n"
        "SET n.test_count = test_count,\n"
        "    n.coverage_status = CASE\n"
        "      WHEN test_count = 0 THEN 'UNCOVERED'\n"
        f"      WHEN any(s IN statuses WHERE s IN {_FAILING}) THEN 'FAIL'\n"
        f"      WHEN all(s IN statuses WHERE s IN {_PASSING}) THEN 'OK'\n"
        "      ELSE 'NOTRUN'\n"
        "    END\n"
        "RETURN count(*) AS affected"
    ), {}


def _defect_counts() -> tuple[str, dict[str, Any]]:
    return (
        f"MATCH (n:{MARKER})\n"
        "WHERE n.uid STARTS WITH $prefix\n"
        "OPTIONAL MATCH (n)-[:FOUND_DEFECT]->(d)\n"
        "WITH n, count(DISTINCT d) AS defects\n"
        "WHERE defects > 0\n"
        "SET n.defect_count = defects\n"
        "RETURN count(*) AS affected"
    ), {}


def _depth_from_root() -> tuple[str, dict[str, Any]]:
    """How far each issue sits below the top of its own hierarchy.

    Cheap to store and awkward to ask for: without it, every "show me this epic's
    whole tree grouped by level" query has to compute the depth itself, and the
    variable-length match that does it is the one people get wrong.
    """
    return (
        f"MATCH (n:{MARKER}:Issue)\n"
        "WHERE n.uid STARTS WITH $prefix\n"
        "OPTIONAL MATCH path = (n)-[:CHILD_OF*1..6]->(root)\n"
        "WHERE NOT (root)-[:CHILD_OF]->()\n"
        "WITH n, max(length(path)) AS depth\n"
        "SET n.hierarchy_depth = coalesce(depth, 0)\n"
        "RETURN count(*) AS affected"
    ), {}


# Order matters: run status has to exist before coverage is computed from it.
DERIVATIONS: tuple[tuple[str, Any], ...] = (
    ("clear_latest_run", _latest_run_clear),
    ("mark_latest_run", _latest_run_mark),
    ("run_status_fallback", _run_status_fallback),
    ("test_coverage_counts", _test_coverage_counts),
    ("requirement_coverage", _requirement_coverage),
    ("defect_counts", _defect_counts),
    ("hierarchy_depth", _depth_from_root),
)


def derivation_statements(prefix: str) -> Iterator[tuple[str, str, dict[str, Any]]]:
    """Every derivation as (name, statement, params), in the order they must run."""
    for name, build in DERIVATIONS:
        statement, params = build()
        yield name, statement, {"prefix": prefix, **params}


def run_derivations(run: Any, prefix: str) -> dict[str, int]:
    """Execute every derivation through a `loader.Runner`, reporting rows touched."""
    touched: dict[str, int] = {}
    for name, statement, params in derivation_statements(prefix):
        rows = run(statement, params)
        touched[name] = int(rows[0].get("affected", 0)) if rows else 0
    return touched


def summarise(counts: Mapping[str, int]) -> str:
    """One line per derivation, for the CLI."""
    return "\n".join(f"  {name:<24} {count}" for name, count in counts.items())
