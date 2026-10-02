"""Xray's own payloads as nodes and edges — the execution half of the graph.

Jira alone can say a test *exists* and, through an issue link, that it is meant to
cover a story. Only Xray can say the test ran, when, against which environment, with
which result per step, and which defect came out of it. That is the half that turns a
coverage list into evidence, so it gets the same treatment as the Jira side: every
distinguishable thing becomes a node.

One structural mismatch runs through all of it. Xray's GraphQL API identifies issues
by Jira's *numeric* id, while the rest of this graph keys issues on their key — the
thing people read, type and search for. Every reference therefore goes through
`IssueIndex`, built from the Jira read and topped up from the `jira { key }` field
Xray returns alongside each result. A reference that still cannot be resolved becomes
a node keyed on the numeric id and flagged, rather than being dropped: an unresolvable
reference is a gap in the read scope, which is worth seeing, and a missing edge is not.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .model import GraphBatch, GraphEdge, GraphNode, uid
from .ontology import Ontology

__all__ = ["IssueIndex", "extract_runs", "extract_tests", "extract_xray"]

SYSTEM = "jira"


class IssueIndex:
    """Numeric Jira issue id to issue key, with an honest fallback.

    Populated from whatever is already known — the Jira read, and every `jira { key }`
    Xray hands back — so that the common case resolves. When it does not, the uid
    falls back to `issue:id:12345` and the node is marked `unresolved_reference`,
    which is a thing `graph doctor` can count and a human can widen the JQL to fix.
    """

    __slots__ = ("_by_id", "site")

    def __init__(self, site: str, mapping: Mapping[str, str] | None = None) -> None:
        self.site = site
        self._by_id: dict[str, str] = {
            str(k): str(v) for k, v in (mapping or {}).items() if k and v
        }

    def learn(self, issue_id: Any, key: Any) -> None:
        if issue_id and key:
            self._by_id[str(issue_id)] = str(key)

    def key_of(self, issue_id: Any) -> str | None:
        return self._by_id.get(str(issue_id)) if issue_id else None

    def uid_of(self, issue_id: Any) -> str | None:
        if not issue_id:
            return None
        key = self.key_of(issue_id)
        return uid(SYSTEM, self.site, "issue", key or f"id:{issue_id}")

    def node_for(self, issue_id: Any, labels: tuple[str, ...]) -> GraphNode | None:
        """A stub for a referenced issue, carrying whichever identifier we have."""
        node_uid = self.uid_of(issue_id)
        if node_uid is None:
            return None
        key = self.key_of(issue_id)
        return GraphNode(
            uid=node_uid,
            labels=labels,
            props={
                "issue_id": str(issue_id),
                "key": key,
                "stub": True,
                "unresolved_reference": key is None,
            },
        )


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _jira_status(jira: Mapping[str, Any]) -> str:
    status = jira.get("status")
    if isinstance(status, Mapping):
        return _text(status.get("name"))
    return _text(status)


def _folder(batch: GraphBatch, site: str, folder: Any, owner_uid: str) -> None:
    """The test repository folder a test or plan lives in.

    Stored as one node per full path rather than a chain of nested folders: Xray
    reports the path as a string and nothing in it distinguishes a folder from its
    name, so building a hierarchy would mean inventing parentage the API never stated.
    """
    if not isinstance(folder, Mapping):
        return
    path = _text(folder.get("path"))
    if not path:
        return
    node = GraphNode(
        uid=uid(SYSTEM, site, "folder", path),
        labels=("RepositoryFolder",),
        props={"path": path, "name": path.rstrip("/").rsplit("/", 1)[-1], "stub": False},
    )
    batch.add_node(node)
    batch.add_edge(GraphEdge("IN_FOLDER", owner_uid, node.uid))


def _refs_with_total(block: Any) -> tuple[list[dict[str, Any]], int]:
    """The `results` of one of Xray's `{total, results}` sub-collections, and `total`.

    Every nested connection this package queries is capped at `limit: 100` (see
    `xray.py`), because GraphQL cannot page a connection nested inside another
    connection's page. `total` is what tells a caller whether that cap was actually
    hit — silently keeping only the first hundred members of a five-hundred-test
    plan is a wrong answer, not an incomplete one, so every owner of a nested
    connection is expected to compare `len(results)` against `total` and flag it.
    """
    if isinstance(block, Mapping):
        results = [
            entry for entry in (block.get("results") or []) if isinstance(entry, Mapping)
        ]
        total = block.get("total")
        return results, int(total) if isinstance(total, (int, float)) else len(results)
    if isinstance(block, list):
        results = [entry for entry in block if isinstance(entry, Mapping)]
        return results, len(results)
    return [], 0


def _refs(block: Any) -> list[dict[str, Any]]:
    """The `results` of one of Xray's `{total, results}` sub-collections."""
    return _refs_with_total(block)[0]


