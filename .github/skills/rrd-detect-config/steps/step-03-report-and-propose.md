---
name: 'step-03-report-and-propose'
description: 'Write findings summary and diff proposals'
nextStepFile: null
---

# Step 3: Report and Propose

## SEQUENCE

### 1. Write Findings Summary

Write `{rrd_artifacts}/detect-config-{target_project}.md` (following `./resources/knowledge/detect-report-template.md`'s structure): the exact hardcoded value/inline logic, its location, category (URL/credential/timeout/env-switch/parallel-config), and evidence citation per finding.

### 2. Write Diff Proposals

For each finding, write a diff to `{target_project_root}/proposals/`:

- Hardcoded URL/credential/secret → externalize into a config file or environment variable, replace the literal with a reference
- Hardcoded timeout literal → replace with a named config value (e.g. `config.timeouts.elementWait`)
- Inline env-switch logic → replace with a lookup against a single externalized per-environment config object/file
- Unsafe parallel setting with no confirmed coupling finding → propose the safer parallel setting plus a note recommending `rrd-detect-dependencies` first

### 3. Summarize to Owner

Report finding count and output/proposal paths, in `{communication_language}`, in Ray's voice.

Workflow complete.
