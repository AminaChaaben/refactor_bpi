"""Xray-shaped collections from a Jira site with no Xray at all.

The three real Xray tiers — Cloud GraphQL, Server/DC REST, and nothing — leave a
plain-Jira site with the same problem as Xray Server: its tests, sets, plans and
executions are ordinary issues, and the graph has to know which tests ran in which
execution with which result. Server/DC answers those questions on `/rest/raven`;
a plain site answers them with the same vocabulary Jira always had: issue links and
custom fields.

So this module normalises rather than extracts, exactly like `xray_server`: it walks
the already-read Jira records and emits the Cloud-shaped payloads `extract_xray`
knows how to read. The conventions it reads are deliberately the ones a site can
already express with Jira's own primitives:

*Membership is an issue link.* A test belongs to a set, plan or execution through the
standard "Test" link (outward "tests"). The linked test list of a container is read
from the container's own record, so membership costs no extra calls.

*Steps are Gherkin in the description.* A test whose description parses as Gherkin
gets one `:TestStep` per Given/When/Then/And/But line and its raw text on the
`gherkin` property. The same text is what a person sees on the issue page.

*Preconditions are declared in the description.* Lines under a `Preconditions:`
section become inline `:Precondition` nodes the test `REQUIRES` — the inline
counterpart of a separate Precondition issue type.

*Run results are labels.* A `state:passed` label on a test is that test's current
result; a `run:<test-key>:<state>` label on an execution records one specific run of
one test inside it. Labels are visible, editable and picked up by change detection
like any other tag change, which is what makes the run status part of the synced
history rather than a number only the graph can see.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from .xray_server import ServerCollection, _BY_LABEL, _jira_of

__all__ = [
    "classify",
    "collect",
    "gherkin_steps",
    "preconditions_of",
    "state_label_of",
    "run_state_of",
]

# An issue belongs to a container through the link type whose outward wording is
# "tests" (Jira's stock "Test" link). The name is matched case-insensitively so a
# site that renamed the type still works.
_LINK_TESTS = ("tests", "test", "test case linkage")

# Labels a project can use to mark an issue as a container when its issue type
# cannot carry that meaning — a next-gen project with no "Test Plan" type models the
# plan as a Story labelled `test-plan`.
_PLAN_LABELS = ("test-plan", "test plan", "testplan")
_PRECONDITION_LABELS = ("precondition", "précondition", "preconditions")

# The label prefix that records a test's execution result on the issue itself.
_STATE_PREFIX = "state:"
# The label prefix on an execution that records one specific run: `run:DEM-42:failed`.
_RUN_PREFIX = "run:"

_STEP_RE = re.compile(r"^\s*(?:Given|When|Then|And|But|\*)\b", re.IGNORECASE)
_GHERKIN_RE = re.compile(
    r"^\s*(?:Feature|Scenario Outline|Scenario|Example|Examples|Background|Rule)\s*[:@]",
    re.IGNORECASE,
)
_PRECONDITION_RE = re.compile(r"^\s*(?:Preconditions?|Conditions?)\s*:\s*(.*)$", re.IGNORECASE)


def classify(records: Iterable[Any], ontology: Any) -> dict[str, list[str]]:
    """Sort already-read Jira issues into the five Xray collections.

    By issue type through the ontology, exactly as the server tier does, plus the
    label conventions above for a container a site could not model as its own type.
    """
    found: dict[str, list[str]] = {name: [] for _, name in _BY_LABEL}
    for record in records:
        key = getattr(record, "key", "") or ""
        if not key:
            continue
        labels = set(ontology.labels_for_type(getattr(record, "type", "") or ""))
        for label, collection in _BY_LABEL:
            if label in labels:
                found[collection].append(key)
                break
        else:
            tags = {str(tag).strip().lower() for tag in (getattr(record, "tags", None) or ())}
            if tags & set(_PLAN_LABELS):
                found["test_plans"].append(key)
            elif tags & set(_PRECONDITION_LABELS):
                found["preconditions"].append(key)
    return found


def state_label_of(record: Any) -> str | None:
    """The `state:<value>` label of one record, if it has one."""
    for tag in getattr(record, "tags", None) or ():
        text = str(tag).strip()
        if text.lower().startswith(_STATE_PREFIX):
            value = text[len(_STATE_PREFIX):].strip()
            if value:
                return value
    return None


def run_state_of(record: Any, test_key: str) -> str | None:
    """The recorded result of one run: `run:<test-key>:<state>` on the execution.

    An execution can hold one label per linked test, so a test that passed in an
    early smoke run and failed in the latest regression can carry both truths. The
    key is matched case-insensitively; an absent label means the run has no result.
    """
    prefix = f"{_RUN_PREFIX}{test_key.lower()}:"
    for tag in getattr(record, "tags", None) or ():
        text = str(tag).strip()
        if text.lower().startswith(prefix):
            value = text[len(prefix):].strip()
            if value:
                return value
    return None


def gherkin_steps(text: str) -> tuple[list[dict[str, Any]], bool]:
    """The Gherkin steps of a description, and whether it parses as Gherkin at all.

    Each step is one `{id, action, data, result}` entry carrying the full line,
    matching the shape `extract_xray.extract_tests` reads. A description without a
    single Gherkin keyword yields no steps rather than garbage ones, so an ordinary
    paragraph never becomes a one-step test.
    """
    lines = text.splitlines()
    is_gherkin = any(_GHERKIN_RE.match(line) or _STEP_RE.match(line) for line in lines)
    steps: list[dict[str, Any]] = []
    for position, line in enumerate(lines, start=1):
        if not _STEP_RE.match(line):
            continue
        action = re.sub(r"\s+", " ", line).strip()
        action = re.sub(r"\s+@[\w\-]+(?:[,\s]|$)", " ", action).strip()
        if not action:
            continue
        steps.append(
            {"id": f"#step{position}", "action": action, "data": None, "result": None}
        )
    return steps, is_gherkin


def preconditions_of(text: str) -> list[str]:
    """The definitions under a `Preconditions:` section of a description.

    One definition per `Preconditions:` line; indented continuation lines are
    appended to the current definition until the next Gherkin keyword.
    """
    out: list[str] = []
    current: str | None = None
    in_section = False
    for line in text.splitlines():
        match = _PRECONDITION_RE.match(line)
        if match:
            in_section = True
            first = match.group(1).strip()
            current = first or None
            if current:
                out.append(current)
            continue
        if not in_section:
            continue
        if _GHERKIN_RE.match(line) or _STEP_RE.match(line):
            in_section = False
            current = None
            continue
        if line.strip():
            if current:
                out[-1] = f"{current} {line.strip()}"
            else:
                out.append(line.strip())
    return out


def _links(record: Any) -> list[tuple[str, str]]:
    """Every "tests"-style link of one record as (tester, tested) key pairs.

    Jira returns each link from whichever end was asked, so one side is always
    missing: the tester's copy carries `inwardIssue`, the tested's copy carries
    `outwardIssue`. Both are folded onto the same (tester, tested) pair.
    """
    raw = getattr(record, "raw", None)
    fields = raw.get("fields") if isinstance(raw, Mapping) else None
    key = getattr(record, "key", "") or ""
    if not isinstance(fields, Mapping):
        return []
    out: list[tuple[str, str]] = []
    for link in fields.get("issuelinks") or ():
        if not isinstance(link, Mapping):
            continue
        type_name = (link.get("type") or {}).get("name") if isinstance(link.get("type"), Mapping) else None
        if str(type_name or "").strip().lower() not in _LINK_TESTS:
            continue
        outward = (link.get("outwardIssue") or {}).get("key")
        inward = (link.get("inwardIssue") or {}).get("key")
        if outward:
            out.append((str(outward), key))
        elif inward:
            out.append((key, str(inward)))
    return out


def _owned(links: Iterable[tuple[str, str]], key: str, role: int) -> list[str]:
    """The keys on one side of every link involving `key`, sorted."""
    return sorted({pair[role] for pair in links if pair[1 - role] == key})


def _block(keys: Iterable[str]) -> dict[str, Any]:
    listed = [k for k in dict.fromkeys(keys) if k]
    return {"total": len(listed), "results": [{"issueId": k} for k in listed]}


def _description(record: Any) -> str:
    """The description as plain text, one line per paragraph.

    Unlike the sync's `detail_text`, which joins every paragraph with a space for
    stable hashing, Gherkin parsing is line-based: `Scenario:` and step keywords
    only mean something at the start of a line, so the ADF tree has to be flattened
    paragraph by paragraph.
    """
    raw = getattr(record, "raw", None)
    fields = raw.get("fields") if isinstance(raw, Mapping) else {}
    value = fields.get("description") if isinstance(fields, Mapping) else None
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return _adf_block(value)
    return ""


def _adf_block(node: Any) -> str:
    if isinstance(node, Mapping):
        if node.get("type") == "text" and isinstance(node.get("text"), str):
            return node["text"]
        if node.get("type") == "paragraph":
            return "".join(_adf_block(child) for child in node.get("content") or [])
        return "\n".join(
            part for part in (_adf_block(child) for child in node.get("content") or []) if part
        )
    return ""


def _run_of(
    execution_key: str, test_key: str, execution_record: Any, test_record: Any, status: str
) -> dict[str, Any]:
    """One execution's run of one test, Cloud-shaped.

    The run's start comes from the execution's creation and its finish from the
    test's own `updated` — which moves when the result label is written — and the
    executor is the test's assignee, the person who actually ran it.
    """
    exec_fields = (getattr(execution_record, "raw", None) or {}).get("fields") or {}
    test_fields = (getattr(test_record, "raw", None) or {}).get("fields") or {}
    assignee = test_fields.get("assignee") if isinstance(test_fields.get("assignee"), Mapping) else {}
    return {
        "id": f"{execution_key}-{test_key}",
        "status": {"name": status},
        "startedOn": exec_fields.get("created"),
        "finishedOn": test_fields.get("updated"),
        "comment": None,
        "executedById": assignee.get("accountId"),
        "assigneeId": assignee.get("accountId"),
        "testEnvironments": [],
        "test": {"issueId": test_key},
        "testExecution": {"issueId": execution_key},
        "defects": [],
        "evidence": [],
        "steps": [],
    }


def collect(
    records: Iterable[Any],
    classified: Mapping[str, list[str]],
    ontology: Any,
) -> ServerCollection:
    """Every Xray collection this plain-Jira site has, in Cloud shape.

    The record set is the Jira read that already happened: summaries, statuses,
    links and descriptions all come from it, so collecting costs no extra calls.
    """
    out = ServerCollection()
    by_key = {getattr(r, "key", "") or "": r for r in records if getattr(r, "key", "")}
    links = {key: _links(record) for key, record in by_key.items()}

    def members(key: str) -> list[str]:
        return sorted({tester for tester, tested in links.get(key, ()) if tested == key})

    def owners(test_key: str) -> dict[str, list[str]]:
        """The containers one test belongs to, scanned across every record's links.

        Jira renders each link from whichever end was asked, so one record never
        shows both sides. Scanning every record (not just the test's own) makes
        membership correct even when the container's record was the only one that
        carried the link — and it is free, because the records are already in hand.
        """
        owned: dict[str, list[str]] = {"sets": [], "plans": [], "executions": []}
        for pairs in links.values():
            for tester, tested in pairs:
                owner = tested if tester == test_key else (tester if tested == test_key else None)
                if owner is None or owner == test_key:
                    continue
                record = by_key.get(owner)
                if record is None:
                    continue
                labels = set(ontology.labels_for_type(record.type))
                if "TestSet" in labels:
                    owned["sets"].append(owner)
                elif "TestPlan" in labels:
                    owned["plans"].append(owner)
                elif "TestExecution" in labels:
                    owned["executions"].append(owner)
        return {name: sorted(set(keys)) for name, keys in owned.items()}

    inline: dict[str, list[str]] = {}

    for key in classified.get("test_sets", ()):
        out["test_sets"].append(
            {"issueId": key, "jira": _jira_of(by_key.get(key)), "tests": _block(members(key))}
        )

    for key in classified.get("test_plans", ()):
        linked = members(key)
        tests: list[str] = []
        executions: list[str] = []
        for member in linked:
            record = by_key.get(member)
            if record is None:
                continue
            if "TestExecution" in set(ontology.labels_for_type(record.type)):
                executions.append(member)
            else:
                tests.append(member)
        out["test_plans"].append(
            {
                "issueId": key,
                "jira": _jira_of(by_key.get(key)),
                "tests": _block(tests),
                "testExecutions": _block(executions),
            }
        )

    for key in classified.get("executions", ()):
        execution_record = by_key.get(key)
        tests = members(key)
        out["executions"].append(
            {
                "issueId": key,
                "jira": _jira_of(execution_record),
                "testEnvironments": [],
                "tests": _block(tests),
            }
        )
        for test_key in tests:
            test_record = by_key.get(test_key)
            if test_record is None:
                continue
            # A per-run label on the execution wins; the test's own state label is
            # the fallback when the run was recorded before per-run labels existed.
            status = run_state_of(execution_record, test_key) or state_label_of(test_record)
            if not status:
                continue
            out["runs"].append(
                _run_of(key, test_key, execution_record, test_record, status)
            )

    for key in classified.get("preconditions", ()):
        record = by_key.get(key)
        out["preconditions"].append(
            {
                "issueId": key,
                "jira": _jira_of(record),
                "definition": _description(record) or None,
                "tests": _block(members(key)),
            }
        )

    for key in classified.get("tests", ()):
        record = by_key.get(key)
        if record is None:
            continue
        text = _description(record)
        steps, is_gherkin = gherkin_steps(text)
        for position, step in enumerate(steps, start=1):
            step["id"] = f"{key}#{position}"
        conditions = preconditions_of(text)
        inline[key] = conditions
        owned = owners(key)
        out["tests"].append(
            {
                "issueId": key,
                "jira": _jira_of(record),
                "testType": {"name": None, "kind": None},
                "gherkin": text if is_gherkin else None,
                "scenarioType": "cucumber" if is_gherkin else None,
                "unstructured": None,
                "steps": steps,
                "preconditions": {
                    "total": len(conditions),
                    "results": [
                        {
                            "preconditionRef": {"issueId": f"{key}#precondition{position}"},
                            "definition": definition,
                            "preconditionType": {"name": "Inline"},
                        }
                        for position, definition in enumerate(conditions, start=1)
                    ],
                },
                "testSets": _block(owned["sets"]),
                "testPlans": _block(owned["plans"]),
                "testExecutions": _block(owned["executions"]),
            }
        )

    for test_key, conditions in inline.items():
        for position, definition in enumerate(conditions, start=1):
            out["preconditions"].append(
                {
                    "issueId": f"{test_key}#precondition{position}",
                    "jira": {},
                    "definition": definition,
                    "preconditionType": {"name": "Inline"},
                    "inline": True,
                    "tests": {"total": 1, "results": [{"issueId": test_key}]},
                }
            )

    return out