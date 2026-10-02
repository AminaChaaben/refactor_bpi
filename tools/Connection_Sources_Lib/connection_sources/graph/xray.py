"""Reading Xray, whichever of the three ways this site actually has it.

Xray is not one API. Xray Cloud is a separate product on its own host with its own
credentials and a GraphQL schema keyed on Jira's numeric issue ids; Xray Server/DC is
a Jira plugin answering REST on the Jira host under `/rest/raven`; and a great many
teams have no Xray at all and model tests as ordinary Jira issue types with custom
fields standing in for steps and preconditions. All three are real, they look almost
identical from the Jira side, and the graph has to be built from whichever is present.

A plain-Jira site models tests as ordinary issue types with custom fields standing in
for steps and preconditions, so it runs entirely through `extract_jira`. Everything in
this module is the path for a site that does have the plugin, written and tested
against recorded payloads rather than being left as a stub — discovering the shape of
the API on the day somebody connects a real Xray is how a migration turns into a
rewrite.

Read-only throughout. The graph never writes to a test management system: an
extraction bug that corrupts a test plan is a far worse outcome than an extraction bug
that produces a wrong picture.
"""

from __future__ import annotations

from typing import Any, Iterable, Iterator, Mapping
from urllib.parse import urlparse

import httpx

from ..errors import ApiError, CredentialError, SourcesConfigError
from ..models import AlmRecord, Identity
from .readonly import ReadOnlyClient

__all__ = [
    "CLOUD_HOST",
    "XrayCloudClient",
    "XrayServerApi",
    "XrayServerClient",
    "XrayServerVia",
    "detect_tier",
]

CLOUD_HOST = "https://xray.cloud.getxray.app"
CLOUD_API = "/api/v2"
SERVER_API = "/rest/raven/2.0/api"
JIRA_API = "/rest/api/3"

# Xray Cloud pages everything with (start, limit) and caps limit at 100.
PAGE = 100


# The one query the whole test side of the graph is built from.
#
# It is written as a single deep query rather than a query per relationship because
# Xray Cloud counts GraphQL *calls* against a per-minute quota, not fields: asking for
# a test's steps, preconditions, sets, plans and executions in five calls costs five
# times as much quota as asking once, and the quota is what a full extraction runs out
# of first.
TESTS_QUERY = """
query GetTests($jql: String!, $limit: Int!, $start: Int!) {
  getTests(jql: $jql, limit: $limit, start: $start) {
    total
    start
    limit
    results {
      issueId
      projectId
      testType { name kind }
      unstructured
      gherkin
      scenarioType
      folder { path }
      steps {
        id
        action
        data
        result
        attachments { id filename }
      }
      preconditions(limit: 100) {
        total
        results { issueId definition preconditionType { name kind } }
      }
      testSets(limit: 100) { total results { issueId } }
      testPlans(limit: 100) { total results { issueId } }
      testExecutions(limit: 100) { total results { issueId } }
      jira(fields: ["key", "summary", "status", "assignee"])
    }
  }
}
"""

EXECUTIONS_QUERY = """
query GetExecutions($jql: String!, $limit: Int!, $start: Int!) {
  getTestExecutions(jql: $jql, limit: $limit, start: $start) {
    total
    start
    limit
    results {
      issueId
      projectId
      testEnvironments
      testPlans(limit: 100) { total results { issueId } }
      tests(limit: 100) { total results { issueId } }
      jira(fields: ["key", "summary", "status"])
    }
  }
}
"""

RUNS_QUERY = """
query GetRuns($testExecIssueIds: [String], $limit: Int!, $start: Int!) {
  getTestRuns(testExecIssueIds: $testExecIssueIds, limit: $limit, start: $start) {
    total
    start
    limit
    results {
      id
      status { name color description }
      startedOn
      finishedOn
      comment
      executedById
      assigneeId
      test { issueId }
      testExecution { issueId }
      steps {
        id
        action
        data
        result
        actualResult
        status { name }
        defects
        evidence { id filename }
      }
      defects
      evidence { id filename }
    }
  }
}
"""

PRECONDITIONS_QUERY = """
query GetPreconditions($jql: String!, $limit: Int!, $start: Int!) {
  getPreconditions(jql: $jql, limit: $limit, start: $start) {
    total
    start
    limit
    results {
      issueId
      projectId
      definition
      preconditionType { name kind }
      folder { path }
      tests(limit: 100) { total results { issueId } }
      jira(fields: ["key", "summary"])
    }
  }
}
"""

PLANS_QUERY = """
query GetPlans($jql: String!, $limit: Int!, $start: Int!) {
  getTestPlans(jql: $jql, limit: $limit, start: $start) {
    total
    start
    limit
    results {
      issueId
      projectId
      folders { path }
      tests(limit: 100) { total results { issueId } }
      testExecutions(limit: 100) { total results { issueId } }
      jira(fields: ["key", "summary", "status", "duedate"])
    }
  }
}
"""

SETS_QUERY = """
query GetSets($jql: String!, $limit: Int!, $start: Int!) {
  getTestSets(jql: $jql, limit: $limit, start: $start) {
    total
    start
    limit
    results {
      issueId
      projectId
      tests(limit: 100) { total results { issueId } }
      jira(fields: ["key", "summary"])
    }
  }
}
"""

# -- write mutations ---------------------------------------------------------
#
# Every string below is one GraphQL mutation, called through `graphql()` the same
# way the queries above are — Xray Cloud has a single endpoint for both. Field
# selections are kept to what each write needs back (an id and the Jira key), not
# the full shape the read queries ask for, since a mutation's answer is only ever
# used to confirm what just happened.