def _truncation(props: dict[str, Any], name: str, seen: int, total: int) -> None:
    """Flag on the owning node when a nested connection was capped by `limit: 100`."""
    if total > seen:
        props[f"{name}_total"] = total
        props[f"{name}_truncated"] = True


def steps_text(steps: Any) -> str | None:
    """A test's steps (action, data, expected result) as one searchable text, or None.

    The steps live on their own `:TestStep` nodes, which a full-text index over the test
    cannot reach. Copying their words onto the test is what lets a manual test be found
    by what it does ("open the billing page", "the invoice is exported") and not only by
    its title. One line per step, so a match stays attributable to a step when read.
    """
    lines: list[str] = []
    for step in steps or ():
        if not isinstance(step, Mapping):
            continue
        parts = [_text(step.get(name)) for name in ("action", "data", "result")]
        line = " | ".join(part for part in parts if part)
        if line:
            lines.append(line)
    return "\n".join(lines) or None


def extract_tests(
    tests: Iterable[Mapping[str, Any]],
    *,
    site: str,
    index: IssueIndex,
    ontology: Ontology,
) -> GraphBatch:
    """Tests with their steps, type, folder, preconditions, sets, plans and executions."""
    batch = GraphBatch()
    entries = list(tests)
    for test in entries:
        index.learn(test.get("issueId"), (test.get("jira") or {}).get("key"))

    for test in entries:
        test_uid = index.uid_of(test.get("issueId"))
        if test_uid is None:
            continue
        jira = test.get("jira") or {}
        batch.add_node(
            GraphNode(
                uid=test_uid,
                labels=("Issue", "Test"),
                props={
                    "issue_id": str(test.get("issueId")),
                    "key": jira.get("key"),
                    "title": jira.get("summary"),
                    "test_type": (test.get("testType") or {}).get("name"),
                    "test_kind": (test.get("testType") or {}).get("kind"),
                    "scenario_type": test.get("scenarioType"),
                    "gherkin": test.get("gherkin"),
                    "unstructured": test.get("unstructured"),
                    "steps_text": steps_text(test.get("steps")),
                    "xray": True,
                    "stub": False,
                },
            )
        )

        type_name = _text((test.get("testType") or {}).get("name"))
        if type_name:
            type_node = GraphNode(
                uid=uid(SYSTEM, site, "testtype", type_name),
                labels=("TestType",),
                props={
                    "name": type_name,
                    "kind": (test.get("testType") or {}).get("kind"),
                    "stub": False,
                },
            )
            batch.add_node(type_node)
            batch.add_edge(GraphEdge("HAS_TEST_TYPE", test_uid, type_node.uid))

        _folder(batch, site, test.get("folder"), test_uid)

        for position, step in enumerate(test.get("steps") or (), start=1):
            if not isinstance(step, Mapping):
                continue
            step_uid = uid(SYSTEM, site, "teststep", step.get("id") or f"{test.get('issueId')}#{position}")
            batch.add_node(
                GraphNode(
                    uid=step_uid,
                    labels=("TestStep",),
                    props={
                        "step_id": _text(step.get("id")) or None,
                        "index": position,
                        "action": step.get("action"),
                        "data": step.get("data"),
                        "expected": step.get("result"),
                        "stub": False,
                    },
                )
            )
            batch.add_edge(
                GraphEdge("HAS_STEP", test_uid, step_uid, props={"index": position})
            )

        precondition_refs, precondition_total = _refs_with_total(test.get("preconditions"))
        for precondition in precondition_refs:
            ref = precondition.get("preconditionRef")
            issue_id = ref.get("issueId") if isinstance(ref, Mapping) else precondition.get("issueId")
            node = index.node_for(issue_id, ("Issue", "Precondition"))
            if node is None:
                continue
            batch.add_node(node)
            batch.add_edge(GraphEdge("REQUIRES", test_uid, node.uid))
        _truncation(
            batch.nodes[test_uid].props, "preconditions", len(precondition_refs), precondition_total
        )

        for owner_block, labels, rel, reverse, name in (
            (test.get("testSets"), ("Issue", "TestSet"), "CONTAINS", True, "test_sets"),
            (test.get("testPlans"), ("Issue", "TestPlan"), "PLANS", True, "test_plans"),
            (test.get("testExecutions"), ("Issue", "TestExecution"), "CONTAINS", True, "test_executions"),
        ):
            refs, total = _refs_with_total(owner_block)
            for ref in refs:
                node = index.node_for(ref.get("issueId"), labels)
                if node is None:
                    continue
                batch.add_node(node)
                start, end = (node.uid, test_uid) if reverse else (test_uid, node.uid)
                batch.add_edge(GraphEdge(rel, start, end))
            _truncation(batch.nodes[test_uid].props, name, len(refs), total)
    return batch


