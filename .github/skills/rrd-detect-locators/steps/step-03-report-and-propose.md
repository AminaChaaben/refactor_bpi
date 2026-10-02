---
name: 'step-03-report-and-propose'
description: 'Write findings summary and diff proposals'
nextStepFile: null
---

# Step 3: Report and Propose

## SEQUENCE

### 1. Write Findings Summary

Write `{rrd_artifacts}/detect-locators-{target_project}.md` (following `./resources/knowledge/detect-report-template.md`'s structure): the duplicated locator or strategy gap, its location(s), category (duplication / priority-tiering / missing-repository), and evidence citation per finding.

### 2. Write Diff Proposals

For each finding, write a diff to `{target_project_root}/proposals/`:

- Duplicated locator across files → extract into a single shared locator/element module, point all call sites at it
- Missing or inconsistent priority tiering → propose the higher-tier selector for the specific flagged locator(s), scoped to those lines, not a project-wide rewrite
- No centralized repository at all → propose a minimal starting structure (e.g. one `elements/`/`locators/` module for the page(s) actually flagged this run), not a full-project migration in one diff

### 3. Summarize to Owner

Report finding count and output/proposal paths, in `{communication_language}`, in Ray's voice.

Workflow complete.
