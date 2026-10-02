---
name: 'step-03-report-and-propose'
description: 'Write findings summary and diff proposals'
nextStepFile: null
---

# Step 3: Report and Propose

## SEQUENCE

### 1. Write Findings Summary

Write `{rrd_artifacts}/detect-duplication-{target_project}.md` (following `./resources/knowledge/detect-report-template.md`'s structure): each duplicate group with similarity score, files/symbols, and evidence citation.

### 2. Write Diff Proposals

For each group, write a factor-out diff (extract shared logic, point duplicates at it) to `{target_project_root}/proposals/`.

### 3. Summarize to Owner

Report group count, similarity scores, and output/proposal paths, in `{communication_language}`, in Ray's voice.

Workflow complete.