def extract_preconditions(
    preconditions: Iterable[Mapping[str, Any]], *, site: str, index: IssueIndex
) -> GraphBatch:
    """Preconditions as first-class issues, with the tests that require them."""
    batch = GraphBatch()
    entries = list(preconditions)
    for entry in entries:
        index.learn(entry.get("issueId"), (entry.get("jira") or {}).get("key"))

    for entry in entries:
        node_uid = index.uid_of(entry.get("issueId"))
        if node_uid is None:
            continue
        jira = entry.get("jira") or {}
        batch.add_node(
            GraphNode(
                uid=node_uid,
                labels=("Issue", "Precondition"),
                props={
                    "issue_id": str(entry.get("issueId")),
                    "key": jira.get("key"),
                    "title": jira.get("summary"),
                    "definition": entry.get("definition"),
                    "precondition_type": (entry.get("preconditionType") or {}).get("name"),
                    "inline": bool(entry.get("inline")),
                    "xray": True,
                    "stub": False,
                },
            )
        )
        _folder(batch, site, entry.get("folder"), node_uid)
        refs, total = _refs_with_total(entry.get("tests"))
        for ref in refs:
            test = index.node_for(ref.get("issueId"), ("Issue", "Test"))
            if test is None:
                continue
            batch.add_node(test)
            batch.add_edge(GraphEdge("REQUIRES", test.uid, node_uid))
        _truncation(batch.nodes[node_uid].props, "tests", len(refs), total)
    return batch


def extract_containers(
    entries: Iterable[Mapping[str, Any]],
    *,
    site: str,
    index: IssueIndex,
    label: str,
    rel: str,
) -> GraphBatch:
    """Test sets and test plans — the two things that hold a list of tests.

    One function for both because their payloads differ only in the label and the
    relationship: a set groups tests for reuse, a plan schedules them for a release.
    Splitting them into two near-identical functions would just double the places a
    field has to be added.
    """
    batch = GraphBatch()
    listed = list(entries)
    for entry in listed:
        index.learn(entry.get("issueId"), (entry.get("jira") or {}).get("key"))

    for entry in listed:
        node_uid = index.uid_of(entry.get("issueId"))
        if node_uid is None:
            continue
        jira = entry.get("jira") or {}
        batch.add_node(
            GraphNode(
                uid=node_uid,
                labels=("Issue", label),
                props={
                    "issue_id": str(entry.get("issueId")),
                    "key": jira.get("key"),
                    "title": jira.get("summary"),
                    "status": _jira_status(jira) or None,
                    "ends_at": jira.get("duedate"),
                    "xray": True,
                    "stub": False,
                },
            )
        )
        for folder in entry.get("folders") or ():
            _folder(batch, site, folder, node_uid)

        test_refs, test_total = _refs_with_total(entry.get("tests"))
        for ref in test_refs:
            test = index.node_for(ref.get("issueId"), ("Issue", "Test"))
            if test is None:
                continue
            batch.add_node(test)
            batch.add_edge(GraphEdge(rel, node_uid, test.uid))
        _truncation(batch.nodes[node_uid].props, "tests", len(test_refs), test_total)

        exec_refs, exec_total = _refs_with_total(entry.get("testExecutions"))
        for ref in exec_refs:
            execution = index.node_for(ref.get("issueId"), ("Issue", "TestExecution"))
            if execution is None:
                continue
            batch.add_node(execution)
            batch.add_edge(GraphEdge("HAS_EXECUTION", node_uid, execution.uid))
        _truncation(
            batch.nodes[node_uid].props, "test_executions", len(exec_refs), exec_total
        )
    return batch


