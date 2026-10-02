"""The exact state of a project, as one JSON document.

The graph answers questions by traversal, which is the right shape for a database and
the wrong shape for anything that has to read the whole project at once — a report, an
agent deciding what to work on next, a diff between yesterday and today. Those need
the state laid out: every requirement with its coverage, every test with its steps and
its last result, every gap named.

So this module projects a `GraphBatch` into that document. It reads the same batch the
loader writes, which is the only reason the two can be trusted to agree: a state file
built from its own second extraction would be a second opinion, and the first time it
disagreed with the graph nobody would know which one was wrong. The coverage verdict
comes from `derive.coverage_status`, which is also the rule the Cypher derivation
applies, for the same reason.

Three rules decide what goes in.

*Resolved, not raw.* A requirement carries `assignee: "Sam Rivera"`, not an account id
to be joined against a people table. The graph keeps ids because it needs them for
identity; the document is for reading, so it spends the joins once here rather than
making every reader do them again.

*Structure over repetition.* A test's steps are nested inside the test, because a step
has no meaning apart from it. A person is listed once at the top level, because they
appear on forty issues and inlining them forty times would make the document larger
than the graph it summarises.

*Gaps are first-class.* Uncovered requirements, orphan tests, failing tests and
references that could not be resolved get their own section. They are the reason
somebody opens this file, and leaving them to be derived by the reader means every
reader re-implements the same four filters, slightly differently.

Nothing here is tracker-specific. Jira, Xray and Azure DevOps all land in the same
labels and relationships, so the projection walks the vocabulary and never asks which
system a node came from.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .derive import coverage_status
from .model import GraphBatch, GraphEdge, GraphNode

__all__ = ["REQUIREMENT_LABELS", "SCHEMA", "build_state", "state_of"]

SCHEMA = "alm.project-state/1"

# Labels that make a node a requirement — the things coverage is measured against.
REQUIREMENT_LABELS = ("Story", "Epic", "Feature", "Task", "Bug", "Requirement")

# Issue links worth listing on a requirement. `CHILD_OF` is excluded because the
# hierarchy is already expressed as `parent`/`children`, and stating it twice invites
# a reader to count one relationship as two facts.
_LINKS = (
    "BLOCKS",
    "RELATES_TO",
    "DUPLICATES",
    "CLONES",
    "CAUSES",
    "IMPLEMENTS",
    "AFFECTS",
    "LINKED_TO",
)


class _Index:
    """Adjacency over one batch, built once and asked many times.

    A projection walks the same relationship types out of thousands of nodes, so a
    scan per lookup would make the document an O(n²) build on any real project.
    Indexing once up front is what keeps it linear.
    """

    __slots__ = ("nodes", "_out", "_inc", "_by_label")

    def __init__(self, batch: GraphBatch) -> None:
        self.nodes: dict[str, GraphNode] = dict(batch.nodes)
        self._out: dict[tuple[str, str], list[GraphEdge]] = {}
        self._inc: dict[tuple[str, str], list[GraphEdge]] = {}
        self._by_label: dict[str, list[GraphNode]] = {}
        for edge in batch.edges.values():
            self._out.setdefault((edge.type, edge.start), []).append(edge)
            self._inc.setdefault((edge.type, edge.end), []).append(edge)
        for node in self.nodes.values():
            for label in node.labels:
                self._by_label.setdefault(label, []).append(node)

    def _resolve(self, edges: Iterable[GraphEdge], *, far: str) -> list[GraphNode]:
        found = (self.nodes.get(getattr(edge, far)) for edge in edges)
        return [node for node in found if node is not None]

    def targets(self, rel: str, start: str, *, label: str = "") -> list[GraphNode]:
        """Nodes this one points at along `rel`, optionally only those with a label."""
        nodes = self._resolve(self._out.get((rel, start), ()), far="end")
        return [n for n in nodes if label in n.labels] if label else nodes

    def sources(self, rel: str, end: str, *, label: str = "") -> list[GraphNode]:
        """Nodes pointing at this one along `rel`, optionally only those with a label."""
        nodes = self._resolve(self._inc.get((rel, end), ()), far="start")
        return [n for n in nodes if label in n.labels] if label else nodes

    def edges_out(self, rel: str, start: str) -> list[GraphEdge]:
        return list(self._out.get((rel, start), ()))

    def by_label(self, label: str) -> list[GraphNode]:
        return list(self._by_label.get(label, ()))

    def real(self, label: str) -> list[GraphNode]:
        """Nodes with this label that were actually fetched, in key order."""
        return sorted(
            (n for n in self.by_label(label) if not n.is_stub),
            key=lambda n: str(n.props.get("key") or n.uid),
        )


def _prop(node: GraphNode | None, key: str, default: Any = None) -> Any:
    if node is None:
        return default
    value = node.props.get(key)
    return default if value in (None, "") else value


def _name(node: GraphNode | None, *keys: str) -> str:
    """The most human-readable identifier a node carries."""
    if node is None:
        return ""
    for key in keys or ("name", "display_name", "title", "path", "key"):
        value = node.props.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _names(nodes: Iterable[GraphNode], *keys: str) -> list[str]:
    found = [_name(node, *keys) for node in nodes]
    return sorted({name for name in found if name})


def _first(nodes: list[GraphNode], *keys: str) -> str:
    return _name(nodes[0], *keys) if nodes else ""


def _key_of(node: GraphNode) -> str:
    """A node's identifier as a reader would cite it.

    Falls back to `source_issue` for the inline preconditions a site without Xray
    models as a custom field: those have no key of their own, and identifying them by
    the issue that declares them is both true and what somebody would search for.
    """
    return str(node.props.get("key") or node.props.get("source_issue") or "").strip()


def _keys(nodes: Iterable[GraphNode]) -> list[str]:
    return sorted({key for key in (_key_of(node) for node in nodes) if key})


def _ordered_steps(nodes: Iterable[GraphNode]) -> list[GraphNode]:
    return sorted(nodes, key=lambda n: int(n.props.get("index") or 0))


def _compact(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The same mapping with empty values dropped.

    An absent field says "this project does not use this" far more clearly than a
    field present as `null` on every one of four hundred issues, and this document is
    meant to be read. `False` and `0` are kept — they are answers.
    """
    return {k: v for k, v in payload.items() if v not in (None, "", [], {})}


