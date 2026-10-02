---
name: 'edit'
description: 'Review and manually correct auto-classifications, invoke detectors for structural confirmation. Output: curated findings (user can stop here).'
nextMode: 'validate'
---

# Edit: Classify & Review

## MODE GOAL

For every test's aggregated history from Create, determine which of the four buckets it belongs to (Real Failure / False Positive / False Negative / Healthy), with cited evidence. User can manually correct classifications and add structural confirmation via detector invocations.

## INPUT

Read from Create mode's output: `{project-root}/.refactor-radar-work/test-runs-parsed-{target_project}.json`

## SEQUENCE

### 1. Consistent Fail → Real Failure

If every run for a test shows the same/similar failure: report as a Real Failure. Do not adjudicate whether the underlying app-code bug is correct or not — this workflow doesn't fix application logic, only test-result reliability. Do sub-classify the failure mode itself (see "Failure Mode Sub-Classification" below) — that's a claim about *what kind of thing* broke, not a claim about *whether* the app should have behaved differently.

### 2. Mixed Status → False Positive Candidate (Requires Manual Review)

If a test shows both pass and fail across runs on unchanged code:

1. Read the actual failure message/stack trace from every failing run — not just the first one.
2. Pick a candidate structural cause from the real error text:
   - Timeout/element-not-found/stale-element errors → `rrd-detect-instability` (fragile selectors, fixed waits, unhandled overlays)
   - Record-not-found/duplicate-key/state-dependent errors → `rrd-detect-dependencies` (shared fixtures, order-dependence, shared live state) or `rrd-detect-data-issues` (data lifecycle)

3. **Invoke the matching detector skill itself, scoped to the flagged target** — not just its knowledge fragment read from memory. Pass the target project and the specific flagged file/class/method as args (e.g. `project {target_project}, scope to {flagged_file_or_target}`) so it examines that code specifically rather than sweeping the whole project. This produces an actual, independently-reproducible Finding to cite.
   - If the invoked detector confirms a real structural cause on that target: use its finding as the cited evidence for this False Positive.
   - If the invoked detector finds nothing on that specific target despite the error text superficially matching a pattern: do not force the classification — say so explicitly, keep it as a lower-confidence False Positive candidate, and note that the structural cause is unconfirmed.

4. **User Review**: Display the auto-classification and ask the user:
   - Accept this classification? (yes/no)
   - Correct the failure-mode tag if needed (app-error / env-error / data-error / script-bug)
   - Add notes or context about this flaky test

5. Record which specific runs failed, with which specific error, the specific structural cause found (with the invoked detector's own finding as the citation), and the user's confirmation/correction.

### 3. Consistent Pass → False Negative Candidate (Requires Code Review)

If every run for a test passes: read the actual test source in full (`code-graph query symbol`/`Read`) and check for the patterns in `analyze-test-reliability.md` Step 3 (swallowed exceptions, unreachable assertions, tautological/weak assertions, race-condition false passes, no-op tests). Cross-check duration against peer tests doing comparable work as a corroborating (not sufficient) signal.

- If a disqualifying pattern is found: **False Negative**, with the exact line(s) quoted.
- If the assertions are real, reachable, and specific: **Healthy**.

**User Review**: Display the classification and exact source lines, ask the user to confirm.

### 4. Failure Mode Sub-Classification (app / env / data / script)

For every Real Failure and every False Positive whose structural cause is confirmed, additionally tag the failure mode:

| Tag | The failure text/behavior indicates... |
|---|---|
| **app-error** | Genuine application/business-logic bug — wrong response body, unexpected state, an assertion that's correctly catching a real defect |
| **env-error** | Infrastructure/environment — connection refused, DNS failure, container not ready, resource exhaustion, timeout against a live dependency |
| **data-error** | Test-data related — record not found, duplicate key, unexpected pre-existing state |
| **script-bug** | The automation code itself is wrong — bad locator, off-by-one, incorrect assertion logic |

Derive the tag from the actual error text and stack trace.

### 5. Record Confidence Per Finding

Per `evidence-and-diff-discipline.md`:
- **High**: 5+ runs, structured log format
- **Medium**: minimum run count with structured format, or more runs from console-text source
- **Low**: anything short of that, must be stated explicitly

### 6. Output: Curated Findings

Write `{project-root}/.refactor-radar-work/test-runs-classified-{target_project}.json` containing:
- All classified findings (Real Failures / False Positives / False Negatives / Healthy)
- User corrections and notes
- Invoked detector findings as citations
- Confidence levels and failure-mode tags

**User can stop here.** Validate mode (report generation) is optional. If the user stops, they have the curated findings.

### 7. Continue to Validate (Optional)

If the user confirms, proceed to the Validate mode. Otherwise, end.