def extract_executions(
    executions: Iterable[Mapping[str, Any]], *, site: str, index: IssueIndex
) -> GraphBatch:
    """Test executions, the tests inside them, and the environments they ran against."""
    batch = GraphBatch()
    listed = list(executions)
    for entry in listed:
        index.learn(entry.get("issueId"), (entry.get("jira") or {}).get("key"))

    for entry in listed:
        node_uid = index.uid_of(entry.get("issueId"))
        if node_uid is None:
            continue
        jira = entry.get("jira") or {}
        batch.add_node(
            GraphNode(
                uid=node_uid,
                labels=("Issue", "TestExecution"),
                props={
                    "issue_id": str(entry.get("issueId")),
                    "key": jira.get("key"),
                    "title": jira.get("summary"),
                    "environments": list(entry.get("testEnvironments") or ()),
                    "xray": True,
                    "stub": False,
                },
            )
        )
        for name in entry.get("testEnvironments") or ():
            env = _environment_node(site, name)
            if env is None:
                continue
            batch.add_node(env)
            batch.add_edge(GraphEdge("IN_ENVIRONMENT", node_uid, env.uid))

        test_refs, test_total = _refs_with_total(entry.get("tests"))
        for ref in test_refs:
            test = index.node_for(ref.get("issueId"), ("Issue", "Test"))
            if test is None:
                continue
            batch.add_node(test)
            batch.add_edge(GraphEdge("CONTAINS", node_uid, test.uid))
        _truncation(batch.nodes[node_uid].props, "tests", len(test_refs), test_total)

        for ref in _refs(entry.get("testPlans")):
            plan = index.node_for(ref.get("issueId"), ("Issue", "TestPlan"))
            if plan is None:
                continue
            batch.add_node(plan)
            batch.add_edge(GraphEdge("HAS_EXECUTION", plan.uid, node_uid))
    return batch


def _environment_node(site: str, name: Any) -> GraphNode | None:
    text = _text(name)
    if not text:
        return None
    return GraphNode(
        uid=uid(SYSTEM, site, "environment", text),
        labels=("TestEnvironment",),
        props={"name": text, "stub": False},
    )