# -- per-node projections --------------------------------------------------


def _person(index: _Index, node: GraphNode) -> dict[str, Any]:
    return _compact(
        {
            "id": _prop(node, "account_id") or _prop(node, "username"),
            "name": _name(node, "display_name", "username", "name"),
            "email": _prop(node, "email"),
            "active": node.props.get("active"),
            "assigned": len(index.sources("ASSIGNED_TO", node.uid)),
            "reported": len(index.sources("REPORTED_BY", node.uid)),
            "executed": len(index.sources("EXECUTED_BY", node.uid)),
            "watching": len(index.sources("WATCHED_BY", node.uid)),
        }
    )


def _run(index: _Index, node: GraphNode) -> dict[str, Any]:
    return _compact(
        {
            "id": _prop(node, "run_id"),
            "status": _prop(node, "status"),
            "reported_as": _prop(node, "status_name"),
            "started_at": _prop(node, "started_on"),
            "finished_at": _prop(node, "finished_on"),
            "duration_ms": node.props.get("duration_ms"),
            "executed_by": _names(index.targets("EXECUTED_BY", node.uid)),
            "environments": _names(index.targets("IN_ENVIRONMENT", node.uid)),
            "defects": _keys(index.targets("FOUND_DEFECT", node.uid)),
            "evidence": _names(index.targets("HAS_EVIDENCE", node.uid), "filename"),
            "comment": _prop(node, "comment"),
            "failure_type": _prop(node, "failure_type"),
            "error": _prop(node, "error_message"),
            "steps": [
                _compact(
                    {
                        "index": step.props.get("index"),
                        "status": _prop(step, "status"),
                        "action": _prop(step, "action"),
                        "expected": _prop(step, "expected"),
                        "actual": _prop(step, "actual"),
                    }
                )
                for step in _ordered_steps(index.targets("HAS_STEP_RESULT", node.uid))
            ],
        }
    )


