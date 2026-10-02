---
name: 'step-03-report-and-propose'
description: 'Write findings summary and diff proposals'
nextStepFile: null
---

# Step 3: Report and Propose

## SEQUENCE

### 1. Write Findings Summary

Write `{rrd_artifacts}/detect-complexity-{target_project}.md` (following `./resources/knowledge/detect-report-template.md`'s structure): each finding with the function/method, the metric(s) that flagged it, the confirmed source-level cause (named concretely, not just restated as a number), and evidence citation (the exact `code-graph query` command and the row it returned).

### 2. Write Diff Proposals

For each finding, write a concrete simplify/extract diff to `{target_project_root}/proposals/`, grounded in the actual code read — not a generic "reduce complexity" suggestion.

### 3. Summarize to Owner

Report finding count, the metrics involved, any cross-detector corroboration found, and output/proposal paths, in `{communication_language}`, in Ray's voice.

Workflow complete.
