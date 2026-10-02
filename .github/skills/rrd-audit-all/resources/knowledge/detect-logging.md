# Detect Logging Heuristics

## What Success Looks Like

The owner learns exactly which error paths, external calls, and loops carry no diagnostic signal — so that when something breaks or flakes in the future, there's a log line to point at instead of a stack trace and a shrug. This axis is different from the other five: it isn't measuring test-suite reliability (Dependencies/Instability/Data Issues) or code maintainability (Duplication/Complexity) directly — it's **instrumentation-for-diagnosability**, deliberately built as a bridge between Category 2 (refactor for best practices) and Category 1 (analyze real execution logs). Every gap this detector closes today is a future `rrd-analyze-test-reliability` run that gets a real error message instead of "test failed, cause unknown."

## Approach

The graph has no precomputed "has logging" or "has catch block" property the way it has `complexity`/`cognitive` — locate candidates via what the graph *does* expose, then confirm by reading source.

### 1. External-Call Sites (via `CALLS` edges)

```bash
code-graph query calls-matching --param 'pattern=.*(request|http|fetch|execute|query|driver|client|connect|open|read|write|send).*' --limit 500
```

The pattern is a case-insensitive regex that must match the whole callee name or call text (hence the leading and trailing `.*`). Each row gives the calling function, `path`, `line`, the `call`, and whether the call sits inside a loop (`in_loop_depth`).

Any function calling out to something that can fail (HTTP, DB, WebDriver, filesystem, subprocess) is a candidate. The callee-name regex is a starting filter, not a final one — read the actual call to confirm it's a real failure-capable boundary, not a false match (e.g. a `queryParams` variable name matching `.*query.*` incidentally).

### 2. Failure-Capable Loops (via precomputed loop properties)

```bash
code-graph query loops --limit 200
```

Reuses Detect Complexity's existing properties rather than inventing new graph metrics. A loop is only a real candidate if its body also contains a failure-capable operation (an external call, a parse/cast, an index access) — cross-reference against the external-call query above, or read the loop body directly.

### 3. Catch Blocks (via `Grep`, no graph representation)

The indexer doesn't model exception-handling blocks as graph nodes, so use `Grep` for the language's syntax directly: `catch`, `except`, `rescue`, `.catch(`. Scope with `glob`/`type` and `path` per the Tool Usage Discipline in `evidence-and-diff-discipline.md`, and don't conclude "no catch blocks" from zero matches without checking the pattern and scope first. Attribute each hit to its containing function with `code-graph query file-symbols --param path=<relative path>` (the function whose `line`..`end_line` range contains the hit).

## What Counts as a Gap (confirm by reading actual source — never from the query alone)

| Gap type | Pattern |
|---|---|
| **Silent catch** | Catch block with no logging statement of any kind — the exception is swallowed with zero trace it ever happened |
| **Exception-dropping log** | Catch block logs a message string but not the original exception/error object (e.g. `log.warn("failed: " + e.getMessage())` instead of passing `e` itself) — breaks stack-trace continuity, the single most common gap that turns a 30-second root-cause lookup into a guessing exercise |
| **Unlogged external call** | A call to something that can fail (network, DB, driver, filesystem) with no logging on the failure path — no log on a non-2xx response, no log before/after a retry, no log on a timeout |
| **Unlogged loop failure path** | A loop body containing a failure-capable operation with no per-iteration or per-failure log signal — if iteration 47 of 200 is where things went wrong, nothing points at it |
| **Context-free stack trace** | Catch block calls `printStackTrace()` (or equivalent) on the real exception object — so, strictly, neither silent nor exception-dropping — but with no operational context (which locator, which URL, which attempt number) and no integration with a structured log/report the test framework actually captures. Weaker than a routed `log.warn(msg, e)` call even though the raw trace technically exists somewhere in console output. Found in a real project's retry-loop and page-load-wait methods, both calling `error.printStackTrace()` with zero indication of *what* was being attempted. |

None of these are findings from the graph query alone — always read the actual code (`code-graph query symbol`/`Read`) and quote the specific line(s) missing the log statement or dropping the exception object.

## Failure-Classification Tagging

Every missing-log site found above should also be tagged with which failure category it would most likely mask if it fired silently, using the same four-way split `rrd-analyze-test-reliability` uses when classifying a real failure from run history:

| Tag | Fires when the masked failure is... |
|---|---|
| **app-error** | A genuine application/business-logic bug (wrong response body, unexpected state, assertion-worthy behavior) |
| **env-error** | Infrastructure/environment (connection refused, DNS failure, container not ready, resource exhaustion) |
| **data-error** | Test-data related (record not found, duplicate key, unexpected pre-existing state) |
| **script-bug** | A bug in the test/automation code itself, not the system under test (wrong locator, off-by-one, bad assertion logic) |

