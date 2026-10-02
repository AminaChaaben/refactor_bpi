"""Near-duplicate functions (type-2 clones): what `SIMILAR_TO` edges carry.

Tokens are normalised (identifiers -> ID, strings -> STR, numbers -> NUM; keywords
and punctuation kept), so renamed copies still match. Each function becomes a set of
5-token shingles hashed with blake2b. Candidates come from a bottom-k MinHash sketch
(the k smallest shingle hashes): two functions with Jaccard similarity J share a
sketch entry with high probability when J is high, and the inverted index over sketch
entries finds those pairs without comparing every pair. Candidates are then checked
with the exact Jaccard of their shingle sets. Fully deterministic.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

__all__ = ["FunctionTokens", "similar_pairs"]


@dataclass(frozen=True)
class FunctionTokens:
    uid: str
    path: str
    start: int
    end: int
    tokens: tuple[str, ...]


def _shingles(tokens: tuple[str, ...], size: int) -> set[int]:
    out: set[int] = set()
    for i in range(len(tokens) - size + 1):
        digest = hashlib.blake2b("\x1f".join(tokens[i : i + size]).encode(), digest_size=8).digest()
        out.add(int.from_bytes(digest, "big"))
    return out


def similar_pairs(
    functions: list[FunctionTokens],
    *,
    threshold: float = 0.7,
    min_tokens: int = 30,
    shingle: int = 5,
    sketch: int = 16,
    max_bucket: int = 500,
    max_per_function: int = 10,
) -> list[tuple[str, str, float, bool]]:
    """`(uid_a, uid_b, jaccard, same_file)` with uid_a < uid_b, sorted."""
    sets: dict[str, set[int]] = {}
    meta: dict[str, FunctionTokens] = {}
    buckets: dict[int, list[str]] = {}
    for fn in sorted(functions, key=lambda f: f.uid):
        if len(fn.tokens) < min_tokens:
            continue
        shingles = _shingles(fn.tokens, shingle)
        if not shingles:
            continue
        sets[fn.uid] = shingles
        meta[fn.uid] = fn
        for h in sorted(shingles)[:sketch]:
            buckets.setdefault(h, []).append(fn.uid)

    candidates: set[tuple[str, str]] = set()
    for members in buckets.values():
        if len(members) < 2 or len(members) > max_bucket:
            continue
        for i, a in enumerate(members):
            for b in members[i + 1 :]:
                candidates.add((a, b) if a < b else (b, a))

    scored: list[tuple[str, str, float, bool]] = []
    for a, b in sorted(candidates):
        fa, fb = meta[a], meta[b]
        same_file = fa.path == fb.path
        if same_file and fa.start <= fb.end and fb.start <= fa.end:
            continue  # one function nested in the other
        sa, sb = sets[a], sets[b]
        jaccard = len(sa & sb) / len(sa | sb)
        if jaccard >= threshold:
            scored.append((a, b, round(jaccard, 3), same_file))

    # Keep each function's strongest matches only, deterministically.
    scored.sort(key=lambda p: (-p[2], p[0], p[1]))
    per_fn: dict[str, int] = {}
    kept: list[tuple[str, str, float, bool]] = []
    for a, b, j, same in scored:
        if per_fn.get(a, 0) >= max_per_function or per_fn.get(b, 0) >= max_per_function:
            continue
        per_fn[a] = per_fn.get(a, 0) + 1
        per_fn[b] = per_fn.get(b, 0) + 1
        kept.append((a, b, j, same))
    kept.sort(key=lambda p: (p[0], p[1]))
    return kept
