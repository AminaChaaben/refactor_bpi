# Apply and Verify Heuristics

Unlike the five detection workflows, this workflow **does** touch source in the target project — the one deliberate exception to the Investigation Contract's "never edit source directly" rule, and only because the owner explicitly invoked this skill to do exactly that. Every other workflow's diffs remain proposals until the owner runs this one.

## What Success Looks Like

The owner picks one or more proposed diffs, this workflow applies them for real, runs the target project's own test suite, and reports genuine pass/fail counts pulled from the toolchain's own report files — not console text, not exit codes alone, not an assumption that "it probably worked." If a diff turns out to be a bad idea once the real file is read in full, this workflow says so and skips it rather than forcing it through.

## Safety Gate — Before Touching Anything

1. Run `git status` on the target project. If it's not a git repo, or the tree is dirty with changes unrelated to this workflow's own prior runs, stop and tell the owner — applying diffs on top of unrelated uncommitted work risks conflating changes.
2. Never commit. Applying a diff and running tests are this workflow's job; deciding whether to `git add`/`git commit` is always the owner's.
3. Never touch files outside the target project, and never touch this skill's own installed folder.
4. Never force multiple diffs through in a batch without checking each one's real-file context first (see below) — batching the *test run* is fine; batching the *apply* without individual review is not.

## Read the Real File Before Applying — Not Just the Diff

A diff was written against `code-graph query symbol` excerpts and the detector's understanding at proposal time. Before applying, always read the **full current file(s)** the diff touches. Two things can have changed since the diff was written:

1. **The file itself changed** (someone edited it since the audit ran) — the diff's line numbers or context may no longer match. Re-derive the equivalent edit against current content rather than forcing a stale patch.
2. **Fuller context reveals the diff's plan is wrong.** A real example: a proposed diff merged two "duplicate" test methods into one `@ParameterizedTest`, based on an excerpt showing both bodies were near-identical. Reading the *whole* file revealed the two methods lived in separate `@Nested` test classes, each grouping ~5 related tests for deliberate organizational reasons — merging them would have required breaking that structure for a marginal DRY gain. The correct action was to skip that diff and explain why, not force it through because "the diff said so."

**Rule: if reading the full file changes your assessment of whether the diff is still a good idea, stop and report the discrepancy instead of applying it.** The diff is a proposal, not a mandate — even at apply time.

## Applying the Change

- Prefer direct file edits over blind `git apply`/`patch` on the stored `.patch` text — hand-written/illustrative diffs (especially ones with abbreviated "... unchanged ..." sections) often don't apply mechanically clean. Read the patch's rationale and intended end-state, then edit the real file to match that intent.
- If the target project has its own formatter/linter (e.g. a Maven `spring-javaformat`/`checkstyle` plugin, `prettier`, `black`, `gofmt`), run it after applying and before testing — catching a formatting violation before the test run is faster feedback than discovering it at build time.
- If applying a diff requires judgment calls the original proposal didn't anticipate (e.g. resolving an import path that turns out different from what was assumed), make the smallest correct change consistent with the rest of the file's existing conventions — don't introduce a new style.

## Running Tests — Detect the Toolchain, Don't Guess

| Marker file | Toolchain | Typical test command |
|---|---|---|
| `mvnw`/`pom.xml` | Maven | `./mvnw -Dtest=<Classes> test` (targeted), then `./mvnw test` (full) |
| `gradlew`/`build.gradle*` | Gradle | `./gradlew test --tests <Class>` |
| `package.json` with a `test` script | npm/yarn/pnpm | `npm test` (check `scripts.test` for the actual runner — Jest, Vitest, Mocha) |
| `pytest.ini`/`pyproject.toml` + `tests/` | pytest | `pytest <path>` |
| `go.mod` | Go | `go test ./...` |

Run the **affected test classes/files first** for fast feedback, then the **full suite** to check for regressions elsewhere — a change that passes its own targeted test can still break something nearby.

### Environment failures are not test failures

Before concluding a diff broke something, rule out environment issues: wrong JDK vs JRE (`JAVA_HOME`), a flaky plugin download, a missing toolchain. These produce build/compile errors distinct from actual test assertion failures — diagnose the specific error message rather than assuming the code change is at fault. Retry once after a clear environment fix; if the failure persists after that, it's a real result.

### Verify via the toolchain's own report format — not console output or exit code alone

Exit code 0 is a fast signal but not proof; console text can be truncated by quiet flags or paging. Always confirm against the toolchain's structured report:

- Maven Surefire: `target/surefire-reports/*.txt` (per-class summary) and `TEST-*.xml` (includes `@Nested`/parameterized sub-results the `.txt` summary can misreport as 0)
- Jest: `--json` output or the JSON report file
- pytest: `--junit-xml` output
- Go: `go test -json` output

Aggregate real `tests run / failures / errors / skipped` counts across every affected report file, not just the last one printed to console.

## Reporting Results

For each diff:
- **Applied, tests pass** — report before/after test counts if they changed meaningfully (e.g. a fix that closes a coverage gap should show more tests running afterward, not just the same count passing).
- **Applied, tests fail** — report which test(s), the real failure output, and stop before applying further diffs in the same batch until the owner decides how to proceed. Suggest (don't perform) `git diff`/`git checkout --` as owner-initiated recovery.
- **Skipped** — state the specific reason discovered by reading the full file, per the rule above.

Always end by reminding the owner nothing was committed — `git status`/`git diff` is their own next step.
