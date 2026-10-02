"""Offline preflight (doctor) and live connection probe (check_project)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .clients import REGISTRY as CLIENT_REGISTRY
from .clients.base import DEFAULT_TIMEOUT
from .config import SourcesConfigLoader
from .env import BROKER_URL_VAR, broker_configured, load_project_env
from .errors import ConnectionSourceError
from .models import ConnectionReport, ProjectSourcesConfig
from .operations import probe
from .report import ConnectionReportBuilder

SOURCES_RELPATH = Path("Config_Connection_Sources") / "sources.json"

_FILE_HINTS = {
    "project_dir": "create the client/project folder",
    "sources_json": f"create {SOURCES_RELPATH.as_posix()}",
    "credential_broker": "set TALAN_CREDENTIALS_URL and TALAN_CREDENTIALS_TOKEN",
}


def sources_path(project_path: str | Path) -> Path:
    return Path(project_path) / SOURCES_RELPATH


def load_project(project_path: str | Path) -> ProjectSourcesConfig:
    return SourcesConfigLoader(sources_path(project_path)).load()


def doctor(project_path: str | Path) -> dict[str, Any]:
    """Validate config and credentials without any network call."""
    project_dir = Path(project_path)
    findings: list[dict[str, Any]] = []

    checks = {
        "project_dir": {"path": str(project_dir), "exists": project_dir.is_dir()},
        "sources_json": {
            "path": str(sources_path(project_dir)),
            "exists": sources_path(project_dir).exists(),
        },
        "credential_broker": {
            "path": os.environ.get(BROKER_URL_VAR, ""),
            "exists": broker_configured(),
        },
    }
    for name, check in checks.items():
        if not check["exists"]:
            findings.append(
                {
                    "level": "error",
                    "check": name,
                    "message": f"missing: {check['path']}",
                    "remediation": _FILE_HINTS[name],
                }
            )

    if not checks["sources_json"]["exists"]:
        return _result(project_dir, checks, findings, [])

    try:
        config = load_project(project_dir)
    except ConnectionSourceError as exc:
        findings.append(
            {
                "level": "error",
                "check": "sources_json",
                "message": exc.message,
                "remediation": exc.remediation,
            }
        )
        return _result(project_dir, checks, findings, [])

    try:
        env: Mapping[str, str] = load_project_env(project_dir)
    except ConnectionSourceError:
        env = {}

    reports = [
        _check_source(s, env, project_dir, findings) for s in config.enabled_sources()
    ]

    if not config.enabled_sources():
        findings.append(
            {
                "level": "warning",
                "check": "sources_json",
                "message": "no source is enabled",
                "remediation": 'set "enabled": true on at least one source',
            }
        )

    return _result(project_dir, checks, findings, reports)


def _check_source(source, env, project_dir, findings) -> dict[str, Any]:
    entry: dict[str, Any] = {"source": source.name, "server": source.server, "ok": True}

    def fail(message: str, remediation: str, **extra: Any) -> None:
        entry.update(ok=False, **extra)
        findings.append(
            {
                "level": "error",
                "check": f"source:{source.name}",
                "message": message,
                "remediation": remediation,
            }
        )

    client_cls = CLIENT_REGISTRY.get(source.server)
    if client_cls is None:
        fail(
            f"unknown server {source.server!r}",
            f"use one of: {', '.join(sorted(CLIENT_REGISTRY))}",
        )
        return entry

    spec = client_cls.spec

    absent = spec.missing(env)
    if absent:
        fail(
            f"missing credential(s): {', '.join(absent)}",
            spec.token_hint or f"add a Vault reference for {absent} to this project",
            missing_env=absent,
        )

    missing_scope = [key for key in spec.scope_keys if not source.scope.get(key)]
    if missing_scope:
        fail(
            f"missing scope key(s): {', '.join(missing_scope)}",
            f"add {missing_scope} to the {source.name} source in sources.json",
            missing_scope=missing_scope,
        )

    for suspect in client_cls.suspect_credentials(env):
        fail(suspect.message, suspect.remediation, suspect_credential=suspect.key)

    return entry


def _result(project_dir, checks, findings, sources) -> dict[str, Any]:
    return {
        "project_dir": str(project_dir),
        "ok": not any(f["level"] == "error" for f in findings),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "files": checks,
        "sources": sources,
        "findings": findings,
    }


def check_project(
    project_path: str | Path,
    *,
    only: str | None = None,
    limit: int = 5,
    timeout: float = DEFAULT_TIMEOUT,
) -> ConnectionReport:
    """Probe every enabled source. A source failure is reported, not raised."""
    config = load_project(project_path)
    env = load_project_env(project_path)

    selected = [
        source
        for source in config.enabled_sources()
        if only is None or source.name == only
    ]

    return ConnectionReport(
        project=config.project,
        checked_at=datetime.now(timezone.utc).isoformat(),
        connections=tuple(
            probe(source, env, limit=limit, timeout=timeout) for source in selected
        ),
    )


def write_report(report: ConnectionReport, out: str | Path) -> Path:
    return ConnectionReportBuilder(report.project).write(report, out)
