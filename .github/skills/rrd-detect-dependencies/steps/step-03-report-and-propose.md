---
name: 'step-03-report-and-propose'
description: 'Write findings summary and diff proposals'
nextStepFile: null
---

# Step 3: Report and Propose

## STEP GOAL

Produce the findings summary and a reviewable diff proposal per finding, per the Evidence and Diff Discipline contract.

## SEQUENCE

### 1. Write Findings Summary

Write `{rrd_artifacts}/detect-dependencies-{target_project}.md` (following `./resources/knowledge/detect-report-template.md`'s structure) containing, for each finding: title, coupling shape, risk level, evidence citation, and a link to its diff proposal.

### 2. Write Diff Proposals

For each finding, write a unified diff to `{target_project_root}/proposals/` (never edit target source directly). Name diffs after the affected file, e.g. `proposals/tests__e2e__crm-workspace__settings.spec.ts.1.patch`.

### 3. Summarize to Owner

Report: finding count, risk-level breakdown, files/tests involved, and the output/proposal paths. Speak in `{communication_language}`, in Ray's voice — terse, evidence-led, confidence levels not absolutes.

Workflow complete.
