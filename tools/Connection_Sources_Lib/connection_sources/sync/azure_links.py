"""Azure DevOps work item links, read for the sync half.

The sync read (`AzureDevOpsClient.get_batch`) names its fields, and Azure refuses
`fields` together with `$expand`, so relations never arrive with it. This makes
the second, relation-expanded read for the same ids and folds the links into the
named relation fields state.json stores, via `normalize.azure_relations`.
"""

from __future__ import annotations

from typing import Any

from .. import operations
from ..env import load_project_env
from ..errors import ConnectionSourceError
from ..models import AlmRecord, SourceConfig
from .normalize import azure_relations

__all__ = ["azure_relations_for"]


def azure_relations_for(
    project: Any,
    source: SourceConfig,
    *,
    records: list[AlmRecord],
    timeout: float,
) -> tuple[dict[str, dict[str, list[dict[str, str]]]], dict[str, Any] | None]:
    """This source's work item links, keyed by work item id.

    Returns `({}, None)` when there is nothing to read, and `({}, error)` when
    Azure refused the read, so one failed pass is recorded and never fatal.
    """
    known = {record.id: record for record in records if record.id}
    if not known:
        return {}, None
    env = load_project_env(project)
    try:
        with operations.open_client(source, env, timeout=timeout) as client:
            items = client.graph_batch(list(known))
    except ConnectionSourceError as exc:
        return {}, {"stage": "azure_relations", "source": source.name, **exc.to_dict()}
    return azure_relations(items, known=known), None
