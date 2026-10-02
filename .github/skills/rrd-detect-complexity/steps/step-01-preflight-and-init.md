---
name: 'step-01-preflight-and-init'
description: 'Resolve target project, verify it is indexed, load knowledge fragments, confirm complexity thresholds'
nextStepFile: '{skill-root}/steps/step-02-investigate.md'
---

# Step 1: Preflight & Init

## SEQUENCE

### 1. Resolve Target Project

Run `code-graph status`. If it reports `indexed: false` or `stale: true`, run `code-graph index --wait 600` (read-only on the source tree) and check `status` again. Halt if indexing fails, and say why.

### 2. Confirm Complexity Thresholds

Ask the owner if they want to narrow or widen the default thresholds (complexity > 10, cognitive > 15, transitive_loop_depth >= 3, linear_scan_in_loop >= 1, param_count > 5, max_access_depth > 3 — see `detect-complexity.md` for the full table and rationale). Record `{complexity_thresholds}`.

### 3. Load Knowledge Fragments

Consult `./resources/rrd-index.csv`, then load:

- `./resources/knowledge/evidence-and-diff-discipline.md`
- `./resources/knowledge/detect-complexity.md`

### 4. Continue

Load `./step-02-investigate.md`.
