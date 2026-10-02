"""One full graph build, start to finish: read, extract, load, derive.

Every other module in this package is deliberately inert — a pure extractor, a
statement builder, a loader that only writes through a `Runner` it is handed. This
is the one module that actually opens a connection, decides what a build touches, and
calls the others in order. It is the `sync.runner` of this package, built the same
way: thin orchestration over pure functions, so the judgement stays testable and only
the wiring is imperative.

Three things about the shape of a build are decided here rather than anywhere else.

*A build is one version.* Every node and edge written during it is stamped with the
same integer (`int(time.time())`), and that stamp is what `--prune` compares against
afterwards to find what this run did not confirm. Using wall-clock time rather than a
counter file means two builds can never collide on the same version by accident, and
a build needs no state of its own to know what generation it is.

*Trackers are read before delivery systems.* GitLab's contribution to the graph is
the set of tracker issue keys its branches and merge requests name, and those keys
have to resolve into the namespace the tracker's own nodes were written under. So the
Jira and Azure passes run first and hand their site down; a GitLab-only build still
works and simply has nothing to attach its mentions to.

*Xray's tier is detected per source, not configured.* The same command has to produce
a full execution graph on a site with Xray Cloud, on a self-hosted site with the
Server/DC plugin, and on a site with neither — and which of those is true is a
property of the site, not a decision the operator should have to record correctly in
config for the build to be right.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from .. import operations
from ..clients.base import DEFAULT_TIMEOUT
from ..clients.jira import GRAPH_FIELDS, GRAPH_OPTIONAL_FIELDS
from ..env import load_project_env
from ..errors import ConnectionSourceError, SourcesConfigError
from ..health import load_project
from ..models import AlmRecord, SourceConfig
from ..sync.runner import load_detail_fields, load_sync_config
from ..sync.store import now_iso
from ..sync.taxonomy import load_buckets
from . import derive as derive_mod
from . import extract_azure, extract_confluence, extract_gitlab, extract_jira, extract_xray
from . import loader as loader_mod
from . import plain as plain_mod
from . import xray as xray_mod
from . import xray_server
from .config import GraphConfig, load_graph_config
from .model import GraphBatch, Namespace, site_of

__all__ = ["BuildResult", "build_batch", "run_build"]

# Which servers contribute the requirement and test halves of the graph, and which
# contribute delivery context. The order matters — see the module docstring.
TRACKER_SERVERS = ("jira", "azuredevops")
DELIVERY_SERVERS = ("gitlab",)
# Written context: the wiki. Read after the trackers for the same reason delivery is
# -- both resolve issue keys they merely *mention* into the tracker's own namespace,
# and doing that before the tracker has been read would leave the graph joined to
# stubs that a later pass has to reconcile.
CONTEXT_SERVERS = ("confluence",)
BUILDABLE = TRACKER_SERVERS + DELIVERY_SERVERS + CONTEXT_SERVERS


@dataclass
class BuildResult:
    """What one build read, wrote and computed — the whole story of a run."""

    project: str
    started_at: str
    version: int
    finished_at: str = ""
    dry_run: bool = False
    sources: list[str] = field(default_factory=list)
    sites: list[str] = field(default_factory=list)
    namespaces: list[str] = field(default_factory=list)
    issues_read: int = 0
    xray_tier: dict[str, Any] = field(default_factory=dict)
    read: dict[str, Any] = field(default_factory=dict)
    counts: dict[str, Any] = field(default_factory=dict)
    load: dict[str, Any] = field(default_factory=dict)
    pruned_nodes: int = 0
    pruned_relationships: int = 0
    derivations: dict[str, int] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "version": self.version,
            "dry_run": self.dry_run,
            "sources": self.sources,
            "sites": self.sites,
            "namespaces": self.namespaces,
            "issues_read": self.issues_read,
            "xray_tier": self.xray_tier,
            "read": self.read,
            "counts": self.counts,
            "load": self.load,
            "pruned_nodes": self.pruned_nodes,
            "pruned_relationships": self.pruned_relationships,
            "derivations": self.derivations,
            "errors": self.errors,
        }


def _extra_fields(
    config: GraphConfig, sprint_field: str | None, detail_fields: Mapping[str, str]
) -> list[str]:
    fields = list(GRAPH_FIELDS)
    for name in config.include:
        field_id = GRAPH_OPTIONAL_FIELDS.get(name)
        if field_id and field_id not in fields:
            fields.append(field_id)
    if sprint_field and sprint_field not in fields:
        fields.append(sprint_field)
    for field_id in detail_fields.values():
        if field_id not in fields:
            fields.append(field_id)
    return fields


def _project_key_of(records: Iterable[AlmRecord], scope: Mapping[str, Any]) -> str | None:
    declared = scope.get("project_key")
    if declared:
        return str(declared)
    for record in records:
        raw = record.raw if isinstance(record.raw, Mapping) else {}
        fields = raw.get("fields") if isinstance(raw.get("fields"), Mapping) else {}
        project = fields.get("project")
        if isinstance(project, Mapping) and project.get("key"):
            return str(project["key"])
    return None


def _azure_project_of(records: Iterable[AlmRecord]) -> str | None:
    for record in records:
        raw = record.raw if isinstance(record.raw, Mapping) else {}
        fields = raw.get("fields") if isinstance(raw.get("fields"), Mapping) else {}
        project = fields.get("System.TeamProject")
        if project:
            return str(project)
    return None


def _guarded(report: dict[str, Any], stage: str, call: Any, default: Any, **context: Any) -> Any:
    """One read whose failure is recorded against the build rather than ending it.

    A build reads a dozen different resources across three systems, and any one of
    them can be refused on its own — a token without the test-plan scope, a project
    with pipelines disabled. Losing the whole graph because one optional collection
    was unreadable would be the wrong trade every time: a graph missing its pipelines
    still answers every coverage question, and the error says exactly what is absent.
    """
    try:
        return call()
    except ConnectionSourceError as exc:
        report["errors"].append({"stage": stage, **context, **exc.to_dict()})
        return default


def build_batch(
    project: str | Path,
    *,
    source: SourceConfig,
    env: Mapping[str, str],
    config: GraphConfig,
    limit: int,
    timeout: float,
    jql: str | None = None,
    tracker_system: str = "",
    tracker_site: str = "",
    platform_project_id: str | None = None,
) -> tuple[GraphBatch, dict[str, Any]]:
    """Everything one source contributes, plus a report of what it managed to read.

    The report matters as much as the batch: a build is meaningless without knowing
    how much of the configured scope it actually saw, and a thin graph with no
    explanation is indistinguishable from a broken one.
    """
    if source.server not in BUILDABLE:
        raise SourcesConfigError(
            f"graph build cannot read a {source.server!r} source",
            source=source.name,
            remediation="the graph is built from " + ", ".join(BUILDABLE),
        )
    report: dict[str, Any] = {"issues_read": 0, "errors": [], "xray_tier": {}, "read": {}}

    if source.server == "jira":
        return _build_jira(
            project, source=source, env=env, config=config, limit=limit,
            timeout=timeout, jql=jql, report=report,
            platform_project_id=platform_project_id,
        )
    if source.server == "azuredevops":
        return _build_azure(
            source=source, env=env, config=config, limit=limit, timeout=timeout,
            report=report, platform_project_id=platform_project_id,
        )
    if source.server == "confluence":
        return _build_confluence(
            source=source, env=env, config=config, limit=limit, timeout=timeout,
            report=report, tracker_system=tracker_system, tracker_site=tracker_site,
            platform_project_id=platform_project_id,
        )
    return _build_gitlab(
        source=source, env=env, config=config, limit=limit, timeout=timeout,
        report=report, tracker_system=tracker_system, tracker_site=tracker_site,
        platform_project_id=platform_project_id,
    )


# -- Jira, and whichever Xray this site has --------------------------------


def _build_jira(
    project: str | Path,
    *,
    source: SourceConfig,
    env: Mapping[str, str],
    config: GraphConfig,
    limit: int,
    timeout: float,
    jql: str | None,
    report: dict[str, Any],
    platform_project_id: str | None = None,
) -> tuple[GraphBatch, dict[str, Any]]:
    sync_config = load_sync_config(project)
    buckets = load_buckets(sync_config)
    sprint_field = sync_config.get("sprint_field") if config.wants("sprints") else None
    detail_fields = load_detail_fields(sync_config)
    fields = _extra_fields(config, sprint_field, detail_fields)

    scope = dict(source.scope)
    if jql:
        scope["jql"] = jql

    batch = GraphBatch()
    with operations.open_client(source, env, timeout=timeout) as client:
        base_url = client.base_url
        site = site_of(base_url)
        records = client.search(scope, limit=limit, extra_fields=fields)
        report["issues_read"] = len(records)
        report["read"]["issues"] = len(records)

        ns = Namespace.make(
            extract_jira.SYSTEM, site, _project_key_of(records, scope),
            platform=platform_project_id,
        )
        site = ns.site_scope
        report["namespace"] = ns

        batch.extend(
            extract_jira.extract_issues(
                records,
                site=site,
                ontology=config.ontology,
                source=source.name,
                base_url=base_url,
                buckets=buckets,
                sprint_field=sprint_field,
                detail_fields=detail_fields,
                include=config.include,
            )
        )

        for wanted, stage, call, apply in (
            (
                "changelog",
                "changelog",
                lambda key: client.changelog(key, limit=config.changelog_limit),
                lambda key, rows: extract_jira.extract_changelog(key, rows, site=site),
            ),
            (
                "remote_links",
                "remote_links",
                lambda key: client.remote_links(key),
                lambda key, rows: extract_jira.extract_remote_links(key, rows, site=site),
            ),
            (
                "watchers",
                "watchers",
                lambda key: client.watchers(key),
                lambda key, rows: extract_jira.extract_watchers(key, rows, site=site),
            ),
        ):
            if not config.wants(wanted):
                continue
            for record in records:
                if not record.key:
                    continue
                rows = _guarded(report, stage, lambda k=record.key: call(k), None, issue=record.key)
                if rows is not None:
                    batch.extend(apply(record.key, rows))

        if sprint_field:
            batch.extend(_jira_sprints(client, records, source, site, report))

        if config.wants("xray"):
            batch.extend(
                _jira_xray(
                    client,
                    records=records,
                    env=env,
                    config=config,
                    site=site,
                    base_url=base_url,
                    jql=str(scope.get("jql") or ""),
                    detail_fields=detail_fields,
                    timeout=timeout,
                    report=report,
                )
            )

    report["site"] = site
    report["system"] = extract_jira.SYSTEM
    return batch, report


def _jira_sprints(
    client: Any,
    records: list[AlmRecord],
    source: SourceConfig,
    site: str,
    report: dict[str, Any],
) -> GraphBatch:
    project_key = _project_key_of(records, source.scope)
    boards = (
        _guarded(report, "boards", lambda: client.boards(project_key), [])
        if project_key
        else []
    )
    sprints_by_board: dict[Any, list[dict[str, Any]]] = {}
    for board in boards:
        board_id = board.get("id")
        sprints_by_board[board_id] = _guarded(
            report, "sprints", lambda b=board_id: client.sprints(b), [], board=board_id
        )
    report["read"]["boards"] = len(boards)
    report["read"]["sprints"] = sum(len(v) for v in sprints_by_board.values())
    return extract_jira.extract_sprints(
        boards, sprints_by_board, site=site, project_key=project_key
    )


def _jira_xray(
    client: Any,
    *,
    records: list[AlmRecord],
    env: Mapping[str, str],
    config: GraphConfig,
    site: str,
    base_url: str,
    jql: str,
    detail_fields: Mapping[str, str],
    timeout: float,
    report: dict[str, Any],
) -> GraphBatch:
    """Whichever Xray this site has, read into the one shared vocabulary.

    All three tiers converge on `extract_xray`: Cloud feeds it GraphQL results
    directly, Server/DC feeds it the same shapes rebuilt from REST by `xray_server`,
    and a site with no Xray at all contributes nothing here because its tests are
    ordinary issues that the Jira pass has already extracted in full.
    """
    index = extract_xray.IssueIndex(site)
    for record in records:
        if record.key:
            index.learn(record.id, record.key)

    server = xray_mod.XrayServerVia(client)
    tier = xray_mod.detect_tier(
        env,
        jira_probe=server,
        field_ids=list(detail_fields.values()),
        jira_url=base_url,
    )
    report["xray_tier"] = tier

    if tier["tier"] == "cloud":
        batch, errors, counts = _xray_cloud(
            env=env, config=config, site=site, index=index, jql=jql, timeout=timeout
        )
        report["errors"].extend(errors)
        report["read"]["xray"] = counts
        return batch

    if tier["tier"] == "server":
        return _xray_server(
            server,
            records=records,
            config=config,
            site=site,
            index=index,
            detail_fields=detail_fields,
            report=report,
        )

    return _xray_plain(records, config=config, site=site, index=index, report=report)


def _xray_plain(
    records: list[AlmRecord],
    *,
    config: GraphConfig,
    site: str,
    index: extract_xray.IssueIndex,
    report: dict[str, Any],
) -> GraphBatch:
    """No Xray at all: build the Xray-shaped collections out of the Jira read.

    A plain site still answers every question the graph asks of a real Xray — which
    tests a set or plan holds, which tests an execution ran and with which result —
    with the primitives Jira has always had: "Test" issue links for membership and
    coverage, Gherkin in the test's description for steps and preconditions, and
    `state:` labels for run results. `plain.collect` rewrites those into the same
    Cloud-shaped payloads the server tier produces, so one extractor serves all three.
    """
    classified = plain_mod.classify(records, config.ontology)
    if not any(classified.values()):
        report["read"]["xray"] = {name: 0 for name in classified}
        return GraphBatch()

    for keys in classified.values():
        for key in keys:
            index.learn(key, key)

    collection = plain_mod.collect(records, classified, ontology=config.ontology)
    report["read"]["xray"] = collection.counts()
    return extract_xray.extract_xray(
        site=site, ontology=config.ontology, index=index, **collection.payloads
    )


def _xray_cloud(
    *,
    env: Mapping[str, str],
    config: GraphConfig,
    site: str,
    index: extract_xray.IssueIndex,
    jql: str,
    timeout: float,
) -> tuple[GraphBatch, list[dict[str, Any]], dict[str, int]]:
    """The six Xray Cloud collections, read and turned into a batch.

    One try/except around the whole read rather than one per collection: Xray Cloud
    shares one rate limit and one bearer token across every call, so a failure partway
    through (an expired token, a quota trip) means the rest would fail identically —
    catching once says so plainly instead of repeating the same error six times.
    """
    errors: list[dict[str, Any]] = []
    try:
        with xray_mod.XrayCloudClient.from_env(env, timeout=timeout) as client:
            tests = client.tests(jql)
            preconditions = client.preconditions(jql)
            test_sets = client.test_sets(jql)
            test_plans = client.test_plans(jql)
            executions = client.test_executions(jql)
            execution_ids = [str(e.get("issueId")) for e in executions if e.get("issueId")]
            runs = (
                client.test_runs(execution_ids, limit=config.run_limit * 50)
                if execution_ids
                else []
            )
    except ConnectionSourceError as exc:
        errors.append({"stage": "xray", **exc.to_dict()})
        return GraphBatch(), errors, {}

    counts = {
        "tests": len(tests),
        "preconditions": len(preconditions),
        "test_sets": len(test_sets),
        "test_plans": len(test_plans),
        "executions": len(executions),
        "runs": len(runs),
    }
    batch = extract_xray.extract_xray(
        site=site,
        ontology=config.ontology,
        index=index,
        tests=tests,
        preconditions=preconditions,
        test_sets=test_sets,
        test_plans=test_plans,
        executions=executions,
        runs=runs,
    )
    return batch, errors, counts


def _xray_server(
    server: Any,
    *,
    records: list[AlmRecord],
    config: GraphConfig,
    site: str,
    index: extract_xray.IssueIndex,
    detail_fields: Mapping[str, str],
    report: dict[str, Any],
) -> GraphBatch:
    """Xray Server/DC, read through the Jira credential it shares.

    Server/DC has no search of its own — `/rest/raven` answers about issues, never
    about which issues exist — so the set of tests, plans and executions comes from
    the Jira read that already happened, classified by issue type. That is also why
    it costs nothing extra to discover: the issues are already in hand.

    The index is seeded with each key mapped to itself because Server/DC references
    issues by key while `extract_xray` resolves references through numeric ids. The
    identity mapping makes a key its own identifier, and every extractor downstream
    is unable to tell the difference.
    """
    classified = xray_server.classify(records, config.ontology)
    if not any(classified.values()):
        report["read"]["xray"] = {name: 0 for name in classified}
        return GraphBatch()

    records_by_key = {r.key: r for r in records if r.key}
    for keys in classified.values():
        for key in keys:
            index.learn(key, key)

    collection = xray_server.collect(
        server,
        records_by_key=records_by_key,
        classified=classified,
        detail_fields=detail_fields,
        with_runs=config.wants("executions"),
    )
    report["errors"].extend(collection.errors)
    report["read"]["xray"] = collection.counts()
    return extract_xray.extract_xray(
        site=site, ontology=config.ontology, index=index, **collection.payloads
    )


# -- Azure DevOps ----------------------------------------------------------


def _build_azure(
    *,
    source: SourceConfig,
    env: Mapping[str, str],
    config: GraphConfig,
    limit: int,
    timeout: float,
    report: dict[str, Any],
    platform_project_id: str | None = None,
) -> tuple[GraphBatch, dict[str, Any]]:
    """Work items, test plans and test runs from one Azure DevOps project.

    The work items are re-read through `graph_batch` rather than reused from
    `search`: `search` asks for a named field list, and Azure refuses to return
    relations alongside a field list. Relations are the parent/child and tested-by
    edges, which is most of what makes this a graph rather than a list, so the second
    read is worth its one extra round trip per two hundred items.
    """
    scope = dict(source.scope)
    azure_project = str(scope.get("project") or "")
    batch = GraphBatch()

    with operations.open_client(source, env, timeout=timeout) as client:
        base_url = client.base_url
        site = site_of(base_url)
        records = client.search(scope, limit=limit)
        report["issues_read"] = len(records)
        report["read"]["work_items"] = len(records)

        ns = Namespace.make(
            extract_azure.SYSTEM, site, azure_project or _azure_project_of(records),
            platform=platform_project_id,
        )
        site = ns.site_scope
        report["namespace"] = ns

        ids = [r.id for r in records if r.id]
        items = _guarded(report, "work_items", lambda: client.graph_batch(ids), []) if ids else []
        batch.extend(
            extract_azure.extract_work_items(
                items or [r.raw for r in records if isinstance(r.raw, Mapping)],
                site=site,
                ontology=config.ontology,
                source=source.name,
                base_url=base_url,
            )
        )

        plans: list[dict[str, Any]] = []
        suites_by_plan: dict[Any, list[dict[str, Any]]] = {}
        cases_by_suite: dict[Any, list[dict[str, Any]]] = {}
        if azure_project and config.wants("test_plans"):
            plans = _guarded(
                report, "test_plans", lambda: client.test_plans(azure_project), []
            )
            for plan in plans:
                plan_id = plan.get("id")
                suites = _guarded(
                    report,
                    "test_suites",
                    lambda p=plan_id: client.test_suites(azure_project, p),
                    [],
                    plan=plan_id,
                )
                suites_by_plan[plan_id] = suites
                for suite in suites:
                    suite_id = suite.get("id")
                    cases_by_suite[suite_id] = _guarded(
                        report,
                        "test_cases",
                        lambda p=plan_id, s=suite_id: client.test_cases(azure_project, p, s),
                        [],
                        plan=plan_id,
                        suite=suite_id,
                    )
            report["read"]["test_plans"] = len(plans)
            report["read"]["test_suites"] = sum(len(v) for v in suites_by_plan.values())
            report["read"]["test_cases"] = sum(len(v) for v in cases_by_suite.values())

        batch.extend(
            extract_azure.extract_test_plans(
                plans, site=site, suites_by_plan=suites_by_plan, cases_by_suite=cases_by_suite
            )
        )

        runs: list[dict[str, Any]] = []
        results_by_run: dict[Any, list[dict[str, Any]]] = {}
        if azure_project and config.wants("executions"):
            runs = _guarded(
                report,
                "test_runs",
                lambda: client.test_runs(azure_project, limit=config.run_limit),
                [],
            )
            for run in runs:
                run_id = run.get("id")
                results_by_run[run_id] = _guarded(
                    report,
                    "test_results",
                    lambda r=run_id: client.test_run_results(azure_project, r),
                    [],
                    run=run_id,
                )
            report["read"]["test_runs"] = len(runs)
            report["read"]["test_results"] = sum(len(v) for v in results_by_run.values())

        batch.extend(
            extract_azure.extract_test_results(
                runs, site=site, ontology=config.ontology, results_by_run=results_by_run
            )
        )

    report["site"] = site
    report["system"] = extract_azure.SYSTEM
    return batch, report


# -- GitLab ----------------------------------------------------------------


def _build_gitlab(
    *,
    source: SourceConfig,
    env: Mapping[str, str],
    config: GraphConfig,
    limit: int,
    timeout: float,
    report: dict[str, Any],
    tracker_system: str,
    tracker_site: str,
    platform_project_id: str | None = None,
) -> tuple[GraphBatch, dict[str, Any]]:
    """One GitLab project's delivery context."""
    scope = dict(source.scope)
    with operations.open_client(source, env, timeout=timeout) as client:
        site = site_of(client.base_url)
        ns = Namespace.make(
            extract_gitlab.SYSTEM, site, scope.get("project_id"),
            platform=platform_project_id,
        )
        site = ns.site_scope
        report["namespace"] = ns
        project_info = _guarded(report, "project", lambda: client.project(scope), {})

        pipelines: list[dict[str, Any]] = []
        merge_requests: list[dict[str, Any]] = []
        if config.wants("delivery"):
            pipelines = _guarded(
                report,
                "pipelines",
                lambda: client.pipelines(scope, limit=config.delivery_limit),
                [],
            )
            merge_requests = _guarded(
                report,
                "merge_requests",
                lambda: client.merge_requests(scope, limit=config.delivery_limit),
                [],
            )

        project_id = (project_info or {}).get("id") or scope.get("project_id")
        milestones = (
            _guarded(report, "milestones", lambda: client.milestones(project_id), [])
            if project_id
            else []
        )
        issues = _guarded(report, "issues", lambda: client.search(scope, limit=limit), [])

    report["issues_read"] = len(issues)
    report["read"] = {
        "issues": len(issues),
        "pipelines": len(pipelines),
        "merge_requests": len(merge_requests),
        "milestones": len(milestones),
    }
    report["site"] = site
    report["system"] = extract_gitlab.SYSTEM

    batch = extract_gitlab.extract_gitlab(
        site=site,
        source=source.name,
        tracker_system=tracker_system or extract_jira.SYSTEM,
        tracker_site=tracker_site or site,
        project=project_info,
        pipelines=pipelines,
        merge_requests=merge_requests,
        milestones=milestones,
        issues=issues,
    )
    return batch, report


