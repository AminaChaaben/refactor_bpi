---
name: 'step-03-generate-multi-run-set'
description: 'Run the suite N times, preserving each run native report before the next overwrites it'
nextStepFile: '{skill-root}/steps/step-04-write-manifest.md'
---

# Step 3: Generate the Multi-Run Set

## SEQUENCE

### 1. Run `{run_count}` Times

Once Step 2 established a genuine result (in whole, or scoped to genuine dimensions), run the suite `{run_count}` times total (default 3 — a reasonable middle ground: enough to catch real inconsistency, not so many it's wasteful; the owner may ask for more).

### 2. Preserve Every Run's Native Report

Most toolchains write to one fixed location (`target/surefire-reports/`, `test-results/`) that gets clobbered on the next invocation. Immediately after each run, copy the report to a staging directory:

```
{target_project_root}/.refactor-radar-logs/run{N}-<original-filename>
```

### 3. Continue

Load `./step-04-write-manifest.md`.
