---
name: 'step-04-write-manifest'
description: 'Write the manifest describing what was produced, report to owner/consuming workflow'
nextStepFile: null
---

# Step 4: Write Manifest

## SEQUENCE

### 1. Write the Manifest

Write `{target_project_root}/.refactor-radar-logs/manifest.json` per the format in `establish-execution-baseline-heuristics.md`:

```json
{
  "format": "testng-xml" | "junit-xml" | "playwright-json" | "jenkins-console",
  "run_count": 3,
  "runs": ["run1-<filename>", "run2-<filename>", "run3-<filename>"],
  "log_sources_dir": "{target_project_root}/.refactor-radar-logs",
  "excluded_dimensions": [
    {"dimension": "browser=edge", "reason": "..."}
  ],
  "fixes_applied": [
    {"file": "...", "change": "...", "method": "diff+rrd-apply-and-verify" | "direct-fixture-creation"}
  ]
}
```

### 2. Summarize to Owner (or Consuming Workflow)

Report in Ray's voice (terse, evidence-led), in `{communication_language}`:

```
Baseline established: {run_count} genuine runs.
  Format: {format}
  Excluded: {excluded_dimensions, or "none"}
  Fixes applied: {fixes_applied, or "none needed"}

Manifest: {manifest_path}
Runs: {log_sources_dir}
```

If this workflow was invoked by another workflow (e.g. `rrd-analyze-test-reliability`) rather than the owner directly, that workflow reads this manifest instead of re-diagnosing the same environment.

Workflow complete.
