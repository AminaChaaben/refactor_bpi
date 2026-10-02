---
name: 'step-04-report'
description: 'Report per-diff outcome and real test results; remind the owner nothing is committed'
nextStepFile: null
---

# Step 4: Report

## SEQUENCE

### 1. Act on Regression, Per the `auto_rollback_on_regression` Flag

For any diff Step 3 marked as causing a regression:

- **If `auto_rollback_on_regression` is `false` (the default)**: do not touch git state. This diff's failure is reported in Step 2 below exactly as before, and Step 3's existing "suggest, don't perform `git checkout`" language stands unchanged.
- **If `auto_rollback_on_regression` is `true`** (owner explicitly set this in `customize.toml`/team or user override): run `git checkout -- <file>` for exactly the file(s) this specific diff touched — never a broader revert — immediately after confirming the regression, before writing the summary. State plainly in the summary that this happened and why (which regression triggered it), so the owner sees an auto-action reported, not silently absorbed. This still never applies to files an *earlier*, non-regressing diff in the same run touched — only the regressing diff's own files are reverted.

### 2. Write Summary

Write `{rrd_artifacts}/apply-and-verify-{target_project}-{date}.md`:

- Per diff: applied+passed / applied+failed (with real failure detail from the native report) / skipped (with the specific reason)
- Before/after test counts where a diff was expected to change coverage
- Full aggregate: tests run / failures / errors / skipped across the full-suite run

### 3. Summarize to Owner

Report in Ray's voice (terse, evidence-led), in `{communication_language}`:

```
Applied: {n} of {total} proposals
  #{id}: {title} -> applied, tests pass ({before} -> {after} tests)
  #{id}: {title} -> skipped: {specific reason}
  #{id}: {title} -> applied, REGRESSION: {real failure detail} -> auto-reverted (auto_rollback_on_regression=true)
  #{id}: {title} -> applied, TESTS FAILED: {real failure detail}

Full suite: {tests_run} run, {failures} failures, {errors} errors, {skipped} skipped

Nothing committed. `git status`/`git diff` in {target_project_root} is your call.
```

### 4. If Any Diff Left Tests Failing (and Wasn't Auto-Reverted)

Stop here — do not apply further diffs in the same run once a failure is found, unless the owner explicitly says to continue. Suggest (do not perform) `git diff <file>` to inspect, or `git checkout -- <file>` to revert, as owner-initiated options. This step is skipped for any diff already auto-reverted per Step 1 — there's nothing left to suggest reverting.

Workflow complete.
