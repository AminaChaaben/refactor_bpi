---
name: 'step-03-report-and-propose'
description: 'Write findings summary and diff proposals'
nextStepFile: null
---

# Step 3: Report and Propose

## SEQUENCE

### 1. Write Findings Summary

Write `{rrd_artifacts}/detect-layering-{target_project}.md` (following `./resources/knowledge/detect-report-template.md`'s structure): each finding grouped by category (Missing/Mixed Layer / Cross-Layer Violation / Naming Inconsistency), naming the specific directory, file, or file:line, and evidence citation (`code-graph query`/`Grep` command and result). State plainly which of the seven candidate layers were checked and found already correctly separated — don't only report problems.

### 2. Write Diff Proposals

For each finding, write a diff to `{target_project_root}/proposals/`, scoped to what was actually found:

- Missing/mixed layer → propose extracting the specific mixed-in concern into its own directory/module, not a full-project restructure
- Cross-layer violation → propose moving the specific assertion out of the page object into the calling test, or routing the specific raw-data manipulation through the existing data layer
- Naming inconsistency → propose renaming the specific outlier file(s) to match the layer's dominant convention, not rewriting the whole layer's convention from scratch

### 3. Summarize to Owner

Report finding count by category, which layers were checked and found clean, and output/proposal paths, in `{communication_language}`, in Ray's voice.

Workflow complete.
