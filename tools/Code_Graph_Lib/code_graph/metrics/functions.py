"""Per-function metrics from one tree-sitter walk.

Definitions (applied the same way in every language):
- cyclomatic: 1 + decision points (if/elif, loops, catch, ternary, non-default case,
  each `&&`/`||`/`??`/`and`/`or`, comprehension for/if clauses).
- cognitive: SonarSource-style. +1 for if/else-if/else/switch/loop/catch/ternary, +1
  per run of the same boolean operator, +1 per labelled jump and per recursive call;
  if/switch/loop/catch/ternary also add the current nesting level. Lambdas/callbacks
  nest.
- loop_depth: deepest loop nesting (comprehensions and iterating-call callbacks count).
- linear_scan_in_loop / alloc_in_loop: scan calls / allocations under a loop.
- max_access_depth: longest `a.b.c.d` member chain.
- recursive / recursion_in_loop / unguarded_recursion: a self-call; one under a loop; a
  self-call with no conditional ancestor and no earlier `if` that returns/throws.

Nested named functions are their own CGC functions and are skipped; lambdas and
callbacks passed as arguments belong to the enclosing function.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from typing import Any

from .lang import Lang

__all__ = ["FnResult", "analyze_source"]

sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))

_JS = {"javascript", "typescript", "tsx"}
_TYPE_TAIL = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")


@dataclass
class FnResult:
    name: str
    start_line: int
    end_line: int
    class_names: tuple[str, ...]
    cyclomatic: int = 1
    cognitive: int = 0
    loop_depth: int = 0
    param_count: int = 0
    max_access_depth: int = 0
    alloc_in_loop: int = 0
    linear_scan_in_loop: int = 0
    recursive: bool = False
    recursion_in_loop: bool = False
    unguarded_recursion: bool = False
    call_sites: list[tuple[int, str, int]] = field(default_factory=list)
    tokens: list[str] = field(default_factory=list)


def _text(node: Any) -> str:
    return node.text.decode("utf-8", "replace") if node is not None else ""


def _is_inline(node: Any, lang: Lang) -> bool:
    if not node.is_named:  # the `function` keyword token shares the node type name
        return False
    if node.type in lang.inline_function_types:
        return True
    return (
        lang.name in _JS
        and node.type in lang.function_types
        and node.parent is not None
        and node.parent.type == "arguments"
    )


def _is_function(node: Any, lang: Lang) -> bool:
    return node.is_named and node.type in lang.function_types and not _is_inline(node, lang)


def _function_name(node: Any, lang: Lang) -> str:
    named = node.child_by_field_name("name")
    if named is not None:
        return _text(named)
    parent = node.parent
    if lang.name in _JS and parent is not None:
        if parent.type == "variable_declarator":
            return _text(parent.child_by_field_name("name"))
        if parent.type == "pair":
            return _text(parent.child_by_field_name("key")).strip("'\"")
        if parent.type in ("public_field_definition", "field_definition"):
            return _text(parent.child_by_field_name("name") or parent.child_by_field_name("property"))
        if parent.type == "assignment_expression":
            left = parent.child_by_field_name("left")
            if left is not None and left.type == "member_expression":
                return _text(left.child_by_field_name("property"))
            return _text(left)
    return "anonymous"


def _class_names(node: Any, lang: Lang) -> tuple[str, ...]:
    names: list[str] = []
    cur = node.parent
    while cur is not None:
        if cur.type in lang.class_types:
            n = cur.child_by_field_name("name")
            if n is not None:
                names.append(_text(n))
        cur = cur.parent
    return tuple(reversed(names))


def _operator(node: Any) -> str:
    op = node.child_by_field_name("operator")
    if op is not None:
        return op.type
    for child in node.children:
        if not child.is_named:
            return child.type
    return ""


def _call_target(node: Any, lang: Lang) -> tuple[str, str | None]:
    """(tail name, qualifier text or None) of a call or construction."""
    t = node.type
    if lang.name == "java":
        if t == "method_invocation":
            obj = node.child_by_field_name("object")
            return _text(node.child_by_field_name("name")), (_text(obj) if obj is not None else None)
        if t == "object_creation_expression":
            names = _TYPE_TAIL.findall(_text(node.child_by_field_name("type")))
            return (names[-1] if names else ""), None
    if lang.name == "python" and t == "call":
        fn = node.child_by_field_name("function")
        if fn is not None and fn.type == "attribute":
            return _text(fn.child_by_field_name("attribute")), _text(fn.child_by_field_name("object"))
        return _text(fn), None
    if lang.name in _JS:
        if t == "call_expression":
            fn = node.child_by_field_name("function")
            if fn is not None and fn.type == "member_expression":
                return _text(fn.child_by_field_name("property")), _text(fn.child_by_field_name("object"))
            return _text(fn), None
        if t == "new_expression":
            names = _TYPE_TAIL.findall(_text(node.child_by_field_name("constructor")))
            return (names[-1] if names else ""), None
    return "", None


def _member_object(node: Any, lang: Lang) -> Any:
    """The receiver one hop down a member chain, or None when the chain ends."""
    t = node.type
    if lang.name == "java" and t in ("field_access", "method_invocation"):
        return node.child_by_field_name("object")
    if lang.name == "python":
        if t == "attribute":
            return node.child_by_field_name("object")
        if t == "call":
            return node.child_by_field_name("function")
    if lang.name in _JS:
        if t == "member_expression":
            return node.child_by_field_name("object")
        if t == "call_expression":
            return node.child_by_field_name("function")
    return None


def _chain_depth(node: Any, lang: Lang) -> int:
    depth = 0
    cur = node
    while cur is not None:
        if cur.type in lang.member_types:
            nxt = _member_object(cur, lang)
            if nxt is None:  # java method_invocation without a receiver
                break
            depth += 1
            cur = nxt
        elif cur.type in lang.call_types:  # py/js call wrapping a member access
            cur = _member_object(cur, lang)
        else:
            break
    return depth


def _count_params(fn: Any, lang: Lang) -> int:
    params = fn.child_by_field_name("parameters")
    if params is None:
        for child in fn.children:
            if child.type in lang.param_list_types:
                params = child
                break
    if params is None:
        # arrow function with a single bare parameter: `x => ...`
        single = fn.child_by_field_name("parameter")
        return 1 if single is not None else 0
    named = [c for c in params.named_children if c.type not in ("comment", "keyword_separator", "positional_separator", "receiver_parameter")]
    if lang.name == "python" and named:
        first = named[0]
        first_name = _text(first.child_by_field_name("name") or first) if first.type != "identifier" else _text(first)
        if first_name in lang.self_names:
            named = named[1:]
    return len(named)


def _contains(node: Any, types: frozenset[str]) -> bool:
    stack = [node]
    while stack:
        cur = stack.pop()
        if cur.type in types:
            return True
        stack.extend(cur.children)
    return False


def _token(leaf: Any) -> str | None:
    t = leaf.type
    if "comment" in t:
        return None
    if leaf.is_named:
        parent = leaf.parent.type if leaf.parent is not None else ""
        if "string" in t or "char" in t or "string" in parent or "template" in parent:
            return "STR"
        if any(k in t for k in ("integer", "float", "number", "decimal", "hex", "octal", "binary")):
            return "NUM"
        if "identifier" in t or t in ("this", "self", "super"):
            return "ID"
        return t
    return t


def _tokens(node: Any) -> list[str]:
    out: list[str] = []
    stack = [node]
    while stack:
        cur = stack.pop()
        if cur.child_count == 0:
            tok = _token(cur)
            if tok is not None:
                out.append(tok)
        else:
            stack.extend(reversed(cur.children))
    return out


class _Walker:
    def __init__(self, fn: Any, lang: Lang, result: FnResult) -> None:
        self.fn = fn
        self.lang = lang
        self.r = result
        self.recursive_sites: list[tuple[int, bool, bool]] = []  # (start_byte, conditional, in_loop)
        self.guard_ifs: list[Any] = []

    # -- traversal -------------------------------------------------------
    def walk(self) -> None:
        for child in self.fn.children:
            self.visit(child, 0, 0, 0)

    def visit(self, node: Any, nesting: int, loop: int, cond: int) -> None:
        lang, r, t = self.lang, self.r, node.type
        if node is not self.fn and _is_function(node, lang):
            return
        if _is_inline(node, lang):
            loop_inc = 1 if self._iterating_callback(node) else 0
            if loop_inc:
                r.loop_depth = max(r.loop_depth, loop + 1)
            self.children(node, nesting + 1, loop + loop_inc, cond)
            return
        if t in lang.if_types:
            self.visit_if(node, nesting, loop, cond, else_if=False)
            return
        if t in lang.loop_types or t in lang.comprehension_types:
            if t in lang.loop_types:
                r.cyclomatic += 1
            r.cognitive += 1 + nesting
            r.loop_depth = max(r.loop_depth, loop + 1)
            if t in lang.alloc_types and loop > 0:
                r.alloc_in_loop += 1
            self.children(node, nesting + 1, loop + 1, cond + 1)
            return
        if t in lang.switch_types:
            r.cognitive += 1 + nesting
            self.children(node, nesting + 1, loop, cond + 1)
            return
        if t in lang.catch_types:
            r.cyclomatic += 1
            r.cognitive += 1 + nesting
            self.children(node, nesting + 1, loop, cond + 1)
            return
        if t in lang.ternary_types:
            r.cyclomatic += 1
            r.cognitive += 1 + nesting
            self.children(node, nesting + 1, loop, cond + 1)
            return
        if t in lang.case_types and t not in lang.default_case_types:
            if not (lang.name == "java" and _text(node).lstrip().startswith("default")):
                r.cyclomatic += 1
        if t in lang.comprehension_clause_types:
            r.cyclomatic += 1
        if t in lang.boolean_types:
            op = _operator(node)
            if op in lang.boolean_ops:
                r.cyclomatic += 1
                parent = node.parent
                if not (parent is not None and parent.type == t and _operator(parent) == op):
                    r.cognitive += 1
                self.children(node, nesting, loop, cond + 1)
                return
        if t in lang.jump_types and any(c.type in ("identifier", "statement_identifier") for c in node.named_children):
            r.cognitive += 1
        if lang.name == "python" and t == "comparison_operator" and loop > 0:
            if any(not c.is_named and c.type in ("in", "not in") for c in node.children):
                r.linear_scan_in_loop += 1
        if t in lang.call_types or t in lang.new_types:
            self.visit_call(node, loop, cond)
        if t in lang.alloc_types and loop > 0:
            r.alloc_in_loop += 1
        if t in lang.member_types:
            r.max_access_depth = max(r.max_access_depth, _chain_depth(node, lang))
        self.children(node, nesting, loop, cond)

    def children(self, node: Any, nesting: int, loop: int, cond: int) -> None:
        for child in node.children:
            self.visit(child, nesting, loop, cond)

    def visit_if(self, node: Any, nesting: int, loop: int, cond: int, *, else_if: bool) -> None:
        r, lang = self.r, self.lang
        r.cyclomatic += 1
        r.cognitive += 1 if else_if else 1 + nesting
        if _contains(node, lang.return_types):
            self.guard_ifs.append(node)
        condition = node.child_by_field_name("condition")
        if condition is not None:
            self.visit(condition, nesting, loop, cond)
        consequence = node.child_by_field_name("consequence")
        if consequence is not None:
            self.visit(consequence, nesting + 1, loop, cond + 1)
        for alt in node.children_by_field_name("alternative"):
            if alt.type in lang.elif_types:  # python elif
                r.cyclomatic += 1
                r.cognitive += 1
                c = alt.child_by_field_name("condition")
                if c is not None:
                    self.visit(c, nesting, loop, cond)
                body = alt.child_by_field_name("consequence")
                if body is not None:
                    self.visit(body, nesting + 1, loop, cond + 1)
            elif alt.type in lang.else_types:
                inner = [c for c in alt.named_children if c.type != "comment"]
                if len(inner) == 1 and inner[0].type in lang.if_types:  # js `else if`
                    self.visit_if(inner[0], nesting, loop, cond, else_if=True)
                else:
                    r.cognitive += 1
                    for c in inner:
                        self.visit(c, nesting + 1, loop, cond + 1)
            elif alt.type in lang.if_types:  # java `else if`
                self.visit_if(alt, nesting, loop, cond, else_if=True)
            else:  # java plain else block
                r.cognitive += 1
                self.visit(alt, nesting + 1, loop, cond + 1)

    def visit_call(self, node: Any, loop: int, cond: int) -> None:
        r, lang = self.r, self.lang
        tail, qualifier = _call_target(node, lang)
        if not tail:
            return
        line = node.start_point[0] + 1
        r.call_sites.append((line, tail, loop))
        if loop > 0 and qualifier is not None and tail in lang.scan_methods:
            r.linear_scan_in_loop += 1
        if (
            lang.name == "python"
            and loop > 0
            and qualifier is None
            and node.type == "call"
            and tail[:1].isupper()
        ):
            r.alloc_in_loop += 1
        if tail == r.name and (qualifier is None or qualifier in lang.self_names or qualifier in r.class_names):
            r.recursive = True
            r.cognitive += 1
            self.recursive_sites.append((node.start_byte, cond > 0, loop > 0))
            if loop > 0:
                r.recursion_in_loop = True

    def _iterating_callback(self, node: Any) -> bool:
        args = node.parent
        call = args.parent if args is not None else None
        if call is None or args.type not in ("arguments", "argument_list"):
            return False
        tail, qualifier = _call_target(call, self.lang)
        return qualifier is not None and tail in self.lang.iterating_methods

    def finish(self) -> None:
        unconditional = [start for start, conditional, _ in self.recursive_sites if not conditional]
        if unconditional:
            first = min(unconditional)
            guarded = any(g.start_byte < first for g in self.guard_ifs)
            self.r.unguarded_recursion = not guarded


def analyze_source(source: bytes, lang: Lang) -> tuple[list[FnResult], str | None]:
    """Every function in one file, and (Java) its package."""
    import tree_sitter_language_pack as tslp  # noqa: PLC0415

    tree = tslp.get_parser(lang.grammar).parse(source)
    root = tree.root_node
    package = None
    if lang.name == "java":
        for child in root.named_children:
            if child.type == "package_declaration":
                ident = [c for c in child.named_children if c.type in ("scoped_identifier", "identifier")]
                package = _text(ident[0]) if ident else None
                break

    results: list[FnResult] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if _is_function(node, lang):
            res = FnResult(
                name=_function_name(node, lang),
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                class_names=_class_names(node, lang),
            )
            res.param_count = _count_params(node, lang)
            walker = _Walker(node, lang, res)
            walker.walk()
            walker.finish()
            res.tokens = _tokens(node)
            results.append(res)
        stack.extend(node.children)
    results.sort(key=lambda f: (f.start_line, f.name))
    return results, package
