"""The code graph's vocabulary: the same kind of closed sets the ALM graph has.

Labels and relationship types are the only fragments interpolated into Cypher, so
they are checked against these sets. They mirror what CodeGraphContext 0.6.13 writes:
its `schema_contract` sets plus the labels/relationships its writer creates without
declaring them there (checked by a test, so a CGC upgrade that adds one fails loudly
instead of silently falling back to `CodeSymbol`/`RELATED_TO`).
"""

from __future__ import annotations

from connection_sources.graph.schema import GraphSchema

__all__ = ["CODE_LABELS", "CODE_RELATIONSHIPS", "CODE_SCHEMA", "SYSTEM"]

SYSTEM = "code"

CODE_LABELS: frozenset[str] = frozenset(
    {
        # schema_contract.NODE_LABELS
        "Repository", "Directory", "File", "Function", "Class", "Trait", "Variable",
        "Interface", "Macro", "Struct", "Enum", "EnumMember", "Union", "Record",
        "Property", "Annotation", "Module", "Parameter", "MavenModule", "GradleModule",
        "ExternalLibrary", "Datasource", "DbTable", "DbColumn", "RedisKeyPattern",
        # written by CGC's writer but not declared in the contract
        "Mixin", "Extension", "Object", "ExternalClass",
        # fallback for anything else
        "CodeSymbol",
    }
)

CODE_RELATIONSHIPS: frozenset[str] = frozenset(
    {
        # schema_contract.RELATIONSHIP_TYPES
        "CONTAINS", "CALLS", "HEURISTIC_CALLS", "IMPORTS", "INHERITS", "HAS_PARAMETER",
        "INCLUDES", "IMPLEMENTS", "PARTIAL_OF", "PART_OF", "DECORATED_BY", "METACLASS",
        "COMPANION_OF", "EMBEDS", "INJECTS", "EXPOSES_ENDPOINT", "PROVIDES_BEAN",
        "MODULE_DEPENDS_ON", "USES_LIBRARY", "CHILD_MODULE", "FILE_BELONGS_TO", "READS",
        "WRITES", "MAPS_TO", "HAS_COLUMN", "STORED_IN",
        # written by CGC's writer but not declared in the contract
        "PREVIEWS", "BINDS",
        # computed by this package
        "SIMILAR_TO",
        # fallback for anything else
        "RELATED_TO",
    }
)

CODE_SCHEMA = GraphSchema(
    name="code",
    marker="CodeNode",
    labels=CODE_LABELS,
    relationships=CODE_RELATIONSHIPS,
    fallback_label="CodeSymbol",
    fallback_relationship="RELATED_TO",
    index_prefix="code_node",
    indexed_props=("name", "path", "qualified_name", "lang"),
)
