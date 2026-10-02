# Apply and Verify Validation Checklist

## Prerequisites

- [ ] Target project resolved and its `proposals/` directory located
- [ ] `git status` run on the target project; tree is clean, or the owner has explicitly accepted the risk of a dirty tree
- [ ] Knowledge fragments loaded: `evidence-and-diff-discipline.md`, `apply-and-verify-heuristics.md`
- [ ] Owner has specified (or confirmed a default of) which proposal(s) to apply

**Halt if missing:** target project not resolved, or tree is dirty and owner has not explicitly accepted that risk.

## Apply

- [ ] For every diff about to be applied, the full current file(s) it touches were read in full — not just an excerpt — before editing
- [ ] Any diff whose plan no longer makes sense given the full file's real context was skipped, with the specific reason stated, not forced through
- [ ] Edits made match the diff's intent, adjusted for the file's real current imports/structure/conventions
- [ ] No file outside the target project, and no file inside this skill's own installed folder, was touched
- [ ] The target project's own formatter/linter (if any) was run after applying, before testing

## Verify

- [ ] Toolchain auto-detected correctly (Maven/Gradle/npm/pytest/Go, or asked the owner if ambiguous)
- [ ] Environment-level failures (wrong JDK/JRE, flaky plugin download, missing toolchain) were diagnosed and distinguished from real test failures before concluding anything
- [ ] Affected test classes/files run first, then the full suite for regression coverage
- [ ] Pass/fail counts verified against the toolchain's own native report format (Surefire XML/txt, Jest JSON, pytest junit-xml, go test JSON) — not console text or exit code alone
- [ ] Before/after test counts compared when a diff was expected to change test coverage (e.g. closing a gap)

## Completion Criteria

- [ ] Per-diff outcome reported: applied+passed, applied+failed (with real failure detail), or skipped (with the specific reason)
- [ ] Summary written to `{rrd_artifacts}/apply-and-verify-{target_project}-{date}.md`
- [ ] Owner reminded that nothing was committed — `git status`/`git diff` is their own next step
