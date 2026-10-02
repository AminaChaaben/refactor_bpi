"""Load and validate a project's sources.json (the initial project config)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .errors import SourcesConfigError
from .models import ProjectSourcesConfig, SourceConfig

# The top-level groups a source can be declared under. They are three because a
# project relates to them differently, not for tidiness: `things` is where the work
# is tracked (and one of them is the backend of record), `ci` is where it was built
# and shipped, `context` is what it was written from -- read-only by nature, never a
# backend, and never something the factory writes back to.
#
# Order is irrelevant here (the graph runner imposes its own read order); what
# matters is that a name not in this tuple is silently ignored, so adding a section
# to sources.json without adding it here produces a config that loads cleanly and
# does nothing.
_SOURCE_SECTIONS = ("things", "ci", "context")

__all__ = ["SourcesConfigError", "SourcesConfigLoader"]


class SourcesConfigLoader:
    """Reusable loader: path or dict -> ProjectSourcesConfig, plus plan output."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> ProjectSourcesConfig:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SourcesConfigError(f"sources config not found: {self.path}") from exc
        except json.JSONDecodeError as exc:
            raise SourcesConfigError(f"invalid JSON in {self.path}: {exc}") from exc
        return self.from_dict(raw, alm_sprint=self._load_alm_sprint())

    def _load_alm_sprint(self) -> str | None:
        """`_bmad_references/config.yaml`'s `alm.sprint` is the sprint scope's
        source of truth -- resolved relative to this loader's own sources.json
        path (`Talan_usine_config/Config_Connection_Sources/sources.json` ->
        `../../_bmad_references/config.yaml`), same layout `alm.connector_project`
        already assumes in `orchestrator/engine.py`/`usine.py`. A non-empty value
        here overrides `sources.json`'s own `things.jira.sprint`; missing/empty
        (or no config.yaml at all) falls back to that field instead, so a project
        with no config.yaml still works exactly as before this existed.
        """
        config_path = (self.path.parent / ".." / ".." / "_bmad_references" / "config.yaml").resolve()
        if not config_path.is_file():
            return None
        try:
            doc = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            return None
        sprint = (doc.get("alm") or {}).get("sprint")
        return str(sprint) if sprint not in (None, "") else None

    def plan(self) -> dict[str, Any]:
        """The deterministic connection plan the agent must execute."""
        config = self.load()
        return {
            "project": config.project,
            "things_backend": config.things_backend,
            "connections": [
                {
                    "source": source.name,
                    "server": source.server,
                    "enabled": source.enabled,
                    "scope_type": source.scope_type,
                    "scope": source.scope,
                }
                for source in config.sources
            ],
        }

    @staticmethod
    def from_dict(raw: dict[str, Any], alm_sprint: str | None = None) -> ProjectSourcesConfig:
        if not isinstance(raw, dict) or not raw.get("project"):
            raise SourcesConfigError("missing required key: 'project'")

        sources: list[SourceConfig] = []
        things_backend = "none"
        for section in _SOURCE_SECTIONS:
            section_raw = raw.get(section, {})
            if not isinstance(section_raw, dict):
                continue
            for key, value in section_raw.items():
                if key == "backend":
                    if section == "things" and isinstance(value, str):
                        things_backend = value
                    continue
                if isinstance(value, dict):
                    sources.append(SourcesConfigLoader._to_source(key, value, section, alm_sprint=alm_sprint))

        return ProjectSourcesConfig(
            project=str(raw["project"]),
            things_backend=things_backend,
            sources=tuple(sources),
            safety=dict(raw.get("safety", {})),
        )

    @staticmethod
    def _to_source(
        key: str, value: dict[str, Any], section: str = "things", *, alm_sprint: str | None = None
    ) -> SourceConfig:
        name = str(value.get("source", key))
        server = str(value.get("server", key))
        enabled = bool(value.get("enabled", False))
        scope = {
            k: v
            for k, v in value.items()
            if k not in ("source", "server", "enabled")
        }
        if server == "jira":
            sprint = alm_sprint if alm_sprint is not None else scope.get("sprint")
            scope["jql"] = _apply_sprint_scope(scope.get("jql"), sprint)
        return SourceConfig(
            name=name, server=server, enabled=enabled, scope=scope, section=section
        )


def _apply_sprint_scope(jql: str | None, sprint: Any) -> str | None:
    """Narrow a declared Jira `jql` scope to one sprint, if configured.

    `sprint` is a plain config value (`things.jira.sprint` in sources.json), not
    JQL -- an all-digit value is sent as a sprint id, unquoted (`Sprint = 2`);
    anything else is quoted as a sprint name (`Sprint = "Sprint 12"`). Empty or
    missing leaves `jql` untouched, so a project with no sprint configured keeps
    importing its full declared scope exactly as before this field existed. This
    is the single point every consumer's `scope["jql"]` is read from (fetch,
    incremental sync, graph load), so the narrowing applies uniformly without
    each call site templating it separately.
    """
    if not jql or sprint in (None, ""):
        return jql
    text = str(sprint)
    clause = f"Sprint = {text}" if text.isdigit() else f'Sprint = "{text}"'
    return f"({jql}) AND {clause}"