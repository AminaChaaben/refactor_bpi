# Establish Execution Baseline Heuristics

This skill's job: get any test suite from zero to N genuine execution runs, independently of any other workflow.

## What Success Looks Like

The owner (or another workflow, like `rrd-analyze-test-reliability`) gets `{run_count}` genuine, preserved execution runs of the target's real test suite — with every environment/discovery blocker along the way diagnosed and fixed via the module's one source-touching exception, not misdiagnosed as "flaky" or silently worked around.

## Known Failure Modes to Check For

1. **Build/compile-level failure** — wrong JDK vs JRE was hit twice. Fix: point `JAVA_HOME` at an actual JDK, retry once.
2. **Discovery gap** — Maven Surefire's default TestNG/JUnit auto-discovery (`**/*Test.java`/`**/*Tests.java`/`**/*TestCase.java`) silently excluded real test classes named outside that convention, twice, out of three real projects. The report showed "0 failures" having tested nothing. Always cross-check the report's test count against the real `@Test`-annotated method count in source (`code-graph query by-decorator --param decorator=Test` or `Grep`) — never trust "few/no failures" as "few/no tests" without this check.
3. **Environment/config blocker, whole-run** — a `PLATFORM="remote"` config constant pointing at a Selenium Grid that wasn't running. Fix: change the constant (via a diff, applied through `rrd-apply-and-verify`).
4. **Environment/config blocker, missing fixture** — a config-referenced local target site that was never included in the repo. Fix: build a minimal stand-in matching every locator the test code expects, read from the actual `@FindBy`/selector declarations — not guessed.
5. **Environment/config blocker, partial** — real, and not yet common in generic guidance: *some* dimensions of a run work and others don't (Chrome driver resolved fine, Edge's didn't via Selenium Manager, Firefox wasn't installed at all). This is not "the whole run is blocked" — it's "some slices of the run are blocked, others are genuine." Diagnose and exclude at the dimension level (browser, tag, environment), not at the whole-run level. Report explicitly which dimensions were excluded and why, rather than silently dropping them or misreporting them as failures.

## Sequence Summary (see step files for the authoritative version)

1. Detect toolchain (Maven/Gradle/npm/pytest/Go) — check the README for the project's own documented command before assuming a default (e.g. some Maven projects bind Surefire to `verify`, not `test`).
2. Run once. Verify via the toolchain's native structured report, never console text/exit code alone.
3. Diagnose in order: compile failure → discovery gap → environment/config blocker (whole-run or partial) → genuine result.
4. Fix via the module's one exception (`rrd-apply-and-verify`, after writing a diff) for changes to existing tracked source/config. Create directly, without a diff, only for a *missing fixture* the project's own config already expects (net-new, not a modification). Bound retries to 3 cycles before stopping and reporting the remaining blocker.
5. Once genuine (possibly partially, per partial-blocker handling above), run `{run_count}` times total, preserving each run's native report before the next overwrites it.
6. Write a manifest describing what was produced — format, run count, preserved file paths, any excluded dimensions and why — so a consuming workflow doesn't have to re-derive this.

## Manifest Format

Written to `{target_project_root}/.refactor-radar-logs/manifest.json`:

```json
{
  "format": "testng-xml" | "junit-xml" | "playwright-json" | "jenkins-console",
  "run_count": 3,
  "runs": ["run1-testng-results.xml", "run2-testng-results.xml", "run3-testng-results.xml"],
  "log_sources_dir": "{target_project_root}/.refactor-radar-logs",
  "excluded_dimensions": [
    {"dimension": "browser=edge", "reason": "NoSuchDriverException — Selenium Manager could not obtain a matching driver, despite Edge being installed"},
    {"dimension": "browser=firefox", "reason": "Firefox not installed on this machine"}
  ],
  "fixes_applied": [
    {"file": "core/App.java", "change": "PLATFORM: remote -> local", "method": "direct (owner-approved outside formal diff flow)"}
  ]
}
```

A consuming workflow reads this manifest instead of re-diagnosing the same environment from scratch.
