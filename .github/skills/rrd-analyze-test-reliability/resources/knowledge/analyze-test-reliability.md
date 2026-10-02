# Analyze Test Reliability Heuristics

## What Success Looks Like

The owner learns, from real execution history (not speculation), which of their test failures are genuine bugs, which are false positives (the test cried wolf — code is fine, the test is flaky), and which are false negatives (the test says everything's fine, but it isn't really checking). This is log-first, multi-run analysis — a different shape of work from the five structural detectors, which is why it's its own workflow rather than a sixth detector. It reuses those detectors' heuristics as the *explanation* layer once a test's behavior across runs tells you *where to look*.

Generalizes across stacks — Java/Selenium with JUnit/Surefire XML and Jenkins console logs, TypeScript/Playwright with its own JSON report format, or any other combination that produces a parseable per-test outcome record. Not hardcoded to one language or CI system.

## Important Constraint: There Is No Trace-Ingestion Tool Anymore

An earlier knowledge-graph backend (`codebase-memory-mcp`) exposed `ingest_traces`, which accepted `{caller, callee, count}` triples to boost graph edge weights with real call-frequency data. **The code graph has no equivalent**: `code-graph` indexes source structure and metrics only, and nothing ingests execution history into it. Any older Refactor Radar knowledge fragment that says "correlate with `ingest_traces` if logs are available" refers to a capability that no longer exists; skip it. This workflow's log parsing (Step 2 below) is unaffected: it is a separate, from-scratch capability built with `Read`/`Grep`/`Bash`, and the graph is still used afterward (`code-graph query symbol`, `callers`, `callees`) to explain *why* a test behaves the way its execution history shows.

---

## Step 1: Normalize Whatever Log Format the Owner Has

Every source format gets parsed into the same shape:

```
TestRun {
  test_id: string        // stable identifier, e.g. fully-qualified test method name
  file: string
  run_id: string          // which execution this came from — a build number, a timestamp, a log filename
  status: "pass" | "fail" | "error" | "skipped"
  duration_ms: number
  error_message: string | null
  stack_trace: string | null
}
```

### JUnit/Surefire XML (Java/Selenium, and anything else using the JUnit report convention)

One `TEST-*.xml` file per test class per run. Each `<testcase>` element is one `TestRun`:
- No `<failure>`/`<error>`/`<skipped>` child → status `pass`
- `<failure>` child → status `fail`, message/stack from the element's text and `message` attribute
- `<error>` child → status `error` (distinct from assertion failure — often an unhandled exception, itself a signal worth separating from `fail` in the report)
- `<skipped>` → status `skipped`, generally excluded from reliability analysis (never ran, can't be flaky or lying)

Read the file with a real XML-aware pass (e.g. `python -c "import xml.etree.ElementTree..."` via Bash, or a language-appropriate library) — do not regex-parse XML; attribute ordering and self-closing tags will break naive patterns.

### Playwright JSON Report (TypeScript, or any Playwright-based stack)

The `test-results.json` (or equivalent) has a nested `suites → specs → tests → results` structure. Each `results[]` entry (Playwright records one per retry) is a `TestRun`:
- `status` field maps directly (`passed`/`failed`/`timedOut`/`skipped` — normalize `timedOut` to `fail` with the timeout noted in `error_message`)
- `retry` count matters: if `results.length > 1` for one test, Playwright itself already detected instability and retried — a test with `retry > 0` even in a single log file is a false-positive candidate without needing multiple separate runs.
- `duration` is in milliseconds already.

### Jenkins Console Text (or any raw CI console log)

Best-effort, lower confidence than structured formats — say so explicitly in any finding sourced this way. Look for the underlying test runner's own summary lines embedded in the console output (Surefire prints `Tests run: X, Failures: Y, Errors: Z, Skipped: W -- in <ClassName>`; Playwright's list reporter prints `✓`/`✘` lines per test with duration). Extract what the embedded runner already structured rather than trying to infer results from arbitrary log prose.

### Multiple Runs Are Required

A single run's failure cannot distinguish a real bug from flakiness — it could be either. This workflow needs **at least 2-3 runs** of the same suite (separate log files/directories, or a Playwright report with retries) to compute per-test consistency. If fewer exist — the common case, not an edge case, since most owners invoking this workflow don't already have multi-run logs sitting around — `step-01b-generate-execution-logs.md` delegates to `rrd-establish-execution-baseline`, which owns turning that into a real multi-run set: diagnosing and fixing whatever environment/discovery-configuration blockers stand in the way (a wrong platform/driver config, a missing fixture, a test-discovery naming-convention gap that silently excludes real tests from ever running, or even a *partial* blocker where only some dimensions like a specific browser are affected), then generating and preserving the runs. Only skip generation and proceed with fewer runs if the owner explicitly declines it.

### Discovery Gap Check Applies Even to Owner-Supplied Logs

If the owner brings their own pre-existing multi-run logs (skipping generation entirely), still cross-check the report's test count against the real number of `@Test`-annotated methods in source before classifying anything — the same naming-convention discovery gap that `rrd-establish-execution-baseline` checks for when generating logs can just as easily be silently present in logs the owner already had lying around. Do this in Step 2 (parsing), unconditionally, not only when this workflow generated the logs itself.

