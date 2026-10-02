"""Xray Server/DC read as if it were Xray Cloud.

Server/DC and Cloud expose the same domain through two APIs that agree on almost
nothing at the wire level: Cloud answers one deep GraphQL query keyed on numeric
issue ids, Server/DC answers a dozen shallow REST resources keyed on issue keys, and
its text fields arrive wrapped in `{raw, rendered}` envelopes that Cloud does not
have. What the two *mean* is identical — a test has steps, belongs to sets and plans,
runs inside executions, and produces per-step results and defects.

So this module normalises rather than extracts. It walks the REST resources and emits
the Cloud-shaped payloads `extract_xray` already knows how to read, which is what
makes a self-hosted site and a cloud site produce the same graph rather than two
graphs that merely resemble each other. Writing a second family of extractors would
have meant every future field being added in two places and drifting the first time
somebody forgot one.

Two consequences worth knowing:

*Issue keys stand in for numeric ids.* `IssueIndex` maps a numeric id to a key so
that Cloud references resolve; Server/DC already speaks in keys, so a key is fed in
as its own identifier and the index resolves it to itself. Nothing downstream has to
know which tier it came from.

*Reads are budgeted, not exhaustive.* Cloud fetches a hundred tests per call while
Server/DC costs a call per test for steps and another per test for each container it
belongs to. A large project would otherwise spend thousands of round trips on
relationships the issue links already recorded, so the per-issue passes are the ones
that carry information nothing else has — steps, preconditions and run results — and
the container memberships are read from the container's own single call instead.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..errors import ConnectionSourceError

__all__ = ["ServerCollection", "classify", "collect"]

# The labels the ontology gives an issue type, mapped onto the collection an issue of
# that type belongs in. An issue carrying none of them is an ordinary requirement and
# is not Xray's business.
_BY_LABEL: tuple[tuple[str, str], ...] = (
    ("Test", "tests"),
    ("Precondition", "preconditions"),
    ("TestSet", "test_sets"),
    ("TestPlan", "test_plans"),
    ("TestExecution", "executions"),
)


class ServerCollection(dict):
    """The six Cloud-shaped collections, plus whatever failed while reading them.

    A dict subclass rather than a dataclass because it is handed straight to
    `extract_xray(**collection.payloads)` — keeping the six keys as literal keys means
    the call site cannot fall out of step with the extractor's own signature.
    """

    def __init__(self) -> None:
        super().__init__(
            tests=[], preconditions=[], test_sets=[], test_plans=[], executions=[], runs=[]
        )
        self.errors: list[dict[str, Any]] = []

    @property
    def payloads(self) -> dict[str, list[dict[str, Any]]]:
        return {key: list(value) for key, value in self.items()}

    def counts(self) -> dict[str, int]:
        return {key: len(value) for key, value in sorted(self.items())}


def _text(value: Any) -> str:
    """One Server/DC text field, unwrapped.

    Rich-text fields arrive as `{"raw": "...", "rendered": "<p>...</p>"}`. The raw
    form is the one worth keeping: the rendered form is HTML built for Jira's own
    stylesheet, and storing markup in a graph property makes every later comparison
    a string-matching problem instead of a content one.
    """
    if value is None:
        return ""
    if isinstance(value, Mapping):
        for key in ("raw", "value", "rendered", "name"):
            inner = value.get(key)
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
        return ""
    return str(value).strip()


def _key_of(entry: Mapping[str, Any]) -> str:
    """The issue key of a referenced issue, under whichever name it was reported."""
    for name in ("key", "testKey", "issueKey", "testExecKey", "preconditionKey"):
        value = entry.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _ref(key: str) -> dict[str, str]:
    """A Cloud-shaped `{issueId}` reference carrying a key as the identifier."""
    return {"issueId": key}


def _block(keys: Iterable[str]) -> dict[str, Any]:
    """A Cloud-shaped `{total, results}` sub-collection."""
    listed = [k for k in dict.fromkeys(keys) if k]
    return {"total": len(listed), "results": [_ref(k) for k in listed]}


def _listed(raw: Any) -> list[str]:
    """A value that may be a list, a comma-separated string or a single item."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        raw = raw.split(",")
    elif not isinstance(raw, (list, tuple)):
        raw = [raw]
    return [text for text in (_text(item) for item in raw) if text]


def _environments(entry: Mapping[str, Any]) -> list[str]:
    """The environments one run entry reports, as the REST resource returns them."""
    return _listed(entry.get("testEnvironments") or entry.get("environments"))


