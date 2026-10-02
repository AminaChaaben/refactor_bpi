"""Jira Cloud over the REST v3 API."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

import httpx

from ..errors import ApiError, SourcesConfigError
from ..models import AlmRecord, Identity
from .base import MAX_PAGE, AlmClient, ClientSpec, basic_auth

API = "/rest/api/3"
AGILE_API = "/rest/agile/1.0"

_FIELDS = (
    "summary",
    "status",
    "issuetype",
    "assignee",
    "priority",
    "created",
    "updated",
    "labels",
    "parent",
    "subtasks",
)

# What a graph build asks for on top of `_FIELDS`, passed through the same
# `extra_fields` channel a caller uses for custom field ids.
#
# It stays separate rather than being folded into `_FIELDS` because the two reads
# want opposite things. Change detection runs every five minutes and compares a
# dozen scalars, so every field it does not use is payload it pays for on every
# cycle forever. A graph build runs when someone asks for it and wants each edge
# the tracker will admit to — issuelinks, components, versions, the reporter, the
# resolution. Naming them here keeps that cost on the caller who wanted it, and
# keeps the sync path byte-for-byte what it was.
GRAPH_FIELDS: tuple[str, ...] = (
    "project",
    "reporter",
    "creator",
    "issuelinks",
    "components",
    "fixVersions",
    "versions",
    "resolution",
    "resolutiondate",
    "duedate",
    "statuscategorychangedate",
    "description",
    "environment",
    "timetracking",
    "votes",
    "watches",
)

# Asked for only when the graph config turns them on. Each is a list that can run
# to hundreds of entries on a busy issue, and Jira returns them inline in the
# search response, so a project that does not want comments in its graph should
# not be made to download them.
GRAPH_OPTIONAL_FIELDS: dict[str, str] = {
    "comments": "comment",
    "attachments": "attachment",
    "worklogs": "worklog",
}


def _with_extra_fields(extra_fields: Iterable[str] | None) -> list[str]:
    """The fixed field set, plus caller-named extras (e.g. instance-specific
    Xray-simulating custom fields like customfield_12345) for this call only.

    Custom field ids are per-instance, so they are never baked into _FIELDS —
    a caller who knows the id asks for it explicitly, same as --raw already
    surfaces the untouched payload without the client hardcoding its shape.
    """
    if not extra_fields:
        return list(_FIELDS)
    seen = list(_FIELDS)
    for field in extra_fields:
        if field and field not in seen:
            seen.append(field)
    return seen


def to_adf(text: str) -> dict[str, Any]:
    """Wrap plain text as an Atlassian Document Format paragraph, as v3 requires."""
    paragraphs = [line for line in str(text).split("\n")]
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": line}] if line else [],
            }
            for line in paragraphs
        ],
    }


class JiraClient(AlmClient):
    spec = ClientSpec(
        name="jira",
        required_env=("JIRA_URL", "JIRA_USERNAME", "JIRA_API_TOKEN"),
        optional_env=("JIRA_PROJECTS_FILTER",),
        scope_keys=("jql",),
        token_hint=(
            "create an API token at id.atlassian.com/manage-profile/security/api-tokens "
            "and set JIRA_USERNAME to the account email"
        ),
    )

    @classmethod
    def _build(
        cls,
        env: Mapping[str, str],
        *,
        timeout: float,
        transport: httpx.BaseTransport | None,
    ) -> "JiraClient":
        return cls(
            base_url=env["JIRA_URL"],
            auth=basic_auth(env["JIRA_USERNAME"], env["JIRA_API_TOKEN"]),
            env=env,
            timeout=timeout,
            transport=transport,
        )

    def ping(self) -> Identity:
        me = self.request("GET", f"{API}/myself")
        return Identity(
            system="jira",
            account=me.get("emailAddress") or me.get("displayName") or "unknown",
            base_url=self.base_url,
        )

    def search(
        self,
        scope: Mapping[str, Any],
        *,
        limit: int = MAX_PAGE,
        extra_fields: Iterable[str] | None = None,
    ) -> list[AlmRecord]:
        jql = scope.get("jql")
        if not jql:
            raise SourcesConfigError(
                "jira source has no 'jql' scope",
                server="jira",
                remediation="add a 'jql' key to the jira source in sources.json",
            )
        return list(self.iter_search(jql, limit=limit, extra_fields=extra_fields))

    def iter_search(
        self,
        jql: str,
        *,
        limit: int = MAX_PAGE,
        extra_fields: Iterable[str] | None = None,
    ) -> Iterable[AlmRecord]:
        """Page through a JQL result set, stopping at limit."""
        fields = _with_extra_fields(extra_fields)
        token: str | None = None
        seen = 0
        while seen < limit:
            payload: dict[str, Any] = {
                "jql": jql,
                "maxResults": min(MAX_PAGE, limit - seen),
                "fields": fields,
            }
            if token:
                payload["nextPageToken"] = token

            body = self.request("POST", f"{API}/search/jql", json=payload)
            issues = body.get("issues") or []
            for issue in issues:
                yield self._record(issue)
                seen += 1
                if seen >= limit:
                    return

            token = body.get("nextPageToken")
            if not token or not issues:
                return

    def get(self, ident: str, *, extra_fields: Iterable[str] | None = None) -> AlmRecord:
        issue = self.request(
            "GET",
            f"{API}/issue/{ident}",
            params={"fields": ",".join(_with_extra_fields(extra_fields))},
        )
        return self._record(issue)

    def create(self, scope: Mapping[str, Any], **fields: Any) -> AlmRecord:
        project = fields.pop("project", None) or scope.get("project_key")
        if not project:
            raise SourcesConfigError(
                "creating a Jira issue needs a project key",
                server="jira",
                remediation="pass project=KEY, or add project_key to the jira source",
            )
        summary = fields.pop("summary", None)
        if not summary:
            raise SourcesConfigError(
                "creating a Jira issue needs a summary",
                server="jira",
                remediation="pass summary='...'",
            )

        payload: dict[str, Any] = {
            "project": {"key": project},
            "summary": summary,
            "issuetype": {"name": fields.pop("issuetype", None) or "Task"},
        }
        description = fields.pop("description", None)
        if description is not None:
            payload["description"] = to_adf(description)
        if (assignee := fields.pop("assignee", None)) is not None:
            payload["assignee"] = {"accountId": assignee}
        payload.update(fields)

        created = self.request("POST", f"{API}/issue", json={"fields": payload})
        return self.get(created["key"])

    def update(self, ident: str, **fields: Any) -> AlmRecord:
        if not fields:
            raise SourcesConfigError(
                "update needs at least one field",
                server="jira",
                remediation="pass summary=..., description=... or any field name",
            )
        payload = dict(fields)
        if "description" in payload:
            payload["description"] = to_adf(payload["description"])
        if "issuetype" in payload:
            payload["issuetype"] = {"name": payload["issuetype"]}
        if "assignee" in payload:
            payload["assignee"] = {"accountId": payload["assignee"]}

        self.request("PUT", f"{API}/issue/{ident}", json={"fields": payload})
        return self.get(ident)

    def transitions(self, ident: str) -> dict[str, str]:
        body = self.request("GET", f"{API}/issue/{ident}/transitions")
        return {t["name"]: t["id"] for t in body.get("transitions", [])}

    def transition(self, ident: str, status: str) -> AlmRecord:
        available = self.transitions(ident)
        match = next(
            (tid for name, tid in available.items() if name.lower() == status.lower()),
            None,
        )
        if match is None:
            raise ApiError(
                f"jira: {ident} has no transition to {status!r}",
                server="jira",
                remediation=f"available from here: {', '.join(available) or 'none'}",
            )
        self.request(
            "POST", f"{API}/issue/{ident}/transitions", json={"transition": {"id": match}}
        )
        return self.get(ident)

    def delete(self, ident: str, *, permanent: bool = False) -> dict[str, Any]:
        """Delete an issue. Jira has no recycle bin, so this is always permanent."""
        record = self.get(ident)
        self.request(
            "DELETE", f"{API}/issue/{ident}", params={"deleteSubtasks": "true"}
        )
        return {
            "system": "jira",
            "id": record.id,
            "key": record.key,
            "title": record.title,
            "deleted": True,
            "recoverable": False,
        }

    def boards(self, project_key: str) -> list[dict[str, Any]]:
        """Agile boards for this project. A sprint always belongs to a board."""
        body = self.request(
            "GET", f"{AGILE_API}/board", params={"projectKeyOrId": project_key}
        )
        return list(body.get("values", []))

    def sprints(self, board_id: str | int) -> list[dict[str, Any]]:
        """This board's sprints, each carrying its own state: future, active, closed."""
        body = self.request("GET", f"{AGILE_API}/board/{board_id}/sprint")
        return list(body.get("values", []))

    def create_sprint(
        self,
        board_id: str | int,
        name: str,
        *,
        start: str | None = None,
        end: str | None = None,
        goal: str | None = None,
    ) -> dict[str, Any]:
        """A new sprint on this board, in state 'future' until started."""
        payload: dict[str, Any] = {"originBoardId": board_id, "name": name}
        if start is not None:
            payload["startDate"] = start
        if end is not None:
            payload["endDate"] = end
        if goal is not None:
            payload["goal"] = goal
        return self.request("POST", f"{AGILE_API}/sprint", json=payload)

    def start_sprint(
        self,
        sprint_id: str | int,
        *,
        start: str,
        end: str,
        goal: str | None = None,
    ) -> dict[str, Any]:
        """Move a sprint from 'future' to 'active'. Needs both dates; Jira refuses
        without them. There is no separate activation call — this is a state
        change on the sprint resource itself."""
        payload: dict[str, Any] = {"state": "active", "startDate": start, "endDate": end}
        if goal is not None:
            payload["goal"] = goal
        return self.request("POST", f"{AGILE_API}/sprint/{sprint_id}", json=payload)

    def complete_sprint(self, sprint_id: str | int) -> dict[str, Any]:
        """Close an active sprint. Jira does not support reopening a closed sprint."""
        return self.request(
            "POST", f"{AGILE_API}/sprint/{sprint_id}", json={"state": "closed"}
        )

    def sprint(self, sprint_id: str | int) -> dict[str, Any]:
        return self.request("GET", f"{AGILE_API}/sprint/{sprint_id}")

    def update_sprint(self, sprint_id: str | int, **fields: Any) -> dict[str, Any]:
        """Rename a sprint or change its goal/dates without touching its state —
        the same resource start_sprint/complete_sprint act on, minus the state field."""
        if not fields:
            raise SourcesConfigError(
                "update_sprint needs at least one field",
                server="jira",
                remediation="pass name=..., goal=..., startDate=... or endDate=...",
            )
        return self.request("POST", f"{AGILE_API}/sprint/{sprint_id}", json=dict(fields))

    def delete_sprint(self, sprint_id: str | int) -> None:
        """Only a sprint that has never been started (still 'future') can be deleted."""
        self.request("DELETE", f"{AGILE_API}/sprint/{sprint_id}")

    def sprint_issues(self, sprint_id: str | int) -> list[AlmRecord]:
        body = self.request(
            "GET", f"{AGILE_API}/sprint/{sprint_id}/issue", params={"fields": ",".join(_FIELDS)}
        )
        return [self._record(issue) for issue in body.get("issues", [])]

    def backlog_issues(self, board_id: str | int) -> list[AlmRecord]:
        body = self.request(
            "GET", f"{AGILE_API}/board/{board_id}/backlog", params={"fields": ",".join(_FIELDS)}
        )
        return [self._record(issue) for issue in body.get("issues", [])]

    def board_issues(self, board_id: str | int) -> list[AlmRecord]:
        """Every issue on the board, sprints and backlog alike."""
        body = self.request(
            "GET", f"{AGILE_API}/board/{board_id}/issue", params={"fields": ",".join(_FIELDS)}
        )
        return [self._record(issue) for issue in body.get("issues", [])]

    def move_to_sprint(self, sprint_id: str | int, issue_keys: list[str]) -> None:
        """File one or more issues into a sprint, out of the backlog or another sprint."""
        self.request(
            "POST", f"{AGILE_API}/sprint/{sprint_id}/issue", json={"issues": issue_keys}
        )

    def move_to_backlog(self, issue_keys: list[str]) -> None:
        """Pull one or more issues out of whatever sprint they were in."""
        self.request("POST", f"{AGILE_API}/backlog/issue", json={"issues": issue_keys})

    def comment(self, ident: str, text: str) -> str:
        body = self.request(
            "POST", f"{API}/issue/{ident}/comment", json={"body": to_adf(text)}
        )
        return str(body.get("id", ""))

    def link_issues(
        self, inward_key: str, outward_key: str, link_type: str = "Test"
    ) -> None:
        """Create one issue link: `outward` carries the outward sense of the type.

        For the "Test" link type, outward is the test and inward is the thing it
        tests — a story, a test set, a plan or an execution. The graph reads both
        directions from either record, so the two sides only matter for the link
        type's wording, not for what ends up in Neo4j.
        """
        self.request(
            "POST",
            f"{API}/issueLink",
            json={
                "type": {"name": link_type},
                "inwardIssue": {"key": inward_key},
                "outwardIssue": {"key": outward_key},
            },
        )

    def issue_links(self, ident: str) -> list[dict[str, Any]]:
        """Every issue link of one issue, as Jira reports them.

        Each entry carries `id`, the `type` (with its inward/outward wording) and
        exactly one of `inwardIssue`/`outwardIssue` naming the other side, per the
        perspective Jira renders from the issue that was asked.
        """
        record = self.get(ident, extra_fields=("issuelinks",))
        fields = (record.raw or {}).get("fields") or {}
        return list(fields.get("issuelinks") or ())

    def delete_issue_link(self, link_id: str | int) -> None:
        """Remove one issue link by its id, breaking the relationship both ways."""
        self.request("DELETE", f"{API}/issueLink/{link_id}")

    def changelog(self, ident: str, *, limit: int = 200) -> list[dict[str, Any]]:
        """This issue's field-change history, oldest page first.

        Read from the dedicated `/changelog` resource rather than `expand=changelog`
        on the issue: the expand form silently truncates at 100 entries with no
        marker, so an issue with a long history would quietly lose its early
        transitions — precisely the ones a "how long did this sit in review" query
        needs. This form pages, and says when it is done.
        """
        entries: list[dict[str, Any]] = []
        start = 0
        while len(entries) < limit:
            body = self.request(
                "GET",
                f"{API}/issue/{ident}/changelog",
                params={"startAt": start, "maxResults": min(MAX_PAGE, limit - len(entries))},
            )
            values = body.get("values") or []
            entries.extend(values)
            if body.get("isLast") is True or not values:
                break
            start += len(values)
        return entries[:limit]

    def remote_links(self, ident: str) -> list[dict[str, Any]]:
        """Links out to systems Jira does not own — a Confluence page, a build, a PR."""
        body = self.request("GET", f"{API}/issue/{ident}/remotelink")
        return list(body) if isinstance(body, list) else []

    def watchers(self, ident: str) -> list[dict[str, Any]]:
        """Who is watching this issue.

        Its own call because the `watches` field carries only a count; the names
        need this resource, and it needs the browse-users permission, which not
        every token has.
        """
        body = self.request("GET", f"{API}/issue/{ident}/watchers")
        return list(body.get("watchers") or [])

    def link_types(self) -> list[dict[str, Any]]:
        """Every issue link type this site defines, with its inward/outward wording.

        Used by `graph doctor` to report link types the ontology has no mapping for,
        so a renamed or site-specific link shows up as a gap to configure rather than
        silently landing on the generic LINKED_TO.
        """
        body = self.request("GET", f"{API}/issueLinkType")
        return list(body.get("issueLinkTypes") or [])

    def _record(self, issue: Mapping[str, Any]) -> AlmRecord:
        fields = issue.get("fields") or {}
        assignee = fields.get("assignee") or {}
        return AlmRecord(
            system="jira",
            id=str(issue.get("id", "")),
            key=str(issue.get("key", "")),
            title=fields.get("summary") or "",
            status=(fields.get("status") or {}).get("name") or "",
            type=(fields.get("issuetype") or {}).get("name") or "",
            url=f"{self.base_url}/browse/{issue.get('key', '')}",
            assignee=assignee.get("displayName"),
            tags=list(fields.get("labels") or []),
            raw=dict(issue),
        )
