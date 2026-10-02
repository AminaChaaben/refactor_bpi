"""Per-language tree-sitter node types the metrics walker needs.

One table per language family so the walker itself stays language-agnostic: which
nodes are functions, decisions, loops, calls, member accesses, allocations and
linear scans. Node type names are those of tree-sitter-language-pack's grammars.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["LANGS", "Lang", "lang_for_path"]


@dataclass(frozen=True)
class Lang:
    name: str
    grammar: str
    # Nodes that are functions in their own right (CGC gives them a Function node).
    function_types: frozenset[str]
    # Anonymous functions that belong to the enclosing function (lambdas, callbacks).
    inline_function_types: frozenset[str]
    if_types: frozenset[str]
    else_types: frozenset[str] = frozenset()
    elif_types: frozenset[str] = frozenset()
    loop_types: frozenset[str] = frozenset()
    comprehension_types: frozenset[str] = frozenset()
    switch_types: frozenset[str] = frozenset()
    case_types: frozenset[str] = frozenset()
    default_case_types: frozenset[str] = frozenset()
    catch_types: frozenset[str] = frozenset()
    ternary_types: frozenset[str] = frozenset()
    boolean_types: frozenset[str] = frozenset()
    boolean_ops: frozenset[str] = frozenset()
    comprehension_clause_types: frozenset[str] = frozenset()
    call_types: frozenset[str] = frozenset()
    new_types: frozenset[str] = frozenset()
    member_types: frozenset[str] = frozenset()
    alloc_types: frozenset[str] = frozenset()
    # Method names that iterate their receiver, so a callback argument is a loop body.
    iterating_methods: frozenset[str] = frozenset()
    scan_methods: frozenset[str] = frozenset()
    return_types: frozenset[str] = frozenset()
    jump_types: frozenset[str] = frozenset()
    class_types: frozenset[str] = frozenset()
    self_names: frozenset[str] = frozenset()
    param_list_types: frozenset[str] = frozenset()
    extensions: tuple[str, ...] = field(default_factory=tuple)


_JS_COMMON = dict(
    function_types=frozenset(
        {"function_declaration", "generator_function_declaration", "method_definition",
         "function_expression", "function", "generator_function", "arrow_function"}
    ),
    inline_function_types=frozenset(),  # decided by position: see walker (callbacks)
    if_types=frozenset({"if_statement"}),
    else_types=frozenset({"else_clause"}),
    loop_types=frozenset({"for_statement", "for_in_statement", "while_statement", "do_statement"}),
    switch_types=frozenset({"switch_statement"}),
    case_types=frozenset({"switch_case"}),
    default_case_types=frozenset({"switch_default"}),
    catch_types=frozenset({"catch_clause"}),
    ternary_types=frozenset({"ternary_expression"}),
    boolean_types=frozenset({"binary_expression"}),
    boolean_ops=frozenset({"&&", "||", "??"}),
    call_types=frozenset({"call_expression"}),
    new_types=frozenset({"new_expression"}),
    member_types=frozenset({"member_expression"}),
    alloc_types=frozenset({"new_expression", "array", "object"}),
    iterating_methods=frozenset({"forEach", "map", "filter", "reduce", "reduceRight", "some", "every", "flatMap", "find", "findIndex"}),
    scan_methods=frozenset({"includes", "indexOf", "lastIndexOf", "find", "findIndex", "some", "every", "filter"}),
    return_types=frozenset({"return_statement", "throw_statement"}),
    jump_types=frozenset({"break_statement", "continue_statement"}),
    class_types=frozenset({"class_declaration", "class", "abstract_class_declaration"}),
    self_names=frozenset({"this"}),
    param_list_types=frozenset({"formal_parameters"}),
)

LANGS: dict[str, Lang] = {
    "java": Lang(
        name="java",
        grammar="java",
        function_types=frozenset({"method_declaration", "constructor_declaration", "compact_constructor_declaration"}),
        inline_function_types=frozenset({"lambda_expression"}),
        if_types=frozenset({"if_statement"}),
        loop_types=frozenset({"for_statement", "enhanced_for_statement", "while_statement", "do_statement"}),
        switch_types=frozenset({"switch_expression", "switch_statement"}),
        case_types=frozenset({"switch_label"}),
        catch_types=frozenset({"catch_clause"}),
        ternary_types=frozenset({"ternary_expression"}),
        boolean_types=frozenset({"binary_expression"}),
        boolean_ops=frozenset({"&&", "||"}),
        call_types=frozenset({"method_invocation"}),
        new_types=frozenset({"object_creation_expression"}),
        member_types=frozenset({"field_access", "method_invocation"}),
        alloc_types=frozenset({"object_creation_expression", "array_creation_expression"}),
        iterating_methods=frozenset({"forEach", "map", "filter", "flatMap", "reduce", "anyMatch", "allMatch", "noneMatch", "peek", "removeIf"}),
        scan_methods=frozenset({"contains", "indexOf", "lastIndexOf", "remove", "containsValue", "anyMatch", "allMatch", "noneMatch"}),
        return_types=frozenset({"return_statement", "throw_statement"}),
        jump_types=frozenset({"break_statement", "continue_statement"}),
        class_types=frozenset({"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}),
        self_names=frozenset({"this"}),
        param_list_types=frozenset({"formal_parameters"}),
        extensions=(".java",),
    ),
    "python": Lang(
        name="python",
        grammar="python",
        function_types=frozenset({"function_definition"}),
        inline_function_types=frozenset({"lambda"}),
        if_types=frozenset({"if_statement"}),
        else_types=frozenset({"else_clause"}),
        elif_types=frozenset({"elif_clause"}),
        loop_types=frozenset({"for_statement", "while_statement"}),
        comprehension_types=frozenset({"list_comprehension", "set_comprehension", "dictionary_comprehension", "generator_expression"}),
        switch_types=frozenset({"match_statement"}),
        case_types=frozenset({"case_clause"}),
        catch_types=frozenset({"except_clause"}),
        ternary_types=frozenset({"conditional_expression"}),
        boolean_types=frozenset({"boolean_operator"}),
        boolean_ops=frozenset({"and", "or"}),
        comprehension_clause_types=frozenset({"for_in_clause", "if_clause"}),
        call_types=frozenset({"call"}),
        member_types=frozenset({"attribute"}),
        alloc_types=frozenset({"list", "dictionary", "set", "list_comprehension", "dictionary_comprehension", "set_comprehension"}),
        scan_methods=frozenset({"index", "count", "remove"}),
        return_types=frozenset({"return_statement", "raise_statement"}),
        jump_types=frozenset(),
        class_types=frozenset({"class_definition"}),
        self_names=frozenset({"self", "cls"}),
        param_list_types=frozenset({"parameters"}),
        extensions=(".py",),
    ),
    "javascript": Lang(name="javascript", grammar="javascript", extensions=(".js", ".jsx", ".mjs", ".cjs"), **_JS_COMMON),
    "typescript": Lang(name="typescript", grammar="typescript", extensions=(".ts",), **_JS_COMMON),
    "tsx": Lang(name="tsx", grammar="tsx", extensions=(".tsx",), **_JS_COMMON),
}

_BY_EXT = {ext: lang for lang in LANGS.values() for ext in lang.extensions}


def lang_for_path(path: str) -> Lang | None:
    lower = path.lower()
    for ext, lang in _BY_EXT.items():
        if lower.endswith(ext):
            return lang
    return None
