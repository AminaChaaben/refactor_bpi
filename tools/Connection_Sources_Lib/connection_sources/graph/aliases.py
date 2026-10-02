"""Naming the graph's vocabulary in the client's own words, and remembering the change.

This is the answer to one specific problem: `ontology_defaults.json` ships a list of
names for each concept — a Story is also "User Story" or "Product Backlog Item"; a
Test is also "Test Case" or "Xray Test" — exactly the way `taxonomy_defaults.json`
already lists names for a bucket. But a list shipped with the package can never be
exhaustive. The next client's Jira might call a story a "Besoin", or a test a "Cas de
Test", and the graph must not silently file that under the fallback `:Issue` label
forever.

`load_ontology` (in `ontology.py`) already merges `graph.ontology.*` from
`sources.json` over the shipped defaults — the mechanism to *use* an override already
existed. What was missing is a way to *add* one without hand-editing JSON: this module
is that — read the current mapping, add or remove one name, write it back — so the
answer to "the site calls it something else" is one CLI call, not a manual edit deep
in a config file.

Everything here edits the `graph` block of `sources.json` and nothing else, using the
same read-modify-write-atomically pattern `sync.state` uses for `state.json`: read the
whole file, change one key, write the whole file back through a temp file and
`os.replace`, so a crash mid-write can never leave `sources.json` — the file every
other command in this package depends on — truncated.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ..errors import SourcesConfigError
from ..health import sources_path
from ..sync.store import atomic_write_json, file_lock, read_json
from .config import load_graph_block
from .ontology import FALLBACK_RELATIONSHIP, load_ontology, safe_label, safe_relationship

__all__ = [
    "add_issue_type_alias",
    "add_link_type_alias",
    "list_issue_type_aliases",
    "list_link_type_aliases",
    "remove_issue_type_alias",
    "remove_link_type_alias",
]


def _read_sources(project: str | Path) -> tuple[Path, dict[str, Any]]:
    path = sources_path(project)
    raw = read_json(path, default=None)
    if not isinstance(raw, dict):
        raise SourcesConfigError(
            f"sources config not found or invalid: {path}",
            remediation="run this from a project that already has Config_Connection_Sources/sources.json",
        )
    return path, raw


@contextmanager
def _edit_sources(project: str | Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Hold `sources.json`'s advisory lock for one read-modify-write.

    Sync never touches this file — only humans running these commands do — but two
    `graph-alias-add` calls racing (or one racing a hand-edit in a text editor) would
    otherwise be a lost-update bug, so this reuses the same exclusive-create lock
    `sync.store` uses for `state.json` rather than trusting that collision away.
    """
    path, raw = _read_sources(project)
    lock_path = path.with_name(f".{path.name}.lock")
    with file_lock(lock_path):
        # Re-read inside the lock: another writer may have landed between our
        # first read (used only to fail fast if the file is missing) and here.
        raw = read_json(path, default=raw)
        yield path, raw


def _graph_block(raw: dict[str, Any]) -> dict[str, Any]:
    block = raw.get("graph")
    if not isinstance(block, dict):
        block = {}
        raw["graph"] = block
    ontology = block.get("ontology")
    if not isinstance(ontology, dict):
        ontology = {}
        block["ontology"] = ontology
    return ontology


def list_issue_type_aliases(project: str | Path) -> dict[str, list[str]]:
    """Every issue-type name known for each label — shipped defaults plus this
    project's own additions — inverted to `{label: [names, ...]}` because that is
    the direction a human asks the question in: "what do we call a Test here?"
    """
    ontology = load_ontology(load_graph_block(project))
    by_label: dict[str, list[str]] = {}
    for name, labels in ontology.issue_type_labels.items():
        for label in labels:
            by_label.setdefault(label, []).append(name)
    return {label: sorted(names) for label, names in sorted(by_label.items())}


