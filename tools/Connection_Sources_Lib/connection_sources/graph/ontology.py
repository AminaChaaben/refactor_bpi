"""The vocabulary of the graph: which labels exist, which relationships exist, and
how one tracker's own names map onto them.

This is `sync.taxonomy` taken one level further. Taxonomy answers "which state.json
bucket is this?" with four coarse buckets; the graph needs the finer distinction
between a Test, a Test Set, a Test Plan and a Test Execution, because they are four
different nodes joined by three different relationships.

Everything that varies per instance lives in `ontology_defaults.json` next to this
file and is merged over by `sources.json` — issue type names differ by site and by
language, and link type names are renamed freely by administrators. None of that
belongs in Python.

`LABELS` and `RELATIONSHIPS` are closed sets on purpose. Neo4j cannot parameterise a
label or a relationship type, so those two fragments are the only part of a
statement built by string interpolation — and interpolating anything a tracker
returned would be an injection. Every writer therefore checks membership here
first, and an unmapped name falls back to a generic member of the set rather than
travelling into a query.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .schema import GraphSchema

__all__ = [
    "ALM_SCHEMA",
    "DOMAIN",
    "LABELS",
    "MARKER",
    "RELATIONSHIPS",
    "Ontology",
    "load_ontology",
    "safe_label",
    "safe_relationship",
]

_DEFAULTS_PATH = Path(__file__).with_name("ontology_defaults.json")

# The label every node also carries. One uniqueness constraint on (:AlmNode {uid})
# then covers the whole graph, which is what keeps MERGE index-backed on Neo4j
# Community, where composite node keys are unavailable.
MARKER = "AlmNode"

# Which visual/analytical domain a label belongs to. Not used in Cypher — used by
# `graph doctor` and the report so counts are grouped the way people think.
DOMAIN: dict[str, str] = {
    # requirement side — Jira issues
    "Issue": "requirement",
    "Epic": "requirement",
    "Feature": "requirement",
    "Story": "requirement",
    "Task": "requirement",
    "SubTask": "requirement",
    "Bug": "requirement",
    "Requirement": "requirement",
    # test assets — what a test *is*
    "Test": "test",
    "TestStep": "test",
    "Precondition": "test",
    "TestSet": "test",
    "TestType": "test",
    "RepositoryFolder": "test",
    # execution — what a test *did*
    "TestPlan": "execution",
    "TestExecution": "execution",
    "TestRun": "execution",
    "TestRunStep": "execution",
    "TestEnvironment": "execution",
    "TestRunStatus": "execution",
    "Evidence": "execution",
    # shared vocabulary and structure
    "Project": "shared",
    "IssueType": "shared",
    "Status": "shared",
    "StatusCategory": "shared",
    "Priority": "shared",
    "Resolution": "shared",
    "User": "shared",
    "Label": "shared",
    "Component": "shared",
    "Version": "shared",
    "Sprint": "shared",
    "Board": "shared",
    "Comment": "shared",
    "Attachment": "shared",
    "Worklog": "shared",
    "ChangeEvent": "shared",
    "FieldChange": "shared",
    "RemoteLink": "shared",
    "Team": "shared",
    "Milestone": "shared",
    # delivery context — where the work was actually built and shipped. Carried so a
    # requirement can be traced to the pipeline that deployed it, never so the graph
    # becomes a model of the source code: no files, no commits, no diffs.
    # `GitRepository`, not `Repository`: the code graph in the same database uses
    # `Repository` for an indexed codebase.
    "GitRepository": "delivery",
    "Pipeline": "delivery",
    "MergeRequest": "delivery",
    # written context — the specifications, decisions and runbooks a requirement was
    # written from. Same restraint as `delivery`: the graph records that a page
    # exists, who wrote it, where it sits and which issues it names — not the prose.
    # A page's text belongs in the wiki; what belongs here is the fact that this
    # story and that specification are about each other.
    "Space": "knowledge",
    "Page": "knowledge",
}

LABELS: frozenset[str] = frozenset(DOMAIN) | {MARKER}

RELATIONSHIPS: frozenset[str] = frozenset(
    {
        # structure
        "IN_PROJECT",
        "HAS_TYPE",
        "HAS_STATUS",
        "IN_CATEGORY",
        "HAS_PRIORITY",
        "RESOLVED_AS",
        "CHILD_OF",
        # people
        "ASSIGNED_TO",
        "REPORTED_BY",
        "CREATED_BY",
        "WATCHED_BY",
        "AUTHORED",
        "UPLOADED",
        "LOGGED",
        "MADE",
        "EXECUTED_BY",
        # classification
        "HAS_LABEL",
        "HAS_COMPONENT",
        "FIXED_IN",
        "AFFECTS",
        # agile
        "IN_SPRINT",
        "ON_BOARD",
        "FOR_PROJECT",
        # issue links
        "LINKED_TO",
        "BLOCKS",
        "CLONES",
        "DUPLICATES",
        "RELATES_TO",
        "CAUSES",
        "IMPLEMENTS",
        # discussion and audit
        "HAS_COMMENT",
        "HAS_ATTACHMENT",
        "HAS_WORKLOG",
        "HAS_REMOTE_LINK",
        "CHANGED",
        "ON",
        "BY",
        "NEXT",
        # test assets
        "COVERS",
        "HAS_STEP",
        "CALLS",
        "REQUIRES",
        "HAS_TEST_TYPE",
        "CONTAINS",
        "IN_FOLDER",
        # execution
        "PLANS",
        "HAS_EXECUTION",
        "HAS_RUN",
        "OF_TEST",
        "IN_EXECUTION",
        "HAS_RESULT",
        "HAS_STEP_RESULT",
        "OF_STEP",
        "FOUND_DEFECT",
        "IN_ENVIRONMENT",
        "HAS_EVIDENCE",
        "LATEST_RUN",
        # organisation
        "MEMBER_OF",
        "OWNED_BY",
        "IN_MILESTONE",
        # delivery context
        "HAS_PIPELINE",
        "HAS_MERGE_REQUEST",
        "TRIGGERED_BY",
        "MENTIONS",
        # written context. Page hierarchy reuses CHILD_OF and authorship reuses
        # AUTHORED rather than inventing wiki-specific twins: a question like "what
        # is the parent of this" should be one relationship type whether the thing
        # is an epic or a page, or the graph has two vocabularies pretending to be
        # one. IN_SPACE is genuinely new -- a space is not a project.
        "IN_SPACE",
    }
)

# Where an unknown name lands. Never dropped: an edge whose type nobody mapped is
# still a real edge, and losing it would make the graph quietly incomplete in
# exactly the way `taxonomy.bucket_for` refuses to be.
FALLBACK_LABEL = "Issue"
FALLBACK_RELATIONSHIP = "LINKED_TO"

# The ALM graph as the loader sees it. The default everywhere a schema is accepted, so
# every existing caller keeps writing exactly the statements it always did.
ALM_SCHEMA = GraphSchema(
    name="alm",
    marker=MARKER,
    labels=LABELS,
    relationships=RELATIONSHIPS,
    fallback_label=FALLBACK_LABEL,
    fallback_relationship=FALLBACK_RELATIONSHIP,
    index_prefix="alm_node",
    indexed_props=("key", "updated", "bucket"),
    # What test design searches when it looks for stories related to the one it is
    # designing: `acceptance_criteria` lands on the issue as a declared detail field.
    fulltext_props=("title", "description", "acceptance_criteria"),
    # What test design searches when it looks for tests, anywhere in the project, that
    # exercise the same subject as the story it is designing. `steps_text` is the test's
    # steps (action, data, expected result) flattened by the Xray extractor, so a manual
    # test is found by what it does and not only by its title.
    # The Jira-only path has no `steps_text`: a site without Xray keeps the steps in custom
    # fields the extractor lands as `test_steps`, `manual_steps` and `cucumber_script`.
    test_fulltext_label="Test",
    test_fulltext_props=(
        "title", "gherkin", "unstructured", "steps_text",
        "test_steps", "manual_steps", "cucumber_script",
    ),
)


def safe_label(name: str) -> str:
    """A label that is safe to interpolate into Cypher, or the fallback."""
    return ALM_SCHEMA.safe_label(name)


def safe_relationship(name: str) -> str:
    """A relationship type that is safe to interpolate into Cypher, or the fallback."""
    return ALM_SCHEMA.safe_relationship(name)


class Ontology:
    """The per-project mapping tables, defaults merged with any overrides."""

    __slots__ = (
        "issue_type_labels",
        "link_types",
        "run_status_aliases",
        "coverage_statuses",
        "status_categories",
    )

    def __init__(
        self,
        *,
        issue_type_labels: Mapping[str, Iterable[str]],
        link_types: Mapping[str, Mapping[str, Any]],
        run_status_aliases: Mapping[str, str],
        coverage_statuses: Iterable[str],
        status_categories: Mapping[str, str] | None = None,
    ) -> None:
        self.issue_type_labels = {
            str(name).strip().lower(): tuple(safe_label(str(l)) for l in labels)
            for name, labels in issue_type_labels.items()
            if str(name).strip()
        }
        self.link_types = {
            str(name).strip().lower(): {
                "rel": safe_relationship(str(spec.get("rel", FALLBACK_RELATIONSHIP))),
                "reverse": bool(spec.get("reverse", False)),
            }
            for name, spec in link_types.items()
            if isinstance(spec, Mapping) and str(name).strip()
        }
        self.run_status_aliases = {
            str(k).strip().lower(): str(v).strip().upper()
            for k, v in run_status_aliases.items()
            if str(k).strip() and str(v).strip()
        }
        self.coverage_statuses = tuple(str(s) for s in coverage_statuses)
        self.status_categories = {
            str(k).strip().lower(): str(v).strip()
            for k, v in (status_categories or {}).items()
            if str(k).strip() and str(v).strip()
        }

    def labels_for_type(self, type_name: str) -> tuple[str, ...]:
        """Every label an issue of this tracker type carries, `Issue` included.

        An unmapped type still becomes an `:Issue` rather than being dropped or
        given a label invented from its name — a type nobody has mapped yet is a
        real issue whose edges we want, and a label built from tracker text is the
        one string that must never reach a query.
        """
        extra = self.issue_type_labels.get((type_name or "").strip().lower(), ())
        return ("Issue",) + tuple(l for l in extra if l != "Issue")

    def link_rel(self, link_type_name: str) -> tuple[str, bool]:
        """(relationship type, reverse?) for a Jira issue-link type name.

        `reverse` says the outward direction Jira reports runs opposite to the
        direction the graph stores, so the extractor can normalise both halves of a
        link — Jira reports the same link twice, once from each end — onto one edge.
        """
        spec = self.link_types.get((link_type_name or "").strip().lower())
        if not spec:
            return FALLBACK_RELATIONSHIP, False
        return spec["rel"], spec["reverse"]

    def run_status(self, raw: str) -> str:
        """A tracker's own execution-result wording, normalised.

        Unknown statuses are upper-cased and kept rather than forced into one of the
        five standard ones: sites define their own, and a run reported as
        `PARTIALLY_BLOCKED` is more useful stored as itself than flattened to FAIL.
        """
        text = (raw or "").strip()
        if not text:
            return "TODO"
        return self.run_status_aliases.get(text.lower(), text.upper())

    def status_category(self, raw: str) -> str:
        """A tracker's own status/state name, rolled up to Jira's three categories.

        `"To Do"` / `"In Progress"` / `"Done"` is the vocabulary every cross-system
        query (`workload`, `uncovered`) filters on, because it is the one category
        set Jira already hands over verbatim. An unrecognised state is left "" rather
        than guessed at: a query that treats "" as still open is safer wrong than one
        that silently counts a custom state as finished work.
        """
        return self.status_categories.get((raw or "").strip().lower(), "")


def _load_defaults() -> dict[str, Any]:
    return json.loads(_DEFAULTS_PATH.read_text(encoding="utf-8"))


def load_ontology(graph_config: Mapping[str, Any] | None = None) -> Ontology:
    """The default vocabulary, with this project's `graph.ontology` merged over it.

    Merged per key rather than replaced, exactly as `taxonomy.load_buckets` does, so
    an override naming one renamed link type does not silently discard every other
    mapping the defaults provide.
    """
    defaults = _load_defaults()
    overrides = (graph_config or {}).get("ontology") or {}
    if not isinstance(overrides, Mapping):
        overrides = {}

    def merged(key: str) -> dict[str, Any]:
        base = dict(defaults.get(key) or {})
        extra = overrides.get(key)
        if isinstance(extra, Mapping):
            base.update(extra)
        return base

    statuses = overrides.get("coverage_statuses")
    return Ontology(
        issue_type_labels=merged("issue_type_labels"),
        link_types=merged("link_types"),
        run_status_aliases=merged("run_status_aliases"),
        coverage_statuses=(
            statuses if isinstance(statuses, list) else defaults.get("coverage_statuses") or ()
        ),
        status_categories=merged("status_categories"),
    )
