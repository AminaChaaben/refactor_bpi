---
name: 'step-01-preflight-and-init'
description: 'Resolve target project, verify it is indexed, load knowledge fragments'
nextStepFile: '{skill-root}/steps/step-02-investigate.md'
---

# Step 1: Preflight & Init

## SEQUENCE

### 1. Resolve Target Project

Run `code-graph status`. If it reports `indexed: false` or `stale: true`, run `code-graph index --wait 600` (read-only on the source tree) and check `status` again. Halt if indexing fails, and say why.

### 2. Check for Existing Detect Instability Findings

Ask the owner (or check `{rrd_artifacts}/detect-instability-{target_project}-*.md` if present) whether `rrd-detect-instability` has already run on this target. If so, load its findings as input for Step 2 rather than re-detecting the same fragile selectors from scratch — this workflow's job is strategy-level gaps (duplication, missing tiering, no repository), not re-litigating individual selector fragility.

### 3. Load Knowledge Fragments

Consult `./resources/rrd-index.csv`, then load:

- `./resources/knowledge/evidence-and-diff-discipline.md`
- `./resources/knowledge/detect-locators.md`

### 4. Continue

Load `./step-02-investigate.md`.
