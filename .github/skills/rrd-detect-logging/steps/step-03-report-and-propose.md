---
name: 'step-03-report-and-propose'
description: 'Write findings summary and diff proposals'
nextStepFile: null
---

# Step 3: Report and Propose

## SEQUENCE

### 1. Write Findings Summary

Write `{rrd_artifacts}/detect-logging-{target_project}.md` (following `./resources/knowledge/detect-report-template.md`'s structure): each logging-gap finding grouped by gap type (Silent Catch / Exception-Dropping Log / Unlogged External Call / Unlogged Loop Failure Path / Context-Free Stack Trace), naming the function/method, file:line, the confirmed source-level gap, its failure-classification tag, and evidence citation (the exact `code-graph query` or `Grep` command and result). Report the Failure-Diagnostics Capture check as its own section, separate from the logging-gap findings — state plainly whether it's present-and-correct, present-but-gapped (which browsers/runners lack coverage), or absent entirely.

### 2. Write Diff Proposals

For each logging-gap finding, write a concrete diff to `{target_project_root}/proposals/` adding (or fixing) the logging statement at the specific site — include the original exception object where one was being dropped, and let the failure-classification tag shape what the log message actually captures (response status for env-leaning, record ID for data-leaning, etc.), not a generic "add logging" comment. For a Failure-Diagnostics Capture gap, propose the specific failure-hook addition (e.g. the `@AfterMethod` status-check-plus-screenshot block, or the `playwright.config.*` option) rather than a generic "add screenshots" note.

### 3. Summarize to Owner

Report finding count by gap type, any cross-detector corroboration found, and output/proposal paths, in `{communication_language}`, in Ray's voice. Note explicitly that these fixes pay off later: better logging here means real signal for `rrd-analyze-test-reliability` runs against this project going forward.

Workflow complete.
