"""Azure DevOps work items and test plans, in the same vocabulary as everything else.

The point of this module is that it produces no vocabulary of its own. An Azure
Product Backlog Item becomes a `:Story`, an area path becomes a `:Component`, an
iteration becomes a `:Sprint`, a test suite becomes a `:TestSet`, a test run becomes a
`:TestExecution` holding one `:TestRun` per result. A question asked of the graph —
which requirements are uncovered, which tests last failed, who is carrying the sprint
— is then the same question and the same Cypher whether the project lives in Jira,
in Xray, or here.

That mapping is a decision, not a coincidence, and it is worth being explicit about
what it costs: Azure's own words are kept as properties (`work_item_type`,
`area_path`, `outcome`) so nothing is destroyed, but the labels and relationships are
the shared ones. The alternative — an `:AzureWorkItem` label beside `:Issue` — would
have meant every traversal in `queries.py` growing a second branch, and the second
branch is the one that stops being maintained.

Pure, like the Jira and Xray extractors: dictionaries in, `GraphBatch` out.
"""

from __future__ import annotations

import re
from html import unescape
from typing import Any, Iterable, Mapping

from .model import GraphBatch, GraphEdge, GraphNode, uid
from .ontology import Ontology

__all__ = [
    "extract_test_plans",
    "extract_test_results",
    "extract_work_items",
    "extract_azure",
]

SYSTEM = "azuredevops"

# The trailing `-Forward` / `-Reverse` on an Azure link type says which end of the
# pair this side is, which the ontology's own `reverse` flag already expresses. The
# suffix is stripped before lookup so one mapping entry covers both halves.
_DIRECTION = re.compile(r"-(forward|reverse)$", re.IGNORECASE)

# Work item ids are the last segment of the relation's URL, which is the only place
# Azure states them — the relation itself carries no id field.
_ID_IN_URL = re.compile(r"/(?:workItems|workitems)/(\d+)\s*$")

