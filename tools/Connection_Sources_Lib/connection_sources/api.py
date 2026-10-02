"""Reusable entry points. Import these from anywhere in the factory.

    from connection_sources import resolve, check, read, create, update, transition

Every function takes a client project path and does its own config and credential
loading, so a caller never has to assemble state first. The same functions back the
`alm-conn` CLI and the ALM skills, so code results and agent results cannot diverge.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from . import operations
from .clients import REGISTRY as CLIENT_REGISTRY
from .clients.base import DEFAULT_TIMEOUT
from .env import load_project_env
from .errors import SourcesConfigError
from .health import check_project, doctor, load_project
from .models import (
    AlmRecord,
    ConnectionReport,
    Identity,
    ProjectSourcesConfig,
    ResolvedProject,
    ResolvedSource,
    SourceConfig,
)

__all__ = [
    "check",
    "client",
    "create",
    "delete",
    "identify",
    "preflight",
    "read",
    "resolve",
    "source_for",
    "transition",
    "update",
    "writes_allowed",
]

_REST_VERSIONS = {
    "jira": "Jira Cloud REST v3",
    "azuredevops": "Azure DevOps REST 7.1",
    "gitlab": "GitLab REST v4",
}


def resolve(project: str | Path) -> ResolvedProject:
    """Answer "what does this project connect to, and can it?" without calling out.

    The first thing any caller should run: it reports every enabled source, whether
    credentials and scope are present, and whether a credential looks like the wrong
    kind. No network, no credentials spent.
    """
    config = load_project(project)
    try:
        env: Mapping[str, str] = load_project_env(project)
    except Exception:
        env = {}

    resolved: list[ResolvedSource] = []
    for source in config.enabled_sources():
        client_cls = CLIENT_REGISTRY.get(source.server)
        if client_cls is None:
            resolved.append(
                ResolvedSource(
                    name=source.name,
                    server=source.server,
                    scope=dict(source.scope),
                    scope_type=source.scope_type,
                    tool="",
                    verified_version="",
                    ready=False,
                    scope_error=f"unknown server {source.server!r}; "
                    f"known: {', '.join(sorted(CLIENT_REGISTRY))}",
                )
            )
            continue
        resolved.append(_resolve_direct(source, env, client_cls))

    return ResolvedProject(
        project=config.project,
        project_path=str(project),
        things_backend=config.things_backend,
        writes_allowed=writes_allowed(config),
        sources=tuple(resolved),
    )


def _resolve_direct(
    source: SourceConfig, env: Mapping[str, str], client_cls: Any
) -> ResolvedSource:
    spec = client_cls.spec
    absent = tuple(spec.missing(env))
    missing_scope = [key for key in spec.scope_keys if not source.scope.get(key)]
    scope_error = (
        f"missing scope key(s): {', '.join(missing_scope)}" if missing_scope else None
    )
    suspects = client_cls.suspect_credentials(env)
    return ResolvedSource(
        name=source.name,
        server=source.server,
        scope=dict(source.scope),
        scope_type=source.scope_type,
        tool="rest",
        verified_version=_REST_VERSIONS.get(source.server, "rest"),
        ready=not absent and scope_error is None and not suspects,
        missing_env=absent,
        scope_error=scope_error or (suspects[0].message if suspects else None),
        notes=spec.token_hint,
    )


def writes_allowed(config: ProjectSourcesConfig | str | Path) -> bool:
    """Whether this project's config permits writing to a tracker."""
    if not isinstance(config, ProjectSourcesConfig):
        config = load_project(config)
    return not bool(config.safety.get("read_only", False))


def source_for(project: str | Path, name: str) -> SourceConfig:
    """The enabled source with this name, or a SourcesConfigError naming the options."""
    config = load_project(project)
    for source in config.enabled_sources():
        if source.name == name:
            return source
    raise SourcesConfigError(
        f"no enabled source named {name!r}",
        remediation="enabled sources: "
        + (", ".join(s.name for s in config.enabled_sources()) or "none"),
    )


def preflight(project: str | Path) -> dict[str, Any]:
    """Offline validation: files, config, scope and credential shape."""
    return doctor(project)


def check(
    project: str | Path,
    *,
    source: str | None = None,
    limit: int = 5,
    timeout: float = DEFAULT_TIMEOUT,
) -> ConnectionReport:
    """Probe every enabled source (or one), reporting each independently.

    A source failure is reported in the result, never raised, so one broken system
    cannot hide a working one.
    """
    return check_project(project, only=source, limit=limit, timeout=timeout)


def client(
    project: str | Path,
    source: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """Open the underlying REST client for a source, for anything not wrapped here.

    Caller owns the connection; use it as a context manager.
    """
    return operations.open_client(
        source_for(project, source), load_project_env(project), timeout=timeout
    )


def identify(
    project: str | Path,
    source: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> Identity:
    """Prove the credential works and report who it authenticates as."""
    with client(project, source, timeout=timeout) as conn:
        return conn.ping()


def read(
    project: str | Path,
    source: str,
    *,
    limit: int = 100,
    timeout: float = DEFAULT_TIMEOUT,
    extra_fields: Iterable[str] | None = None,
) -> list[AlmRecord]:
    """Read records from one source using the scope its config declares.

    `extra_fields` names instance-specific custom field ids (jira only, e.g.
    Xray-simulating fields like customfield_12345) to fetch for this call
    alongside the fixed field set, without hardcoding them into the shared model.
    """
    return operations.read(
        source_for(project, source),
        load_project_env(project),
        limit=limit,
        timeout=timeout,
        extra_fields=extra_fields,
    )


def get(
    project: str | Path,
    source: str,
    ident: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    extra_fields: Iterable[str] | None = None,
) -> AlmRecord:
    """Read exactly one item by id/key — the targeted check a write's claimed
    result should be read back against, cheaper than re-running the full scope.

    `extra_fields` names instance-specific custom field ids (jira only) to fetch
    for this call alongside the fixed field set.
    """
    return operations.get(
        source_for(project, source),
        load_project_env(project),
        ident,
        timeout=timeout,
        extra_fields=extra_fields,
    )


def create(
    project: str | Path,
    source: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    **fields: Any,
) -> AlmRecord:
    """Create an issue or work item, refused if the project disables writes."""
    return operations.create(
        source_for(project, source),
        load_project_env(project),
        writes_allowed=writes_allowed(project),
        timeout=timeout,
        **fields,
    )


def update(
    project: str | Path,
    source: str,
    ident: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    **fields: Any,
) -> AlmRecord:
    """Change fields on one item, refused if the project disables writes."""
    return operations.update(
        source_for(project, source),
        load_project_env(project),
        ident,
        writes_allowed=writes_allowed(project),
        timeout=timeout,
        **fields,
    )


def transition(
    project: str | Path,
    source: str,
    ident: str,
    status: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> AlmRecord:
    """Move one item to a new status, refused if the project disables writes."""
    return operations.transition(
        source_for(project, source),
        load_project_env(project),
        ident,
        status,
        writes_allowed=writes_allowed(project),
        timeout=timeout,
    )


def delete(
    project: str | Path,
    source: str,
    ident: str,
    *,
    permanent: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Delete one item, refused if the project disables writes.

    Jira and GitLab deletes are permanent. Azure DevOps sends the item to the
    recycle bin unless `permanent` is set. The result says which happened.
    """
    return operations.delete(
        source_for(project, source),
        load_project_env(project),
        ident,
        writes_allowed=writes_allowed(project),
        permanent=permanent,
        timeout=timeout,
    )
