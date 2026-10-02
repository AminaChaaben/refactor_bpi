"""What makes one graph distinct from another sharing the same Neo4j database.

Each project's Neo4j holds two graphs side by side: the ALM traceability graph and
the code graph. They are written by the same loader, so everything that tells them
apart lives here: the marker label every node of the graph carries, the closed label
and relationship sets (the only fragments ever interpolated into Cypher), where an
unknown name falls back to, and the names of the graph's own constraint and indexes.

Neo4j Community has one database and no per-label security, so the marker label plus
the uid prefix is the entire separation. Constraint and index names are global per
database, hence the per-graph prefix on each of them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["GraphSchema", "SEEN_AT", "SEEN_VERSION"]

# The generation counter is written onto everything a run touches, so a later sweep
# can tell what this run confirmed from what it merely left behind.
SEEN_VERSION = "last_seen_version"
SEEN_AT = "last_seen_at"

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class GraphSchema:
    """One graph's vocabulary and identity inside a shared database."""

    name: str
    marker: str
    labels: frozenset[str]
    relationships: frozenset[str]
    fallback_label: str
    fallback_relationship: str
    index_prefix: str
    indexed_props: tuple[str, ...] = ()
    # Text properties searched together through one full-text (Lucene) index, so a
    # consumer can ask "which nodes talk about this" without an exact key.
    fulltext_props: tuple[str, ...] = ()
    # A second full-text index, on the nodes of one label only, over what a TEST says it
    # exercises (its title, its Gherkin, its steps flattened to text). It exists because
    # the first index searches requirements: a test written for another story can only be
    # found by subject through the words its own steps use.
    test_fulltext_label: str = ""
    test_fulltext_props: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        names = [
            self.marker,
            self.index_prefix,
            *self.labels,
            *self.relationships,
            *self.indexed_props,
            *self.fulltext_props,
            *self.test_fulltext_props,
            *([self.test_fulltext_label] if self.test_fulltext_label else []),
        ]
        bad = sorted(n for n in names if not _IDENT.match(n))
        if bad:
            raise ValueError(f"graph schema {self.name!r}: unsafe identifiers {bad}")
        if self.fallback_label not in self.labels:
            raise ValueError(f"graph schema {self.name!r}: fallback label not in labels")
        if self.fallback_relationship not in self.relationships:
            raise ValueError(f"graph schema {self.name!r}: fallback relationship not in relationships")

    def safe_label(self, name: str) -> str:
        """A label that is safe to interpolate into Cypher, or the fallback."""
        return name if name in self.labels else self.fallback_label

    def safe_relationship(self, name: str) -> str:
        """A relationship type that is safe to interpolate into Cypher, or the fallback."""
        return name if name in self.relationships else self.fallback_relationship

    def constraint_statements(self) -> list[str]:
        """The uid constraint plus the graph's indexes, all `IF NOT EXISTS`."""
        prefix, marker = self.index_prefix, self.marker
        statements = [
            f"CREATE CONSTRAINT {prefix}_uid IF NOT EXISTS "
            f"FOR (n:{marker}) REQUIRE n.uid IS UNIQUE"
        ]
        statements += [
            f"CREATE INDEX {prefix}_{prop} IF NOT EXISTS FOR (n:{marker}) ON (n.{prop})"
            for prop in self.indexed_props
        ]
        statements.append(
            f"CREATE INDEX {prefix}_seen IF NOT EXISTS FOR (n:{marker}) ON (n.{SEEN_VERSION})"
        )
        if self.fulltext_props:
            fields = ", ".join(f"n.{prop}" for prop in self.fulltext_props)
            statements.append(
                f"CREATE FULLTEXT INDEX {self.fulltext_index} IF NOT EXISTS "
                f"FOR (n:{marker}) ON EACH [{fields}]"
            )
        if self.test_fulltext_props and self.test_fulltext_label:
            fields = ", ".join(f"n.{prop}" for prop in self.test_fulltext_props)
            statements.append(
                f"CREATE FULLTEXT INDEX {self.test_fulltext_index} IF NOT EXISTS "
                f"FOR (n:{self.test_fulltext_label}) ON EACH [{fields}]"
            )
        return statements

    @property
    def fulltext_index(self) -> str:
        """The full-text index name, for `db.index.fulltext.queryNodes`."""
        return f"{self.index_prefix}_text"

    @property
    def test_fulltext_index(self) -> str:
        """The tests' full-text index name (only created when `test_fulltext_props` is set)."""
        return f"{self.index_prefix}_test_text"
