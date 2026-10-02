# Analyze Test Reliability Validation Checklist

**Workflow Architecture:** Create → Edit → Validate (tri-modal). User can stop after Create or Edit.

## CREATE MODE — Prerequisites & Log Scanning

- [ ] `code-graph status` run; index confirmed present and not stale (re-indexed first if needed)
- [ ] Knowledge fragments loaded: `evidence-and-diff-discipline.md`, `analyze-test-reliability.md`
- [ ] Log source(s) resolved and format identified (JUnit XML / Playwright JSON / Jenkins console / other)
- [ ] At least 2 separate runs available — if not, offer to generate them via `rrd-establish-execution-baseline` (the common case)
- [ ] If generating logs required a source/config fix: offered to the user to apply via `rrd-apply-and-verify`, never edited directly by this workflow
- [ ] If a discovery gap found (report shows far fewer tests than real `@Test`-annotated methods), diagnosed and explained
- [ ] All log sources parsed successfully, every TestRun record contains: test_id, file, run_id, status, duration, error/stack_trace
- [ ] Parsing coverage reported: N runs parsed, M distinct tests found, any parse failures listed
- [ ] Raw parsed data written to `{project-root}/.refactor-radar-work/test-runs-parsed-{target_project}.json`

**Output:** User can review raw data and decide whether to proceed to Edit mode.

## EDIT MODE — Classification & Manual Review

- [ ] Every test's full run history aggregated before classifying (not judged from a single run)
- [ ] Consistent-fail tests reported as Real Failures without further false-positive/negative classification
- [ ] Mixed-status tests: actual failure messages/stack traces read across runs
- [ ] For each Mixed-status test: the matching detector skill (`rrd-detect-instability`/`rrd-detect-dependencies`/`rrd-detect-data-issues`) actually invoked, scoped to the flagged target — not just its knowledge fragment pattern-matched from memory
- [ ] User manually reviews each classification (accept/correct/skip) and can update failure-mode tags
- [ ] Consistent-pass tests: source read in full for false-negative patterns (swallowed exceptions, unreachable assertions, tautological checks, race-condition false passes, no-op tests) before being called Healthy
- [ ] User confirms False Negative classifications and exact source lines
- [ ] Every Real Failure and confirmed False Positive tagged with failure-mode (app-error / env-error / data-error / script-bug), derived from actual error text
- [ ] Genuine ambiguity stated explicitly (not forced into one tag)
- [ ] Every finding cites a specific run ID, error text, or source line — no unsupported classification
- [ ] Curated findings written to `{project-root}/.refactor-radar-work/test-runs-classified-{target_project}.json`

**Output:** User can review curated findings and decide whether to proceed to Validate mode.

## VALIDATE MODE — Report Generation & Verification

- [ ] Quality gates passed: all findings have required evidence, confidence levels, failure-mode tags
- [ ] False positives get a stabilization diff (per the matched structural heuristic's usual fix)
- [ ] False negatives get a diff fixing the actual assertion/exception-handling/wait logic
- [ ] Real failures: no diffs proposed (for owner's own app-code investigation)
- [ ] Every fix written as a diff to the target project's `proposals/`, never applied directly
- [ ] Findings summary written to `{rrd_artifacts}/test-reliability-{target_project}.md`, grouped into Real Failures / False Positives / False Negatives / Healthy
- [ ] All findings cited in report with run IDs, error text/source lines, confidence levels, failure-mode tags
- [ ] All diff proposals listed in report and written to `{project-root}/proposals/`
- [ ] Report completeness verified: every classified test represented, no missing entries
- [ ] Owner notified with summary: count per category, confidence breakdown, report/proposal paths

**Output:** Final findings summary and diff proposals. Optional next step: `rrd-apply-and-verify` to test the fixes.
