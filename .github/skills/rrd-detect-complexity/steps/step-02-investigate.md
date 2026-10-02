---
name: 'step-02-investigate'
description: 'Find complexity hotspots via the graph precomputed properties, confirm by reading source'
nextStepFile: '{skill-root}/steps/step-03-report-and-propose.md'
---

# Step 2: Investigate

## SEQUENCE

### 1. Query Complexity Hotspots — Multiple Axes

Query the precomputed complexity properties. A single ORDER BY misses hotspots that only rank high on one axis, so run all of these:

```bash
code-graph query complexity-hotspots --limit 50
code-graph query complexity-by-cyclomatic --limit 20
code-graph query complexity-by-cognitive --limit 20
code-graph query complexity-by-tld --limit 20
code-graph query complexity-risks
```

`complexity-hotspots` applies every threshold at once; pass `{complexity_thresholds}` as parameters when the owner changed them (e.g. `--param min_cyclomatic=16 --param min_cognitive=21`; the parameters are `min_cyclomatic`, `min_cognitive`, `min_tld`, `min_params`, `min_access`, each the smallest flagged value). `complexity-risks` catches the correctness/performance risks a complexity sort alone would miss (`linear_scan_in_loop >= 1`, `unguarded_recursion`, `recursion_in_loop`, `alloc_in_loop >= 3`). If `count` equals the limit, raise `--limit` before concluding the list is complete.

### 2. Read Source and Confirm

For every candidate crossing a threshold, read the actual code with `code-graph query symbol --param qualified_name=<qn>`. Identify the concrete pattern driving the number (e.g. repeated near-identical blocks, a nested scan, an unguarded base case) — do not write a finding from the metric alone.

### 3. Check Cross-Detector Corroboration

For each confirmed candidate, note whether the same `affected_target` was also flagged by another detector family this run (or in a prior finding the owner shares). If so, record this explicitly — it materially strengthens the finding per `evidence-fusion-heuristics.md`'s cross-family corroboration rule.

### 4. Filter Test/Fixture Code

Deprioritize findings in test files or fixtures unless the owner asked otherwise — complexity in test setup is a lower-priority problem than complexity in the code under test.

### 5. Continue

Load `./step-03-report-and-propose.md`.
