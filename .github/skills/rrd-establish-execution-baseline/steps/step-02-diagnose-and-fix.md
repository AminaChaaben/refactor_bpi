---
name: 'step-02-diagnose-and-fix'
description: 'First run, diagnose blocker type in strict order, fix via the one exception, bounded retry'
nextStepFile: '{skill-root}/steps/step-03-generate-multi-run-set.md'
---

# Step 2: Diagnose and Fix

## STEP GOAL

Get from "first run attempted" to "genuine result, in whole or in the dimensions that are genuine" — diagnosing the real blocker type before attempting any fix, since compile failures, discovery gaps, and environment blockers each need a different fix and conflating them wastes cycles.

## SEQUENCE

### 1. First Run

Run the suite once. Verify the result via the toolchain's own structured report (Surefire/TestNG XML, Playwright JSON, pytest junit-xml) — never console text or exit code alone.

### 2. Diagnose in Strict Order

**a. Build/compile-level failure** (nothing ran at all): wrong JDK vs JRE, a flaky dependency download, a missing toolchain. Fix the obvious ones (e.g. point `JAVA_HOME` at an actual JDK) and retry once before treating it as a real blocker.

**b. Discovery gap** (suspiciously few or zero tests ran relative to what's really annotated in source): cross-check the report's test count against the real number of test-annotated methods — `code-graph query by-decorator --param decorator=Test` for annotated methods, or a direct `Grep` for `@Test`/`it(`/`test(`, independent of what the report claims. A real, recurring pattern: default TestNG/JUnit discovery by filename convention (`**/*Test.java`/`**/*Tests.java`/`**/*TestCase.java`) silently excludes a real test class named outside that pattern — the report shows "0 failures" having tested nothing. If the counts don't match, this is the cause, not "few tests exist."

**c. Environment/config blocker — whole-run**: wrong driver/browser version pin, a "remote" platform config pointing at infrastructure that isn't running (a Selenium Grid, a Docker container), a missing target fixture, missing credentials. The whole run is blocked the same way regardless of any dimension (browser, tag, environment) you'd otherwise slice it by.

**d. Environment/config blocker — partial**: some dimensions of the run are genuine, others aren't — e.g. one browser's driver resolves fine, another's doesn't (`NoSuchDriverException` despite the browser being installed), or a browser isn't installed at all. This is common and easy to conflate with (c) — diagnose and exclude at the dimension level, don't treat the whole run as blocked when only a slice of it is.

**e. Genuine result** — none of the above apply (in whole, or in the dimensions not excluded by (d)). Proceed to Step 3.

### 3. Fix the Blocker (for a, b, c — not d, which is excluded rather than fixed)

Diagnose the specific root cause with evidence (read the actual config/source causing it), then:

- **Missing fixture the project's own config already references** (e.g. a config-pointed local file/site never included in the repo): create it directly, matching every locator/selector the test code expects — read those first, don't guess. Net-new, not a modification of existing tracked source.
- **Change to existing tracked source/config** (version pin, platform constant, class rename for discovery, API migration): write a diff proposal to `{target_project_root}/proposals/`, then apply it via `rrd-apply-and-verify` — invoke that skill, or follow its documented safety gate and full-file-read-before-apply discipline inline.

Re-run once after each fix. Repeat for a new blocker if one surfaces — bounded to 3 fix cycles total; if still unresolved, stop and report the specific remaining blocker to the owner.

### 4. Record Excluded Dimensions (for d)

For any partial environment blocker, record which specific dimension (browser/tag/environment) is excluded and the exact error/reason — this goes into the manifest in Step 4, not silently dropped.

### 5. Continue

Load `./step-03-generate-multi-run-set.md`.