Infer the tag from what the call site actually does, not from the gap type alone: an unlogged external-call site calling a live HTTP endpoint is usually **env-error**-leaning; an unlogged catch block around a data-lookup helper is usually **data-error**-leaning; a silent catch wrapping a locator interaction is usually **script-bug**-leaning (see also `rrd-detect-instability`/`rrd-detect-locators` if that same site is independently flagged there). State the tag as a leaning, not a certainty — the whole point of this axis is that there's currently no log line to confirm which one actually happened; the tag is a hypothesis for the fix's docstring/log-message content (e.g. "log the response status" for an env-leaning site vs. "log the record ID being looked up" for a data-leaning site), not a settled classification.

This tagging directly feeds `rrd-analyze-test-reliability` Step 3: a False Positive candidate whose error text traces back to a site this detector already tagged **env-error** gets that classification for free instead of re-deriving it from scratch.

## The "Silent Catch That Causes a Confusing Downstream Failure" Exception, Worked Example

The "What NOT to Flag" section below carves out an exception for catch blocks whose fallback path could cause a confusing downstream failure with no trace back to the original decision — this is worth a concrete example, since it's easy to under-apply. A static initializer loading a config file, catching `Exception`, calling `printStackTrace()`, and leaving a `Properties` object `null` on failure is *technically* not silent (the trace prints) — but every later call site that reads from that `null` `Properties` object throws an unrelated `NullPointerException`, with nothing connecting that NPE back to "the config file failed to load at startup." This is exactly the exception case: flag it, and propose failing fast (throw a wrapped exception at the point of failure) rather than only adding a log line — a log line alone still leaves the confusing downstream NPE in place, it just adds a second, disconnected signal instead of preventing the disconnect.

## Failure-Diagnostics Capture (Screenshots/Traces)

Distinct from *logging* a failure (a text signal) is *capturing* a failure (a visual/replayable artifact) — a missing screenshot or trace is its own diagnosability gap, common enough in Selenium/Playwright suites to check for explicitly.

### What to Check

Search for the target framework's failure-hook mechanism and confirm it actually captures something:

- **Selenium/TestNG/JUnit**: an `@AfterMethod`/`@AfterEach`(or equivalent)-scoped hook that checks `ITestResult`/test-outcome status and calls a screenshot method (`((TakesScreenshot) driver).getScreenshotAs(...)`) only on failure — not unconditionally (an unconditional screenshot on every test is noise, not a gap fix).
- **Playwright**: `use.screenshot`/`use.trace` config set to `'only-on-failure'`/`'retain-on-failure'` in `playwright.config.*`, or an explicit `page.screenshot()`/`context.tracing.stop({ path: ... })` call inside a failure branch.
- **Cypress**: screenshots-on-failure are on by default; check only that `screenshotOnRunFailure` hasn't been explicitly disabled.

### What Counts as a Gap

- No failure-hook mechanism exists at all — a test suite with zero capture-on-failure anywhere.
- A hook exists but doesn't actually branch on failure (captures unconditionally, or the failure check is dead code).
- Capture exists for one test runner/browser configuration but not others in a multi-browser/multi-runner suite (partial coverage).

### What NOT to Flag

- A project with `retain-on-failure`/equivalent already correctly configured — say so explicitly as checked-and-clean, don't manufacture a finding.
- Capturing on every run (not just failure) is a cost/noise concern for the owner to weigh, not a diagnosability gap this detector should flag — the axis is "can I diagnose a failure when it happens," not "should you capture less."

## Cross-Detector Corroboration

A function flagged here that's *also* flagged by Detect Instability (fragile wait/selector) or Detect Dependencies (shared/coupled state) is a higher-priority fix than either signal alone — it's exactly the kind of site likely to fail intermittently *and* currently gives no signal when it does. Cite both findings' evidence when this overlap occurs, per `evidence-fusion-heuristics.md`'s cross-family corroboration rule.

## What NOT to Flag

- Catch blocks that intentionally suppress a truly benign, expected condition (e.g. catching `NumberFormatException` to fall back to a documented default) are not gaps by themselves — only flag if the fallback path itself could later cause a confusing downstream failure with no trace back to this decision.
- A loop with no failure-capable operation in its body (pure in-memory transformation, no I/O, no parsing, no external call) is not a candidate regardless of `loop_depth` — logging every iteration of a pure computation is noise, not diagnosability.
- Findings inside test methods themselves are lower priority than findings in the application/framework/page-object code the tests call — a test's own failure is already visible in the test report; the diagnosability gap that matters is one layer down, in the code whose *silent* failure caused the test to behave unexpectedly.
- Don't propose swapping a caught-and-logged exception into a re-thrown one unless the owner's control flow already expects failures to propagate — adding a log line is almost always the right fix; changing control flow is a separate, larger decision the owner should make explicitly.

## Reference Pattern (generalized, not project-specific)

The canonical "silent catch" this axis exists to catch:

```java
try {
    element.click();
} catch (Exception e) {
    // swallowed — the caller has no idea this failed
}
```

versus the fix that actually helps a future `rrd-analyze-test-reliability` run:

```java
try {
    element.click();
} catch (Exception e) {
    log.warn("click failed on {}: {}", element, e.getMessage(), e);  // e itself passed, not just its message
}
```

The second version doesn't change behavior — it changes whether the next flaky-test investigation has anything to look at.