# -- Confluence ------------------------------------------------------------


def _build_confluence(
    *,
    source: SourceConfig,
    env: Mapping[str, str],
    config: GraphConfig,
    limit: int,
    timeout: float,
    report: dict[str, Any],
    tracker_system: str,
    tracker_site: str,
    platform_project_id: str | None = None,
) -> tuple[GraphBatch, dict[str, Any]]:
    """One Confluence space's pages, and the tracker issues they name."""
    scope = dict(source.scope)
    with operations.open_client(source, env, timeout=timeout) as client:
        site = site_of(client.base_url)
        ns = Namespace.make(
            extract_confluence.SYSTEM, site, scope.get("space_key"),
            platform=platform_project_id,
        )
        site = ns.site_scope
        report["namespace"] = ns
        base_url = client.base_url
        # `knowledge_bodies` off means the page map without the prose: much cheaper,
        # and still finds every issue key that appears in a page TITLE. Turning it
        # off is the right call on a wiki whose pages are large and whose links are
        # written in titles; leaving it on is the right default everywhere else,
        # because most references live in the body.
        with_body = config.wants("knowledge_bodies")
        pages = _guarded(
            report,
            "pages",
            lambda: list(client.iter_pages(scope, limit=limit, with_body=with_body)),
            [],
        )

    report["issues_read"] = 0  # a page is not an issue; do not inflate the count
    report["read"] = {"pages": len(pages), "bodies": with_body}
    report["site"] = site
    report["system"] = extract_confluence.SYSTEM

    batch = extract_confluence.extract_confluence(
        site=site,
        base_url=base_url,
        source=source.name,
        tracker_system=tracker_system or extract_jira.SYSTEM,
        tracker_site=tracker_site or site,
        pages=pages,
    )
    return batch, report


