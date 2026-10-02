---
name: 'step-01-preflight-and-init'
description: 'Resolve target project, locate a prior rrd-audit-all report, load knowledge fragments'
nextStepFile: '{skill-root}/steps/step-02-audit-governance.md'
---

# Step 1: Preflight & Init

## SEQUENCE

### 1. Resolve Target Project

Run `code-graph status`. If it reports `indexed: false` or `stale: true`, run `code-graph index --wait 600` (read-only on the source tree) and check `status` again. Halt if indexing fails, and say why.

### 2. Locate a Prior `rrd-audit-all` Report

Check `{rrd_artifacts}/` for an existing `refactor-radar-audit-*.html` report or the per-detector findings this workflow's evidence model depends on. Three cases:

1. **A recent report exists** — use it as the evidence base for Step 2. This is the intended, high-confidence path.
2. **No report exists, but individual detector reports exist** (e.g. `detect-locators-{target}-*.md`, `detect-config-{target}-*.md`) — usable as a partial evidence base; say so explicitly and note the audit is scoped to whichever detectors actually ran, not the full nine.
3. **Nothing exists at all** — tell the owner plainly that this workflow's evidence model depends on prior detector output, and ask whether to (a) stop and run `rrd-audit-all` first, or (b) explicitly proceed anyway on a much lower-confidence basis (repo-metadata checks only, no recurring-pattern evidence). Do not silently fall back to (b) without the owner choosing it.

### 3. Load Knowledge Fragments

Consult `./resources/rrd-index.csv`, then load:

- `./resources/knowledge/evidence-and-diff-discipline.md`
- `./resources/knowledge/standards-audit.md`

### 4. Continue

Load `./step-02-audit-governance.md`.
