---
name: 'step-02-investigate'
description: 'Find structurally similar code via the graph'
nextStepFile: '{skill-root}/steps/step-03-report-and-propose.md'
---

# Step 2: Investigate

## SEQUENCE

### 1. Query SIMILAR_TO First

Query the graph's own precomputed similarity edges before doing anything else:

```bash
code-graph query similar-pairs --limit 100
code-graph query similar-pairs --param min_jaccard=0.9          # near-certain duplicates only
code-graph query similar-pairs --param same_file=false          # cross-file copies only
```

`SIMILAR_TO` edges are computed at index time by `code-graph` (MinHash over normalised 5-token shingles of each function of at least 30 tokens, confirmed with the exact Jaccard; at most 10 per function, threshold 0.7). Each row gives both functions' `qualified_name`, `path` and `line`, plus `jaccard` and `same_file`.

This is exact, graph-computed evidence — not an estimate. Apply `{similarity_threshold}` against `jaccard` (default: treat >=0.9 as near-certain, 0.7-0.9 as a real candidate worth investigating, below 0.7 as a hint only).

### 2. Fallback Search for Uncovered Areas

If the owner is asking about a specific area with no `SIMILAR_TO` coverage, or duplication takes a shape the similarity model doesn't capture (e.g. same responsibility, different structure), fall back to `code-graph query callees` (compare call patterns), `file-symbols` and `Grep` for graph-similar functions/classes — same call pattern or data-flow structure, even if the code has drifted slightly.

### 3. Read Source and Confirm

For every candidate group (from either step), run `code-graph query symbol` on each member and read the actual code before treating it as a finding. A high jaccard score or a matched call pattern tells you where to look, not that the duplication is real or worth fixing — confirm by reading, especially for scores near the 0.7-0.9 boundary or short/boilerplate-heavy functions where high similarity can be coincidental.

### 4. Group and Score

Group confirmed matches into duplicate groups, each with its similarity score (jaccard if available, otherwise the estimated pattern match) and the files/symbols involved.

### 5. Continue

Load `./step-03-report-and-propose.md`.
