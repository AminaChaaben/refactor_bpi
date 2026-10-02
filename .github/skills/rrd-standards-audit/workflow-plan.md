# Workflow Plan: rrd-standards-audit

## Single Mode (steps) — no separate Create/Edit/Validate: a governance audit has no meaningful "edit" or "validate without re-running" mode
- step-01-preflight-and-init.md
- step-02-audit-governance.md
- step-03-report-and-propose.md

## Outputs
- {rrd_artifacts}/standards-audit-{target_project}-{date}.md
- Diff proposals (convention doc stubs, lint config seeds) written to the target project's own `proposals/`

## Position in the Pipeline

Runs *after* `rrd-audit-all`, not instead of it and not in parallel with it — its evidence is the prior run's pooled findings, not a fresh code scan. Running this workflow against a target with no prior `rrd-audit-all` report is a supported but explicitly lower-confidence mode (see step-01).
