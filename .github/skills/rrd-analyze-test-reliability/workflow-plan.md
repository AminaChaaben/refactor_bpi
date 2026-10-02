# Workflow Plan: rrd-analyze-test-reliability

## Single Mode (steps) — no separate Create/Edit/Validate: log-driven classification has no meaningful separate "edit" or "validate without re-running" mode
- step-01-preflight-and-init.md (resolve project + log sources, confirm minimum run count)
- step-01b-generate-execution-logs.md (conditional — only when fewer than the minimum runs exist yet, the common case: toolchain detection, environment/discovery-gap diagnosis and fix via rrd-apply-and-verify, multi-run generation with preservation)
- step-02-parse-logs.md (normalize whatever format into TestRun records, aggregate per-test history)
- step-03-classify.md (Real Failure / False Positive / False Negative / Healthy, cross-referencing existing detector heuristics)
- step-04-report-and-propose.md (write findings summary + diffs, grouped by classification)

## Outputs
- {rrd_artifacts}/test-reliability-{target_project}-{date}.md
- Diff proposals written to the target project's own `proposals/`
