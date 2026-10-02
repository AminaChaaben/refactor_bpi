---
name: 'step-02-investigate'
description: 'Run graph queries to find coupling and cascade risk'
nextStepFile: '{skill-root}/steps/step-03-report-and-propose.md'
---

# Step 2: Investigate

## STEP GOAL

Find real dependency/coupling risk in `{target_project}` using graph exploration, not guesswork.

## SEQUENCE

### 1. Surface Candidates

Run these against `{target_project}` (`code-graph query variables`, `by-decorator`, `caller-count`, `importers`, plus `Grep` for literals such as shared URLs or connection strings) for:

- Shared mutable fixtures and module-level state
- Singletons
- Shared live services, databases, or hosted app instances touched by more than one test

### 2. Confirm Real Coupling

For each candidate, run `code-graph query callers --param qualified_name=<qn> --param depth=3` (and `importers` for module-level state) to confirm two or more tests actually reach the same mutable state — not just a shared import of a read-only constant.

### 3. Classify

For each confirmed finding, record:

- Coupling shape: shared fixture / order-dependence / shared live resource
- Risk level (weight live/shared hosted state heavily even without explicit code coupling)
- Evidence citation (the exact query/trace call and result)

### 4. Continue

Load `./step-03-report-and-propose.md`.
