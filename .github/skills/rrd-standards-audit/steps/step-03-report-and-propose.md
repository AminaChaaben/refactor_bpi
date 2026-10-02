---
name: 'step-03-report-and-propose'
description: 'Write findings summary and diff proposals'
nextStepFile: null
---

# Step 3: Report and Propose

## SEQUENCE

### 1. Write Findings Summary

Write `{rrd_artifacts}/standards-audit-{target_project}-{date}.md`: each recurring-pattern finding naming the gap type, occurrence count, the files/detectors it came from, and whether the fix is documentation (no convention exists) or enforcement (convention exists, isn't followed). Each repo-governance-artifact finding naming the specific missing artifact and the evidence that its absence is actually being felt (not just "it's missing").

### 2. Write Diff Proposals

For each finding, write a diff to `{target_project_root}/proposals/`:

- No convention exists → propose a minimal doc stub (a `docs/conventions.md` section, or a `CONTRIBUTING.md` addition) that names the *actual* pattern found and the *actual* fix already established by the original detector — not a generic style guide.
- Convention exists but isn't enforced → propose a concrete enforcement mechanism (a lint rule matching the actual violation pattern, a PR-template checklist item) rather than more documentation.
- Missing repo-governance artifact whose absence is evidenced → propose a minimal starting version scoped to what this run actually found (e.g. a `CODEOWNERS` stub covering the directories this audit actually touched, not a full-repo speculative ownership map).

### 3. Summarize to Owner

Report finding count (split into recurring-pattern findings vs. repo-governance-artifact findings), which evidence base was used (full `rrd-audit-all` report vs. partial detector reports vs. owner-approved lower-confidence mode), and output/proposal paths, in `{communication_language}`, in Ray's voice.

Workflow complete.
