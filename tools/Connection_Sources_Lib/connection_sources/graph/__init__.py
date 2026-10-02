"""A Neo4j traceability graph built from Jira, Xray, Azure DevOps and GitLab — every
issue, its people, its hierarchy, its links, its sprints, its comments, its history,
its tests, steps, preconditions, runs, evidence and defects, and the pipelines and
merge requests that delivered them.

Every tracker lands in one shared vocabulary, so a question asked of the graph is the
same question and the same Cypher whichever system the project actually lives in.

The package is layered so each layer can be tested without the ones above it:

  ontology      -- the vocabulary: which labels and relationships exist, and how a
                   tracker's own type/link names map onto them (`aliases` edits this).
  model         -- GraphNode / GraphEdge / GraphBatch and the uid naming scheme.
  extract_jira, extract_xray, extract_azure, extract_gitlab
                -- pure functions, tracker payload in, GraphBatch out.
  xray          -- which Xray a site has, and the clients for Cloud and Server/DC.
  xray_server   -- Server/DC REST rewritten into the Cloud shapes `extract_xray` reads.
  cypher        -- MERGE statements built as text + parameters, nothing else.
  loader        -- sends a batch through a `Runner` (a real session, or a test double).
  derive        -- coverage, latest-run and hierarchy-depth, computed after loading.
  queries       -- the named, read-only questions the graph answers.
  project_state -- one batch projected into the project's whole state as JSON.
  runner        -- orchestrates all of the above into one build.
"""

from __future__ import annotations

from .aliases import (
    add_issue_type_alias,
    add_link_type_alias,
    list_issue_type_aliases,
    list_link_type_aliases,
    remove_issue_type_alias,
    remove_link_type_alias,
)
from .config import GraphConfig, load_graph_config
from .derive import coverage_status
from .extract_jira import SYSTEM
from .loader import open_session, session_runner
from .model import GraphBatch, GraphEdge, GraphNode, Namespace, UNSCOPED, site_of, uid
from .ontology import LABELS, RELATIONSHIPS, Ontology, load_ontology
from .project_state import build_state, state_of
from .queries import QUERIES, build as build_query, catalogue as query_catalogue
from .runner import BuildResult, run_build
from .xray import detect_tier

__all__ = [
    "LABELS",
    "QUERIES",
    "RELATIONSHIPS",
    "SYSTEM",
    "BuildResult",
    "GraphBatch",
    "GraphConfig",
    "GraphEdge",
    "GraphNode",
    "Namespace",
    "Ontology",
    "UNSCOPED",
    "add_issue_type_alias",
    "add_link_type_alias",
    "build_query",
    "build_state",
    "coverage_status",
    "detect_tier",
    "list_issue_type_aliases",
    "list_link_type_aliases",
    "load_graph_config",
    "load_ontology",
    "open_session",
    "query_catalogue",
    "remove_issue_type_alias",
    "remove_link_type_alias",
    "run_build",
    "session_runner",
    "site_of",
    "state_of",
    "uid",
]
