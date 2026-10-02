"""A CGC bundle mapped onto the code graph's own identities.

CGC's `_id` is backend-internal (a KùzuDB `{offset, table}` pair) and not stable
between runs, and its paths are absolute host paths. Neither can be a uid. Each node
is renamed the way the ALM graph names its nodes — `Namespace.uid(kind, natural_id)`,
so `code:<site>:<platform>-<scope>:<kind>:<id>` — with a natural id built from the
repository-relative path and CGC's own merge key (name, line, occurrence index).
Edges are then re-linked through a map from `_id` to uid.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from connection_sources.graph.model import GraphBatch, GraphEdge, GraphNode, Namespace

from .schema import CODE_SCHEMA

__all__ = ["MapReport", "map_bundle", "relative_path"]

# Relationship -> properties that identify one relationship among several between the
# same two nodes (two calls from f to g on different lines are two CALLS).
KEY_PROPS: dict[str, tuple[str, ...]] = {
    "CALLS": ("line_number", "full_call_name", "args_key"),
    "HEURISTIC_CALLS": ("line_number", "full_call_name", "args_key"),
    "IMPORTS": ("line_number", "imported_name"),
    "MAPS_TO": ("datastore", "line_number"),
    "READS": ("line_number",),
    "WRITES": ("line_number",),
}

# CGC properties that must not reach the project graph.
_DROP = {"_id", "_labels", "uid", "embedding"}
_PATH_PROPS = ("path", "repo_path", "pom_path")

# Labels whose natural id is the relative path alone.
_PATH_KINDS = {"File": "file", "Directory": "dir"}
# Labels whose natural id is a name (per project namespace, so no cross-project sharing).
_NAME_KINDS = {"Module": "module", "ExternalClass": "extclass", "Datasource": "ds", "GradleModule": "gradle"}


def relative_path(value: Any, root: str) -> Any:
    """`value` relative to the repository root, with `/` separators; `.` for the root."""
    if not isinstance(value, str) or not value:
        return value
    norm = value.replace("\\", "/").rstrip("/")
    base = root.replace("\\", "/").rstrip("/")
    if norm.lower() == base.lower():
        return "."
    if norm.lower().startswith(base.lower() + "/"):
        return norm[len(base) + 1 :]
    return norm


def _key(ref: Any) -> str:
    return json.dumps(ref, sort_keys=True)


def _symbol_id(props: Mapping[str, Any]) -> str:
    base = f"{props.get('path', '')}#{props.get('name', '')}@{props.get('line_number', '')}"
    occ = props.get("occurrence_index") or 0
    return f"{base}~{occ}" if occ else base


def _kind_and_id(label: str, props: Mapping[str, Any], raw: Mapping[str, Any]) -> tuple[str, str] | None:
    if label == "Repository":
        return "repo", "."
    if label in _PATH_KINDS:
        return _PATH_KINDS[label], str(props.get("path") or "")
    if label == "Parameter":
        return "param", f"{props.get('path', '')}#{props.get('function_line_number', '')}#{props.get('name', '')}"
    if label in ("MavenModule", "ExternalLibrary"):
        coords = f"{raw.get('group_id', '')}:{raw.get('artifact_id', '')}"
        return ("maven" if label == "MavenModule" else "lib"), coords
    if label in _NAME_KINDS:
        name = props.get("full_import_name") or props.get("name")
        return (_NAME_KINDS[label], str(name)) if name else None
    if label == "DbTable":
        return "table", str(raw.get("fqn") or props.get("name") or "")
    if label == "DbColumn":
        return "column", f"{raw.get('table_fqn') or raw.get('table', '')}.{props.get('name', '')}"
    if label == "RedisKeyPattern":
        return "redis", f"{raw.get('datasource', '')}:{raw.get('pattern') or props.get('name', '')}"
    if props.get("path") or props.get("line_number") is not None:
        return label.lower(), _symbol_id(props)
    name = props.get("name") or raw.get("uid")
    return (label.lower(), str(name)) if name else None


@dataclass
class MapReport:
    nodes: int = 0
    edges: int = 0
    dropped_nodes: int = 0
    dangling_edges: int = 0
    unknown_labels: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": self.nodes,
            "edges": self.edges,
            "dropped_nodes": self.dropped_nodes,
            "dangling_edges": self.dangling_edges,
            "unknown_labels": self.unknown_labels,
        }


def map_bundle(
    nodes: Iterable[Mapping[str, Any]],
    edges: Iterable[Mapping[str, Any]],
    *,
    namespace: Namespace,
    repo_root: str | Path,
) -> tuple[GraphBatch, MapReport, dict[str, str]]:
    """The bundle as a `GraphBatch`, a report, and the `_id` -> uid map."""
    root = str(repo_root)
    batch = GraphBatch()
    report = MapReport()
    id_to_uid: dict[str, str] = {}

    for raw in nodes:
        labels = list(raw.get("_labels") or [])
        label = labels[0] if labels else "CodeSymbol"
        props = {k: v for k, v in raw.items() if k not in _DROP}
        for name in _PATH_PROPS:
            if name in props:
                props[name] = relative_path(props[name], root)
        kind_id = _kind_and_id(label, props, raw)
        if kind_id is None or not kind_id[1]:
            report.dropped_nodes += 1
            continue
        kind, natural = kind_id
        if label not in CODE_SCHEMA.labels:
            report.unknown_labels[label] = report.unknown_labels.get(label, 0) + 1
            props["cgc_label"] = label
        if label == "Repository" and "commit_hash" in props:
            props["head_sha"] = props["commit_hash"]
        props["kind"] = kind
        uid = namespace.uid(kind, natural)
        id_to_uid[_key(raw.get("_id"))] = uid
        batch.add_node(GraphNode(uid, (label,), props, schema=CODE_SCHEMA))

    for raw in edges:
        start = id_to_uid.get(_key(raw.get("from")))
        end = id_to_uid.get(_key(raw.get("to")))
        if not start or not end:
            report.dangling_edges += 1
            continue
        rel = str(raw.get("type") or "RELATED_TO")
        props = dict(raw.get("properties") or {})
        batch.add_edge(
            GraphEdge(rel, start, end, props, key_props=KEY_PROPS.get(rel, ()), schema=CODE_SCHEMA)
        )

    report.nodes = len(batch.nodes)
    report.edges = len(batch.edges)
    return batch, report, id_to_uid


def posix(path: str) -> str:
    return str(PurePosixPath(path))
