---
name: 'step-02-investigate'
description: 'Locate catch blocks, external-call sites, and failure-capable loops via the graph, confirm missing/broken logging by reading source'
nextStepFile: '{skill-root}/steps/step-03-report-and-propose.md'
---

# Step 2: Investigate

## SEQUENCE

### 1. Locate Candidate Sites via the Graph

The graph has no dedicated "has logging"/"has catch block" property. Locate candidates via `CALLS` edges and the existing loop properties, plus `Grep` for catch blocks, then confirm by reading source (per `detect-logging.md`).

**External-call sites** — functions that call out to something that can fail (HTTP/DB/driver/filesystem/subprocess):

```bash
code-graph query calls-matching --param 'pattern=.*(request|http|fetch|execute|query|driver|client|connect|open|read|write|send).*' --limit 500
```

The pattern is a case-insensitive regex that must match the whole callee name or call text (hence the leading and trailing `.*`). Each row gives the calling function, `path`, `line`, the `call`, and whether the call sits inside a loop (`in_loop_depth`).

**Failure-capable loops** — reuse Detect Complexity's precomputed loop properties to find loop bodies worth checking for per-iteration log signal:

```bash
code-graph query loops --limit 200
```

**Catch blocks** have no direct graph representation. Use `Grep` for the language's exception-handling syntax (`catch`, `except`, `rescue`, `.catch(`) scoped to the target project, find each hit's containing function with `code-graph query file-symbols --param path=<relative path>`, then cross-reference it against the `calls-matching` results above.

### 2. Read Source and Confirm

For every candidate, read the source (`code-graph query symbol`, or `Read` around the `Grep` hit) and check for these patterns (full detail in `detect-logging.md`):

- Catch block with no logging statement at all
- Catch block that logs a message but not the original exception object (breaks stack-trace continuity)
- External-call site with no logging around the failure path (no log on non-2xx/error response, no log before a retry)
- Loop body containing a failure-capable operation (an external call, a parse/cast, an assertion) with no per-iteration or per-failure log signal

Do not flag from the query results alone — name the concrete gap after reading the actual code.

### 3. Tag Failure Classification

For every confirmed gap, tag which failure category it would most likely mask (app-error / env-error / data-error / script-bug), per `detect-logging.md`'s Failure-Classification Tagging section — infer from what the call site actually does, state it as a leaning not a certainty, and let it inform the fix's log-message content (e.g. log the response status for an env-leaning site, the record ID for a data-leaning site).

### 4. Check Failure-Diagnostics Capture

Independent of the catch-block/logging sweep above, check whether the target's test-failure hook actually captures a screenshot/trace on failure — per `detect-logging.md`'s Failure-Diagnostics Capture section. Search for the framework's failure-hook mechanism (`@AfterMethod`/`@AfterEach` + status check + screenshot call for Selenium/TestNG/JUnit; `screenshot`/`trace` config in `playwright.config.*` for Playwright; `screenshotOnRunFailure` for Cypress) and confirm it actually branches on failure rather than firing unconditionally or being dead code. Report "already configured correctly" explicitly if that's what's found — this is a binary presence/absence check per framework, not a gradient like the logging gaps above.

### 5. Check Cross-Detector Corroboration

Note whether the same function was also flagged by Detect Instability (timing/overlay fragility) or Detect Dependencies (shared/coupled state) — a function that's both flaky-prone *and* unlogged is a materially higher-priority fix than either fact alone, since it's exactly the kind of failure this axis exists to make diagnosable later.

### 6. Filter Test/Fixture Code

Deprioritize findings inside test methods themselves (as opposed to the application/framework code they call) unless the owner asked otherwise — a test's own assertion failure is already visible in the test report; the diagnosability gap that matters most is in the code under test and the framework/page-object layer between the test and the app. The failure-diagnostics capture check (Step 4) is the exception — that check is specifically about the test-runner/suite-level failure hook, which by definition lives in test infrastructure, not application code.

### 7. Continue

Load `./step-03-report-and-propose.md`.
