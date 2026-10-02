"""Azure DevOps Services over the REST 7.1 API."""

from __future__ import annotations

import re
from html import escape as _html_escape
from typing import Any, Iterable, Mapping
from xml.sax.saxutils import escape as _xml_escape

import httpx

from ..errors import ApiError, PermissionDeniedError, SourcesConfigError
from ..models import AlmRecord, Identity
from .base import MAX_PAGE, AlmClient, ClientSpec, SuspectCredential, basic_auth

API_VERSION = "7.1"
PATCH_CONTENT_TYPE = "application/json-patch+json"
ATLASSIAN_TOKEN_PREFIX = "ATATT"

# "Reverse" names which end of the link this item is: the child, pointing back
# up at its parent. It is not a direction to apply the link in.
HIERARCHY_REVERSE = "System.LinkTypes.Hierarchy-Reverse"

# Set on a story, pointing at a test case: the story is "Tested By" that test.
# The test case then shows the mirror "Tests" link without a second write.
TESTED_BY_FORWARD = "Microsoft.VSTS.Common.TestedBy-Forward"

TEST_CASE_TYPE = "Test Case"

_FIELDS = (
    "System.Id",
    "System.Title",
    "System.State",
    "System.WorkItemType",
    "System.AssignedTo",
    "System.TeamProject",
    "System.IterationPath",
    "System.AreaPath",
    "System.Tags",
    "System.Parent",
    "System.ChangedDate",
)

_BATCH_MAX = 200


