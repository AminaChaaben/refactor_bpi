"""GitLab over the REST v4 API.

The REST API authenticates with a personal, project or group access token. An SSH
key authenticates the git transport only (clone, fetch, push) and cannot read
issues, pipelines or merge requests.
"""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import quote

import httpx

from ..errors import SourcesConfigError
from ..models import AlmRecord, Identity
from .base import MAX_PAGE, AlmClient, ClientSpec, SuspectCredential

API = "/api/v4"

TOKEN_PREFIXES = ("glpat-", "glptt-", "gldt-", "glsoat-", "glrt-")

# state_event names the verb, not the resulting state.
_STATE_EVENTS = {
    "close": "close",
    "closed": "close",
    "reopen": "reopen",
    "reopened": "reopen",
    "open": "reopen",
    "opened": "reopen",
}


class GitLabClient(AlmClient):
    spec = ClientSpec(
        name="gitlab",
        required_env=("GITLAB_URL", "GITLAB_TOKEN"),
        scope_keys=("project_id",),
        token_hint=(
            "create a token at <gitlab>/-/user_settings/personal_access_tokens with "
            "scope 'api' (or 'read_api' for read-only); an SSH key cannot "
            "authenticate the REST API"
        ),
    )

    @classmethod
    def _build(
        cls,
        env: Mapping[str, str],
        *,
        timeout: float,
        transport: httpx.BaseTransport | None,
    ) -> "GitLabClient":
        return cls(
            base_url=env["GITLAB_URL"],
            auth=env["GITLAB_TOKEN"],
            auth_header="PRIVATE-TOKEN",
            env=env,
            timeout=timeout,
            transport=transport,
        )

    @classmethod
    def suspect_credentials(cls, env: Mapping[str, str]) -> list[SuspectCredential]:
        token = env.get("GITLAB_TOKEN", "")
        if not token or token.startswith(TOKEN_PREFIXES):
            return []
        if token.startswith("ssh-") or "PRIVATE KEY" in token:
            message = "GITLAB_TOKEN holds an SSH key, which the REST API cannot use"
        else:
            message = "GITLAB_TOKEN does not look like a GitLab access token"
        return [
            SuspectCredential(
                key="GITLAB_TOKEN", message=message, remediation=cls.spec.token_hint
            )
        ]

    def ping(self) -> Identity:
        me = self.request("GET", f"{API}/user")
        return Identity(
            system="gitlab",
            account=me.get("username") or me.get("name") or "unknown",
            base_url=self.base_url,
        )

    def search(self, scope: Mapping[str, Any], *, limit: int = MAX_PAGE) -> list[AlmRecord]:
        project = _project(scope)
        issues = self.request(
            "GET",
            f"{API}/projects/{project}/issues",
            params={"per_page": min(limit, MAX_PAGE), "state": scope.get("state", "opened")},
        )
        return [self._record(issue) for issue in issues[:limit]]

    def pipelines(self, scope: Mapping[str, Any], *, limit: int = 20) -> list[dict[str, Any]]:
        project = _project(scope)
        return self.request(
            "GET",
            f"{API}/projects/{project}/pipelines",
            params={"per_page": min(limit, MAX_PAGE)},
        )

    def run_pipeline(
        self,
        scope: Mapping[str, Any],
        *,
        ref: str,
        variables: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start a pipeline for `ref`. `variables` become top-level CI variables."""
        payload: dict[str, Any] = {"ref": ref}
        if variables:
            payload["variables"] = [
                {"key": str(key), "value": str(value)} for key, value in variables.items()
            ]
        return self.request("POST", f"{API}/projects/{_project(scope)}/pipeline", json=payload)

    def pipeline(self, scope: Mapping[str, Any], pipeline_id: int) -> dict[str, Any]:
        return self.request(
            "GET", f"{API}/projects/{_project(scope)}/pipelines/{pipeline_id}"
        )

    def pipeline_jobs(
        self,
        scope: Mapping[str, Any],
        pipeline_id: int,
        *,
        include_retried: bool = True,
    ) -> list[dict[str, Any]]:
        return self.request(
            "GET",
            f"{API}/projects/{_project(scope)}/pipelines/{pipeline_id}/jobs",
            params={"include_retried": "true" if include_retried else "false"},
        )

    def retry_pipeline(self, scope: Mapping[str, Any], pipeline_id: int) -> dict[str, Any]:
        return self.request(
            "POST", f"{API}/projects/{_project(scope)}/pipelines/{pipeline_id}/retry"
        )

    def cancel_pipeline(self, scope: Mapping[str, Any], pipeline_id: int) -> dict[str, Any]:
        return self.request(
            "POST", f"{API}/projects/{_project(scope)}/pipelines/{pipeline_id}/cancel"
        )

    def job(self, scope: Mapping[str, Any], job_id: int) -> dict[str, Any]:
        return self.request("GET", f"{API}/projects/{_project(scope)}/jobs/{job_id}")

    def retry_job(self, scope: Mapping[str, Any], job_id: int) -> dict[str, Any]:
        return self.request("POST", f"{API}/projects/{_project(scope)}/jobs/{job_id}/retry")

    def cancel_job(self, scope: Mapping[str, Any], job_id: int) -> dict[str, Any]:
        return self.request("POST", f"{API}/projects/{_project(scope)}/jobs/{job_id}/cancel")

    def job_trace(self, scope: Mapping[str, Any], job_id: int) -> str:
        """The raw job log. The trace endpoint redirects to object storage, which
        is the one place this client follows a redirect."""
        response = self.request(
            "GET",
            f"{API}/projects/{_project(scope)}/jobs/{job_id}/trace",
            raw=True,
            follow_redirects=True,
        )
        return response.text

    def job_artifacts(self, scope: Mapping[str, Any], job_id: int) -> bytes:
        """The job's artifact archive (a zip), or the typed 404 if it has none."""
        response = self.request(
            "GET",
            f"{API}/projects/{_project(scope)}/jobs/{job_id}/artifacts",
            raw=True,
            follow_redirects=True,
        )
        return response.content

    def pipeline_artifacts(self, scope: Mapping[str, Any], pipeline_id: int) -> bytes:
        """One archive holding every artifact produced by the pipeline's jobs."""
        response = self.request(
            "GET",
            f"{API}/projects/{_project(scope)}/pipelines/{pipeline_id}/artifacts",
            raw=True,
            follow_redirects=True,
        )
        return response.content

    def merge_requests(
        self, scope: Mapping[str, Any], *, limit: int = 50, state: str = "all"
    ) -> list[dict[str, Any]]:
        """Merge requests newest-updated first, whatever their state.

        `state="all"` rather than the API's `opened` default because a merge request
        that shipped is the one worth knowing about when tracing a requirement to the
        change that delivered it — restricting to open ones would show only the work
        that has not happened yet.
        """
        project = _project(scope)
        return self.request(
            "GET",
            f"{API}/projects/{project}/merge_requests",
            params={
                "per_page": min(limit, MAX_PAGE),
                "state": state,
                "order_by": "updated_at",
                "sort": "desc",
            },
        )

    def project(self, scope: Mapping[str, Any]) -> dict[str, Any]:
        return self.request("GET", f"{API}/projects/{_project(scope)}")

    def get(self, ident: str) -> AlmRecord:
        return self.get_issue(self._scoped_project(), ident)

    def get_issue(self, project_id: str | int, iid: str | int) -> AlmRecord:
        issue = self.request("GET", f"{API}/projects/{_encode(project_id)}/issues/{iid}")
        return self._record(issue)

    def create(self, scope: Mapping[str, Any], **fields: Any) -> AlmRecord:
        project = _project(scope)
        title = fields.pop("title", None) or fields.pop("summary", None)
        if not title:
            raise SourcesConfigError(
                "creating a GitLab issue needs a title",
                server="gitlab",
                remediation="pass title='...'",
            )
        payload = {"title": title}
        if (description := fields.pop("description", None)) is not None:
            payload["description"] = description
        if (labels := fields.pop("labels", None)) is not None:
            payload["labels"] = labels if isinstance(labels, str) else ",".join(labels)
        payload.update(fields)

        created = self.request("POST", f"{API}/projects/{project}/issues", json=payload)
        return self._record(created)

    def _scoped_project(self, explicit: Any = None) -> Any:
        """An iid is only unique inside its project, so every write needs one.

        An explicit project_id wins; otherwise fall back to the configured scope.
        """
        if explicit is not None:
            return explicit
        project = (self.scope or {}).get("project_id")
        if project is None:
            raise SourcesConfigError(
                "gitlab: no project_id — pass one, or set it on the gitlab source",
                server="gitlab",
                remediation="pass project_id=..., or add project_id to the source scope",
            )
        return project

    def update(self, ident: str, **fields: Any) -> AlmRecord:
        project = self._scoped_project(fields.pop("project_id", None))
        if (labels := fields.pop("labels", None)) is not None:
            fields["labels"] = labels if isinstance(labels, str) else ",".join(labels)
        if not fields:
            raise SourcesConfigError(
                "update needs at least one field",
                server="gitlab",
                remediation="pass title=..., description=... or state_event=...",
            )
        updated = self.request(
            "PUT", f"{API}/projects/{_encode(project)}/issues/{ident}", json=fields
        )
        return self._record(updated)

    def transition(self, ident: str, status: str) -> AlmRecord:
        """An issue is opened or closed; there are no named statuses to move between."""
        wanted = status.strip().lower()
        event = _STATE_EVENTS.get(wanted)
        if event is None:
            raise SourcesConfigError(
                f"gitlab: no such state {status!r}; an issue is opened or closed",
                server="gitlab",
                remediation=f"use one of: {', '.join(sorted(_STATE_EVENTS))}",
            )
        project = self._scoped_project()
        updated = self.request(
            "PUT",
            f"{API}/projects/{_encode(project)}/issues/{ident}",
            json={"state_event": event},
        )
        return self._record(updated)

    def delete(self, ident: str, *, permanent: bool = False) -> dict[str, Any]:
        """GitLab has no recycle bin: `permanent` is accepted for signature parity
        and changes nothing."""
        return self.delete_issue(self._scoped_project(), ident)

    def delete_issue(self, project_id: str | int, iid: str | int) -> dict[str, Any]:
        record = self.get_issue(project_id, iid)
        self.request("DELETE", f"{API}/projects/{_encode(project_id)}/issues/{iid}")
        return {
            "system": "gitlab",
            "id": record.id,
            "key": record.key,
            "title": record.title,
            "deleted": True,
            "recoverable": False,
        }

    def milestones(self, project_id: str | int, *, state: str | None = None) -> list[dict[str, Any]]:
        """GitLab has no sprint object — milestones are its equivalent: a due date
        and a bucket of issues, opened or closed, no separate 'active' state."""
        params = {"state": state} if state else {}
        return self.request(
            "GET", f"{API}/projects/{_encode(project_id)}/milestones", params=params
        )

    def milestone(self, project_id: str | int, milestone_id: str | int) -> dict[str, Any]:
        return self.request(
            "GET", f"{API}/projects/{_encode(project_id)}/milestones/{milestone_id}"
        )

    def create_milestone(
        self,
        project_id: str | int,
        title: str,
        *,
        description: str | None = None,
        start_date: str | None = None,
        due_date: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"title": title}
        if description is not None:
            payload["description"] = description
        if start_date is not None:
            payload["start_date"] = start_date
        if due_date is not None:
            payload["due_date"] = due_date
        return self.request(
            "POST", f"{API}/projects/{_encode(project_id)}/milestones", json=payload
        )

    def update_milestone(
        self, project_id: str | int, milestone_id: str | int, **fields: Any
    ) -> dict[str, Any]:
        """Closing or reactivating is state_event='close'|'activate' here too."""
        if not fields:
            raise SourcesConfigError(
                "update_milestone needs at least one field",
                server="gitlab",
                remediation="pass title=..., due_date=... or state_event='close'|'activate'",
            )
        return self.request(
            "PUT",
            f"{API}/projects/{_encode(project_id)}/milestones/{milestone_id}",
            json=dict(fields),
        )

    def delete_milestone(self, project_id: str | int, milestone_id: str | int) -> None:
        self.request(
            "DELETE", f"{API}/projects/{_encode(project_id)}/milestones/{milestone_id}"
        )

    def milestone_issues(self, project_id: str | int, milestone_id: str | int) -> list[AlmRecord]:
        issues = self.request(
            "GET",
            f"{API}/projects/{_encode(project_id)}/milestones/{milestone_id}/issues",
        )
        return [self._record(i) for i in issues]

    def move_to_milestone(
        self, project_id: str | int, iid: str | int, milestone_id: str | int
    ) -> AlmRecord:
        updated = self.request(
            "PUT",
            f"{API}/projects/{_encode(project_id)}/issues/{iid}",
            json={"milestone_id": int(milestone_id)},
        )
        return self._record(updated)

    def remove_from_milestone(self, project_id: str | int, iid: str | int) -> AlmRecord:
        updated = self.request(
            "PUT",
            f"{API}/projects/{_encode(project_id)}/issues/{iid}",
            json={"milestone_id": None},
        )
        return self._record(updated)

    def comment(self, ident: str, text: str, *, project_id: str | int | None = None) -> str:
        """Post a note on an issue. GitLab has no separate 'comment' object — a note
        is the same thing an issue's own `_links.notes` URL points at."""
        project = self._scoped_project(project_id)
        body = self.request(
            "POST",
            f"{API}/projects/{_encode(project)}/issues/{ident}/notes",
            json={"body": text},
        )
        return str(body.get("id", ""))

    def _record(self, issue: Mapping[str, Any]) -> AlmRecord:
        assignee = issue.get("assignee") or {}
        return AlmRecord(
            system="gitlab",
            id=str(issue.get("id", "")),
            key=f"#{issue.get('iid', '')}",
            title=issue.get("title") or "",
            status=issue.get("state") or "",
            type=issue.get("type") or "ISSUE",
            url=issue.get("web_url") or "",
            assignee=assignee.get("name") if isinstance(assignee, dict) else None,
            tags=list(issue.get("labels") or []),
            raw=dict(issue),
        )


def _project(scope: Mapping[str, Any]) -> str:
    project = scope.get("project_id") or scope.get("project")
    if not project:
        raise SourcesConfigError(
            "gitlab source has no 'project_id' scope",
            server="gitlab",
            remediation="add 'project_id' (numeric id or group/path) to the source",
        )
    return _encode(project)


def _encode(project: str | int) -> str:
    return quote(str(project), safe="")
