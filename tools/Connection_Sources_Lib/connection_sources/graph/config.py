"""Where the graph connects, what it collects, and how much it collects at a time.

Same split the rest of the package already uses: connection details live in the
project's `.env` because they are secrets, and everything about *shape* lives in the
`graph` block of `sources.json` because it is a decision the project made and wants
to keep in version control.

The one judgement encoded here is what `include` defaults to. Comments, attachments
and worklogs ride along in the issue payload, so asking for them costs bytes; remote
links and the changelog are each a separate call per issue, so asking for them costs
a round trip per issue and are the first things to turn off on a large project.
Watchers cost a call *and* the browse-users permission, which plenty of tokens do not
have, so that one is off until a project asks — a build that half-fails on
permissions is worse than one that never tried.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..errors import SourcesConfigError
from ..health import sources_path
from ..sync.store import read_json
from .ontology import Ontology, load_ontology

__all__ = [
    "COLLECTABLE",
    "DEFAULT_INCLUDE",
    "GraphConfig",
    "load_graph_block",
    "load_graph_config",
]

# Everything a build can be asked to collect beyond the issue's own scalar fields.
COLLECTABLE: tuple[str, ...] = (
    "comments",
    "attachments",
    "worklogs",
    "remote_links",
    "changelog",
    "watchers",
    "sprints",
    "xray",
    "test_plans",
    "executions",
    "delivery",
    # Whether a Confluence build reads page BODIES as well as the page map. The map
    # is always read when a confluence source is enabled; the body is what costs --
    # it is the largest field in the payload by an order of magnitude, and its only
    # use here is finding issue keys written in the prose (extract_confluence).
    # Titles are searched either way, so turning this off keeps the cheap half of
    # the link recovery.
    "knowledge_bodies",
)

DEFAULT_INCLUDE: frozenset[str] = frozenset(
    {
        "comments",
        "attachments",
        "worklogs",
        "remote_links",
        "changelog",
        "sprints",
        "xray",
        "test_plans",
        "executions",
        "delivery",
        # On by default: most references to a ticket live in a page's body, not its
        # title, so a wiki read without bodies finds a small fraction of the links
        # that are actually there. Projects with very large pages turn it off.
        "knowledge_bodies",
    }
)

DEFAULT_BATCH_SIZE = 500
DEFAULT_CHANGELOG_LIMIT = 200
# One execution carries every result inside it, so this is a window on *history*, not
# on volume: a hundred runs is several months of nightly execution on most projects,
# and reading further back adds evidence nobody queries while costing a call per run.
DEFAULT_RUN_LIMIT = 100
DEFAULT_DELIVERY_LIMIT = 50
DEFAULT_DATABASE = "neo4j"
DEFAULT_URI = "bolt://localhost:7687"


@dataclass(frozen=True, slots=True)
class GraphConfig:
    """One project's graph settings, credentials and shape decisions both."""

    uri: str
    user: str
    password: str
    database: str
    batch_size: int
    changelog_limit: int
    include: frozenset[str]
    # Which `sources.json` sources to build from. Empty means every enabled source
    # the graph knows how to extract: the Jira, Azure DevOps and GitLab ones.
    sources: tuple[str, ...]
    ontology: Ontology
    # Depth knobs for the two collections that are unbounded in principle — execution
    # history and delivery history both grow forever, while everything else is
    # bounded by how much work the project has. They carry defaults because a caller
    # that does not care about either should not have to name them.
    run_limit: int = DEFAULT_RUN_LIMIT
    delivery_limit: int = DEFAULT_DELIVERY_LIMIT
    block: dict[str, Any] = field(default_factory=dict)

    def wants(self, what: str) -> bool:
        return what in self.include

    def redacted(self) -> dict[str, Any]:
        """The settings as they can safely be printed — the password never is."""
        return {
            "uri": self.uri,
            "user": self.user,
            "database": self.database,
            "password": "***" if self.password else "",
            "batch_size": self.batch_size,
            "changelog_limit": self.changelog_limit,
            "run_limit": self.run_limit,
            "delivery_limit": self.delivery_limit,
            "include": sorted(self.include),
            "sources": list(self.sources),
        }

    def require_credentials(self) -> None:
        """Fail before the driver is built, naming the variable that is missing.

        The Neo4j driver's own error for an absent password is an authentication
        failure raised on first use, which reads as "wrong password" and sends people
        looking in the wrong place.
        """
        missing = [
            name
            for name, value in (("NEO4J_URI", self.uri), ("NEO4J_PASSWORD", self.password))
            if not value
        ]
        if missing:
            raise SourcesConfigError(
                f"graph: missing {', '.join(missing)}",
                server="neo4j",
                remediation=(
                    "add NEO4J_URI, NEO4J_USERNAME and NEO4J_PASSWORD to the project's "
                    "the environment — resolved from Vault by the backend"
                ),
            )


