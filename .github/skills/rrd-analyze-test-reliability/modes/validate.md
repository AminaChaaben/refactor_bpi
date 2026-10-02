---
name: 'validate'
description: 'Verify curated findings, generate final report with diffs, check quality gates. Output: findings summary and proposals.'
---

# Validate: Generate Report & Verify

## MODE GOAL

Verify all curated findings from Edit mode, generate the final findings summary and diff proposals, and run quality gates to ensure the report is complete and accurate.

## INPUT

Read from Edit mode's output: `{project-root}/.refactor-radar-work/test-runs-classified-{target_project}.json`

## SEQUENCE

### 1. Quality Gates — Prerequisite Validation

Before proceeding, verify:
- [ ] Findings JSON exists and contains all classified test categories
- [ ] Every classified finding has: title, description, failure-mode tag (if applicable), confidence level, run IDs, cited evidence
- [ ] No findings have null/missing evidence
- [ ] Every False Positive has a detector-invoked structural cause or is explicitly marked lower-confidence
- [ ] Every False Negative has exact source line citations
- [ ] All run IDs match the parsed runs from Create mode

If any check fails, **HALT**. Surface the missing data and ask the user to return to Edit mode to complete the classifications.

### 2. Write Diff Proposals

- **False Positives:** propose the stabilization fix implied by the matched structural heuristic (e.g. a stable wait/selector fix for Instability matches, a fixture-isolation fix for Dependencies/Data Issues matches) — same shape of diff those detectors would produce.
- **False Negatives:** propose a diff that fixes the actual defect (stop swallowing the exception, move/fix the unreachable assertion, replace a tautological check with a real one, replace a fixed sleep with an explicit wait-for-condition).
- **Real Failures:** no diff proposed by this workflow — flagged for the owner's own investigation into the app-code bug.

Write every diff to `{project-root}/proposals/`, never applied directly. Name as `{test_file_or_class}.{finding_id}.patch`.

### 3. Write Findings Summary

Write `{rrd_artifacts}/test-reliability-{target_project}.md`, grouped into four sections (Real Failures / False Positives / False Negatives / Healthy), each finding citing its run IDs, real error text or source lines, confidence level, its failure-mode tag where one was assigned (Real Failures and confirmed False Positives only — False Negatives and Healthy aren't failure-mode candidates), and (for the first three) a link to its diff proposal.

### 4. Validate Report Completeness

Before finalizing, verify:
- [ ] Report file is readable and contains all sections
- [ ] Every classified finding is represented (no missing entries)
- [ ] All diffs are written to proposals/ and linked from report
- [ ] Confidence levels are stated for every finding
- [ ] Failure-mode tags are present where appropriate
- [ ] No classified test appears twice

### 5. Summarize to Owner

Report in Ray's voice (terse, evidence-led), in `{communication_language}`:

```
Analyzed {n} runs across {m} tests.

Real Failures: {count} — consistent across all runs, {breakdown by failure-mode tag}
False Positives: {count} — flaky, {breakdown by matched structural cause and failure-mode tag}
False Negatives: {count} — pass but don't meaningfully verify anything
Healthy: {count}

Report: {report_path}
Diffs: {proposal_folder}
```

### 6. Final Quality Gate Check

Verify the report is complete and the summary is accurate. If any discrepancies are found, correct them before finishing.

### 7. Done

The workflow is complete. The user now has:
- **Findings summary**: `{rrd_artifacts}/test-reliability-{target_project}.md`
- **Diff proposals**: `{project_root}/proposals/{test_file}.{finding_id}.patch` (for False Positives and False Negatives)
- **Classified data**: `{project_root}/.refactor-radar-work/test-runs-classified-{target_project}.json` (curated findings with user corrections)

Next step (optional): Invoke `rrd-apply-and-verify` to apply proposed diffs and re-run tests to validate the fixes.
