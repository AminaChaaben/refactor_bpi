"""Xray-native test/container membership, read for the sync half.

`sync.normalize.relations_of` sees only Jira `issuelinks`, which is the whole
membership story on a site with no real Xray. Once Cloud or Server/DC is active,
Xray holds test/set/plan/execution/precondition membership as its own association,
not an issue link, so this reads it the same way `graph.runner` does for the graph
half and folds the result into the same named relation fields `relations_of`
produces, via `normalize.xray_relations` + `normalize.merge_relations`.
"""

from __future__ import annotations

from typing import Any, Mapping

from .. import operations
from ..env import load_project_env
from ..errors import ConnectionSourceError
from ..models import AlmRecord, SourceConfig
from .normalize import xray_relations

__all__ = ["xray_relations_for"]


def _base_url_of(records: list[AlmRecord]) -> str:
    for record in records:
        if record.url and "/browse/" in record.url:
            return record.url.rsplit("/browse/", 1)[0]
    return ""


def xray_relations_for(
    project: Any,
    source: SourceConfig,
    *,
    records: list[AlmRecord],
    jql: str,
    detail_fields: Mapping[str, str],
    timeout: float,
) -> tuple[dict[str, dict[str, list[dict[str, str]]]], dict[str, Any] | None]:
    """This project's Xray-native relations, keyed by Jira key.

    Returns `({}, None)` on a plain site (tier "none"/"fields") — issuelinks are
    already the whole membership story there — and `({}, error)` if Xray answered
    but refused the read, matching how the rest of a cycle treats one source's
    failure: recorded, never fatal to the others.
    """
    from ..graph import xray as xray_mod  # deferred: sync stays importable without graph's optional deps

    env = load_project_env(project)
    base_url = _base_url_of(records)

    if env.get("XRAY_CLIENT_ID") and env.get("XRAY_CLIENT_SECRET"):
        try:
            with xray_mod.XrayCloudClient.from_env(env, timeout=timeout) as cloud:
                collections = {
                    "tests": cloud.tests(jql),
                    "preconditions": cloud.preconditions(jql),
                    "test_sets": cloud.test_sets(jql),
                    "test_plans": cloud.test_plans(jql),
                    "executions": cloud.test_executions(jql),
                }
        except ConnectionSourceError as exc:
            return {}, {"stage": "xray_relations", "source": source.name, **exc.to_dict()}
        return xray_relations(collections, base_url=base_url), None

    # No Cloud credential: the only other way to get a real Xray is the Server/DC
    # plugin on the Jira host itself, so the probe borrows the same connection the
    # Jira read already used rather than opening a second one speculatively.
    try:
        with operations.open_client(source, env, timeout=timeout) as client:
            probe = xray_mod.XrayServerVia(client)
            tier = xray_mod.detect_tier(
                env,
                jira_probe=probe,
                field_ids=list(detail_fields.values()),
                jira_url=client.base_url,
            )
            if tier["tier"] != "server":
                return {}, None

            from ..graph import xray_server
            from ..graph.ontology import load_ontology

            classified = xray_server.classify(records, load_ontology())
            if not any(classified.values()):
                return {}, None
            records_by_key = {r.key: r for r in records if r.key}
            collection = xray_server.collect(
                probe,
                records_by_key=records_by_key,
                classified=classified,
                detail_fields=detail_fields,
            )
    except ConnectionSourceError as exc:
        return {}, {"stage": "xray_relations", "source": source.name, **exc.to_dict()}

    collections = {
        "tests": collection.get("tests", []),
        "preconditions": collection.get("preconditions", []),
        "test_sets": collection.get("test_sets", []),
        "test_plans": collection.get("test_plans", []),
        "executions": collection.get("executions", []),
    }
    return xray_relations(collections, base_url=base_url), None
