"""Immutable data models shared by the agent, the CLI and the orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SourceConfig:
    """One connection entry of a project: what to connect to and how to scope it."""

    name: str
    server: str
    enabled: bool
    scope: dict[str, Any] = field(default_factory=dict)
    #: Which sources.json group declared it: "things" (tracked work), "ci"
    #: (delivery) or "context" (read-only reference material, e.g. a wiki).
    #: Carried because the difference is not derivable from the server name and it
    #: decides real behaviour -- the state sync must never turn wiki pages into
    #: tracked items, and `is_syncable` is how it knows not to. Defaults to
    #: "things" so a SourceConfig built by hand behaves as it always did.
    section: str = "things"

    @property
    def is_syncable(self) -> bool:
        """Whether this source's items belong in state.json.

        Context sources exist to be read into the graph and nothing else: a
        specification page is not a ticket, has no workflow state, and would show up
        in the factory's own backlog as work nobody can do.
        """
        return self.section != "context"

    @property
    def scope_type(self) -> str:
        if "jql" in self.scope or "test_jql" in self.scope:
            return "jql"
        if "wiql" in self.scope:
            return "wiql"
        if "cql" in self.scope:
            return "cql"
        if "space_key" in self.scope:
            return "space_key"
        if "project_id" in self.scope:
            return "project_id"
        return "none"


@dataclass(frozen=True)
class ProjectSourcesConfig:
    """A project's full connection configuration (parsed sources.json)."""

    project: str
    things_backend: str
    sources: tuple[SourceConfig, ...] = field(default_factory=tuple)
    safety: dict[str, Any] = field(default_factory=dict)

    def enabled_sources(self) -> tuple[SourceConfig, ...]:
        return tuple(source for source in self.sources if source.enabled)


@dataclass(frozen=True)
class ResolvedSource:
    """One source, resolved: what it connects to and whether it can run."""

    name: str
    server: str
    scope: dict[str, Any]
    scope_type: str
    tool: str
    verified_version: str
    ready: bool
    missing_env: tuple[str, ...] = field(default_factory=tuple)
    scope_error: str | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.name,
            "server": self.server,
            "scope": self.scope,
            "scope_type": self.scope_type,
            "tool": self.tool,
            "verified_version": self.verified_version,
            "ready": self.ready,
            "missing_env": list(self.missing_env),
            "scope_error": self.scope_error,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class ResolvedProject:
    """What a project connects to, before anything is called."""

    project: str
    project_path: str
    things_backend: str
    writes_allowed: bool
    sources: tuple[ResolvedSource, ...] = field(default_factory=tuple)

    def ready_sources(self) -> tuple[ResolvedSource, ...]:
        return tuple(s for s in self.sources if s.ready)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "project_path": self.project_path,
            "things_backend": self.things_backend,
            "writes_allowed": self.writes_allowed,
            "sources": [s.to_dict() for s in self.sources],
        }


@dataclass(frozen=True, slots=True)
class Identity:
    """Who the credentials authenticate as, and against what."""

    system: str
    account: str
    base_url: str

    def to_dict(self) -> dict[str, Any]:
        return {"system": self.system, "account": self.account, "base_url": self.base_url}


@dataclass(frozen=True, slots=True)
class AlmRecord:
    """One tracked item, in the same shape whatever system it came from."""

    system: str
    id: str
    key: str
    title: str
    status: str
    type: str
    url: str
    assignee: str | None = None
    tags: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        out = {
            "system": self.system,
            "id": self.id,
            "key": self.key,
            "title": self.title,
            "status": self.status,
            "type": self.type,
            "url": self.url,
            "assignee": self.assignee,
            "tags": self.tags,
        }
        if include_raw:
            out["raw"] = self.raw
        return out

    def summary(self) -> str:
        who = self.assignee or "unassigned"
        return f"{self.key}  [{self.status}]  {self.title}  ({self.type}, {who})"


@dataclass(frozen=True)
class ConnectionResult:
    """Outcome of one connection check for one source."""

    source: str
    server: str
    ok: bool
    item_count: int = 0
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConnectionReport:
    """Per-project connection report (status + counts per source)."""

    project: str
    checked_at: str
    connections: tuple[ConnectionResult, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "checked_at": self.checked_at,
            "connections": {
                result.source: {
                    "server": result.server,
                    "ok": result.ok,
                    "item_count": result.item_count,
                    "error": result.error,
                }
                for result in self.connections
            },
        }