def add_issue_type_alias(project: str | Path, type_name: str, label: str) -> dict[str, Any]:
    """Teach the graph that this tracker's own name for an issue type maps to `label`.

    `label` must already be one of the graph's known labels — see `ontology.LABELS`
    — because a label invented from tracker text is exactly the string this whole
    package refuses to let near a Cypher statement. Naming an *unmapped* type is not
    a failure: it becomes a plain `:Issue`, which is why `graph doctor` reports
    unmapped type names it saw in the live data, so this command exists to close
    that gap rather than to be required up front.
    """
    checked_label = safe_label(label)
    if checked_label != label:
        raise SourcesConfigError(
            f"{label!r} is not a known graph label",
            remediation="see `alm-conn graph-labels` for the full list",
        )
    key = type_name.strip().lower()
    if not key:
        raise SourcesConfigError("issue type name cannot be empty")

    with _edit_sources(project) as (path, raw):
        ontology = _graph_block(raw)
        mapping = ontology.setdefault("issue_type_labels", {})
        if not isinstance(mapping, dict):
            mapping = {}
            ontology["issue_type_labels"] = mapping
        existing = mapping.get(key)
        labels = list(existing) if isinstance(existing, list) else []
        if label not in labels:
            labels.append(label)
        mapping[key] = labels
        atomic_write_json(path, raw)
    return {"type_name": type_name, "label": label, "labels_for_type": labels}


def remove_issue_type_alias(project: str | Path, type_name: str) -> dict[str, Any]:
    """Drop a project-level override, falling back to the shipped defaults for it.

    Only ever removes from the project's own override block. A name that came from
    `ontology_defaults.json` cannot be removed this way — the shipped list is the
    part of the vocabulary the package maintains, and a project should not be able
    to make `git blame` on it point somewhere misleading.
    """
    key = type_name.strip().lower()
    with _edit_sources(project) as (path, raw):
        ontology = _graph_block(raw)
        mapping = ontology.get("issue_type_labels")
        removed = isinstance(mapping, dict) and mapping.pop(key, None) is not None
        if removed:
            atomic_write_json(path, raw)
    return {"type_name": type_name, "removed": removed}


def list_link_type_aliases(project: str | Path) -> dict[str, dict[str, Any]]:
    """Every Jira issue-link type name this project maps, and what it becomes."""
    ontology = load_ontology(load_graph_block(project))
    return {
        name: {"relationship": spec["rel"], "reverse": spec["reverse"]}
        for name, spec in sorted(ontology.link_types.items())
    }


def add_link_type_alias(
    project: str | Path, link_type_name: str, relationship: str, *, reverse: bool = False
) -> dict[str, Any]:
    """Map a Jira issue-link type name (as Jira names it, e.g. "is validated by")
    onto a graph relationship, same shape as `issue_type_labels`."""
    checked = safe_relationship(relationship)
    if checked != relationship:
        raise SourcesConfigError(
            f"{relationship!r} is not a known graph relationship type",
            remediation="see `alm-conn graph-relationships` for the full list",
        )
    key = link_type_name.strip().lower()
    if not key:
        raise SourcesConfigError("link type name cannot be empty")

    with _edit_sources(project) as (path, raw):
        ontology = _graph_block(raw)
        mapping = ontology.setdefault("link_types", {})
        if not isinstance(mapping, dict):
            mapping = {}
            ontology["link_types"] = mapping
        mapping[key] = {"rel": relationship, "reverse": reverse}
        atomic_write_json(path, raw)
    return {"link_type": link_type_name, "relationship": relationship, "reverse": reverse}


def remove_link_type_alias(project: str | Path, link_type_name: str) -> dict[str, Any]:
    key = link_type_name.strip().lower()
    with _edit_sources(project) as (path, raw):
        ontology = _graph_block(raw)
        mapping = ontology.get("link_types")
        removed = isinstance(mapping, dict) and mapping.pop(key, None) is not None
        if removed:
            atomic_write_json(path, raw)
    return {
        "link_type": link_type_name,
        "removed": removed,
        "falls_back_to": FALLBACK_RELATIONSHIP if removed else None,
    }