CREATE_TEST_MUTATION = """
mutation CreateTest($testType: UpdateTestTypeInput, $steps: [CreateStepInput], $unstructured: String, $gherkin: String, $preconditionIssueIds: [String], $jira: JSON!) {
  createTest(testType: $testType, steps: $steps, unstructured: $unstructured, gherkin: $gherkin, preconditionIssueIds: $preconditionIssueIds, jira: $jira) {
    test { issueId jira(fields: ["key"]) }
    warnings
  }
}
"""

UPDATE_TEST_TYPE_MUTATION = """
mutation UpdateTestType($issueId: String!, $testType: UpdateTestTypeInput!) {
  updateTestType(issueId: $issueId, testType: $testType) { issueId testType { name kind } }
}
"""

UPDATE_UNSTRUCTURED_MUTATION = """
mutation UpdateUnstructured($issueId: String!, $unstructured: String!) {
  updateUnstructuredTestDefinition(issueId: $issueId, unstructured: $unstructured) { issueId unstructured }
}
"""

UPDATE_GHERKIN_MUTATION = """
mutation UpdateGherkin($issueId: String!, $gherkin: String!) {
  updateGherkinTestDefinition(issueId: $issueId, gherkin: $gherkin) { issueId gherkin }
}
"""

DELETE_TEST_MUTATION = """
mutation DeleteTest($issueId: String!) {
  deleteTest(issueId: $issueId)
}
"""

CREATE_PRECONDITION_MUTATION = """
mutation CreatePrecondition($preconditionType: UpdatePreconditionTypeInput, $definition: String, $testIssueIds: [String], $jira: JSON!) {
  createPrecondition(preconditionType: $preconditionType, definition: $definition, testIssueIds: $testIssueIds, jira: $jira) {
    precondition { issueId jira(fields: ["key"]) }
    warnings
  }
}
"""

UPDATE_PRECONDITION_MUTATION = """
mutation UpdatePrecondition($issueId: String!, $data: UpdatePreconditionInput) {
  updatePrecondition(issueId: $issueId, data: $data) { issueId preconditionType { name kind } definition }
}
"""

DELETE_PRECONDITION_MUTATION = """
mutation DeletePrecondition($issueId: String!) {
  deletePrecondition(issueId: $issueId)
}
"""

CREATE_TEST_SET_MUTATION = """
mutation CreateTestSet($testIssueIds: [String], $jira: JSON!) {
  createTestSet(testIssueIds: $testIssueIds, jira: $jira) {
    testSet { issueId jira(fields: ["key"]) }
    warnings
  }
}
"""

DELETE_TEST_SET_MUTATION = """
mutation DeleteTestSet($issueId: String!) {
  deleteTestSet(issueId: $issueId)
}
"""

CREATE_TEST_PLAN_MUTATION = """
mutation CreateTestPlan($testIssueIds: [String], $jira: JSON!) {
  createTestPlan(testIssueIds: $testIssueIds, jira: $jira) {
    testPlan { issueId jira(fields: ["key"]) }
    warnings
  }
}
"""

DELETE_TEST_PLAN_MUTATION = """
mutation DeleteTestPlan($issueId: String!) {
  deleteTestPlan(issueId: $issueId)
}
"""

CREATE_TEST_EXECUTION_MUTATION = """
mutation CreateTestExecution($testIssueIds: [String], $testEnvironments: [String], $jira: JSON!) {
  createTestExecution(testIssueIds: $testIssueIds, testEnvironments: $testEnvironments, jira: $jira) {
    testExecution { issueId jira(fields: ["key"]) }
    warnings
    createdTestEnvironments
  }
}
"""

DELETE_TEST_EXECUTION_MUTATION = """
mutation DeleteTestExecution($issueId: String!) {
  deleteTestExecution(issueId: $issueId)
}
"""

ADD_TESTS_TO_SET_MUTATION = """
mutation AddTestsToTestSet($issueId: String!, $testIssueIds: [String]!) {
  addTestsToTestSet(issueId: $issueId, testIssueIds: $testIssueIds) { addedTests warning }
}
"""

REMOVE_TESTS_FROM_SET_MUTATION = """
mutation RemoveTestsFromTestSet($issueId: String!, $testIssueIds: [String]!) {
  removeTestsFromTestSet(issueId: $issueId, testIssueIds: $testIssueIds)
}
"""

ADD_TESTS_TO_PLAN_MUTATION = """
mutation AddTestsToTestPlan($issueId: String!, $testIssueIds: [String]!) {
  addTestsToTestPlan(issueId: $issueId, testIssueIds: $testIssueIds) { addedTests warning }
}
"""

REMOVE_TESTS_FROM_PLAN_MUTATION = """
mutation RemoveTestsFromTestPlan($issueId: String!, $testIssueIds: [String]!) {
  removeTestsFromTestPlan(issueId: $issueId, testIssueIds: $testIssueIds)
}
"""

ADD_TESTS_TO_EXECUTION_MUTATION = """
mutation AddTestsToTestExecution($issueId: String!, $testIssueIds: [String]) {
  addTestsToTestExecution(issueId: $issueId, testIssueIds: $testIssueIds) { addedTests warning }
}
"""

REMOVE_TESTS_FROM_EXECUTION_MUTATION = """
mutation RemoveTestsFromTestExecution($issueId: String!, $testIssueIds: [String]!) {
  removeTestsFromTestExecution(issueId: $issueId, testIssueIds: $testIssueIds)
}
"""

ADD_TESTS_TO_PRECONDITION_MUTATION = """
mutation AddTestsToPrecondition($issueId: String!, $testIssueIds: [String]) {
  addTestsToPrecondition(issueId: $issueId, testIssueIds: $testIssueIds) { addedTests warning }
}
"""

