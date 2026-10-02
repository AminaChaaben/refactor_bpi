# Audit All Validation Checklist

**Workflow Architecture:** Create → Edit → Validate (tri-modal). User can stop after Create or Edit.

## CREATE MODE — Run All Detectors

- [ ] `code-graph status` run; index confirmed present and not stale (re-indexed first if needed)
- [ ] Knowledge fragments loaded: `evidence-and-diff-discipline.md` and 10 detector knowledge fragments
- [ ] Detector run ledger maintained (one entry per detector: completed/failed/skipped), updated as each detector finishes — not reconstructed afterward
- [ ] All 10 detectors (DD, DI, DT, DU, DC, DL, DF, DO, DY, DV) confirmed `completed` in the ledger — **halt and surface to the owner if any is `failed`/`skipped` rather than treating it as a 0-finding result**
- [ ] Each detector generated findings with: id, detector_family, file, line, title, description, evidence, confidence, affected_target, root_cause_signals
- [ ] No filtering or grouping applied — raw output only
- [ ] All findings pooled into single array
- [ ] Raw findings written to `{project-root}/.refactor-radar-work/findings.json`
- [ ] Coverage reported: total findings by detector, unique affected targets
- [ ] No null/empty evidence fields

**Output:** User can review raw findings and decide whether to proceed to Edit mode.

## EDIT MODE — Fuse Evidence & Group Opportunities

- [ ] Every pair of findings checked for shared affected_target and root_cause_signals
- [ ] Graph traversal verified direct call paths between correlated findings
- [ ] Correlations recorded with strength (strong/medium/weak)
- [ ] Correlations pooled and written to `{project-root}/.refactor-radar-work/correlations.json`
- [ ] Opportunities formed by grouping correlated findings
- [ ] Each Opportunity includes: title, description, problem_statement, supporting_findings[], correlations[], root_causes[], affected_components[]
- [ ] All findings from Create represented in at least one Opportunity (coverage check)
- [ ] Opportunities written to `{project-root}/.refactor-radar-work/opportunities.json`
- [ ] Grouping summary reported: opportunity count, findings per opportunity (min/max/avg), correlation strength distribution

**Output:** User can review grouped opportunities and decide whether to proceed to Validate mode.

## VALIDATE MODE — Impact, Ranking & Report

- [ ] Opportunities JSON complete: all required fields present, no nulls
- [ ] Impact analysis run: false-fail risk, scope (isolated/moderate/codebase-wide), risk tier (critical/high/medium/low) estimated for each
- [ ] Impact written to `{project-root}/.refactor-radar-work/opportunities-with-impact.json`
- [ ] Ranking formula applied: false-fail impact (primary) + scope (secondary) + fixing cost + confidence
- [ ] Opportunities sorted by rank (descending)
- [ ] HTML report generated from `audit-report-template.html`: summary, opportunities table (rank/title/scope/risk/confidence), detailed findings
- [ ] Report written to `{rrd_artifacts}/audit-report-{target_project}-{date}.html`
- [ ] Diff proposals written for each fixable opportunity to `{project-root}/proposals/{file}.{finding_id}.patch`
- [ ] Report completeness verified: all opportunities represented, no missing entries
- [ ] Detector run ledger from Create mode carried into the report summary (10/10 completed, or explicit list of failed/skipped families and why)
- [ ] Trend computed against `{rrd_artifacts}/audit-history/{target_project}.json` if prior runs exist; reported as "first tracked run" if not
- [ ] This run's summary appended to history file (not overwritten), capped at 20 entries

**Output:** Final HTML report and diff proposals. Optional next step: `rrd-apply-and-verify` to test fixes.
