# Workflow Plan: rrd-apply-and-verify

## Single Mode (steps) — no separate Create/Edit/Validate: applying a diff and running tests has no meaningful separate "edit" or "validate without re-running" mode
- step-01-preflight-and-init.md (safety gate: git status, resolve which proposal(s) to apply)
- step-02-apply-diff.md (read full files, apply or skip each diff with reason)
- step-03-run-tests.md (auto-detect toolchain, run affected tests then full suite, verify via native report format)
- step-04-report.md (per-diff outcome, before/after counts, reminder nothing is committed)

## Outputs
- {rrd_artifacts}/apply-and-verify-{target_project}-{date}.md
- Real source changes in the target project's working tree (never committed by this workflow)