def classify(
    records: Iterable[Any], ontology: Any
) -> dict[str, list[str]]:
    """Sort already-read Jira issues into the five Xray collections by their type.

    Driven by the ontology rather than by hardcoded type names, so a site whose
    "Test" type is called `Cas de test` classifies correctly the moment somebody
    declares that in `graph.ontology.issue_type_labels` — the same mapping the graph's
    labels already come from, rather than a second list that could disagree with it.
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
    return found


def _jira_of(record: Any) -> dict[str, Any]:
    """The `jira { ... }` block Cloud returns beside every result, from a record."""
    status = getattr(record, "status", "") or ""
    return {
        "key": getattr(record, "key", "") or "",
        "summary": getattr(record, "title", "") or "",
        "status": {"name": status} if status else None,
    }


def _step(entry: Mapping[str, Any], position: int, test_key: str) -> dict[str, Any]:
    return {
        "id": _text(entry.get("id")) or f"{test_key}#{position}",
        "action": _text(entry.get("step") or entry.get("action")),
        "data": _text(entry.get("data")),
        "result": _text(entry.get("result") or entry.get("expectedResult")),
        "attachments": [
            {"id": _text(a.get("id")), "filename": _text(a.get("fileName") or a.get("filename"))}
            for a in entry.get("attachments") or ()
            if isinstance(a, Mapping) and a.get("id")
        ],
    }


def _guarded(
    collection: ServerCollection, stage: str, subject: str, call: Any, default: Any
) -> Any:
    """One REST read whose failure costs that read and nothing else.

    A per-issue pass over a few hundred tests will meet a permission error on some of
    them — Xray honours Jira's issue security, and a test in a restricted project is
    exactly the sort of thing a broad JQL sweeps in. Aborting the collection there
    would throw away every test read successfully before it, so the failure is
    recorded against the issue and the walk continues.
    """
    try:
        return call()
    except ConnectionSourceError as exc:
        collection.errors.append({"stage": stage, "issue": subject, **exc.to_dict()})
        return default


def _raw_field(record: Any, field_id: str) -> Any:
    """One field off an already-read issue's raw payload, untouched."""
    if not field_id:
        return None
    raw = getattr(record, "raw", None)
    fields = raw.get("fields") if isinstance(raw, Mapping) else None
    return fields.get(field_id) if isinstance(fields, Mapping) else None


def _custom_field(record: Any, field_id: str) -> str:
    """One declared custom field off an already-read issue.

    Server/DC keeps the test type and the precondition definition on the issue as
    custom fields rather than exposing them on any `/rest/raven` collection, so the
    only way to read them without a second fetch is out of the record the Jira pass
    already returned. Which field that is differs per site, so the id comes from the
    project's declared `test_detail_fields` — the same mapping change detection and
    the export already use — and an undeclared one simply yields nothing.
    """
    return _text(_raw_field(record, field_id))


def collect(
    client: Any,
    *,
    records_by_key: Mapping[str, Any],
    classified: Mapping[str, list[str]],
    detail_fields: Mapping[str, str] | None = None,
    with_steps: bool = True,
    with_runs: bool = True,
) -> ServerCollection:
    """Read every Xray collection this site has, in Cloud shape.

    `records_by_key` is the Jira read that already happened, so summaries, statuses
    and custom fields come from it rather than from a second fetch of issues the
    caller is already holding.
    """
    out = ServerCollection()
    fields = dict(detail_fields or {})
    type_field = fields.get("test_type", "")
    definition_field = fields.get("precondition", "")
    environment_field = fields.get("test_environments", "")

    membership: dict[str, dict[str, list[str]]] = {}

    def remember(test_key: str, field: str, owner: str) -> None:
        membership.setdefault(test_key, {}).setdefault(field, []).append(owner)

    # Containers first: one call each yields every membership inside them, which is
    # the same information a per-test lookup would cost one call per test to learn.
    for key in classified.get("test_sets", ()):
        tests = _guarded(out, "test_set", key, lambda k=key: client.set_tests(k), [])
        members = [_key_of(t) for t in tests]
        for member in members:
            remember(member, "testSets", key)
        out["test_sets"].append(
            {
                "issueId": key,
                "jira": _jira_of(records_by_key.get(key)),
                "tests": _block(members),
            }
        )

    for key in classified.get("test_plans", ()):
        tests = _guarded(out, "test_plan", key, lambda k=key: client.plan_tests(k), [])
        executions = _guarded(
            out, "test_plan", key, lambda k=key: client.plan_executions(k), []
        )
        members = [_key_of(t) for t in tests]
        for member in members:
            remember(member, "testPlans", key)
        out["test_plans"].append(
            {
                "issueId": key,
                "jira": _jira_of(records_by_key.get(key)),
                "tests": _block(members),
                "testExecutions": _block(_key_of(e) for e in executions),
            }
        )

    for key in classified.get("executions", ()):
        entries = _guarded(
            out, "execution", key, lambda k=key: client.execution_tests(k), []
        )
        members = [_key_of(entry) for entry in entries]
        for member in members:
            remember(member, "testExecutions", key)
        out["executions"].append(
            {
                "issueId": key,
                "jira": _jira_of(records_by_key.get(key)),
                # Server/DC keeps an execution's environments on the issue rather than
                # on any `/rest/raven` resource, so they come from the declared field
                # on the record the Jira pass already returned.
                "testEnvironments": _listed(
                    _raw_field(records_by_key.get(key), environment_field)
                ),
                "tests": _block(members),
            }
        )
        if with_runs:
            out["runs"].extend(_runs_of(out, client, key, entries, with_steps=with_steps))

    for key in classified.get("preconditions", ()):
        record = records_by_key.get(key)
        tests = _guarded(
            out, "precondition", key, lambda k=key: client.precondition_tests(k), []
        )
        out["preconditions"].append(
            {
                "issueId": key,
                "jira": _jira_of(record),
                "definition": _custom_field(record, definition_field),
                "tests": _block(_key_of(t) for t in tests),
            }
        )

    for key in classified.get("tests", ()):
        record = records_by_key.get(key)
        steps = (
            _guarded(out, "steps", key, lambda k=key: client.test_steps(k), [])
            if with_steps
            else []
        )
        preconditions = _guarded(
            out, "preconditions", key, lambda k=key: client.test_preconditions(k), []
        )
        owned = membership.get(key, {})
        out["tests"].append(
            {
                "issueId": key,
                "jira": _jira_of(record),
                "testType": {"name": _custom_field(record, type_field), "kind": None},
                "steps": [
                    _step(step, position, key)
                    for position, step in enumerate(steps, start=1)
                    if isinstance(step, Mapping)
                ],
                "preconditions": {
                    "total": len(preconditions),
                    "results": [
                        {
                            "preconditionRef": _ref(_key_of(p)),
                            "definition": _text(p.get("condition") or p.get("definition")),
                            "preconditionType": {"name": _text(p.get("type"))},
                        }
                        for p in preconditions
                        if _key_of(p)
                    ],
                },
                "testSets": _block(owned.get("testSets", ())),
                "testPlans": _block(owned.get("testPlans", ())),
                "testExecutions": _block(owned.get("testExecutions", ())),
            }
        )

    return out


