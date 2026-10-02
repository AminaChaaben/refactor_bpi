# Workflow Plan: rrd-establish-execution-baseline

## Single Mode (steps) — no separate Create/Edit/Validate: getting a suite running has no meaningful separate "edit" or "validate without re-running" mode
- step-01-preflight-and-init.md (resolve project, safety gate, detect toolchain, load knowledge fragments)
- step-02-diagnose-and-fix.md (first run, diagnose blocker type in order, fix via rrd-apply-and-verify or direct fixture creation, bounded retry)
- step-03-generate-multi-run-set.md (run N times, preserve each run's native report)
- step-04-write-manifest.md (write manifest.json, report to owner/consuming workflow)

## Outputs
- {target_project_root}/.refactor-radar-logs/manifest.json
- {target_project_root}/.refactor-radar-logs/run{N}-<original-filename> (preserved native reports)
- Diff proposals written to the target project's own `proposals/`, for any fix requiring one
