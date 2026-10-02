# Opportunity Grouping and Impact Analysis Heuristics

Phase 3: transforming Correlation[] into Opportunity[], and calculating weighted impact via graph traversal.

---

## Part 1: Opportunity Grouping

Transform:
```
Finding[]
Correlation[]
    ↓
Opportunity[]
```

### Algorithm: Connected Components

1. **Initialize:** Each Finding is initially its own component.

2. **Union-Find:** For each Correlation:
   - If `strength == "strong"`: Union the finding_ids (they belong in the same opportunity).
   - If `strength == "medium"`: Consider union (see Rule below).
   - If `strength == "weak"`: Do not union (weak correlations are hints, not grouping evidence).

3. **Rule for Medium Correlations:**
   - If the two findings share a `affected_target`, union them.
   - If they are graph neighbors (direct edge) and already grouped through other strong/medium correlations, keep them grouped.
   - If neither condition, they may remain separate (unless another strong correlation connects them transitively).

4. **Result:** Connected components, where each component becomes one Opportunity.

### Example: Union-Find Result

```
Findings: [DI-014, DU-032, DI-089, DT-045, DU-117]

Correlations:
  DI-014 ↔ DU-032 (strong, same target LoginPage)
  DU-032 ↔ DI-089 (medium, same component)
  DT-045 ↔ DU-117 (medium, same root-cause family)
  
Union-Find:
  Component 1: {DI-014, DU-032, DI-089}  → Opportunity OPP-001
  Component 2: {DT-045, DU-117}          → Opportunity OPP-002
```

### Rule: Do Not Over-Group

- If 10+ findings end up in a single opportunity, consider splitting by root-cause family or affected target.
- If findings have weak impact on each other, keep them separate even if weakly correlated.
- Goal: each opportunity has a coherent refactoring scope.

**Constraint:** One finding can belong to only one opportunity. If a finding could belong to multiple, assign it to the opportunity with the strongest correlation.

---

## Part 2: Impact Analysis

For each Opportunity, calculate:

```
target_nodes (the findings' affected_components)
    ↓
Graph Traversal (2-3 hops)
    ↓
Weighted Impact Filtering
    ↓
affected_components + impact_score
```

### Step 1: Identify Target Nodes

From the Opportunity's supporting_findings:
- Collect all `affected_target` strings (classes, files, methods).
- Query the graph for each target: `code-graph query symbol --param qualified_name={target}` (or `--param name=... --param path=...`) to get the precise graph node(s).

**Example:**
```
Finding DI-014: affected_target = "LoginPage.java"
Finding DU-032: affected_target = "LoginPage.java"
Finding DI-089: affected_target = "LoginPage.java"

target_nodes = ["LoginPage"]  (same entity, deduplicated)
```

### Step 2: Direct Impact (Fan-In/Fan-Out)

For each target_node:
- Run `code-graph query callers --param qualified_name={target}` to find direct callers (`code-graph query impact` for everything within 3 hops, test code flagged).
- Run `code-graph query callees --param qualified_name={target}` to find direct callees.

**Record:**
```
direct_impact = {
  immediate_callers: [list of direct fan-in nodes],
  fan_in_count: N,
  immediate_callees: [list of direct fan-out nodes],
  fan_out_count: M
}
```

### Step 3: Transitive Impact (2-3 Hop Traversal)

For each target_node, perform a BFS (breadth-first search) up to 3 hops:

**Hop 1:** direct callers + callees (distance = 1)
**Hop 2:** their callers + callees (distance = 2)
**Hop 3:** their callers + callees (distance = 3)

For each discovered node, compute a **weight**:

```
weight = base_weight × distance_decay × relationship_type_factor × frequency_factor

Where:
  base_weight = 1.0
  distance_decay = {
    hop_1: 1.0,
    hop_2: 0.7,
    hop_3: 0.4
  }
  relationship_type_factor = {
    call: 1.0,          (direct function call)
    import: 0.8,        (module import)
    data_flow: 0.6,     (data dependency)
    semantic: 0.3       (conceptually related, not code-level)
  }
  frequency_factor = 1.0 (default, 1.5 if usage_frequency > 10% of test runs)
```

**Record:**
```
transitive_impact = {
  hop_1: {nodes: [...], count: N, weight_sum: W1},
  hop_2: {nodes: [...], count: M, weight_sum: W2},
  hop_3: {nodes: [...], count: K, weight_sum: W3}
}
```

### Step 4: High-Confidence Filtering

Not all transitive nodes are equally important. Filter to **high-confidence impact**:

