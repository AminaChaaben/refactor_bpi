---
name: 'step-03-report-and-propose'
description: 'Write findings summary and diff proposals'
nextStepFile: null
---

# Step 3: Report and Propose

## SEQUENCE

### 1. Write Findings Summary

Write `{rrd_artifacts}/detect-data-issues-{target_project}.md` (following `./resources/knowledge/detect-report-template.md`'s structure): data dependency, lifecycle gap, and evidence citation per finding.

### 2. Write Diff Proposals

For each finding, write a diff (externalize hardcoded data, add lifecycle, or scope per-run) to `{target_project_root}/proposals/`.

### 3. Summarize to Owner

Report finding count and output/proposal paths, in `{communication_language}`, in Ray's voice.

Workflow complete.
