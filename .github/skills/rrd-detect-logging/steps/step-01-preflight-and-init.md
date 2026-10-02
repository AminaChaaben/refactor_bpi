---
name: 'step-01-preflight-and-init'
description: 'Resolve target project, verify it is indexed, load knowledge fragments, confirm scope'
nextStepFile: '{skill-root}/steps/step-02-investigate.md'
---

# Step 1: Preflight & Init

## SEQUENCE

### 1. Resolve Target Project

Run `code-graph status`. If it reports `indexed: false` or `stale: true`, run `code-graph index --wait 600` (read-only on the source tree) and check `status` again. Halt if indexing fails, and say why.

### 2. Confirm Scope

Ask the owner if this run should cover the whole codebase or a narrower scope (e.g. only the code paths a specific flaky/failing test exercises — useful when this detector is invoked mid-investigation from `rrd-analyze-test-reliability` rather than standalone). Default to whole-codebase if not specified.

### 3. Load Knowledge Fragments

Consult `./resources/rrd-index.csv`, then load:

- `./resources/knowledge/evidence-and-diff-discipline.md`
- `./resources/knowledge/detect-logging.md`

### 4. Continue

Load `./step-02-investigate.md`.