# -- the whole build -------------------------------------------------------


def run_build(
    project: str | Path,
    *,
    sources: Iterable[str] | None = None,
    limit: int = 1000,
    timeout: float = DEFAULT_TIMEOUT,
    jql: str | None = None,
    dry_run: bool = False,
    prune: bool = False,
    run_derivations: bool = True,
    batch_size: int | None = None,
) -> tuple[BuildResult, GraphBatch]:
    """Build the graph for every configured source (or the ones named).

    Returns the report and the batch together — a dry run needs the batch itself
    (to print or write it), a real run needs only the report, and building both from
    one call keeps the two paths from silently drifting apart.
    """
    started = now_iso()
    version = int(time.time())
    # The platform's own project id, when this build runs inside a sandboxed
    # deployment (PROJECT_ID is set there -- see Application/sandbox-image/
    # listener.py). Folded into every Namespace below so two platform projects
    # pointed at the identical Jira/Azure/GitLab/Confluence target still land in
    # disjoint namespaces in whatever Neo4j they share -- required because
    # Neo4j Community edition (what this platform deploys) has exactly one
    # database, never one per project. Absent outside a sandbox (a bare `alm-conn`
    # run on a laptop), in which case scope is exactly what it was before this
    # existed -- ALM-derived only.
    platform_project_id = os.environ.get("PROJECT_ID") or None
    project_config = load_project(project)
    env = load_project_env(project)
    graph_config = load_graph_config(project, env, sources=sources)
    if batch_size:
        graph_config = replace(graph_config, batch_size=batch_size)

    wanted = set(graph_config.sources) or None
    enabled = [
        s
        for s in project_config.enabled_sources()
        if s.server in BUILDABLE and (wanted is None or s.name in wanted)
    ]
    # Trackers first so a delivery or context source can resolve the issue keys it
    # mentions into the namespace the tracker's own nodes were just written under.
    targets = [s for s in enabled if s.server in TRACKER_SERVERS]
    targets += [s for s in enabled if s.server in DELIVERY_SERVERS]
    targets += [s for s in enabled if s.server in CONTEXT_SERVERS]

    result = BuildResult(
        project=project_config.project, started_at=started, version=version, dry_run=dry_run
    )
    batch = GraphBatch()
    tracker_system = ""
    tracker_site = ""
    namespaces: dict[str, Namespace] = {}

    for target in targets:
        try:
            source_batch, report = build_batch(
                project,
                source=target,
                env=env,
                config=graph_config,
                limit=limit,
                timeout=timeout,
                jql=jql,
                tracker_system=tracker_system,
                tracker_site=tracker_site,
                platform_project_id=platform_project_id,
            )
        except ConnectionSourceError as exc:
            result.errors.append({"source": target.name, **exc.to_dict()})
            continue

        result.sources.append(target.name)
        site = report.get("site") or ""
        system = report.get("system") or ""
        if site and site not in result.sites:
            result.sites.append(site)
        namespace = f"{system}:{site}:" if system and site else ""
        if namespace and namespace not in result.namespaces:
            result.namespaces.append(namespace)
        ns = report.get("namespace")
        if isinstance(ns, Namespace) and ns.prefix not in namespaces:
            namespaces[ns.prefix] = ns
        if target.server in TRACKER_SERVERS and not tracker_site:
            tracker_system, tracker_site = system, site

        result.issues_read += report.get("issues_read", 0)
        if report.get("xray_tier"):
            result.xray_tier[target.name] = report["xray_tier"]
        if report.get("read"):
            result.read[target.name] = report["read"]
        result.errors.extend(
            {"source": target.name, **err} for err in report.get("errors", [])
        )
        batch.extend(source_batch)

    result.counts = batch.counts()

    if dry_run or not targets:
        result.finished_at = now_iso()
        return result, batch

    seen_at = now_iso()
    try:
        with loader_mod.open_session(graph_config) as session:
            run = loader_mod.session_runner(session)
            load_result = loader_mod.load_batch(
                batch, run, version=version, seen_at=seen_at, batch_size=graph_config.batch_size
            )
            result.load = load_result.to_dict()

            if prune:
                for prefix in result.namespaces:
                    ns = namespaces.get(prefix)
                    if ns is None:
                        result.errors.append(
                            {
                                "stage": "prune",
                                "error": "SourcesConfigError",
                                "message": f"graph: no namespace derived for {prefix!r}, skipping prune",
                            }
                        )
                        continue
                    try:
                        nodes, rels = loader_mod.prune(
                            run, ns, version=version, relationships=True
                        )
                    except ConnectionSourceError as exc:
                        result.errors.append({"stage": "prune", **exc.to_dict()})
                        continue
                    result.pruned_nodes += nodes
                    result.pruned_relationships += rels

            if run_derivations:
                for prefix in result.namespaces:
                    touched = derive_mod.run_derivations(run, prefix)
                    for name, count in touched.items():
                        result.derivations[name] = result.derivations.get(name, 0) + count
    except ConnectionSourceError as exc:
        result.errors.append({"stage": "load", **exc.to_dict()})

    result.finished_at = now_iso()
    return result, batch
