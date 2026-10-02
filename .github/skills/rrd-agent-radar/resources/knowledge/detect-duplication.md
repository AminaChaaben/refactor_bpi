# Detect Duplication Heuristics

## What Success Looks Like

The owner learns exactly which functions, classes, or test blocks are redundant or near-duplicate — with a similarity score and a factor-out proposal — so maintenance burden and cascading edits (fix it in one copy, forget the other three) shrink. This is the lowest-drama detector of the four but still a real reliability cost: duplicated setup logic drifts out of sync and produces false-fails that look unrelated. This root-cause family accounted for 18% of false-fails in the reference engagement.

## Approach

**Primary method — query the graph's own similarity edges, don't estimate.** The indexer already computes structural similarity and stores it as `SIMILAR_TO` edges with a `jaccard` score and a `same_file` flag. Query these directly instead of guessing at similarity from names or descriptions:

```bash
code-graph query similar-pairs --limit 100
code-graph query similar-pairs --param min_jaccard=0.9          # near-certain duplicates only
code-graph query similar-pairs --param same_file=false          # cross-file copies only
```

`SIMILAR_TO` edges are computed at index time by `code-graph` (MinHash over normalised 5-token shingles of each function of at least 30 tokens, confirmed with the exact Jaccard; at most 10 per function, threshold 0.7). Each row gives both functions' `qualified_name`, `path` and `line`, plus `jaccard` and `same_file`.

This is graph-computed, exact, and free — always run it first. `jaccard >= 0.9` is a near-certain duplicate (read both snippets via `code-graph query symbol` to confirm before reporting — a high score on two short/boilerplate-heavy functions can still be a false positive). `jaccard 0.7–0.9` is a real candidate worth a source read; below that, treat as a hint only, not a finding by itself.

**Fallback — when no `SIMILAR_TO` edge covers what you're investigating** (e.g. the owner asks about a specific area with no similarity edges yet indexed, or duplication takes a shape the similarity model doesn't capture, like two files with the same responsibility implemented via different structures): use `code-graph query callees` (same call pattern), `code-graph query file-symbols` / `files` (parallel structures) and `Grep` for the shared literals to find structurally similar functions or classes across the target project — not just textual copies, but graph-similar shapes (same call pattern, same data-flow structure) that indicate a duplicated concept even when the code has drifted slightly. An optional similarity threshold from the owner narrows or widens the net.

Either way, **read the actual source (`code-graph query symbol`) before writing the finding** — a jaccard score or a matched call pattern tells you where to look, not what the fix should be. Report each duplicate group with its similarity score (jaccard if available, otherwise the estimated pattern match) and the files/symbols involved. Propose the factor-out — extracting the shared logic into one place and pointing the duplicates at it — as a diff, grounded in what the real code actually does, not just its shape. Factoring out must preserve behavior (see the Behavior Preservation contract in `evidence-and-diff-discipline.md`): when the duplicated block is an action paired with an assertion repeated per item (e.g. navigate to a page **and** check it for errors, once per page), the extracted helper keeps that pairing per iteration — never hoist the shared assertion out of the loop to run once at the end. If the duplication is really "same navigation, different/absent checks," the checks are not the duplicate part; factor out only the navigation and leave every per-page assertion intact.
