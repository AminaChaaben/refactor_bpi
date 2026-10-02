---
name: 'step-03-run-tests'
description: 'Auto-detect the toolchain, run affected tests then the full suite, verify via native report format'
nextStepFile: '{skill-root}/steps/step-04-report.md'
---

# Step 3: Run Tests

## STEP GOAL

Run the target project's own test suite and verify results via its native, structured test-report format — not console text or exit code alone.

## SEQUENCE

### 1. Detect the Toolchain

Look for marker files in `{target_project_root}` and pick accordingly:

| Marker | Toolchain | Command shape |
|---|---|---|
| `mvnw`/`pom.xml` | Maven | `./mvnw -Dtest=<Classes> test`, then `./mvnw test` |
| `gradlew`/`build.gradle*` | Gradle | `./gradlew test --tests <Class>` |
| `package.json` with a `test` script | npm/yarn/pnpm | check `scripts.test` for the real runner |
| `pytest.ini`/`pyproject.toml` + `tests/` | pytest | `pytest <path>` |
| `go.mod` | Go | `go test ./...` |

If ambiguous or multiple markers present, ask the owner rather than guess.

### 2. Rule Out Environment Failures Before Concluding a Test Failure

If the run fails with a build/compile-level error (wrong JDK vs JRE, a flaky plugin download, a missing toolchain), diagnose the specific error message. Fix an obvious environment issue (e.g. point `JAVA_HOME` at an actual JDK) and retry once. If the failure persists after that, treat it as a real result — don't keep attributing failures to environment indefinitely.

### 3. Run Affected Tests First, Then the Full Suite

Run just the test classes/files touched by the applied diff(s) for fast feedback. Then run the full suite to check for regressions elsewhere — a change that passes its own targeted test can still break something nearby.

### 4. Verify via the Toolchain's Native Report Format

Do not trust console output or exit code alone. Read the structured report:

- Maven Surefire: `target/surefire-reports/*.txt` per-class, and `TEST-*.xml` (the `.txt` summary can misreport `@Nested`/parameterized classes as 0 — check the XML `testcase` entries for the real count)
- Jest: `--json` output
- pytest: `--junit-xml` output
- Go: `go test -json` output

Aggregate real `tests run / failures / errors / skipped` counts across every affected report file.

### 5. Compare Before/After Where Relevant

If a diff was expected to change test coverage (e.g. closing a gap by adding an inherited test), note the before/after count explicitly — this is concrete proof the change did what it claimed, not just that it compiles.

### 6. Detect Regression (Feeds Step 4's Rollback Decision)

A **regression** is a specific new failure in the full-suite run that was not failing before this diff was applied — not "the suite has failures" in general, since a pre-existing unrelated failure isn't this diff's fault. If a baseline pre-diff run isn't already available for comparison, run the full suite once more against a clean `git stash` of the just-applied diff to establish one, then restore the diff — don't guess whether a failure is new. Record explicitly, per diff: no regression / regression (naming the specific newly-failing test(s)).

### 7. Continue

Load `./step-04-report.md`.