Include a transitive node only if:
- **Direct relationship:** weight >= 0.8, OR
- **High centrality:** node has fan-in > 5 (it is a hub), OR
- **Test involvement:** node is directly called by 3+ tests, OR
- **Execution evidence:** execution logs show failures in this node

**Record:**
```
weighted_high_confidence = count of nodes passing filter
filtered_nodes = [list of high-confidence nodes]
```

**Example:**
```
All transitive nodes (hops 1-3): 80
After filtering: 24 high-confidence nodes

Reason: 56 nodes have low weight and no strong connectivity.
These are reachable but not practically affected by the refactoring.
```

### Step 5: Affected Components Breakdown

For all nodes in direct_impact + high-confidence transitive_impact:
- Extract files, classes, methods.
- Cross-reference against test files to find affected tests.
- If available, look up test plans from metadata.

**Record:**
```
affected_components = {
  files: [...],
  classes: [...],
  methods: [...],
  tests: [...],
  test_plans: [...],
  entry_points: [...]
}
```

### Step 6: Execution Evidence Correlation

The code graph has no trace ingestion, so this step reads execution logs directly (Read/Grep/Bash) rather than via any graph call -- if execution logs are available:
- Search logs for failures involving any affected node.
- Count flaky test occurrences.
- Measure failure frequency (% of test runs).
- Identify cascade failures (test A fails, causes test B to fail).

**Record:**
```
execution_evidence = {
  flaky_test_count: N,
  failure_frequency: X%,
  cascade_failures: K
}
```

---

## Part 3: Risk Assessment

### Blast Radius

```
if affected_files.count > 20:
  blast_radius = "Large"
elif affected_files.count > 5:
  blast_radius = "Medium"
else:
  blast_radius = "Small"
```

### Complexity

```
if affected_classes.count > 10 AND interconnectedness(graph) > high:
  complexity = "High"
elif affected_methods.count > 20 OR depth_of_call_tree > 4:
  complexity = "Medium"
else:
  complexity = "Low"
```

### Overall Risk

```
risk_level = max(blast_radius_risk, complexity_risk)

Mapping:
  Small + Low → Low risk
  Small + Medium → Low-Medium risk
  Medium + Medium → Medium risk
  Large + High → High risk
  etc.
```

---

## Part 4: Confidence Calculation

### Finding Consensus

```
consensus_score = (count_of_findings_in_opportunity / total_findings) × 100

If all 4 findings agree on root_cause: consensus = 100%
If 2 of 4 findings have different root_causes: consensus = ~75%
```

### Evidence Strength

```
strength_score = avg(correlation_strength_values)

Where correlation_strength ∈ {
  "strong": 0.95,
  "medium": 0.65,
  "weak": 0.2
}

Example:
  1 strong + 1 medium = (0.95 + 0.65) / 2 = 0.80 = 80 points
```

### Execution Corroboration

```
if execution_evidence.flaky_test_count > 0:
  corroboration_score = min(100, 50 + (flaky_test_count × 10))
  # Real failures in logs strongly support the opportunity
else:
  corroboration_score = 0
  # No execution evidence, lower confidence
```

### Overall Confidence

```
overall_confidence = (finding_consensus × 0.4) + (evidence_strength × 100 × 0.4) + (execution_corroboration × 0.2)

Result: 0-100 score
  80-100 → Very High
  60-79  → High
  40-59  → Medium
  < 40   → Low
```

---

## Part 5: Effort Estimation

### Estimated Lines to Change — Family-Specific Strategy

**Rule:** treating every fix as proportional to whole-file size badly overestimates localized fixes. A dependency-injection change on a 327-line file does not touch 327 × 0.4 ≈ 131 lines — it touches the constructor and each call site, realistically far fewer. Use one of two strategies depending on what the recommendation actually does to the file, not a single flat multiplier for every root cause:

**Strategy A — Whole-file-scope changes.** Use when the fix genuinely rewrites or removes a large fraction of a file: exact-duplicate file deletion/merge, a full selector-pattern rewrite across a page object, wholesale data-fixture rearchitecture.

```
lines = size_of_primary_affected_file × refactor_factor

Where refactor_factor depends on root cause:
  Duplication (delete/merge a whole duplicate file) → 1.0  (the whole file is the change)
  Instability (page-object-wide selector rewrite)    → 0.5
  Data issues (fixture lifecycle rearchitecture)      → 0.4
```

Use the size of the file(s) actually being rewritten or deleted — not every file merely touched by a caller.