REMOVE_TESTS_FROM_PRECONDITION_MUTATION = """
mutation RemoveTestsFromPrecondition($issueId: String!, $testIssueIds: [String]) {
  removeTestsFromPrecondition(issueId: $issueId, testIssueIds: $testIssueIds)
}
"""

UPDATE_TEST_RUN_STATUS_MUTATION = """
mutation UpdateTestRunStatus($id: String!, $status: String!) {
  updateTestRunStatus(id: $id, status: $status)
}
"""


class XrayCloudClient(ReadOnlyClient):
    """Xray Cloud over GraphQL, authenticated separately from Jira.

    Its credentials are an API key pair issued inside Jira but presented to a
    different host, so they are their own environment variables and its failures are
    its own — an expired Xray key must not read as a broken Jira connection.
    """

    name = "xray"
    required_env = ("XRAY_CLIENT_ID", "XRAY_CLIENT_SECRET")

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        base_url: str = CLOUD_HOST,
        env: Mapping[str, str] | None = None,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(base_url=base_url, env=env, timeout=timeout, transport=transport)
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str],
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> "XrayCloudClient":
        missing = [key for key in cls.required_env if not env.get(key)]
        if missing:
            raise CredentialError(
                f"xray: missing credential(s): {', '.join(missing)}",
                server="xray",
                remediation=(
                    "create an API key in Jira under Apps > Xray > API Keys and set "
                    "XRAY_CLIENT_ID and XRAY_CLIENT_SECRET"
                ),
            )
        return cls(
            client_id=env["XRAY_CLIENT_ID"],
            client_secret=env["XRAY_CLIENT_SECRET"],
            base_url=env.get("XRAY_URL") or CLOUD_HOST,
            env=env,
            timeout=timeout,
            transport=transport,
        )

    def authenticate(self) -> str:
        """Exchange the key pair for a bearer token.

        The token is cached for the life of the client. Xray issues it for 24 hours
        and a full extraction is minutes, so re-authenticating per call would only
        spend quota — the authenticate endpoint has its own rate limit and it is a
        tight one.
        """
        if self._token:
            return self._token
        body = self.raw_post(
            f"{CLOUD_API}/authenticate",
            {"client_id": self._client_id, "client_secret": self._client_secret},
        )
        # The endpoint answers with a bare JSON string, not an object.
        token = body if isinstance(body, str) else (body or {}).get("token", "")
        if not token:
            raise CredentialError(
                "xray: authentication returned no token",
                server="xray",
                remediation="regenerate the API key pair in Jira under Apps > Xray > API Keys",
            )
        self._token = str(token)
        return self._token

    def ping(self) -> Identity:
        self.authenticate()
        return Identity(system="xray", account=self._client_id, base_url=self.base_url)

    def graphql(self, query: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        """One GraphQL call, with Xray's partial-success answers treated as failures.

        GraphQL returns HTTP 200 with an `errors` array when a query is rejected, so
        the transport's status mapping never sees it. Left unchecked that arrives as
        an empty result set, which reads exactly like "this project has no tests" —
        the single most misleading thing a traceability graph can say.
        """
        token = self.authenticate()
        body = self.raw_post(
            f"{CLOUD_API}/graphql",
            {"query": query, "variables": dict(variables)},
            headers={"Authorization": f"Bearer {token}"},
        )
        if isinstance(body, Mapping) and body.get("errors"):
            messages = "; ".join(
                str(err.get("message", err)) for err in body["errors"] if isinstance(err, Mapping)
            )
            raise ApiError(
                f"xray: GraphQL refused the query — {messages}",
                server="xray",
                remediation="check the JQL and that the API key has access to the project",
            )
        data = body.get("data") if isinstance(body, Mapping) else None
        return dict(data or {})

    def _paged(
        self, query: str, root: str, variables: Mapping[str, Any], *, limit: int
    ) -> Iterator[dict[str, Any]]:
        """Walk one GraphQL collection to `limit`, or to its end.

        Paging is driven by the reported `total` rather than by "a short page means
        the end": Xray returns fewer than `limit` results for perfectly ordinary
        reasons (permissions filtering a page), and stopping there would silently
        truncate the extraction.
        """
        start = 0
        seen = 0
        while seen < limit:
            page = min(PAGE, limit - seen)
            data = self.graphql(query, {**variables, "limit": page, "start": start})
            block = data.get(root) or {}
            results = block.get("results") or []
            for entry in results:
                yield entry
                seen += 1
                if seen >= limit:
                    return
            total = int(block.get("total") or 0)
            start += len(results)
            if not results or start >= total:
                return

    # -- the six collections the graph is built from ------------------------

    def tests(self, jql: str, *, limit: int = 1000) -> list[dict[str, Any]]:
        return list(self._paged(TESTS_QUERY, "getTests", {"jql": jql}, limit=limit))

    def preconditions(self, jql: str, *, limit: int = 1000) -> list[dict[str, Any]]:
        return list(
            self._paged(PRECONDITIONS_QUERY, "getPreconditions", {"jql": jql}, limit=limit)
        )

    def test_sets(self, jql: str, *, limit: int = 1000) -> list[dict[str, Any]]:
        return list(self._paged(SETS_QUERY, "getTestSets", {"jql": jql}, limit=limit))

    def test_plans(self, jql: str, *, limit: int = 1000) -> list[dict[str, Any]]:
        return list(self._paged(PLANS_QUERY, "getTestPlans", {"jql": jql}, limit=limit))

    def test_executions(self, jql: str, *, limit: int = 1000) -> list[dict[str, Any]]:
        return list(
            self._paged(EXECUTIONS_QUERY, "getTestExecutions", {"jql": jql}, limit=limit)
        )

    def test_runs(
        self, execution_issue_ids: Iterable[str], *, limit: int = 5000
    ) -> list[dict[str, Any]]:
        """Every run belonging to these executions, step results included.

        Keyed on execution ids rather than fetched per test: a run belongs to exactly
        one execution, so asking by execution reads each run once, while asking by
        test would read the same execution's runs again for every test in it.
        """
        ids = [str(i) for i in execution_issue_ids if i]
        if not ids:
            return []
        return list(
            self._paged(RUNS_QUERY, "getTestRuns", {"testExecIssueIds": ids}, limit=limit)
        )

    def search(self, scope: Mapping[str, Any], *, limit: int = PAGE) -> list[AlmRecord]:
        """Tests in scope, as ordinary records — enough for `check` to prove access."""
        jql = str(scope.get("jql") or "")
        records = []
        for test in self.tests(jql, limit=limit):
            jira = test.get("jira") or {}
            records.append(
                AlmRecord(
                    system="xray",
                    id=str(test.get("issueId") or ""),
                    key=str(jira.get("key") or ""),
                    title=str(jira.get("summary") or ""),
                    status=str((jira.get("status") or {}).get("name") or ""),
                    type=str((test.get("testType") or {}).get("name") or "Test"),
                    url="",
                    assignee=None,
                    tags=[],
                    raw=dict(test),
                )
            )
        return records

    # -- writes --------------------------------------------------------------
    #
    # Every Xray Cloud entity is a Jira issue, so a create carries the same
    # `jira: {fields: {...}}` shape a plain Jira issue create would — there is no
    # separate "Xray project" to address. Membership and run-status mutations take
    # no project at all, only issue ids the caller already resolved; scoping those
    # to the configured project is the caller's job, not this client's.

    def create_test(
        self,
        project_key: str,
        summary: str,
        *,
        test_type: str,
        steps: Iterable[Mapping[str, str]] = (),
        gherkin: str = "",
        unstructured: str = "",
        description: str = "",
        precondition_issue_ids: Iterable[str] = (),
    ) -> dict[str, Any]:
        """Create one Test issue: Manual, Cucumber, or Generic.

        Mirrors `XrayServerApi.create_test`'s dispatch by `test_type`; Cloud carries
        the shape as mutation arguments rather than per-instance custom fields, so
        nothing needs declaring in `sync.test_detail_fields` for this tier.
        """
        normalized = test_type.strip().lower()
        variables: dict[str, Any] = {
            "testType": {"name": test_type},
            "jira": {
                "fields": {
                    "project": {"key": project_key},
                    "summary": summary,
                    "description": description,
                }
            },
        }
        if precondition_issue_ids:
            variables["preconditionIssueIds"] = list(precondition_issue_ids)
        if normalized == "manual":
            variables["steps"] = [
                {
                    "action": str(step.get("action", "")),
                    "data": str(step.get("data", "")),
                    "result": str(step.get("result", "")),
                }
                for step in steps
            ]
        elif normalized == "cucumber":
            variables["gherkin"] = gherkin
        elif normalized == "generic":
            variables["unstructured"] = unstructured
        else:
            raise SourcesConfigError(
                f"unknown Xray test type {test_type!r}",
                server="xray",
                remediation="use Manual, Cucumber or Generic",
            )
        result = self.graphql(CREATE_TEST_MUTATION, variables)
        return dict(result.get("createTest") or {})

    def update_test_type(self, issue_id: str, test_type: str) -> dict[str, Any]:
        result = self.graphql(
            UPDATE_TEST_TYPE_MUTATION, {"issueId": issue_id, "testType": {"name": test_type}}
        )
        return dict(result.get("updateTestType") or {})

    def update_unstructured_test(self, issue_id: str, unstructured: str) -> dict[str, Any]:
        result = self.graphql(
            UPDATE_UNSTRUCTURED_MUTATION, {"issueId": issue_id, "unstructured": unstructured}
        )
        return dict(result.get("updateUnstructuredTestDefinition") or {})

    def update_gherkin_test(self, issue_id: str, gherkin: str) -> dict[str, Any]:
        result = self.graphql(UPDATE_GHERKIN_MUTATION, {"issueId": issue_id, "gherkin": gherkin})
        return dict(result.get("updateGherkinTestDefinition") or {})

    def delete_test(self, issue_id: str) -> str:
        result = self.graphql(DELETE_TEST_MUTATION, {"issueId": issue_id})
        return str(result.get("deleteTest") or "")

    def create_precondition(
        self,
        project_key: str,
        summary: str,
        *,
        precondition_type: str,
        definition: str = "",
        test_issue_ids: Iterable[str] = (),
        description: str = "",
    ) -> dict[str, Any]:
        variables: dict[str, Any] = {
            "preconditionType": {"name": precondition_type},
            "definition": definition,
            "jira": {
                "fields": {
                    "project": {"key": project_key},
                    "summary": summary,
                    "description": description,
                }
            },
        }
        if test_issue_ids:
            variables["testIssueIds"] = list(test_issue_ids)
        result = self.graphql(CREATE_PRECONDITION_MUTATION, variables)
        return dict(result.get("createPrecondition") or {})

    def update_precondition(
        self,
        issue_id: str,
        *,
        precondition_type: str = "",
        definition: str = "",
        folder_path: str = "",
    ) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if precondition_type:
            data["preconditionType"] = {"name": precondition_type}
        if definition:
            data["definition"] = definition
        if folder_path:
            data["folderPath"] = folder_path
        result = self.graphql(UPDATE_PRECONDITION_MUTATION, {"issueId": issue_id, "data": data})
        return dict(result.get("updatePrecondition") or {})

    def delete_precondition(self, issue_id: str) -> str:
        result = self.graphql(DELETE_PRECONDITION_MUTATION, {"issueId": issue_id})
        return str(result.get("deletePrecondition") or "")

    def create_test_set(
        self,
        project_key: str,
        summary: str,
        *,
        test_issue_ids: Iterable[str] = (),
        description: str = "",
    ) -> dict[str, Any]:
        variables: dict[str, Any] = {
            "jira": {
                "fields": {
                    "project": {"key": project_key},
                    "summary": summary,
                    "description": description,
                }
            },
        }
        if test_issue_ids:
            variables["testIssueIds"] = list(test_issue_ids)
        result = self.graphql(CREATE_TEST_SET_MUTATION, variables)
        return dict(result.get("createTestSet") or {})

    def delete_test_set(self, issue_id: str) -> str:
        result = self.graphql(DELETE_TEST_SET_MUTATION, {"issueId": issue_id})
        return str(result.get("deleteTestSet") or "")

    def create_test_plan(
        self,
        project_key: str,
        summary: str,
        *,
        test_issue_ids: Iterable[str] = (),
        description: str = "",
    ) -> dict[str, Any]:
        variables: dict[str, Any] = {
            "jira": {
                "fields": {
                    "project": {"key": project_key},
                    "summary": summary,
                    "description": description,
                }
            },
        }
        if test_issue_ids:
            variables["testIssueIds"] = list(test_issue_ids)
        result = self.graphql(CREATE_TEST_PLAN_MUTATION, variables)
        return dict(result.get("createTestPlan") or {})

    def delete_test_plan(self, issue_id: str) -> str:
        result = self.graphql(DELETE_TEST_PLAN_MUTATION, {"issueId": issue_id})
        return str(result.get("deleteTestPlan") or "")

    def create_test_execution(
        self,
        project_key: str,
        summary: str,
        *,
        test_issue_ids: Iterable[str] = (),
        test_environments: Iterable[str] = (),
        description: str = "",
    ) -> dict[str, Any]:
        variables: dict[str, Any] = {
            "jira": {
                "fields": {
                    "project": {"key": project_key},
                    "summary": summary,
                    "description": description,
                }
            },
        }
        if test_issue_ids:
            variables["testIssueIds"] = list(test_issue_ids)
        if test_environments:
            variables["testEnvironments"] = list(test_environments)
        result = self.graphql(CREATE_TEST_EXECUTION_MUTATION, variables)
        return dict(result.get("createTestExecution") or {})

    def delete_test_execution(self, issue_id: str) -> str:
        result = self.graphql(DELETE_TEST_EXECUTION_MUTATION, {"issueId": issue_id})
        return str(result.get("deleteTestExecution") or "")

    def add_tests_to_set(self, set_issue_id: str, test_issue_ids: Iterable[str]) -> dict[str, Any]:
        result = self.graphql(
            ADD_TESTS_TO_SET_MUTATION,
            {"issueId": set_issue_id, "testIssueIds": list(test_issue_ids)},
        )
        return dict(result.get("addTestsToTestSet") or {})

    def remove_tests_from_set(self, set_issue_id: str, test_issue_ids: Iterable[str]) -> str:
        result = self.graphql(
            REMOVE_TESTS_FROM_SET_MUTATION,
            {"issueId": set_issue_id, "testIssueIds": list(test_issue_ids)},
        )
        return str(result.get("removeTestsFromTestSet") or "")

    def add_tests_to_plan(self, plan_issue_id: str, test_issue_ids: Iterable[str]) -> dict[str, Any]:
        result = self.graphql(
            ADD_TESTS_TO_PLAN_MUTATION,
            {"issueId": plan_issue_id, "testIssueIds": list(test_issue_ids)},
        )
        return dict(result.get("addTestsToTestPlan") or {})

    def remove_tests_from_plan(self, plan_issue_id: str, test_issue_ids: Iterable[str]) -> str:
        result = self.graphql(
            REMOVE_TESTS_FROM_PLAN_MUTATION,
            {"issueId": plan_issue_id, "testIssueIds": list(test_issue_ids)},
        )
        return str(result.get("removeTestsFromTestPlan") or "")

    def add_tests_to_execution(
        self, execution_issue_id: str, test_issue_ids: Iterable[str]
    ) -> dict[str, Any]:
        result = self.graphql(
            ADD_TESTS_TO_EXECUTION_MUTATION,
            {"issueId": execution_issue_id, "testIssueIds": list(test_issue_ids)},
        )
        return dict(result.get("addTestsToTestExecution") or {})

    def remove_tests_from_execution(
        self, execution_issue_id: str, test_issue_ids: Iterable[str]
    ) -> str:
        result = self.graphql(
            REMOVE_TESTS_FROM_EXECUTION_MUTATION,
            {"issueId": execution_issue_id, "testIssueIds": list(test_issue_ids)},
        )
        return str(result.get("removeTestsFromTestExecution") or "")

    def add_tests_to_precondition(
        self, precondition_issue_id: str, test_issue_ids: Iterable[str]
    ) -> dict[str, Any]:
        result = self.graphql(
            ADD_TESTS_TO_PRECONDITION_MUTATION,
            {"issueId": precondition_issue_id, "testIssueIds": list(test_issue_ids)},
        )
        return dict(result.get("addTestsToPrecondition") or {})

    def remove_tests_from_precondition(
        self, precondition_issue_id: str, test_issue_ids: Iterable[str]
    ) -> str:
        result = self.graphql(
            REMOVE_TESTS_FROM_PRECONDITION_MUTATION,
            {"issueId": precondition_issue_id, "testIssueIds": list(test_issue_ids)},
        )
        return str(result.get("removeTestsFromPrecondition") or "")

    def find_test_run_id(self, execution_issue_id: str, test_issue_id: str) -> str:
        """The internal TestRun id for one test inside one execution.

        `updateTestRunStatus` takes this id, not the (execution, test) pair the
        Server API's `set_run_status` accepts directly — Xray Cloud has no mutation
        that sets a result by that pair, so every status update looks the run up
        first.
        """
        for run in self.test_runs([execution_issue_id]):
            test_ref = run.get("test") if isinstance(run.get("test"), Mapping) else {}
            if str(test_ref.get("issueId") or "") == str(test_issue_id):
                return str(run.get("id") or "")
        raise ApiError(
            f"xray: no test run found for test {test_issue_id!r} in execution {execution_issue_id!r}",
            server="xray",
            remediation="confirm the test was added to the execution first — that is what creates its run",
        )

    def update_run_status(self, execution_issue_id: str, test_issue_id: str, status: str) -> str:
        """Set one test's result inside one execution: resolve its run, then set it."""
        run_id = self.find_test_run_id(execution_issue_id, test_issue_id)
        result = self.graphql(UPDATE_TEST_RUN_STATUS_MUTATION, {"id": run_id, "status": status})
        return str(result.get("updateTestRunStatus") or "")


class XrayServerApi:
    """Every `/rest/raven` read, expressed over a single `raw_get`.

    Split from the client that owns a connection because Xray Server/DC always
    answers on the Jira host with the Jira credential — so during a build there is
    already an authenticated connection to exactly the right place, and opening a
    second one would mean a second session, a second set of rate-limit headers, and
    a second thing to close. `XrayServerVia` borrows the open one; `XrayServerClient`
    is for the cases with nothing to borrow.
    """

    base_url: str = ""

    def raw_get(  # pragma: no cover - every subclass supplies this
        self, path: str, params: Mapping[str, Any] | None = None
    ) -> Any:
        raise NotImplementedError

    def _listing(self, path: str, *, key: str = "") -> list[dict[str, Any]]:
        """One `/rest/raven` collection, as a list of mappings whatever it answered.

        The Server/DC endpoints are inconsistent about their envelope: some answer a
        bare array, some wrap it under a named key, and an issue with nothing to
        report answers `204 No Content`, which arrives here as `None`. Normalising in
        one place keeps every caller below to a single line and stops an envelope
        change from meaning a fix in eight methods.
        """
        body = self.raw_get(path)
        if isinstance(body, list):
            rows = body
        elif isinstance(body, Mapping):
            rows = body.get(key) if key else None
            if not isinstance(rows, list):
                rows = next(
                    (v for v in body.values() if isinstance(v, list)), [body] if body else []
                )
        else:
            rows = []
        return [row for row in rows if isinstance(row, Mapping)]

    def test_steps(self, issue_key: str) -> list[dict[str, Any]]:
        return self._listing(f"{SERVER_API}/test/{issue_key}/step", key="steps")

    def test_preconditions(self, issue_key: str) -> list[dict[str, Any]]:
        return self._listing(f"{SERVER_API}/test/{issue_key}/preconditions")

    def test_sets_of(self, issue_key: str) -> list[dict[str, Any]]:
        return self._listing(f"{SERVER_API}/test/{issue_key}/testsets")

    def test_plans_of(self, issue_key: str) -> list[dict[str, Any]]:
        return self._listing(f"{SERVER_API}/test/{issue_key}/testplans")

    def test_executions_of(self, issue_key: str) -> list[dict[str, Any]]:
        return self._listing(f"{SERVER_API}/test/{issue_key}/testexecutions")

    def precondition_tests(self, issue_key: str) -> list[dict[str, Any]]:
        return self._listing(f"{SERVER_API}/precondition/{issue_key}/test")

    def set_tests(self, issue_key: str) -> list[dict[str, Any]]:
        return self._listing(f"{SERVER_API}/testset/{issue_key}/test")

    def execution_tests(self, issue_key: str) -> list[dict[str, Any]]:
        return self._listing(f"{SERVER_API}/testexec/{issue_key}/test")

    def plan_tests(self, issue_key: str) -> list[dict[str, Any]]:
        return self._listing(f"{SERVER_API}/testplan/{issue_key}/test")

    def plan_executions(self, issue_key: str) -> list[dict[str, Any]]:
        return self._listing(f"{SERVER_API}/testplan/{issue_key}/testexecution")

    def test_run(self, execution_key: str, test_key: str) -> dict[str, Any]:
        body = self.raw_get(
            f"{SERVER_API}/testrun",
            params={"testExecIssueKey": execution_key, "testIssueKey": test_key},
        )
        return dict(body) if isinstance(body, Mapping) else {}

    def run_steps(self, run_id: Any) -> list[dict[str, Any]]:
        return self._listing(f"{SERVER_API}/testrun/{run_id}/step", key="steps")

    def test_statuses(self) -> list[dict[str, Any]]:
        """The site's own execution statuses — also the cheapest proof of the plugin."""
        return self._listing(f"{SERVER_API}/settings/teststatuses")

    # -- writes ------------------------------------------------------------
    #
    # Server/DC stores a test's shape on the Jira issue itself, through the
    # custom fields the site declares (ids passed in by the caller — they are
    # per-instance configuration, never hardcoded). Issue shells are created
    # over Jira REST; Xray-side membership and run results go through
    # `/rest/raven`. Every method below mirrors one colleague-facing operation.

    def raw_post(self, path: str, json: Any = None, params: Mapping[str, Any] | None = None) -> Any:  # pragma: no cover
        raise NotImplementedError

    def raw_put(self, path: str, json: Any = None, params: Mapping[str, Any] | None = None) -> Any:  # pragma: no cover
        raise NotImplementedError

    def create_test(
        self,
        project_key: str,
        summary: str,
        *,
        test_type: str,
        steps: Iterable[Mapping[str, str]] = (),
        gherkin: str = "",
        description: str = "",
        field_test_type: str,
        field_manual_steps: str = "",
        field_cucumber_script: str = "",
        issue_type_test: str = "Test",
    ) -> dict[str, Any]:
        """Create one test issue: Manual (structured steps) or Cucumber (Gherkin).

        The `test_type` field carries `{"value": "Manual"}` / `{"value":
        "Cucumber"}`; the steps or script land on the matching declared custom
        field, in the shapes this Xray version stores them. A test type whose
        fields were not declared is refused rather than half-created.
        """
        normalized = test_type.strip().lower()
        fields: dict[str, Any] = {
            "project": {"key": project_key},
            "issuetype": {"name": issue_type_test},
            "summary": summary,
            "description": description,
        }
        if not field_test_type:
            raise SourcesConfigError(
                "creating an Xray test needs the Test Type field id",
                server="xray",
                remediation="declare test_type in sync.test_detail_fields",
            )
        fields[field_test_type] = {"value": test_type}

        if normalized == "manual":
            if not field_manual_steps:
                raise SourcesConfigError(
                    "creating a Manual test needs the manual steps field id",
                    server="xray",
                    remediation="declare manual_steps in sync.test_detail_fields",
                )
            fields[field_manual_steps] = {
                "steps": [
                    {
                        "fields": {
                            "Action": str(step.get("action", "")),
                            "Data": str(step.get("data", "")),
                            "Expected Result": str(step.get("result", "")),
                        }
                    }
                    for step in steps
                ]
            }
        elif normalized == "cucumber":
            if not field_cucumber_script:
                raise SourcesConfigError(
                    "creating a Cucumber test needs the cucumber script field id",
                    server="xray",
                    remediation="declare cucumber_script in sync.test_detail_fields",
                )
            fields[field_cucumber_script] = gherkin
        else:
            raise SourcesConfigError(
                f"unknown Xray test type {test_type!r}",
                server="xray",
                remediation="use Manual or Cucumber",
            )
        return self.raw_post(f"{JIRA_API}/issue", {"fields": fields})

    def create_container(
        self,
        project_key: str,
        summary: str,
        issue_type: str,
        *,
        description: str = "",
    ) -> dict[str, Any]:
        """Create a Test Set, Test Plan or Test Execution issue shell."""
        return self.raw_post(
            f"{JIRA_API}/issue",
            {
                "fields": {
                    "project": {"key": project_key},
                    "issuetype": {"name": issue_type},
                    "summary": summary,
                    "description": description,
                }
            },
        )

    def add_tests_to_set(self, set_key: str, test_keys: Iterable[str]) -> dict[str, Any]:
        return self.raw_post(f"{SERVER_API}/testset/{set_key}/test", {"add": list(test_keys)})

    def remove_tests_from_set(self, set_key: str, test_keys: Iterable[str]) -> dict[str, Any]:
        return self.raw_post(f"{SERVER_API}/testset/{set_key}/test", {"remove": list(test_keys)})

    def add_tests_to_plan(self, plan_key: str, test_keys: Iterable[str]) -> dict[str, Any]:
        return self.raw_post(f"{SERVER_API}/testplan/{plan_key}/test", {"add": list(test_keys)})

    def remove_tests_from_plan(self, plan_key: str, test_keys: Iterable[str]) -> dict[str, Any]:
        return self.raw_post(f"{SERVER_API}/testplan/{plan_key}/test", {"remove": list(test_keys)})

    def add_tests_to_execution(self, execution_key: str, test_keys: Iterable[str]) -> dict[str, Any]:
        return self.raw_post(f"{SERVER_API}/testexec/{execution_key}/test", {"add": list(test_keys)})

    def set_run_status(
        self,
        execution_key: str,
        test_key: str,
        status: str,
        *,
        comment: str = "",
    ) -> dict[str, Any]:
        """Record one test's result inside an execution — the real run result."""
        entry: dict[str, Any] = {"testKey": test_key, "status": status}
        if comment:
            entry["comment"] = comment
        return self.raw_post(f"{SERVER_API}/testexec/{execution_key}/test", {"tests": [entry]})

    def update_run_status(self, run_id: Any, status: str) -> dict[str, Any]:
        return self.raw_put(f"{SERVER_API}/testrun/{run_id}/status", {"status": status})

    def import_execution_results(
        self,
        *,
        project_key: str,
        summary: str,
        execution_key: str = "",
        tests: Iterable[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Import a whole execution's results in the standard Xray format."""
        payload: dict[str, Any] = {
            "info": {"project": project_key, "summary": summary},
            "tests": [
                {
                    "testKey": str(test.get("testKey") or ""),
                    "status": str(test.get("status") or ""),
                    "start": test.get("start"),
                    "finish": test.get("finish"),
                    "comment": test.get("comment"),
                    "results": test.get("results") or [],
                }
                for test in tests
            ],
        }
        if execution_key:
            payload["testExecutionKey"] = execution_key
        return self.raw_post(f"{SERVER_API}/import/execution", payload)


class XrayServerVia(XrayServerApi):
    """Xray Server/DC read over an already-open Jira connection.

    Also serves as the probe `detect_tier` uses: asking whether this host answers on
    `/rest/raven` and then reading from it are the same connection and the same
    credential, so they are the same object rather than two wrappers that could be
    pointed at different places.
    """

    __slots__ = ("_client", "base_url")

    def __init__(self, client: Any) -> None:
        self._client = client
        self.base_url = getattr(client, "base_url", "")

    def raw_get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        return self._client.request("GET", path, params=params)

    def raw_post(self, path: str, json: Any = None, params: Mapping[str, Any] | None = None) -> Any:
        return self._client.request("POST", path, json=json, params=params)

    def raw_put(self, path: str, json: Any = None, params: Mapping[str, Any] | None = None) -> Any:
        return self._client.request("PUT", path, json=json, params=params)


class XrayServerClient(ReadOnlyClient, XrayServerApi):
    """Xray Server/DC with a connection of its own.

    Same Jira credential, so it takes the Jira auth header rather than a key pair.
    Its REST shape predates the Cloud GraphQL API and is not a subset of it — steps
    and runs are separate resources reached per issue, so an extraction here costs a
    call per test rather than one call per hundred.
    """

    name = "xray"

    def __init__(
        self,
        *,
        base_url: str,
        auth: str,
        env: Mapping[str, str] | None = None,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url, env=env, timeout=timeout, transport=transport, auth=auth
        )

    def ping(self) -> Identity:
        self.raw_get(f"{SERVER_API}/settings/teststatuses")
        return Identity(system="xray", account="jira-session", base_url=self.base_url)

    def search(self, scope: Mapping[str, Any], *, limit: int = PAGE) -> list[AlmRecord]:
        return []


def _hosting_of(base_url: str | None) -> str:
    """Which kind of Jira host the URL points at: cloud, self-hosted, or unknown.

    Atlassian's cloud sites live under `*.atlassian.net` and cannot run the
    Xray Server/DC plugin — Xray Cloud is a separate product keyed on its own
    credentials. Anything else is a self-hosted host, where Xray, if present,
    answers on `/rest/raven` with the same credential Jira itself uses. The
    URL is what settles the question, so it is read from the connection config
    rather than guessed from which credentials happen to be present.
    """
    if not base_url:
        return "unknown"
    parsed = urlparse(base_url if "://" in base_url else f"https://{base_url}")
    host = (parsed.netloc or parsed.path or "").strip("/").lower()
    if not host:
        return "unknown"
    return "cloud" if host.endswith(".atlassian.net") else "server"


def detect_tier(
    env: Mapping[str, str],
    *,
    jira_probe: Any = None,
    field_ids: Iterable[str] = (),
    jira_url: str | None = None,
) -> dict[str, Any]:
    """Which of the three Xray situations this site is in, and what follows from it.

    The Jira URL decides the framing — an `*.atlassian.net` host is Atlassian
    Cloud, where only the Xray Cloud product can exist; any other host is
    self-hosted, where the Xray Server/DC plugin would answer on `/rest/raven`
    with Jira's own credential. Within that framing, the same questions are
    asked in order: are Xray Cloud keys present, does the Jira host answer on
    `/rest/raven`, are test-shape custom fields declared.

    Deliberately returns a report rather than raising or silently picking: "no
    Xray" is not an error — it is the most common answer, and the graph is
    perfectly buildable without it. What matters is that the operator is told
    which path ran, because the same command produces a very different graph
    in each case and a thin graph with no explanation looks like a bug.
    """
    url = jira_url or env.get("JIRA_URL") or env.get("JIRA_BASE_URL") or ""
    hosting = _hosting_of(url)

    if env.get("XRAY_CLIENT_ID") and env.get("XRAY_CLIENT_SECRET"):
        return {
            "tier": "cloud",
            "hosting": hosting,
            "reason": "XRAY_CLIENT_ID and XRAY_CLIENT_SECRET are set",
            "graphql": True,
            "note": "test steps, preconditions, plans, executions and runs come from Xray",
        }

    if jira_probe is not None:
        try:
            jira_probe.raw_get(f"{SERVER_API}/settings/teststatuses")
        except Exception:  # noqa: BLE001 - any failure here simply means "not this tier"
            pass
        else:
            return {
                "tier": "server",
                "hosting": hosting,
                "reason": "the Jira host answers on /rest/raven",
                "graphql": False,
                "note": "Xray Server/DC: steps and runs are read per issue",
            }

    declared = sorted({str(f) for f in field_ids if str(f).strip()})
    if declared:
        return {
            "tier": "fields",
            "hosting": hosting,
            "reason": f"declared test-shape custom fields: {', '.join(declared)}",
            "graphql": False,
            "note": (
                "test type, steps, preconditions and results are read from the "
                "declared sync.test_detail_fields custom fields on the issues; "
                "membership and runs come from issue links and labels"
            ),
        }

    if hosting == "cloud":
        note = (
            "Jira Cloud site: either the Xray Cloud app is not installed (or its "
            "keys are not set), or the project models tests as plain Jira issue "
            "types — in that case the graph is built from issue links and the "
            "declared test_detail_fields"
        )
    else:
        note = (
            "self-hosted Jira: no Xray credentials, no /rest/raven, no Xray custom "
            "fields — tests, preconditions and plans are ordinary issue types and "
            "the graph is built from issue links and the declared test_detail_fields"
        )
    return {
        "tier": "none",
        "hosting": hosting,
        "reason": "no Xray credentials, no /rest/raven, no Xray custom fields",
        "graphql": False,
        "note": note,
    }
