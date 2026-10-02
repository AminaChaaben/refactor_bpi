---
name: 'step-01-run-and-gate'
description: 'Invoke rrd-audit-all, compare its trend output against the gate threshold, report a verdict'
nextStepFile: null
---

# Step 1: Run and Gate

## SEQUENCE

### 1. Invoke `rrd-audit-all`

Run the full `rrd-audit-all` workflow against `{target_project}` exactly as that workflow defines itself — same incremental/full choice, same ten detectors, same report and history-snapshot write. This wrapper does not alter or skip any of that workflow's own steps.

### 2. Read the Trend Result

From `rrd-audit-all`'s own Step 3 output (the `Closed / New / Regressed` breakdown and the underlying `Opportunity[]` with priority levels), identify every opportunity that is:

- **New this run** (not in the prior snapshot), **and**
- At or above `{gate_fails_at_or_above}` priority (default: `critical`)

### 3. Handle the First-Run Case

If `rrd-audit-all` reported "first tracked run, no trend to report" (no prior snapshot existed):

- If `fail_on_first_run_if_critical_exists` is `false` (default): **PASS**, regardless of how many Critical opportunities exist — there's no "new since last time" to measure yet, and failing a brand-new project's first audit on a baseline that was never going to be clean isn't this gate's job.
- If `true`: **FAIL** if any Critical (or `{gate_fails_at_or_above}`) opportunity exists in this run's `Opportunity[]` at all.

### 4. Render the Verdict

```
CI GATE: {PASS|FAIL}

{if FAIL:}
New {gate_fails_at_or_above}+ opportunities since last run:
  #{id}: {title} (priority: {level})
  ...

{if PASS:}
No new {gate_fails_at_or_above}+ opportunities since last run.
({closed_count} closed, {new_count} new below threshold, {regressed_count} regressed)

Full report: {report_path}
Diffs: {proposal_folder}
```

Render the verdict line first, before any other detail — a calling CI script parsing this output needs `PASS`/`FAIL` unambiguously at the top, not buried after prose.

### 5. Exit Behavior

State explicitly (this workflow doesn't control process exit codes itself, since it runs inside an agent session, not a shell script) what the calling pipeline should treat as the failure signal: the literal string `CI GATE: FAIL` in this output, for a wrapper shell script to `grep` for and translate into a non-zero exit.

Workflow complete.