**Strategy B — Localized, occurrence-scoped changes.** Use when the fix is a small, repeated edit at each call site or occurrence, and the surrounding file's total size is irrelevant to effort: dependency injection, per-call-site duplication-pattern extraction, individual selector/wait fixes.

```
lines = (occurrence_count × lines_per_occurrence) + injection_or_setup_overhead

Where lines_per_occurrence is a small constant by root cause:
  Dependencies (inject instead of import a singleton)   → ~3-5 lines per call site
  Instability (replace one fixed wait / fragile selector) → ~2-4 lines per occurrence
  Data issues (parameterize one hardcoded value)          → ~2-3 lines per occurrence

injection_or_setup_overhead ≈ 10-15 lines (constructor/factory changes, one-time setup)
```

**Decision rule:** if the opportunity's recommendation describes deleting or rewriting an entire file, use Strategy A. If it describes changing N call sites/occurrences while leaving the rest of the file intact, use Strategy B. When genuinely unsure, compute both and use the smaller, more defensible number — but always record which strategy was used and why in the opportunity's `effort` field, so a reviewer can sanity-check it.

### Refactor Scope

```
if lines < 10:
  scope = "Small"
elif lines < 50:
  scope = "Moderate"
else:
  scope = "Large"
```

### Risk of Regression

```
if affected_tests.count == 0:
  regression_risk = "Low"  (no tests exercise this, safe to change)
elif affected_tests.count < 5:
  regression_risk = "Low"
elif affected_tests.count < 20:
  regression_risk = "Medium"
else:
  regression_risk = "High"  (many tests involved, careful refactoring needed)
```

---

## Part 6: Priority Scoring

**Rule:** multiplying `impact_score` (typically a single- or low-double-digit number) by confidence expressed on a 0-100 scale, then dividing by small numbers (`effort_score × risk_factor`, max ~6), lets confidence dominate the ratio — the raw result almost always exceeds 100 before clamping, so nearly every opportunity with any real impact lands in the same "Critical" bucket, making the ranking non-discriminating. Two changes fix this:

1. Confidence is used as a 0-1 fraction (`confidence_ratio`), not a 0-100 value, to match `impact_score`'s units.
2. Raw scores are normalized **relative to the current audit run** (min-max scaled across every opportunity produced this run) rather than compared against fixed absolute thresholds. A single audit's "Critical" is relative to what else that codebase surfaced — a codebase with only minor issues shouldn't have every opportunity screaming Critical.

```
impact_score = (direct_impact_count + weighted_high_confidence_count) × execution_evidence_weight
  where execution_evidence_weight ∈ {
    0.5: no logs,
    1.0: 1-3 flaky tests,
    1.5: 4+ flaky tests OR cascade failures
  }

confidence_ratio = overall_confidence / 100   # 0.0-1.0, NOT the raw 0-100 value

effort_score ∈ { 1.0: 1-10 lines, 2.0: 11-50 lines, 3.0: >50 lines }
risk_factor  ∈ { 1.0: Low risk, 1.5: Medium risk, 2.0: High risk }

raw_score = (impact_score × confidence_ratio) / (effort_score × risk_factor)
```

**Normalization (run-relative, applied after all opportunities in the run have a raw_score):**

```
min_raw = min(raw_score across all opportunities this run)
max_raw = max(raw_score across all opportunities this run)

if max_raw == min_raw:          # only one opportunity, or all tied
  priority_score = 70           # default to High — nothing to rank it against
else:
  priority_score = ((raw_score - min_raw) / (max_raw - min_raw)) × 100
```

Result: 0-100 score, guaranteed to span the actual range produced by this run instead of saturating at the ceiling.
```
90-100 → Critical (fix immediately)
70-89  → High (prioritize)
40-69  → Medium (consider)
< 40   → Low (defer, unless blocking)
```

**Reporting requirement:** every opportunity's `priority` field must record `raw_score` alongside the run's `min_raw`/`max_raw`, so a reviewer can see the run-relative context behind the final 0-100 number, not just the number itself.

---

## Constraints

1. **Every opportunity must cite evidence.** All findings, correlations, and graph relationships are traceable.

2. **Transitive impact uses multiple weighting factors.** No single factor inflates impact; all are combined.

3. **High-confidence filtering prevents false urgency.** Reachable but not practically affected components are excluded.

4. **Risk and effort temper priority.** A high-impact refactoring that touches 100 files and 50 tests is lower priority than a high-impact, low-risk refactoring.

5. **Execution evidence is the tiebreaker.** If two opportunities have similar scores, the one with real failure evidence wins.

6. **Priority scores are transparent.** Every opportunity includes its calculation so reviewers understand the ranking.