def extract_runs(
    runs: Iterable[Mapping[str, Any]],
    *,
    site: str,
    index: IssueIndex,
    ontology: Ontology,
) -> GraphBatch:
    """Test runs: the actual evidence, with per-step results and defects.

    A run is its own node rather than a property on the test, because a test has many
    runs and the interesting questions are all about the sequence — did this start
    failing, how long has it been red, which environment does it fail in. Collapsing
    the history onto the test would answer none of them.

    Run status gets both an edge to a shared `:TestRunStatus` node and a normalised
    `status` property on the run. The node makes "how many runs are in each status"
    a one-hop count; the property keeps a filter on a single run from needing a hop.
    """
    batch = GraphBatch()
    for run in runs:
        if not isinstance(run, Mapping) or not run.get("id"):
            continue
        run_uid = uid(SYSTEM, site, "testrun", run.get("id"))
        raw_status = (run.get("status") or {}).get("name")
        status = ontology.run_status(_text(raw_status))
        batch.add_node(
            GraphNode(
                uid=run_uid,
                labels=("TestRun",),
                props={
                    "run_id": str(run.get("id")),
                    "status": status,
                    "status_name": raw_status,
                    "started_on": run.get("startedOn"),
                    "finished_on": run.get("finishedOn"),
                    "comment": run.get("comment"),
                    "environments": list(run.get("testEnvironments") or ()),
                    "stub": False,
                },
            )
        )

        if status:
            status_node = GraphNode(
                uid=uid(SYSTEM, site, "runstatus", status),
                labels=("TestRunStatus",),
                props={
                    "name": status,
                    "reported_as": raw_status,
                    "color": (run.get("status") or {}).get("color"),
                    "stub": False,
                },
            )
            batch.add_node(status_node)
            batch.add_edge(GraphEdge("HAS_RESULT", run_uid, status_node.uid))

        test_uid = index.uid_of((run.get("test") or {}).get("issueId"))
        if test_uid:
            batch.add_node(index.node_for((run.get("test") or {}).get("issueId"), ("Issue", "Test")))
            batch.add_edge(GraphEdge("OF_TEST", run_uid, test_uid))

        execution_id = (run.get("testExecution") or {}).get("issueId")
        execution = index.node_for(execution_id, ("Issue", "TestExecution"))
        if execution is not None:
            batch.add_node(execution)
            batch.add_edge(GraphEdge("HAS_RUN", execution.uid, run_uid))

        for account in (run.get("executedById"), run.get("assigneeId")):
            if not account:
                continue
            user = GraphNode(
                uid=uid(SYSTEM, site, "user", account),
                labels=("User",),
                props={"account_id": str(account), "stub": True},
            )
            batch.add_node(user)
            batch.add_edge(GraphEdge("EXECUTED_BY", run_uid, user.uid))

        for name in run.get("testEnvironments") or ():
            env = _environment_node(site, name)
            if env is None:
                continue
            batch.add_node(env)
            batch.add_edge(GraphEdge("IN_ENVIRONMENT", run_uid, env.uid))

        for defect in run.get("defects") or ():
            defect_key = _text(defect if isinstance(defect, str) else (defect or {}).get("key"))
            if not defect_key:
                continue
            node = GraphNode(
                uid=uid(SYSTEM, site, "issue", defect_key),
                labels=("Issue", "Bug"),
                props={"key": defect_key, "stub": True},
            )
            batch.add_node(node)
            batch.add_edge(GraphEdge("FOUND_DEFECT", run_uid, node.uid))

        _run_evidence(batch, site, run_uid, run.get("evidence"))

        for position, step in enumerate(run.get("steps") or (), start=1):
            if not isinstance(step, Mapping):
                continue
            step_uid = uid(SYSTEM, site, "runstep", f"{run.get('id')}#{position}")
            step_status = ontology.run_status(_text((step.get("status") or {}).get("name")))
            batch.add_node(
                GraphNode(
                    uid=step_uid,
                    labels=("TestRunStep",),
                    props={
                        "index": position,
                        "status": step_status,
                        "action": step.get("action"),
                        "data": step.get("data"),
                        "expected": step.get("result"),
                        "actual": step.get("actualResult"),
                        "stub": False,
                    },
                )
            )
            batch.add_edge(
                GraphEdge("HAS_STEP_RESULT", run_uid, step_uid, props={"index": position})
            )
            if step.get("id"):
                batch.add_edge(
                    GraphEdge(
                        "OF_STEP", step_uid, uid(SYSTEM, site, "teststep", step.get("id"))
                    )
                )
            _run_evidence(batch, site, step_uid, step.get("evidence"))
    return batch


def _run_evidence(batch: GraphBatch, site: str, owner_uid: str, evidence: Any) -> None:
    for item in evidence or ():
        if not isinstance(item, Mapping) or not item.get("id"):
            continue
        node = GraphNode(
            uid=uid(SYSTEM, site, "evidence", item.get("id")),
            labels=("Evidence",),
            props={
                "evidence_id": str(item.get("id")),
                "filename": item.get("filename"),
                "stub": False,
            },
        )
        batch.add_node(node)
        batch.add_edge(GraphEdge("HAS_EVIDENCE", owner_uid, node.uid))


def extract_xray(
    *,
    site: str,
    ontology: Ontology,
    index: IssueIndex,
    tests: Iterable[Mapping[str, Any]] = (),
    preconditions: Iterable[Mapping[str, Any]] = (),
    test_sets: Iterable[Mapping[str, Any]] = (),
    test_plans: Iterable[Mapping[str, Any]] = (),
    executions: Iterable[Mapping[str, Any]] = (),
    runs: Iterable[Mapping[str, Any]] = (),
) -> GraphBatch:
    """Everything read from Xray, as one batch.

    Ordered so that the passes that *learn* issue id to key mappings run before the
    passes that need them: runs reference tests and executions by numeric id alone,
    so they go last and resolve against everything the earlier passes discovered.
    """
    batch = GraphBatch()
    batch.extend(extract_tests(tests, site=site, index=index, ontology=ontology))
    batch.extend(extract_preconditions(preconditions, site=site, index=index))
    batch.extend(
        extract_containers(test_sets, site=site, index=index, label="TestSet", rel="CONTAINS")
    )
    batch.extend(
        extract_containers(test_plans, site=site, index=index, label="TestPlan", rel="PLANS")
    )
    batch.extend(extract_executions(executions, site=site, index=index))
    batch.extend(extract_runs(runs, site=site, index=index, ontology=ontology))
    return batch