# `<steps><step id="2" type="ValidateStep"><parameterizedString>…` — the test case
# steps field is an XML document stored in a string, and the two parameterized
# strings inside each step are the action and the expected result, in that order.
_STEP = re.compile(r"<step\b[^>]*\bid=\"(\d+)\"[^>]*>(.*?)</step>", re.DOTALL | re.IGNORECASE)
_PARAM = re.compile(r"<parameterizedString[^>]*>(.*?)</parameterizedString>", re.DOTALL | re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        for key in ("displayName", "name", "value", "text"):
            found = value.get(key)
            if isinstance(found, str) and found.strip():
                return found.strip()
        return ""
    return str(value).strip()


def _plain(markup: str) -> str:
    """HTML-ish field content as readable text.

    Azure stores descriptions and test step strings as HTML fragments. Tags are
    stripped rather than kept because a graph property holding markup answers no
    question anybody asks of it — every comparison against it becomes a problem of
    matching tags instead of matching content.

    Entities are decoded before the tags are stripped, not after. A test case's steps
    are HTML that has been escaped once more to survive being stored inside an XML
    element, so its paragraph tags arrive as `&lt;P&gt;` — stripping first would leave
    every one of them in the property as literal text.
    """
    if not markup:
        return ""
    return " ".join(_TAG.sub(" ", unescape(markup)).split())


def _identity(site: str, person: Any) -> GraphNode | None:
    """One Azure identity as a `:User`.

    Merged on the identity GUID where Azure supplied one and on the unique name (the
    sign-in address) otherwise, so the same person read from a work item field and
    from a test result is one node. Display name is never the identity: Azure allows
    duplicates and renames freely.
    """
    if not isinstance(person, Mapping):
        name = _text(person)
        if not name:
            return None
        return GraphNode(
            uid=uid(SYSTEM, site, "user", f"name:{name}"),
            labels=("User",),
            props={"display_name": name, "identified_by": "display_name", "stub": True},
        )
    guid = _text(person.get("id"))
    unique = _text(person.get("uniqueName")) or _text(person.get("mailAddress"))
    display = _text(person.get("displayName"))
    natural = guid or unique or (f"name:{display}" if display else "")
    if not natural:
        return None
    return GraphNode(
        uid=uid(SYSTEM, site, "user", natural),
        labels=("User",),
        props={
            "account_id": guid,
            "display_name": display,
            "email": unique if "@" in unique else None,
            "identified_by": "account_id" if guid else ("email" if unique else "display_name"),
            "stub": False,
        },
    )


def _project_node(site: str, name: str) -> GraphNode | None:
    if not name:
        return None
    return GraphNode(
        uid=uid(SYSTEM, site, "project", name),
        labels=("Project",),
        props={"key": name, "name": name, "system": SYSTEM, "stub": False},
    )


def _path_node(site: str, kind: str, label: str, path: str) -> GraphNode | None:
    """An area or iteration path as a node, keyed on the whole path.

    Kept whole rather than split into a tree for the same reason an Xray repository
    folder is: the leaf name alone is not unique — `Team A\\Backlog` and
    `Team B\\Backlog` are different places — and the path is what Azure actually
    guarantees to be stable.
    """
    cleaned = path.strip().strip("\\").strip()
    if not cleaned:
        return None
    return GraphNode(
        uid=uid(SYSTEM, site, kind, cleaned),
        labels=(label,),
        props={
            "path": cleaned,
            "name": cleaned.rsplit("\\", 1)[-1],
            "stub": False,
        },
    )


def _related_id(relation: Mapping[str, Any]) -> str:
    match = _ID_IN_URL.search(str(relation.get("url") or ""))
    return match.group(1) if match else ""


def _issue_uid(site: str, work_item_id: Any) -> str:
    return uid(SYSTEM, site, "issue", work_item_id)


def _steps_of(site: str, batch: GraphBatch, owner_uid: str, markup: str) -> None:
    """The test case's steps, parsed out of the XML Azure stores them as."""
    for position, (step_id, body) in enumerate(_STEP.findall(markup or ""), start=1):
        parts = [_plain(part) for part in _PARAM.findall(body)]
        action = parts[0] if parts else ""
        expected = parts[1] if len(parts) > 1 else ""
        if not action and not expected:
            continue
        step_uid = uid(SYSTEM, site, "teststep", f"{owner_uid.rsplit(':', 1)[-1]}#{step_id}")
        batch.add_node(
            GraphNode(
                uid=step_uid,
                labels=("TestStep",),
                props={
                    "step_id": step_id,
                    "index": position,
                    "action": action,
                    "expected": expected,
                    "stub": False,
                },
            )
        )
        batch.add_edge(GraphEdge("HAS_STEP", owner_uid, step_uid, props={"index": position}))


def extract_work_items(
    items: Iterable[Mapping[str, Any]],
    *,
    site: str,
    ontology: Ontology,
    source: str,
    base_url: str = "",
) -> GraphBatch:
    """Work items with their fields, people, classification and links."""
    batch = GraphBatch()
    for item in items:
        if not isinstance(item, Mapping) or item.get("id") in (None, ""):
            continue
        fields = item.get("fields") if isinstance(item.get("fields"), Mapping) else {}
        item_id = str(item.get("id"))
        node_uid = _issue_uid(site, item_id)
        work_type = _text(fields.get("System.WorkItemType"))
        project_name = _text(fields.get("System.TeamProject"))
        state = _text(fields.get("System.State"))

        batch.add_node(
            GraphNode(
                uid=node_uid,
                labels=ontology.labels_for_type(work_type),
                props={
                    "key": item_id,
                    "issue_id": item_id,
                    "title": _text(fields.get("System.Title")),
                    "description": _plain(_text(fields.get("System.Description"))),
                    "status": state,
                    "status_category": ontology.status_category(state),
                    "work_item_type": work_type,
                    "area_path": _text(fields.get("System.AreaPath")),
                    "iteration_path": _text(fields.get("System.IterationPath")),
                    "created": _text(fields.get("System.CreatedDate")),
                    "updated": _text(fields.get("System.ChangedDate")),
                    "closed": _text(fields.get("Microsoft.VSTS.Common.ClosedDate")),
                    "priority": fields.get("Microsoft.VSTS.Common.Priority"),
                    "severity": _text(fields.get("Microsoft.VSTS.Common.Severity")),
                    "story_points": fields.get("Microsoft.VSTS.Scheduling.StoryPoints"),
                    "effort": fields.get("Microsoft.VSTS.Scheduling.Effort"),
                    "remaining_work": fields.get("Microsoft.VSTS.Scheduling.RemainingWork"),
                    "automation_status": _text(
                        fields.get("Microsoft.VSTS.TCM.AutomationStatus")
                    ),
                    "revision": item.get("rev"),
                    "url": f"{base_url}/{project_name}/_workitems/edit/{item_id}"
                    if base_url and project_name
                    else _text(item.get("url")),
                    "source": source,
                    "system": SYSTEM,
                    "stub": False,
                },
            )
        )

        project_node = _project_node(site, project_name)
        if project_node is not None:
            batch.add_node(project_node)
            batch.add_edge(GraphEdge("IN_PROJECT", node_uid, project_node.uid))

        if work_type:
            type_node = GraphNode(
                uid=uid(SYSTEM, site, "issuetype", work_type),
                labels=("IssueType",),
                props={"name": work_type, "system": SYSTEM, "stub": False},
            )
            batch.add_node(type_node)
            batch.add_edge(GraphEdge("HAS_TYPE", node_uid, type_node.uid))

        if state:
            status_node = GraphNode(
                uid=uid(SYSTEM, site, "status", state),
                labels=("Status",),
                props={"name": state, "system": SYSTEM, "stub": False},
            )
            batch.add_node(status_node)
            batch.add_edge(GraphEdge("HAS_STATUS", node_uid, status_node.uid))

        for field_name, relationship in (
            ("System.AssignedTo", "ASSIGNED_TO"),
            ("System.CreatedBy", "CREATED_BY"),
            ("System.ChangedBy", "MADE"),
        ):
            person = _identity(site, fields.get(field_name))
            if person is None:
                continue
            batch.add_node(person)
            batch.add_edge(GraphEdge(relationship, node_uid, person.uid))

        area = _path_node(site, "component", "Component", _text(fields.get("System.AreaPath")))
        if area is not None:
            batch.add_node(area)
            batch.add_edge(GraphEdge("HAS_COMPONENT", node_uid, area.uid))

        iteration = _path_node(
            site, "sprint", "Sprint", _text(fields.get("System.IterationPath"))
        )
        if iteration is not None:
            batch.add_node(iteration)
            batch.add_edge(GraphEdge("IN_SPRINT", node_uid, iteration.uid))

        for tag in str(fields.get("System.Tags") or "").split(";"):
            name = tag.strip()
            if not name:
                continue
            label_node = GraphNode(
                uid=uid(SYSTEM, site, "label", name),
                labels=("Label",),
                props={"name": name, "stub": False},
            )
            batch.add_node(label_node)
            batch.add_edge(GraphEdge("HAS_LABEL", node_uid, label_node.uid))

        steps_markup = _text(fields.get("Microsoft.VSTS.TCM.Steps"))
        if steps_markup:
            _steps_of(site, batch, node_uid, steps_markup)

        _relations(batch, site, node_uid, item.get("relations"), ontology)

    return batch


def _relations(
    batch: GraphBatch,
    site: str,
    node_uid: str,
    relations: Any,
    ontology: Ontology,
) -> None:
    """Work item links, attachments and hyperlinks.

    Azure reports both halves of every link — a parent carries `Hierarchy-Forward` to
    its child and the child carries `Hierarchy-Reverse` back — so the direction is
    normalised through the ontology exactly as Jira's inward/outward pair is, and the
    two halves merge onto one edge instead of becoming two opposing ones.
    """
    for relation in relations or ():
        if not isinstance(relation, Mapping):
            continue
        rel_name = _text(relation.get("rel"))
        if not rel_name:
            continue
        attributes = relation.get("attributes")
        attributes = attributes if isinstance(attributes, Mapping) else {}
        url = _text(relation.get("url"))

        if rel_name == "AttachedFile":
            attachment = GraphNode(
                uid=uid(SYSTEM, site, "attachment", url),
                labels=("Attachment",),
                props={
                    "filename": _text(attributes.get("name")),
                    "size": attributes.get("resourceSize"),
                    "created": _text(attributes.get("resourceCreatedDate")),
                    "url": url,
                    "stub": False,
                },
            )
            batch.add_node(attachment)
            batch.add_edge(GraphEdge("HAS_ATTACHMENT", node_uid, attachment.uid))
            continue

        if rel_name in {"Hyperlink", "ArtifactLink"}:
            link = GraphNode(
                uid=uid(SYSTEM, site, "remotelink", url),
                labels=("RemoteLink",),
                props={
                    "url": url,
                    "title": _text(attributes.get("name")),
                    "relation": rel_name,
                    "stub": False,
                },
            )
            batch.add_node(link)
            batch.add_edge(GraphEdge("HAS_REMOTE_LINK", node_uid, link.uid))
            continue

        other_id = _related_id(relation)
        if not other_id:
            continue
        other_uid = _issue_uid(site, other_id)
        batch.add_node(
            GraphNode(
                uid=other_uid,
                labels=("Issue",),
                props={"key": other_id, "issue_id": other_id, "system": SYSTEM, "stub": True},
            )
        )
        direction = _DIRECTION.search(rel_name)
        rel_type, reverse = ontology.link_rel(_DIRECTION.sub("", rel_name))
        # `-Reverse` means this item is the far end of the named relation, so the
        # stored edge runs the other way. The ontology's own `reverse` flag composes
        # with it: a mapping declared backwards flips both halves together.
        backwards = bool(direction) and direction.group(1).lower() == "reverse"
        if reverse:
            backwards = not backwards
        start, end = (other_uid, node_uid) if backwards else (node_uid, other_uid)
        batch.add_edge(
            GraphEdge(
                rel_type,
                start,
                end,
                props={"link_type": rel_name, "comment": _text(attributes.get("comment"))},
                key_props=("link_type",) if rel_type == "LINKED_TO" else (),
            )
        )


def extract_test_plans(
    plans: Iterable[Mapping[str, Any]],
    *,
    site: str,
    suites_by_plan: Mapping[Any, Iterable[Mapping[str, Any]]] | None = None,
    cases_by_suite: Mapping[Any, Iterable[Mapping[str, Any]]] | None = None,
) -> GraphBatch:
    """Test plans, the suites inside them, and the test cases each suite holds.

    A suite becomes a `:TestSet` rather than a label of its own: it is the same idea
    as an Xray test set — a named grouping of test cases that a plan schedules — and
    giving it a second label would split every "what does this plan cover" query in
    two. Nesting is kept through `CHILD_OF` between suites, which is how the rest of
    the graph already expresses containment.
    """
    batch = GraphBatch()
    suites_by_plan = suites_by_plan or {}
    cases_by_suite = cases_by_suite or {}

    for plan in plans:
        if not isinstance(plan, Mapping) or plan.get("id") in (None, ""):
            continue
        plan_id = str(plan.get("id"))
        plan_uid = uid(SYSTEM, site, "testplan", plan_id)
        batch.add_node(
            GraphNode(
                uid=plan_uid,
                labels=("Issue", "TestPlan"),
                props={
                    "key": plan_id,
                    "plan_id": plan_id,
                    "title": _text(plan.get("name")),
                    "status": _text(plan.get("state")),
                    "area_path": _text(plan.get("areaPath")),
                    "iteration_path": _text(plan.get("iteration")),
                    "starts_at": _text(plan.get("startDate")),
                    "ends_at": _text(plan.get("endDate")),
                    "system": SYSTEM,
                    "stub": False,
                },
            )
        )
        owner = _identity(site, plan.get("owner"))
        if owner is not None:
            batch.add_node(owner)
            batch.add_edge(GraphEdge("ASSIGNED_TO", plan_uid, owner.uid))

        project_node = _project_node(site, _text(plan.get("project")))
        if project_node is not None:
            batch.add_node(project_node)
            batch.add_edge(GraphEdge("IN_PROJECT", plan_uid, project_node.uid))

        iteration = _path_node(site, "sprint", "Sprint", _text(plan.get("iteration")))
        if iteration is not None:
            batch.add_node(iteration)
            batch.add_edge(GraphEdge("IN_SPRINT", plan_uid, iteration.uid))

        for suite in suites_by_plan.get(plan.get("id")) or suites_by_plan.get(plan_id) or ():
            if not isinstance(suite, Mapping) or suite.get("id") in (None, ""):
                continue
            suite_id = str(suite.get("id"))
            suite_uid = uid(SYSTEM, site, "testset", suite_id)
            batch.add_node(
                GraphNode(
                    uid=suite_uid,
                    labels=("Issue", "TestSet"),
                    props={
                        "key": suite_id,
                        "suite_id": suite_id,
                        "title": _text(suite.get("name")),
                        "suite_type": _text(suite.get("suiteType")),
                        "test_case_count": suite.get("testCaseCount"),
                        "system": SYSTEM,
                        "stub": False,
                    },
                )
            )
            batch.add_edge(GraphEdge("PLANS", plan_uid, suite_uid))

            parent = suite.get("parentSuite")
            parent_id = _text(parent.get("id")) if isinstance(parent, Mapping) else ""
            if parent_id and parent_id != suite_id:
                parent_uid = uid(SYSTEM, site, "testset", parent_id)
                batch.add_node(
                    GraphNode(
                        uid=parent_uid,
                        labels=("Issue", "TestSet"),
                        props={"key": parent_id, "suite_id": parent_id, "stub": True},
                    )
                )
                batch.add_edge(GraphEdge("CHILD_OF", suite_uid, parent_uid))

            for case in cases_by_suite.get(suite.get("id")) or cases_by_suite.get(suite_id) or ():
                if not isinstance(case, Mapping):
                    continue
                work_item = case.get("workItem")
                case_id = _text(work_item.get("id")) if isinstance(work_item, Mapping) else ""
                if not case_id:
                    continue
                case_uid = _issue_uid(site, case_id)
                batch.add_node(
                    GraphNode(
                        uid=case_uid,
                        labels=("Issue", "Test"),
                        props={
                            "key": case_id,
                            "issue_id": case_id,
                            "title": _text(work_item.get("name")),
                            "system": SYSTEM,
                            "stub": True,
                        },
                    )
                )
                batch.add_edge(GraphEdge("CONTAINS", suite_uid, case_uid))

                for assignment in case.get("pointAssignments") or ():
                    if not isinstance(assignment, Mapping):
                        continue
                    tester = _identity(site, assignment.get("tester"))
                    if tester is not None:
                        batch.add_node(tester)
                        batch.add_edge(GraphEdge("ASSIGNED_TO", case_uid, tester.uid))
                    configuration = _text(assignment.get("configurationName"))
                    if configuration:
                        env = GraphNode(
                            uid=uid(SYSTEM, site, "environment", configuration),
                            labels=("TestEnvironment",),
                            props={"name": configuration, "stub": False},
                        )
                        batch.add_node(env)
                        batch.add_edge(GraphEdge("IN_ENVIRONMENT", case_uid, env.uid))
    return batch


def extract_test_results(
    runs: Iterable[Mapping[str, Any]],
    *,
    site: str,
    ontology: Ontology,
    results_by_run: Mapping[Any, Iterable[Mapping[str, Any]]] | None = None,
) -> GraphBatch:
    """Test runs and their per-case results.

    Azure's "test run" is a batch — one execution of many test cases — which is what
    Xray calls a test execution; its individual results are what Xray calls test runs.
    Mapping them that way round is what makes `LATEST_RUN` and the coverage
    derivations work unchanged over an Azure project: they look for a `:TestRun`
    attached to a `:Test`, and here that is a result attached to its test case.
    """
    batch = GraphBatch()
    results_by_run = results_by_run or {}

    for run in runs:
        if not isinstance(run, Mapping) or run.get("id") in (None, ""):
            continue
        run_id = str(run.get("id"))
        execution_uid = uid(SYSTEM, site, "testexecution", run_id)
        batch.add_node(
            GraphNode(
                uid=execution_uid,
                labels=("Issue", "TestExecution"),
                props={
                    "key": run_id,
                    "run_id": run_id,
                    "title": _text(run.get("name")),
                    "status": _text(run.get("state")),
                    "started_on": _text(run.get("startedDate")),
                    "finished_on": _text(run.get("completedDate")),
                    "total_tests": run.get("totalTests"),
                    "passed_tests": run.get("passedTests"),
                    "unanalyzed_tests": run.get("unanalyzedTests"),
                    "is_automated": run.get("isAutomated"),
                    "system": SYSTEM,
                    "stub": False,
                },
            )
        )
        owner = _identity(site, run.get("owner"))
        if owner is not None:
            batch.add_node(owner)
            batch.add_edge(GraphEdge("ASSIGNED_TO", execution_uid, owner.uid))

        plan = run.get("plan")
        plan_id = _text(plan.get("id")) if isinstance(plan, Mapping) else ""
        if plan_id:
            plan_uid = uid(SYSTEM, site, "testplan", plan_id)
            batch.add_node(
                GraphNode(
                    uid=plan_uid,
                    labels=("Issue", "TestPlan"),
                    props={"key": plan_id, "plan_id": plan_id, "system": SYSTEM, "stub": True},
                )
            )
            batch.add_edge(GraphEdge("HAS_EXECUTION", plan_uid, execution_uid))

        for result in results_by_run.get(run.get("id")) or results_by_run.get(run_id) or ():
            if not isinstance(result, Mapping) or result.get("id") in (None, ""):
                continue
            _result(batch, site, ontology, execution_uid, run_id, result)
    return batch


def _result(
    batch: GraphBatch,
    site: str,
    ontology: Ontology,
    execution_uid: str,
    run_id: str,
    result: Mapping[str, Any],
) -> None:
    result_id = str(result.get("id"))
    result_uid = uid(SYSTEM, site, "testrun", f"{run_id}.{result_id}")
    outcome = _text(result.get("outcome"))
    status = ontology.run_status(outcome)
    batch.add_node(
        GraphNode(
            uid=result_uid,
            labels=("TestRun",),
            props={
                "run_id": result_uid.rsplit(":", 1)[-1],
                "status": status,
                "status_name": outcome,
                "state": _text(result.get("state")),
                "started_on": _text(result.get("startedDate")),
                "finished_on": _text(result.get("completedDate")),
                "duration_ms": result.get("durationInMs"),
                "comment": _text(result.get("comment")),
                "failure_type": _text(result.get("failureType")),
                "error_message": _text(result.get("errorMessage")),
                "system": SYSTEM,
                "stub": False,
            },
        )
    )
    batch.add_edge(GraphEdge("HAS_RUN", execution_uid, result_uid))

    if status:
        status_node = GraphNode(
            uid=uid(SYSTEM, site, "runstatus", status),
            labels=("TestRunStatus",),
            props={"name": status, "reported_as": outcome, "stub": False},
        )
        batch.add_node(status_node)
        batch.add_edge(GraphEdge("HAS_RESULT", result_uid, status_node.uid))

    test_case = result.get("testCase")
    case_id = _text(test_case.get("id")) if isinstance(test_case, Mapping) else ""
    if case_id:
        case_uid = _issue_uid(site, case_id)
        batch.add_node(
            GraphNode(
                uid=case_uid,
                labels=("Issue", "Test"),
                props={
                    "key": case_id,
                    "issue_id": case_id,
                    "title": _text(result.get("testCaseTitle")),
                    "system": SYSTEM,
                    "stub": True,
                },
            )
        )
        batch.add_edge(GraphEdge("OF_TEST", result_uid, case_uid))

    runner = _identity(site, result.get("runBy"))
    if runner is not None:
        batch.add_node(runner)
        batch.add_edge(GraphEdge("EXECUTED_BY", result_uid, runner.uid))

    configuration = _text(result.get("configuration"))
    if configuration:
        env = GraphNode(
            uid=uid(SYSTEM, site, "environment", configuration),
            labels=("TestEnvironment",),
            props={"name": configuration, "stub": False},
        )
        batch.add_node(env)
        batch.add_edge(GraphEdge("IN_ENVIRONMENT", result_uid, env.uid))

    for bug in result.get("associatedBugs") or ():
        bug_id = _text(bug.get("id")) if isinstance(bug, Mapping) else _text(bug)
        if not bug_id:
            continue
        bug_uid = _issue_uid(site, bug_id)
        batch.add_node(
            GraphNode(
                uid=bug_uid,
                labels=("Issue", "Bug"),
                props={"key": bug_id, "issue_id": bug_id, "system": SYSTEM, "stub": True},
            )
        )
        batch.add_edge(GraphEdge("FOUND_DEFECT", result_uid, bug_uid))


def extract_azure(
    *,
    site: str,
    ontology: Ontology,
    source: str,
    base_url: str = "",
    work_items: Iterable[Mapping[str, Any]] = (),
    plans: Iterable[Mapping[str, Any]] = (),
    suites_by_plan: Mapping[Any, Iterable[Mapping[str, Any]]] | None = None,
    cases_by_suite: Mapping[Any, Iterable[Mapping[str, Any]]] | None = None,
    runs: Iterable[Mapping[str, Any]] = (),
    results_by_run: Mapping[Any, Iterable[Mapping[str, Any]]] | None = None,
) -> GraphBatch:
    """Everything read from one Azure DevOps project, as one batch."""
    batch = GraphBatch()
    batch.extend(
        extract_work_items(
            work_items, site=site, ontology=ontology, source=source, base_url=base_url
        )
    )
    batch.extend(
        extract_test_plans(
            plans, site=site, suites_by_plan=suites_by_plan, cases_by_suite=cases_by_suite
        )
    )
    batch.extend(
        extract_test_results(
            runs, site=site, ontology=ontology, results_by_run=results_by_run
        )
    )
    return batch
