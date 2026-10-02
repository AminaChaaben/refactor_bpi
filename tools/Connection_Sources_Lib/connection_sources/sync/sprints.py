"""Poll sprints as objects in their own right.

A sprint starting or finishing changes the project's status without changing any
single issue: the issues keep their fields, and only the sprint's own `state` moves
from future to active to closed. Diffing issues alone would therefore never see it,
which is why sprints get their own pass.

Only Jira is covered here. Azure DevOps calls the same idea an iteration and GitLab
calls it a milestone; both are reachable through their clients and slot in behind
the same `collect_sprints` signature when those adapters are turned on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import api
from ..clients.base import DEFAULT_TIMEOUT
from ..errors import ConnectionSourceError
from ..models import SourceConfig

__all__ = ["collect_sprints"]


def collect_sprints(
    project: str | Path,
    source: SourceConfig,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, dict[str, Any]]:
    """Every sprint on every board of this project, keyed by sprint id.

    Returns an empty mapping rather than raising when the source has no sprint
    concept or no board: a project without sprints is a normal state of affairs and
    must not fail the cycle that is also tracking its issues.
    """
    if source.server != "jira":
        return {}

    project_key = source.scope.get("project_key")
    if not project_key:
        # Sprints hang off boards, and boards are found by project key. Without one
        # there is nothing to enumerate — the issue pass still works fine.
        return {}

    sprints: dict[str, dict[str, Any]] = {}
    with api.client(project, source.name, timeout=timeout) as client:
        for board in client.boards(str(project_key)):
            board_id = board.get("id")
            if board_id is None:
                continue
            try:
                found = client.sprints(board_id)
            except ConnectionSourceError:
                # A board can exist without a sprint backlog (kanban), which answers
                # with a refusal. That is not a failure of the cycle.
                continue
            for sprint in found:
                sprint_id = sprint.get("id")
                if sprint_id is None:
                    continue
                sprints[str(sprint_id)] = dict(sprint)
    return sprints
