---
name: 'validate'
description: 'Impact analysis, ranking, and report generation. Output: final HTML report + diffs.'
---

# Validate: Impact Analysis, Ranking & Report

## MODE GOAL

Take grouped opportunities from Edit mode, analyze impact, rank by false-fail risk, and produce the final consolidated HTML report. This is the full-audit endgame.

## INPUT

Read from Edit mode: `{project-root}/.refactor-radar-work/opportunities.json`

## SEQUENCE

### 1. Quality Gates — Opportunity Validation

Before proceeding, verify:
- [ ] Opportunities JSON exists and contains all grouped findings
- [ ] Every Opportunity has: title, description, problem_statement, supporting_findings array (non-empty)
- [ ] All correlations documented with strength (strong/medium/weak)
- [ ] No missing root_causes or affected_components
- [ ] No null/empty values in critical fields

If any check fails, **HALT**. Surface the issue and ask to return to Edit mode.

### 2. Impact Analysis (from step-02d)

For each Opportunity:
- Analyze graph impact: estimate false-fail risk (how many tests fail when this defect exists)
- Count affected components and call paths
- Estimate scope (isolated / moderate / codebase-wide)
- Assign risk tier (critical / high / medium / low)

Output: `{project-root}/.refactor-radar-work/opportunities-with-impact.json`

### 3. Opportunity Ranking (from step-02e)

Apply the ranking formula combining:
- False-fail impact (primary signal)
- Scope breadth (secondary signal)
- Fixing cost (low/medium/high, derived from affected_component count)
- Confidence (derived from correlation strength)

Sort opportunities by rank (descending impact).

Output: sorted opportunities with final ranking scores.

### 4. Compute Trend vs. Prior Snapshot (audit-history)

Establish the trend before rendering the report so the report — and any `rrd-ci-gate` wrapper — can consume it.

1. Resolve the history file: `{rrd_artifacts}/audit-history/{target_project}.json`.
2. If it exists and its `runs` array is non-empty, the **prior snapshot** is the last entry (`runs[-1]`). If it is absent or empty, this is the **first tracked run** — there is no trend; the report states "first tracked run, no trend to report".
3. Give every ranked Opportunity a stable `fingerprint`: a hash of its root-cause family plus its primary affected target (file/symbol). It must be stable across runs so the same defect matches run-to-run regardless of ordering.
4. Diff the current fingerprints against the prior snapshot's:
   - **New** — present now, absent in the prior snapshot
   - **Resolved** — present in the prior snapshot, absent now
   - **Persisting** — present in both
5. Carry the trend (counts plus the New/Resolved/Persisting fingerprint sets, each tagged with risk tier) into the report and hold it for step 7.

### 5. Generate HTML Report (from step-03)

Render the `audit-report-template.html` template with:
- Summary: total opportunities, breakdown by risk tier, top 3 recommended fixes
- **Detector coverage**: the run ledger from Create mode — 10/10 completed, or which families were `failed`/`skipped` and why. This is not optional trivia; a partial run must be visibly labeled as partial, not presented as a clean full audit.
- **Trend vs. last tracked run**: new / resolved / persisting counts by risk tier (or "first tracked run" if no prior snapshot)
- Opportunities table: rank, title, scope, risk tier, confidence, evidence count, proposed fix count
- Detailed findings: for each opportunity in rank order, full description, supporting findings, correlations, diff proposals
- Confidence key and methodology notes

Write to: `{rrd_artifacts}/audit-report-{target_project}-{date}.html`

### 6. Write Diff Proposals

For each Opportunity's supporting findings that have a clear fix:
- Generate diffs for the proposed refactoring
- Write to `{project-root}/proposals/{finding_file}.{finding_id}.patch`

### 7. Write Audit-History Snapshot

Persist this run so the next run (and `rrd-ci-gate`) can compute a trend against it.

- Path: `{rrd_artifacts}/audit-history/{target_project}.json` (create the `audit-history/` directory if missing).
- Load the existing file, or start `{ "schema_version": 1, "target_project": "{target_project}", "runs": [] }`.
- **Append** this run's snapshot to `runs` (do not overwrite prior runs — the prior snapshot read in step 4 must survive), then cap `runs` to the most recent **20** entries, dropping the oldest.
- Run-snapshot shape:

```json
{
  "generated_at": "{date}",
  "report_path": "{rrd_artifacts}/audit-report-{target_project}-{date}.html",
  "totals": { "opportunities": 0, "critical": 0, "high": 0, "medium": 0, "low": 0 },
  "opportunities": [
    {
      "fingerprint": "<stable hash: root-cause family + primary affected target>",
      "title": "…",
      "risk_tier": "critical|high|medium|low",
      "detector_families": ["…"],
      "affected_components": ["…"]
    }
  ]
}
```

`rrd-ci-gate` reads `runs[-1].opportunities[].fingerprint` + `risk_tier` on the next run to decide whether any **new** at-or-above-threshold opportunity has appeared since last time.

### 8. Final Quality Gate

Before finishing, verify:
- [ ] HTML report is readable and well-formed
- [ ] All opportunities are represented and ranked
- [ ] Trend section is present (or explicitly marked "first tracked run")
- [ ] Audit-history snapshot appended (not overwritten), capped at 20 runs, and its totals match the report summary
- [ ] All diffs are written and linked from report
- [ ] Summary counts match the detailed section

### 9. Summarize to Owner

Report in Ray's voice (terse, evidence-led), in `{communication_language}`:

```
Audited {target_project}. Found {n} opportunities across {m} affected components.
Detector coverage: {completed_count}/10 completed{, failed/skipped: {list} if any}.
Trend vs. last run: +{new} new, -{resolved} resolved (or "first tracked run").

Top 3 by impact:
1. {title} — {scope} scope, {risk_tier} risk
2. ...
3. ...

Report: {report_path}
Diffs: {proposals_folder}
```

**User workflow ends here.** The owner now has the prioritized opportunity backlog and can decide which fixes to apply via `rrd-apply-and-verify`.