class AzureDevOpsClient(AlmClient):
    spec = ClientSpec(
        name="azuredevops",
        required_env=("AZURE_DEVOPS_ORG", "AZURE_DEVOPS_PAT"),
        optional_env=("AZURE_DEVOPS_EMAIL",),
        scope_keys=("project",),
        token_hint=(
            "create a PAT at dev.azure.com/<org>/_usersSettings/tokens with scope "
            "'Work Items (read, write)'; it is a ~52-character token, not an "
            "Atlassian ATATT... token"
        ),
    )

    def __init__(self, *, org: str, **kwargs: Any) -> None:
        self.org = org
        super().__init__(**kwargs)

    @classmethod
    def _build(
        cls,
        env: Mapping[str, str],
        *,
        timeout: float,
        transport: httpx.BaseTransport | None,
    ) -> "AzureDevOpsClient":
        org = env["AZURE_DEVOPS_ORG"]
        return cls(
            org=org,
            base_url=f"https://dev.azure.com/{org}",
            auth=basic_auth("", env["AZURE_DEVOPS_PAT"]),
            env=env,
            timeout=timeout,
            transport=transport,
        )

    @classmethod
    def suspect_credentials(cls, env: Mapping[str, str]) -> list[SuspectCredential]:
        pat = env.get("AZURE_DEVOPS_PAT", "")
        if not pat.startswith(ATLASSIAN_TOKEN_PREFIX):
            return []
        return [
            SuspectCredential(
                key="AZURE_DEVOPS_PAT",
                message=(
                    "AZURE_DEVOPS_PAT holds an Atlassian API token (starts with "
                    f"{ATLASSIAN_TOKEN_PREFIX}), not an Azure DevOps PAT"
                ),
                remediation=cls.spec.token_hint,
            )
        ]

    def ping(self) -> Identity:
        self.request(
            "GET", "/_apis/projects", params={"api-version": API_VERSION, "$top": 1}
        )
        return Identity(
            system="azuredevops",
            account=self.org,
            base_url=self.base_url,
        )

    def projects(self) -> list[str]:
        body = self.request("GET", "/_apis/projects", params={"api-version": API_VERSION})
        return [p["name"] for p in body.get("value", [])]

    def search(
        self,
        scope: Mapping[str, Any],
        *,
        limit: int = MAX_PAGE,
        extra_fields: Iterable[str] | None = None,
    ) -> list[AlmRecord]:
        project = scope.get("project")
        wiql = scope.get("wiql")
        if not project:
            raise SourcesConfigError(
                "azure source has no 'project' scope",
                server="azuredevops",
                remediation="add a 'project' key to the azure source in sources.json",
            )
        if not wiql:
            raise SourcesConfigError(
                "azure source has no 'wiql' scope",
                server="azuredevops",
                remediation="add a 'wiql' key to the azure source in sources.json",
            )

        body = self.request(
            "POST",
            f"/{project}/_apis/wit/wiql",
            params={"api-version": API_VERSION, "$top": limit},
            json={"query": wiql},
        )
        ids = [str(item["id"]) for item in body.get("workItems", [])][:limit]
        return self.get_batch(ids, extra_fields=extra_fields) if ids else []

    def get_batch(
        self, ids: list[str], *, extra_fields: Iterable[str] | None = None
    ) -> list[AlmRecord]:
        fields = _fields_with(extra_fields)
        records: list[AlmRecord] = []
        for start in range(0, len(ids), _BATCH_MAX):
            chunk = ids[start : start + _BATCH_MAX]
            body = self.request(
                "POST",
                "/_apis/wit/workitemsbatch",
                params={"api-version": API_VERSION},
                json={"ids": [int(i) for i in chunk], "fields": fields},
            )
            records.extend(self._record(item) for item in body.get("value", []))
        return records

    def graph_batch(self, ids: list[str]) -> list[dict[str, Any]]:
        """Work items with every field *and* their relations, as raw payloads.

        Separate from `get_batch` because Azure refuses `fields` and `$expand`
        together: naming fields returns exactly those and no links, expanding
        relations returns the links and every field. The graph needs both halves, so
        it takes the expanded form and pays the extra bytes — the alternative is one
        request per work item to discover its parent, which is the single most common
        edge in the whole hierarchy.
        """
        items: list[dict[str, Any]] = []
        for start in range(0, len(ids), _BATCH_MAX):
            chunk = ids[start : start + _BATCH_MAX]
            body = self.request(
                "POST",
                "/_apis/wit/workitemsbatch",
                params={"api-version": API_VERSION},
                json={"ids": [int(i) for i in chunk], "$expand": "Relations"},
            )
            items.extend(item for item in body.get("value", []) if isinstance(item, dict))
        return items

    def test_runs(
        self, project: str, *, plan_id: str | int | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Completed and in-progress test runs, newest first.

        `$top` alone returns the *oldest* runs, which on a long-lived project means
        the graph would fill with history from a release nobody is working on. Azure
        has no ordering parameter here, so the window is taken from the most recent
        run id downwards instead, which is what `minLastUpdatedDate` would express if
        it did not also require a companion max date.
        """
        params: dict[str, Any] = {"api-version": API_VERSION, "$top": int(limit)}
        if plan_id is not None:
            params["planId"] = plan_id
        body = self.request("GET", f"/{project}/_apis/test/runs", params=params)
        runs = [run for run in body.get("value", []) if isinstance(run, dict)]
        runs.sort(key=lambda r: int(r.get("id") or 0), reverse=True)
        return runs[:limit]

    def get(self, ident: str, *, extra_fields: Iterable[str] | None = None) -> AlmRecord:
        item = self.request(
            "GET",
            f"/_apis/wit/workitems/{ident}",
            params={"api-version": API_VERSION, "fields": ",".join(_fields_with(extra_fields))},
        )
        return self._record(item)

    def create(self, scope: Mapping[str, Any], **fields: Any) -> AlmRecord:
        project = fields.pop("project", None) or scope.get("project")
        if not project:
            raise SourcesConfigError(
                "creating a work item needs a project",
                server="azuredevops",
                remediation="pass project=Name, or set it on the azure source",
            )
        title = fields.pop("title", None) or fields.pop("summary", None)
        if not title:
            raise SourcesConfigError(
                "creating a work item needs a title",
                server="azuredevops",
                remediation="pass title='...'",
            )
        item_type = fields.pop("type", None) or fields.pop("work_item_type", None) or "Task"
        parent = fields.pop("parent", None)

        patch = [_add("System.Title", title)]
        if (description := fields.pop("description", None)) is not None:
            patch.append(_add("System.Description", description))
        if (assignee := fields.pop("assignee", None)) is not None:
            patch.append(_add("System.AssignedTo", assignee))
        if (state := fields.pop("state", None)) is not None:
            patch.append(_add("System.State", state))
        if (iteration := fields.pop("iteration", None)) is not None:
            patch.append(_add("System.IterationPath", iteration))
        if (tags := fields.pop("tags", None)) is not None:
            patch.append(_add("System.Tags", _serialize_tags(tags)))
        if parent is not None:
            patch.append(_relate(HIERARCHY_REVERSE, f"{self.base_url}/_apis/wit/workItems/{parent}"))
        patch.extend(_add(_qualify(k), v) for k, v in fields.items())

        created = self.request(
            "POST",
            f"/{project}/_apis/wit/workitems/${item_type}",
            params={"api-version": API_VERSION},
            json=patch,
            content_type=PATCH_CONTENT_TYPE,
        )
        return self._record(created)

    def update(self, ident: str, **fields: Any) -> AlmRecord:
        parent = fields.pop("parent", None)
        if not fields and parent is None:
            raise SourcesConfigError(
                "update needs at least one field",
                server="azuredevops",
                remediation="pass title=..., state=..., parent=<id>, or any System.* field",
            )
        alias = {"title": "System.Title", "description": "System.Description",
                 "state": "System.State", "assignee": "System.AssignedTo",
                 "iteration": "System.IterationPath", "tags": "System.Tags"}
        patch = [
            _add(
                alias.get(key, _qualify(key)),
                _serialize_tags(value) if key == "tags" else value,
            )
            for key, value in fields.items()
        ]
        if parent is not None:
            patch.append(_relate(HIERARCHY_REVERSE, f"{self.base_url}/_apis/wit/workItems/{parent}"))
        updated = self.request(
            "PATCH",
            f"/_apis/wit/workitems/{ident}",
            params={"api-version": API_VERSION},
            json=patch,
            content_type=PATCH_CONTENT_TYPE,
        )
        return self._record(updated)

    def transition(self, ident: str, status: str) -> AlmRecord:
        return self.update(ident, state=status)

    def link_work_items(self, source: str, target: str, *, relation: str) -> AlmRecord:
        """Add one `relation` on work item `source` pointing at work item `target`."""
        linked = self.request(
            "PATCH",
            f"/_apis/wit/workitems/{source}",
            params={"api-version": API_VERSION},
            json=[_relate(relation, f"{self.base_url}/_apis/wit/workItems/{target}")],
            content_type=PATCH_CONTENT_TYPE,
        )
        return self._record(linked)

    def delete(self, ident: str, *, permanent: bool = False) -> dict[str, Any]:
        """Delete a work item. It goes to the recycle bin unless permanent is set."""
        record = self.get(ident)
        if record.type == TEST_CASE_TYPE:
            return self._delete_test_case(record, permanent=permanent)
        params: dict[str, Any] = {"api-version": API_VERSION}
        if permanent:
            params["destroy"] = "true"
        body = self.request("DELETE", f"/_apis/wit/workitems/{ident}", params=params)
        _raise_embedded_error(body, ident)
        return {
            "system": "azuredevops",
            "id": record.id,
            "key": record.key,
            "title": record.title,
            "deleted": True,
            "recoverable": not permanent,
        }

    def _delete_test_case(self, record: AlmRecord, *, permanent: bool) -> dict[str, Any]:
        """Remove a Test Case, which the work item API refuses to touch.

        Azure keeps test artifacts behind the Test Management API instead, and that
        one destroys outright — there is no recycle bin to land in. Deleting without
        `permanent` is refused rather than quietly made irreversible.
        """
        if not permanent:
            raise SourcesConfigError(
                f"work item {record.id} is a Test Case, which cannot be recovered "
                "after deletion",
                server="azuredevops",
                remediation="pass permanent=True (--permanent) to destroy it outright",
            )
        project = (record.raw.get("fields") or {}).get("System.TeamProject")
        self.request(
            "DELETE",
            f"/{project}/_apis/test/testcases/{record.id}",
            params={"api-version": f"{API_VERSION}-preview.1"},
        )
        return {
            "system": "azuredevops",
            "id": record.id,
            "key": record.key,
            "title": record.title,
            "deleted": True,
            "recoverable": False,
        }

    def default_team(self, project: str) -> str:
        """The team id used when a sprint operation isn't given one explicitly."""
        body = self.request(
            "GET", f"/_apis/projects/{project}", params={"api-version": API_VERSION}
        )
        team = body.get("defaultTeam") or {}
        if not team.get("id"):
            raise SourcesConfigError(
                f"azuredevops: project {project!r} has no default team",
                server="azuredevops",
                remediation="pass team explicitly",
            )
        return str(team["id"])

    def team_settings(self, project: str, *, team: str | None = None) -> dict[str, Any]:
        """Where this team's backlog and default sprint currently point.

        Azure has no fixed "backlog" work item state — a backlog is just the
        iteration path a team is configured to file untriaged work under. Read
        this rather than assuming the project root is the backlog.
        """
        team_id = team or self.default_team(project)
        return self.request(
            "GET",
            f"/{project}/{team_id}/_apis/work/teamsettings",
            params={"api-version": API_VERSION},
        )

    def iterations(self, project: str, *, team: str | None = None) -> list[dict[str, Any]]:
        """This team's sprints, with the dates and timeframe (past/current/future)
        Azure computes from them — not the full classification-node tree, which
        includes iterations the team hasn't opted into."""
        team_id = team or self.default_team(project)
        body = self.request(
            "GET",
            f"/{project}/{team_id}/_apis/work/teamsettings/iterations",
            params={"api-version": API_VERSION},
        )
        return list(body.get("value", []))

    def set_iteration_dates(
        self, project: str, path: str, *, start: str, finish: str
    ) -> dict[str, Any]:
        """Set a sprint's start/finish dates — Azure's equivalent of "starting" it.

        There is no separate activation call: Azure derives an iteration's
        timeFrame (past/current/future) from today's date against these two
        values, so a sprint becomes "current" the moment today falls inside them.
        `path` is relative to the project root, e.g. "Iteration 1", matching what
        `iterations()` returns.

        Azure discards a date carrying no time component, answering 200 with an
        attribute-less node. Dates are widened to full ISO-8601 first, and a
        response without attributes raises rather than reporting success.
        """
        node = self.request(
            "PATCH",
            f"/{project}/_apis/wit/classificationnodes/iterations/{path}",
            params={"api-version": API_VERSION},
            json={
                "attributes": {
                    "startDate": _iso_datetime(start),
                    "finishDate": _iso_datetime(finish),
                }
            },
        )
        if not node.get("attributes"):
            raise ApiError(
                f"azuredevops: the dates for {path!r} were not applied — Azure "
                "returned the iteration with no attributes",
                server="azuredevops",
                remediation="check the iteration path is correct and the account "
                "may edit project configuration",
            )
        return node

    def test_plans(self, project: str) -> list[dict[str, Any]]:
        body = self.request(
            "GET", f"/{project}/_apis/testplan/plans", params={"api-version": API_VERSION}
        )
        return list(body.get("value", []))

    def create_test_plan(
        self,
        project: str,
        name: str,
        *,
        area_path: str | None = None,
        iteration: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": name}
        if area_path is not None:
            payload["areaPath"] = area_path
        if iteration is not None:
            payload["iteration"] = iteration
        if start is not None:
            payload["startDate"] = start
        if end is not None:
            payload["endDate"] = end
        return self.request(
            "POST",
            f"/{project}/_apis/testplan/plans",
            params={"api-version": API_VERSION},
            json=payload,
        )

    def delete_test_plan(self, project: str, plan_id: str | int) -> None:
        self.request(
            "DELETE",
            f"/{project}/_apis/testplan/plans/{plan_id}",
            params={"api-version": API_VERSION},
        )

    def test_suites(self, project: str, plan_id: str | int) -> list[dict[str, Any]]:
        body = self.request(
            "GET",
            f"/{project}/_apis/testplan/Plans/{plan_id}/suites",
            params={"api-version": API_VERSION},
        )
        return list(body.get("value", []))

    def create_test_suite(
        self,
        project: str,
        plan_id: str | int,
        name: str,
        *,
        parent_suite_id: str | int,
        suite_type: str = "StaticTestSuite",
    ) -> dict[str, Any]:
        payload = {
            "suiteType": suite_type,
            "name": name,
            "parentSuite": {"id": int(parent_suite_id)},
        }
        return self.request(
            "POST",
            f"/{project}/_apis/testplan/Plans/{plan_id}/suites",
            params={"api-version": API_VERSION},
            json=payload,
        )

    def test_cases(
        self, project: str, plan_id: str | int, suite_id: str | int
    ) -> list[dict[str, Any]]:
        body = self.request(
            "GET",
            f"/{project}/_apis/testplan/Plans/{plan_id}/Suites/{suite_id}/TestCase",
            params={"api-version": API_VERSION},
        )
        return list(body.get("value", [])) if isinstance(body, dict) else list(body)

    def add_test_cases(
        self,
        project: str,
        plan_id: str | int,
        suite_id: str | int,
        test_case_ids: list[str | int],
    ) -> Any:
        """File existing Test Case work items into a suite. They must already exist —
        create them with create_test_case() (or create(type="Test Case")) first."""
        payload = [{"workItem": {"id": int(tid)}} for tid in test_case_ids]
        return self.request(
            "POST",
            f"/{project}/_apis/testplan/Plans/{plan_id}/Suites/{suite_id}/TestCase",
            params={"api-version": API_VERSION},
            json=payload,
        )

    def create_test_case(
        self, scope: Mapping[str, Any], *, title: str, steps: list[Mapping[str, str]] | None = None, **fields: Any
    ) -> AlmRecord:
        """A Test Case is an ordinary work item; this just fixes the type and lets
        steps be passed as [{"action": ..., "expected": ...}] instead of hand-built
        XML."""
        if steps is not None:
            fields["Microsoft.VSTS.TCM.Steps"] = _steps_xml(steps)
        return self.create(scope, type=TEST_CASE_TYPE, title=title, **fields)

    def update_test_case_steps(self, ident: str, steps: list[Mapping[str, str]]) -> AlmRecord:
        return self.update(ident, **{"Microsoft.VSTS.TCM.Steps": _steps_xml(steps)})

    def test_points(
        self, project: str, plan_id: str | int, suite_id: str | int
    ) -> list[dict[str, Any]]:
        body = self.request(
            "GET",
            f"/{project}/_apis/testplan/Plans/{plan_id}/Suites/{suite_id}/TestPoint",
            params={"api-version": API_VERSION},
        )
        return list(body.get("value", []))

    def update_test_point_outcome(
        self,
        project: str,
        plan_id: str | int,
        suite_id: str | int,
        point_ids: list[str | int],
        outcome: str,
    ) -> Any:
        payload = [{"id": int(pid), "results": {"outcome": outcome}} for pid in point_ids]
        return self.request(
            "PATCH",
            f"/{project}/_apis/testplan/Plans/{plan_id}/Suites/{suite_id}/TestPoint",
            params={"api-version": API_VERSION},
            json=payload,
        )

    def create_test_run(
        self,
        project: str,
        name: str,
        plan_id: str | int,
        *,
        point_ids: list[str | int] | None = None,
        automated: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": name,
            "plan": {"id": int(plan_id)},
            "automated": automated,
        }
        if point_ids:
            payload["pointIds"] = [int(p) for p in point_ids]
        return self.request(
            "POST",
            f"/{project}/_apis/test/runs",
            params={"api-version": API_VERSION},
            json=payload,
        )

    def test_run_results(
        self,
        project: str,
        run_id: str | int,
        *,
        outcomes: str | None = None,
        details: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"api-version": API_VERSION}
        if outcomes is not None:
            params["outcomes"] = outcomes
        if details is not None:
            params["detailsToInclude"] = details
        body = self.request(
            "GET", f"/{project}/_apis/test/Runs/{run_id}/results", params=params
        )
        return list(body.get("value", []))

    def update_test_results(
        self, project: str, run_id: str | int, results: list[Mapping[str, Any]]
    ) -> Any:
        """results: [{"id": <resultId>, "outcome": "Passed", "comment": "..."}]"""
        return self.request(
            "PATCH",
            f"/{project}/_apis/test/Runs/{run_id}/results",
            params={"api-version": API_VERSION},
            json=list(results),
        )

    def complete_test_run(
        self, project: str, run_id: str | int, *, state: str = "Completed"
    ) -> dict[str, Any]:
        return self.request(
            "PATCH",
            f"/{project}/_apis/test/runs/{run_id}",
            params={"api-version": API_VERSION},
            json={"state": state},
        )

    def test_results_by_build(self, project: str, build_id: str | int) -> list[dict[str, Any]]:
        """ResultsByBuild is still preview-only server-side: a bare API_VERSION gets
        rejected with 'The -preview flag must be supplied' even at 7.1."""
        body = self.request(
            "GET",
            f"/{project}/_apis/test/ResultsByBuild",
            params={"api-version": f"{API_VERSION}-preview.1", "buildId": build_id},
        )
        return list(body.get("value", []))

    def comment(self, ident: str, text: str, *, project: str) -> str:
        body = self.request(
            "POST",
            f"/{project}/_apis/wit/workItems/{ident}/comments",
            params={"api-version": f"{API_VERSION}-preview.3"},
            json={"text": text},
        )
        return str(body.get("id", ""))

    def _record(self, item: Mapping[str, Any]) -> AlmRecord:
        fields = item.get("fields") or {}
        assigned = fields.get("System.AssignedTo")
        assignee = (
            assigned.get("displayName") if isinstance(assigned, dict) else assigned
        )
        ident = str(item.get("id", ""))
        project = fields.get("System.TeamProject", "")
        tags = [t.strip() for t in (fields.get("System.Tags") or "").split(";") if t.strip()]
        return AlmRecord(
            system="azuredevops",
            id=ident,
            key=ident,
            title=fields.get("System.Title") or "",
            status=fields.get("System.State") or "",
            type=fields.get("System.WorkItemType") or "",
            url=f"{self.base_url}/{project}/_workitems/edit/{ident}" if project else "",
            assignee=assignee,
            tags=tags,
            raw=dict(item),
        )


def _raise_embedded_error(body: Any, ident: str) -> None:
    """Azure answers some failed writes with HTTP 200 and the error inside the body.

    Delete is the notable case: an insufficient-permission refusal arrives as
    200 {"id": N, "code": 404, "message": "VS403145: ..."}. Trusting the status
    alone would report a deletion that never happened.
    """
    if not isinstance(body, dict):
        return
    code = body.get("code")
    if not isinstance(code, int) or code < 400:
        return

    message = str(body.get("message") or f"work item {ident} was not deleted")
    if code in (401, 403) or "permission" in message.lower():
        raise PermissionDeniedError(
            f"azuredevops: {message}",
            server="azuredevops",
            status=code,
            remediation=(
                "the PAT is valid but this account cannot delete work items; grant "
                "the project's 'Delete work items in this project' permission, or use "
                "a PAT whose owner has it"
            ),
        )
    raise ApiError(
        f"azuredevops: {message}",
        server="azuredevops",
        status=code,
        remediation="check the work item id and the account's rights",
    )


def _fields_with(extra_fields: Iterable[str] | None) -> list[str]:
    """The fields every read names, plus the caller's extra reference names, once each."""
    fields = list(_FIELDS)
    fields.extend(f for f in dict.fromkeys(extra_fields or ()) if f not in fields)
    return fields


def _add(path: str, value: Any) -> dict[str, Any]:
    return {"op": "add", "path": f"/fields/{path}", "value": value}


def _relate(rel: str, url: str) -> dict[str, Any]:
    return {"op": "add", "path": "/relations/-", "value": {"rel": rel, "url": url}}


def _qualify(key: str) -> str:
    return key if "." in key else f"System.{key}"


def _serialize_tags(value: Any) -> str:
    if isinstance(value, str):
        return value
    return "; ".join(str(v) for v in value)


def _iso_datetime(value: str) -> str:
    """Widen a bare YYYY-MM-DD to midnight UTC; pass through anything with a time."""
    text = str(value).strip()
    if len(text) == 10 and text.count("-") == 2:
        return f"{text}T00:00:00Z"
    return text


_GHERKIN_STEP = re.compile(r"^\s*(Given|When|Then|And|But)\b", re.IGNORECASE)


def gherkin_test_steps(text: str) -> list[dict[str, str]]:
    """Azure Test Case steps from Gherkin: Given/When lines are actions, Then lines the expected result of the step before."""
    steps: list[dict[str, str]] = []
    in_outcome = False
    for line in text.splitlines():
        match = _GHERKIN_STEP.match(line)
        if not match:
            continue
        keyword = match.group(1).lower()
        body = " ".join(line.split())
        in_outcome = keyword == "then" or (keyword in {"and", "but"} and in_outcome)
        if in_outcome and steps:
            steps[-1]["expected"] = f"{steps[-1]['expected']}\n{body}".strip()
        else:
            steps.append({"action": body, "expected": ""})
    return steps


def gherkin_html(text: str) -> str:
    """The Gherkin as an Azure rich-text description, one line per line."""
    return "<br>".join(_html_escape(line.rstrip()) for line in text.splitlines())


def _steps_xml(steps: list[Mapping[str, str]]) -> str:
    """Build the XML Azure's Microsoft.VSTS.TCM.Steps field expects out of a plain
    [{"action": ..., "expected": ...}] list, rather than making a caller hand-craft it."""
    parts = [f'<steps id="0" last="{len(steps)}">']
    for i, step in enumerate(steps, start=1):
        action = _xml_escape(str(step.get("action", "")))
        expected = _xml_escape(str(step.get("expected", "")))
        parts.append(
            f'<step id="{i}" type="ActionStep">'
            f'<parameterizedString isformatted="true">{action}</parameterizedString>'
            f'<parameterizedString isformatted="true">{expected}</parameterizedString>'
            "</step>"
        )
    parts.append("</steps>")
    return "".join(parts)
