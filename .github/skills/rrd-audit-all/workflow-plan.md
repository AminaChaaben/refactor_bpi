# Workflow Plan: rrd-audit-all

## Single Mode (steps) — no separate Create/Edit/Validate: a detection run has no meaningful "edit" or "validate without re-running" mode
- step-01-preflight-and-init.md
- step-02-run-detectors.md (run DD/DI/DT/DU/DC detectors, pool raw findings)
- step-02b-evidence-fusion.md (Phase 1+2: detect correlations between findings)
- step-02c-opportunity-engine.md (Phase 3a: group findings into opportunities)
- step-02d-impact-analysis.md (Phase 3b: calculate impact, risk, effort)
- step-02e-ranking.md (Phase 3c: prioritize opportunities)
- step-03-rank-and-report.md (Phase 3d: render HTML with opportunities as primary)

## Outputs (Phase 3)
- {rrd_artifacts}/audit-all-{target_project}-{date}.html (opportunities as primary view, findings as evidence)
- Diff proposals written to the target project's own `proposals/` (one per finding)
- Opportunity[] JSON data structure available for downstream tooling