def load_graph_block(project: str | Path) -> dict[str, Any]:
    """The optional `graph` block of sources.json, or an empty one.

    Read directly from the file, exactly as `load_sync_config` does, because
    `ProjectSourcesConfig` models connection concerns only and every existing project
    config must keep working without a `graph` block at all.
    """
    raw = read_json(sources_path(project), default={})
    block = raw.get("graph") if isinstance(raw, dict) else None
    return dict(block) if isinstance(block, Mapping) else {}


def _include_from(block: Mapping[str, Any]) -> frozenset[str]:
    """The `include` setting, in either of the two forms a project may write it.

    A list replaces the defaults outright (`"include": ["changelog"]` means only the
    changelog); a mapping of flags edits them (`{"changelog": false}` means the
    defaults minus the changelog). Both read naturally, and neither can be mistaken
    for the other, so both are accepted rather than forcing one on everybody.
    """
    declared = block.get("include")
    if isinstance(declared, list):
        return frozenset(
            str(name).strip().lower()
            for name in declared
            if str(name).strip().lower() in COLLECTABLE
        )
    if isinstance(declared, Mapping):
        chosen = set(DEFAULT_INCLUDE)
        for name, on in declared.items():
            key = str(name).strip().lower()
            if key not in COLLECTABLE:
                continue
            chosen.add(key) if on else chosen.discard(key)
        return frozenset(chosen)
    return DEFAULT_INCLUDE


def _int_from(block: Mapping[str, Any], key: str, default: int, *, minimum: int) -> int:
    """A positive integer from config, falling back rather than raising.

    A batch size someone typed as a string, or as zero, must not stop a build: the
    default is always a working value, and refusing to run over a tuning knob would
    be a worse outcome than quietly using it.
    """
    try:
        value = int(block.get(key, default))
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


def load_graph_config(
    project: str | Path,
    env: Mapping[str, str] | None = None,
    *,
    sources: Iterable[str] | None = None,
) -> GraphConfig:
    """This project's graph settings: `.env` for the connection, sources.json for the rest."""
    block = load_graph_block(project)
    values = dict(env or {})

    declared_sources = block.get("sources")
    from_config = (
        tuple(str(s).strip() for s in declared_sources if str(s).strip())
        if isinstance(declared_sources, list)
        else ()
    )
    chosen = tuple(str(s).strip() for s in (sources or ()) if str(s).strip())

    return GraphConfig(
        uri=(values.get("NEO4J_URI") or block.get("uri") or DEFAULT_URI).strip(),
        user=(values.get("NEO4J_USERNAME") or values.get("NEO4J_USER") or "neo4j").strip(),
        password=values.get("NEO4J_PASSWORD") or "",
        database=(
            values.get("NEO4J_DATABASE") or block.get("database") or DEFAULT_DATABASE
        ).strip(),
        batch_size=_int_from(block, "batch_size", DEFAULT_BATCH_SIZE, minimum=1),
        changelog_limit=_int_from(
            block, "changelog_limit", DEFAULT_CHANGELOG_LIMIT, minimum=1
        ),
        run_limit=_int_from(block, "run_limit", DEFAULT_RUN_LIMIT, minimum=1),
        delivery_limit=_int_from(
            block, "delivery_limit", DEFAULT_DELIVERY_LIMIT, minimum=1
        ),
        include=_include_from(block),
        sources=chosen or from_config,
        ontology=load_ontology(block),
        block=block,
    )