---

## Step 2: Classify Each Test by Its History

For each `test_id`, aggregate its `TestRun` records across all runs:

### Consistent Fail (all runs fail with the same/similar error)

Likely a **real failure** — the app has a genuine bug, or the test correctly caught a regression. This workflow does not adjudicate whether it's an app bug or a correct test; it reports it as "not a false positive, not a false negative" and stops there — actually fixing app-code bugs is outside a test-reliability workflow's scope.

### Mixed Status (some runs pass, some fail, code unchanged between runs)

**False positive** candidate — the test is flaky, not the app. This is where the existing structural detectors provide the explanation:
- Cross-reference the failing test's source against `detect-instability.md` (fragile selectors, fixed waits, unhandled overlays)
- Cross-reference against `detect-dependencies.md` (shared fixtures, order-dependence, shared live state — does this test's flakiness correlate with which other tests ran before it, or with parallel execution?)
- Cross-reference against `detect-data-issues.md` (does the test depend on data that another test or run mutates?)

Always **read the actual failure messages/stack traces across the failing runs** before attributing a cause — "flaky" is not itself an explanation; a timeout error points at Instability, a "record not found"/"duplicate key" error points at Dependencies or Data Issues. Match the real error text to the real heuristic, don't guess from the test name alone.

### Consistent Pass (all runs pass)

Not automatically "healthy" — proceed to Step 3 to check whether it's actually verifying anything, i.e. a **false negative** candidate.

---

## Step 3: False Negative Detection — "Tests That Lie"

A test that always passes is only trustworthy if it's actually capable of failing when the app is broken. Read the real test source (`code-graph query symbol`/`Read`) for every consistent-pass test and check for these patterns:

### Swallowed exceptions
```java
try {
    doSomethingThatShouldThrowOnFailure();
} catch (Exception e) {
    log.warn("ignoring: " + e.getMessage());  // test continues and passes
}
```
Any broad `catch` inside a test body that doesn't re-throw, `fail()`, or assert on the caught exception is a candidate — the test can never fail via that code path no matter what breaks.

### Assertions that never execute
An assertion behind a conditional that's structurally always false (or always true, skipping the assertion branch), or after an early `return`, or inside a callback that's registered but never actually invoked in the test's execution (e.g. an async callback assertion where the test method returns before the callback fires and the framework doesn't await it — the process exits "green" without the assertion ever running).

### Tautological or overly weak assertions
- `assertTrue(true)`, `assertNotNull(x)` where `x` is constructed a few lines above and can never be null
- A status-code check accepting an implausibly wide range (e.g. `status < 500` treating every 2xx/3xx/4xx as pass)
- An existence check (`element.isDisplayed()`) where the selector is broad enough to match unrelated elements on the page, so it would report "displayed" regardless of whether the actual feature under test rendered correctly

### Race-condition false passes
A fixed short `sleep()`/`wait(500)` followed immediately by a read, with no explicit wait-for-condition — occasionally the assertion runs against stale DOM/state and happens to match by coincidence, or the assertion is checking a value that was already correct *before* the action under test even executed (asserting on initial/default state, not post-action state).

### No-op or setup-only tests
Test method body contains only setup/arrange steps with no assertion at all, or the only assertion is on the setup itself, not on any action the test claims to exercise.

### Corroborating signal from execution logs
A consistent-pass test with a **suspiciously short duration** compared to peer tests doing comparable real work (e.g. every other test in the class takes 2-4s, this one takes 80ms) is worth prioritizing for source review — it's a hint, not proof, that something is being skipped rather than actually executed/awaited.

**Every false-negative finding must quote the specific line(s) causing it** — "this test doesn't really check anything" is not a finding without pointing at the exact swallowed catch block, tautological assertion, or unreached code path.

---

## Report Structure

Group findings into four buckets, each with real evidence (run IDs, actual error text, actual source lines — never a summary without a citation):

1. **Real Failures** — consistent fail across runs. Reported for awareness; not classified further by this workflow.
2. **False Positives** — inconsistent results, explained via the matching structural detector (`rrd-detect-instability`/`rrd-detect-dependencies`/`rrd-detect-data-issues`) actually invoked and scoped to the flagged target — not its knowledge fragment pattern-matched from memory — with a stabilization diff proposed the same way that detector would.
3. **False Negatives** — consistent pass, but source analysis shows the test can't meaningfully fail; diff proposed to fix the actual assertion/exception-handling/wait logic.
4. **Healthy** — consistent pass, and source review shows real, reachable, non-tautological assertions. Report the count; don't enumerate every one in detail unless the owner asks.

## Closing the Loop — Gated on Explicit Approval

A diff is a proposal, not a confirmed fix. After reporting, offer (don't perform) applying it via `rrd-apply-and-verify` and re-running enough times to confirm the flakiness is actually reduced — **wait for the owner's explicit yes for that specific diff** before invoking anything. If approved, report the real before/after run comparison, including honestly reporting a partial result rather than rounding up to "fixed."
