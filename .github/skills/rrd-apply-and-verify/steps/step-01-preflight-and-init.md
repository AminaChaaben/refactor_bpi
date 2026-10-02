---
name: 'step-01-preflight-and-init'
description: 'Safety gate: check git status, load knowledge fragments, resolve which proposal(s) to apply'
nextStepFile: '{skill-root}/steps/step-02-apply-diff.md'
---

# Step 1: Preflight & Init

## SEQUENCE

### 1. Resolve Target Project and Proposals Folder

Confirm `{target_project_root}` and that `{target_project_root}/proposals/` exists with at least one `.patch` file (or an HTML audit report referencing them). If no proposals exist, halt and tell the owner to run a detection workflow or Audit All first.

### 2. Safety Gate — git status

Run `git status` on `{target_project_root}`.

- If it is not a git repository: tell the owner there is no version-control safety net for this apply, and ask them to explicitly confirm they still want to proceed.
- If it is a git repo with a dirty tree (uncommitted changes unrelated to this workflow's own prior runs): show the dirty files and ask the owner to confirm before proceeding — do not silently apply on top of unreviewed uncommitted work.
- If clean (or the only untracked content is this module's own `proposals/`/`.refactor-radar-work/` output): proceed without prompting further.

### 3. Load Knowledge Fragments

Consult `./resources/rrd-index.csv`, then load:

- `./resources/knowledge/evidence-and-diff-discipline.md`
- `./resources/knowledge/apply-and-verify-heuristics.md`

### 4. Resolve Which Proposal(s) to Apply

If `{proposal_selection}` is not already specified, list the available `.patch` files in `proposals/` (with their rationale headers summarized) and ask the owner which to apply: all of them, a specific one, or a specific opportunity/finding ID to look up.

### 5. Continue

Load `./step-02-apply-diff.md`.