def _runs_of(
    collection: ServerCollection,
    client: Any,
    execution_key: str,
    entries: Iterable[Mapping[str, Any]],
    *,
    with_steps: bool,
) -> list[dict[str, Any]]:
    """The runs inside one execution, Cloud-shaped, with their per-step results.

    `/testexec/{key}/test` already answers with one entry per run — its id, its
    status, who executed it and its defects — so the run itself costs no extra call.
    Only the step results need one, and only when asked for: they are the largest
    part of an Xray extraction and the part a coverage question never reads.
    """
    runs: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        run_id = entry.get("id")
        test_key = _key_of(entry)
        if not run_id or not test_key:
            continue
        steps = (
            _guarded(
                collection, "run_steps", f"{execution_key}/{test_key}",
                lambda r=run_id: client.run_steps(r), [],
            )
            if with_steps
            else []
        )
        runs.append(
            {
                "id": str(run_id),
                "status": {"name": _text(entry.get("status"))},
                "startedOn": _text(entry.get("startedOn")),
                "finishedOn": _text(entry.get("finishedOn")),
                "comment": _text(entry.get("comment")),
                "executedById": _text(entry.get("executedBy") or entry.get("executedByUser")),
                "assigneeId": _text(entry.get("assignee")),
                "testEnvironments": _environments(entry),
                "test": _ref(test_key),
                "testExecution": _ref(execution_key),
                "defects": [
                    _key_of(d) if isinstance(d, Mapping) else _text(d)
                    for d in entry.get("defects") or ()
                ],
                "evidence": [
                    {
                        "id": _text(e.get("id")),
                        "filename": _text(e.get("fileName") or e.get("filename")),
                    }
                    for e in entry.get("evidences") or entry.get("evidence") or ()
                    if isinstance(e, Mapping) and e.get("id")
                ],
                "steps": [
                    {
                        "id": _text(step.get("id")),
                        "action": _text(step.get("step") or step.get("action")),
                        "data": _text(step.get("data")),
                        "result": _text(step.get("result") or step.get("expectedResult")),
                        "actualResult": _text(step.get("actualResult")),
                        "status": {"name": _text(step.get("status"))},
                        "defects": [
                            _key_of(d) if isinstance(d, Mapping) else _text(d)
                            for d in step.get("defects") or ()
                        ],
                        "evidence": [
                            {
                                "id": _text(e.get("id")),
                                "filename": _text(
                                    e.get("fileName") or e.get("filename")
                                ),
                            }
                            for e in step.get("evidences") or step.get("evidence") or ()
                            if isinstance(e, Mapping) and e.get("id")
                        ],
                    }
                    for step in steps
                    if isinstance(step, Mapping)
                ],
            }
        )
    return runs