def _newest_first(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Runs ordered by finish time, falling back to start time.

    The same ordering `derive`'s `LATEST_RUN` uses, and for the same reason: a run
    still executing has no finish time, and sorting it as the oldest thing in the
    project is exactly backwards for the run somebody most wants to see.
    """
    return sorted(
        runs,
        key=lambda r: (str(r.get("finished_at") or ""), str(r.get("started_at") or "")),
        reverse=True,
    )


def _run_status(node: GraphNode, latest: Mapping[str, Any]) -> str:
    """What this test's most recent evidence says, however thin that evidence is.

    The same fallback chain the `run_status_fallback` derivation applies: a real run
    if there is one, otherwise whatever the site records on the issue — a declared
    test-state field, or failing that the workflow status. Without it every test on a
    site with no execution data would read as untested, and every requirement those
    tests cover would report as a planning gap it is not.
    """
    status = str(latest.get("status") or "").strip()
    if status:
        return status
    declared = (
        _prop(node, "run_status") or _prop(node, "test_state") or _prop(node, "status")
    )
    return str(declared).strip().upper() if declared else "TODO"


def _runs_of_test(index: _Index, node: GraphNode) -> list[dict[str, Any]]:
    """Every run of one test, newest first."""
    return _newest_first([_run(index, r) for r in index.sources("OF_TEST", node.uid)])


def _test(index: _Index, node: GraphNode, runs: list[dict[str, Any]]) -> dict[str, Any]:
    latest = runs[0] if runs else {}
    return _compact(
        {
            "key": _key_of(node),
            "title": _prop(node, "title"),
            "type": _prop(node, "test_type"),
            "status": _prop(node, "status"),
            "assignee": _first(index.targets("ASSIGNED_TO", node.uid)),
            "folder": _first(index.targets("IN_FOLDER", node.uid), "path"),
            "gherkin": _prop(node, "gherkin"),
            "unstructured": _prop(node, "unstructured"),
            "automation_status": _prop(node, "automation_status"),
            "labels": _names(index.targets("HAS_LABEL", node.uid)),
            "covers": _keys(index.targets("COVERS", node.uid)),
            "preconditions": _keys(index.targets("REQUIRES", node.uid)),
            # A test set and a test execution both hold their tests with `CONTAINS`,
            # so the far end is separated by label rather than by relationship.
            "test_sets": _keys(index.sources("CONTAINS", node.uid, label="TestSet")),
            "test_plans": _keys(index.sources("PLANS", node.uid, label="TestPlan")),
            "executions": _keys(
                index.sources("CONTAINS", node.uid, label="TestExecution")
            ),
            "steps": [
                _compact(
                    {
                        "index": step.props.get("index"),
                        "action": _prop(step, "action"),
                        "data": _prop(step, "data"),
                        "expected": _prop(step, "expected"),
                    }
                )
                for step in _ordered_steps(index.targets("HAS_STEP", node.uid))
            ],
            "run_status": _run_status(node, latest),
            "latest_run": latest,
            "run_count": len(runs),
            "runs": runs,
        }
    )


def _links_of(index: _Index, node: GraphNode) -> list[dict[str, Any]]:
    """This issue's outward links, each as its relationship and the key it points at.

    The far end is named from the uid's trailing segment rather than by looking the
    node up, because a link can point outside the read scope: the stub carries the key
    in its uid whether or not anything ever fetched the issue itself.
    """
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for rel in _LINKS:
        for edge in index.edges_out(rel, node.uid):
            far = str(edge.end).rsplit(":", 1)[-1]
            if not far:
                continue
            entry: dict[str, Any] = {"type": rel, "to": far}
            link_type = edge.props.get("link_type")
            if link_type:
                entry["as"] = link_type
            seen[(rel, far)] = entry
    return [seen[key] for key in sorted(seen)]


def _mentioned_by(index: _Index, node: GraphNode) -> list[dict[str, Any]]:
    """The delivery artefacts naming this issue — the GitLab half of traceability."""
    found = []
    for other in index.sources("MENTIONS", node.uid):
        kind = next((l for l in other.labels if l in ("Pipeline", "MergeRequest")), "")
        if not kind:
            continue
        found.append(
            _compact(
                {
                    "kind": kind,
                    "ref": _name(other, "title", "ref"),
                    "status": _prop(other, "status") or _prop(other, "state"),
                    "url": _prop(other, "url"),
                }
            )
        )
    return sorted(found, key=lambda m: (m.get("kind", ""), m.get("ref", "")))


def _requirement(
    index: _Index, node: GraphNode, run_status: Mapping[str, str]
) -> dict[str, Any]:
    tests = index.sources("COVERS", node.uid)
    # Taken from the same per-test verdict the `tests` section publishes, never
    # recomputed from the test's own properties: a test's status lives on its runs,
    # and a coverage figure derived from a second rule would disagree with the test
    # list in the same document.
    statuses = [run_status.get(test.uid) or _run_status(test, {}) for test in tests]
    return _compact(
        {
            "key": _key_of(node),
            "type": _prop(node, "type") or _prop(node, "work_item_type"),
            "kind": sorted(l for l in node.labels if l in REQUIREMENT_LABELS),
            "title": _prop(node, "title"),
            "status": _prop(node, "status"),
            "status_category": _prop(node, "status_category"),
            "priority": _prop(node, "priority"),
            "severity": _prop(node, "severity"),
            "resolution": _prop(node, "resolution"),
            "assignee": _first(index.targets("ASSIGNED_TO", node.uid)),
            "reporter": _first(index.targets("REPORTED_BY", node.uid)),
            "created": _prop(node, "created"),
            "updated": _prop(node, "updated"),
            "resolved_at": _prop(node, "resolved_at"),
            "due": _prop(node, "due_date"),
            "story_points": node.props.get("story_points"),
            "url": _prop(node, "url"),
            "labels": _names(index.targets("HAS_LABEL", node.uid)),
            "components": _names(index.targets("HAS_COMPONENT", node.uid), "name", "path"),
            "fix_versions": _names(index.targets("FIXED_IN", node.uid)),
            "sprints": _names(index.targets("IN_SPRINT", node.uid), "name", "path"),
            "parent": _keys(index.targets("CHILD_OF", node.uid))[:1],
            "children": _keys(index.sources("CHILD_OF", node.uid)),
            "links": _links_of(index, node),
            "tests": _keys(tests),
            "coverage": {
                "status": coverage_status(statuses),
                "test_count": len(tests),
                "run_statuses": sorted(set(statuses)),
            },
            # Runs that filed this issue as a defect — only ever non-zero on a bug,
            # and the shortest path from a failure back to the test that found it.
            "found_by_runs": len(index.sources("FOUND_DEFECT", node.uid)),
            "comment_count": len(index.targets("HAS_COMMENT", node.uid)),
            "attachment_count": len(index.targets("HAS_ATTACHMENT", node.uid)),
            "change_count": len(index.sources("ON", node.uid)),
            "mentioned_by": _mentioned_by(index, node),
        }
    )


def _precondition(index: _Index, node: GraphNode) -> dict[str, Any]:
    return _compact(
        {
            "key": _key_of(node),
            "title": _prop(node, "title"),
            "definition": _prop(node, "definition"),
            "type": _prop(node, "precondition_type"),
            "inline": node.props.get("inline"),
            "used_by": _keys(index.sources("REQUIRES", node.uid)),
        }
    )


def _container(index: _Index, node: GraphNode, rel: str) -> dict[str, Any]:
    """A test set or a test plan: what it holds, and what has been run from it."""
    return _compact(
        {
            "key": _key_of(node),
            "title": _prop(node, "title"),
            "status": _prop(node, "status"),
            "owner": _first(index.targets("ASSIGNED_TO", node.uid)),
            "starts_at": _prop(node, "starts_at"),
            "ends_at": _prop(node, "ends_at"),
            "tests": _keys(index.targets(rel, node.uid, label="Test")),
            "test_sets": _keys(index.targets(rel, node.uid, label="TestSet")),
            "executions": _keys(index.targets("HAS_EXECUTION", node.uid)),
            "tests_truncated": node.props.get("tests_truncated"),
            "tests_total": node.props.get("tests_total"),
        }
    )


def _execution(index: _Index, node: GraphNode) -> dict[str, Any]:
    runs = index.targets("HAS_RUN", node.uid)
    results: dict[str, int] = {}
    for run in runs:
        status = str(run.props.get("status") or "TODO")
        results[status] = results.get(status, 0) + 1
    return _compact(
        {
            "key": _key_of(node),
            "title": _prop(node, "title"),
            "status": _prop(node, "status"),
            "started_at": _prop(node, "started_on"),
            "finished_at": _prop(node, "finished_on"),
            "environments": _names(index.targets("IN_ENVIRONMENT", node.uid)),
            "tests": _keys(index.targets("CONTAINS", node.uid, label="Test")),
            "test_plans": _keys(
                index.sources("HAS_EXECUTION", node.uid, label="TestPlan")
            ),
            "run_count": len(runs),
            "results": dict(sorted(results.items())),
        }
    )


def _sprint(index: _Index, node: GraphNode) -> dict[str, Any]:
    """One sprint, whichever tracker calls it that.

    Jira reports a sprint as a numbered object with dates and a state; Azure reports
    an iteration path and nothing more. Both are `:Sprint`, so both are read for here
    and the fields the tracker did not supply are simply absent.
    """
    return _compact(
        {
            "id": _prop(node, "sprint_id"),
            "name": _name(node, "name", "path"),
            "path": _prop(node, "path"),
            "state": _prop(node, "state"),
            "starts_at": _prop(node, "start_date"),
            "ends_at": _prop(node, "end_date"),
            "completed_at": _prop(node, "complete_date"),
            "goal": _prop(node, "goal"),
            "boards": _names(index.targets("ON_BOARD", node.uid), "name", "board_id"),
            "issues": _keys(index.sources("IN_SPRINT", node.uid)),
        }
    )


def _delivery(index: _Index) -> dict[str, Any]:
    """Repositories, their pipelines and merge requests, and the issues those name."""
    repositories = []
    for repo in index.by_label("GitRepository"):
        pipelines = sorted(
            index.targets("HAS_PIPELINE", repo.uid),
            key=lambda n: str(n.props.get("created") or ""),
            reverse=True,
        )
        requests = sorted(
            index.targets("HAS_MERGE_REQUEST", repo.uid),
            key=lambda n: str(n.props.get("updated") or ""),
            reverse=True,
        )
        repositories.append(
            _compact(
                {
                    "path": _prop(repo, "path") or _key_of(repo),
                    "name": _name(repo),
                    "url": _prop(repo, "url"),
                    "default_branch": _prop(repo, "default_branch"),
                    "pipelines": [
                        _compact(
                            {
                                "id": _prop(node, "pipeline_id"),
                                "status": _prop(node, "status"),
                                "ref": _prop(node, "ref"),
                                "created": _prop(node, "created"),
                                "url": _prop(node, "url"),
                                "triggered_by": _first(
                                    index.targets("TRIGGERED_BY", node.uid)
                                ),
                                "mentions": _keys(index.targets("MENTIONS", node.uid)),
                            }
                        )
                        for node in pipelines
                    ],
                    "merge_requests": [
                        _compact(
                            {
                                "iid": _prop(node, "iid"),
                                "title": _prop(node, "title"),
                                "state": _prop(node, "state"),
                                "draft": node.props.get("draft"),
                                "source_branch": _prop(node, "source_branch"),
                                "merged_at": _prop(node, "merged_at"),
                                "url": _prop(node, "url"),
                                "author": _first(index.sources("AUTHORED", node.uid)),
                                "mentions": _keys(index.targets("MENTIONS", node.uid)),
                            }
                        )
                        for node in requests
                    ],
                }
            )
        )
    milestones = [
        _compact(
            {
                "title": _prop(node, "title"),
                "state": _prop(node, "state"),
                "starts_on": _prop(node, "starts_on"),
                "due_on": _prop(node, "due_on"),
                "url": _prop(node, "url"),
            }
        )
        for node in index.by_label("Milestone")
    ]
    return _compact({"repositories": repositories, "milestones": milestones})


# -- the document ----------------------------------------------------------


def _requirements_in(index: _Index) -> list[GraphNode]:
    """Every fetched requirement, once, in key order.

    Collected through a dict keyed by uid rather than by concatenating label lists,
    because one node can legitimately carry two requirement labels — an ontology that
    maps a type onto both `:Task` and `:Requirement` is a reasonable thing to write.
    Anything also labelled `:Test` is excluded: a test is evidence about a requirement,
    never one itself, and counting it as both would inflate coverage with tests that
    cover themselves.
    """
    found: dict[str, GraphNode] = {}
    for label in REQUIREMENT_LABELS:
        for node in index.by_label(label):
            if node.is_stub or "Test" in node.labels:
                continue
            found[node.uid] = node
    return sorted(found.values(), key=lambda n: str(n.props.get("key") or n.uid))


def build_state(
    batch: GraphBatch,
    *,
    project: str = "",
    version: int = 0,
    generated_at: str = "",
    sources: Iterable[str] = (),
    sites: Iterable[str] = (),
    xray: Mapping[str, Any] | None = None,
    read: Mapping[str, Any] | None = None,
    errors: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """One batch, laid out as the project's state.

    Stubs are excluded from every listing but still counted. A node that was only
    referenced carries no title, no status and no assignee, so listing it beside real
    issues would put a row of empty fields in the middle of the document and invite a
    reader to read "not fetched" as "has no assignee". How many there are is worth
    knowing, and that is reported under `gaps.referenced_outside_scope`.
    """
    index = _Index(batch)

    # Runs are collected once per test and then read twice — by the test itself and by
    # every requirement it covers. Collecting them per reader would walk the same runs
    # once per coverage edge, which on a well-covered project is most of the work.
    runs_by_test = {node.uid: _runs_of_test(index, node) for node in index.by_label("Test")}
    run_status = {
        node.uid: _run_status(node, runs_by_test[node.uid][0] if runs_by_test[node.uid] else {})
        for node in index.by_label("Test")
    }

    requirements = [
        _requirement(index, node, run_status) for node in _requirements_in(index)
    ]
    tests = [_test(index, node, runs_by_test.get(node.uid, [])) for node in index.real("Test")]
    executions = [_execution(index, node) for node in index.real("TestExecution")]

    coverage: dict[str, int] = {}
    for requirement in requirements:
        status = requirement["coverage"]["status"]
        coverage[status] = coverage.get(status, 0) + 1

    run_results: dict[str, int] = {}
    for node in index.by_label("TestRun"):
        status = str(node.props.get("status") or "TODO")
        run_results[status] = run_results.get(status, 0) + 1

    stubs = [node for node in batch.nodes.values() if node.is_stub]
    unresolved = [n for n in stubs if n.props.get("unresolved_reference")]
    people = [n for n in index.by_label("User") if not n.is_stub]

    return {
        "schema": SCHEMA,
        "project": project,
        "generated_at": generated_at,
        "version": version,
        "sources": list(sources),
        "sites": list(sites),
        "xray": dict(xray or {}),
        "read": dict(read or {}),
        "totals": {
            "requirements": len(requirements),
            "tests": len(tests),
            "preconditions": len(index.real("Precondition")),
            "test_sets": len(index.real("TestSet")),
            "test_plans": len(index.real("TestPlan")),
            "executions": len(executions),
            "runs": sum(run_results.values()),
            "people": len(people),
            "sprints": len(index.real("Sprint")),
            "nodes": len(batch.nodes),
            "relationships": len(batch.edges),
            "coverage": dict(sorted(coverage.items())),
            "run_results": dict(sorted(run_results.items())),
        },
        "requirements": requirements,
        "tests": tests,
        "preconditions": [_precondition(index, n) for n in index.real("Precondition")],
        "test_sets": [_container(index, n, "CONTAINS") for n in index.real("TestSet")],
        "test_plans": [_container(index, n, "PLANS") for n in index.real("TestPlan")],
        "executions": executions,
        "sprints": [_sprint(index, node) for node in index.real("Sprint")],
        "people": sorted(
            (_person(index, node) for node in people), key=lambda p: p.get("name", "")
        ),
        "delivery": _delivery(index),
        "gaps": {
            "uncovered_requirements": [
                r["key"]
                for r in requirements
                if r["coverage"]["status"] == "UNCOVERED" and r.get("key")
            ],
            "failing_requirements": [
                r["key"]
                for r in requirements
                if r["coverage"]["status"] == "FAIL" and r.get("key")
            ],
            "orphan_tests": [
                t["key"] for t in tests if not t.get("covers") and t.get("key")
            ],
            "never_run_tests": [
                t["key"] for t in tests if not t.get("run_count") and t.get("key")
            ],
            "failing_tests": [
                t["key"]
                for t in tests
                if t.get("run_status") in ("FAIL", "ABORTED") and t.get("key")
            ],
            "referenced_outside_scope": len(stubs),
            "unresolved_references": sorted(
                str(n.props.get("issue_id") or n.uid) for n in unresolved
            ),
        },
        "errors": [dict(err) for err in errors],
    }


def state_of(result: Any, batch: GraphBatch) -> dict[str, Any]:
    """The state document for a completed build, taking its metadata from the result."""
    return build_state(
        batch,
        project=getattr(result, "project", ""),
        version=getattr(result, "version", 0),
        generated_at=getattr(result, "finished_at", "")
        or getattr(result, "started_at", ""),
        sources=getattr(result, "sources", ()),
        sites=getattr(result, "sites", ()),
        xray=getattr(result, "xray_tier", {}),
        read=getattr(result, "read", {}),
        errors=getattr(result, "errors", ()),
    )
