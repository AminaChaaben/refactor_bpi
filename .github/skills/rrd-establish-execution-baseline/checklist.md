# Establish Execution Baseline Validation Checklist

## Prerequisites

- [ ] `code-graph status` run; index confirmed present and not stale (re-indexed first if needed)
- [ ] `git status` checked on the target project; tree is clean, or the owner has explicitly accepted a dirty tree
- [ ] Knowledge fragments loaded: `evidence-and-diff-discipline.md`, `establish-execution-baseline-heuristics.md`
- [ ] Toolchain detected (marker files checked; README's documented run command checked, not assumed)

**Halt if missing:** target project not indexed, or tree is dirty and owner has not explicitly accepted that risk.

## Diagnosis

- [ ] First run's result verified via the toolchain's native structured report, not console text/exit code alone
- [ ] Diagnosed in order: compile failure → discovery gap → environment/config blocker (whole-run or partial) → genuine result — not jumped to a conclusion out of order
- [ ] Discovery gap specifically checked by cross-referencing real `@Test`-annotated method count (via graph/grep) against the report's test count — not assumed absent just because a report "looks fine"
- [ ] Partial blockers (some dimensions genuine, others blocked — e.g. one browser works, another doesn't) identified and excluded at the dimension level, not conflated with a whole-run blocker or silently dropped

## Fixing

- [ ] Every fix to existing tracked source/config written as a diff and applied via `rrd-apply-and-verify` — never edited directly by this workflow
- [ ] Missing-fixture creation (the one direct-creation exception) only used for genuinely net-new files a project's own config already references, built by reading the real locators/selectors the test code expects — not guessed
- [ ] Fix cycles bounded to 3 — stopped and reported the specific remaining blocker if still unresolved after that, rather than patching indefinitely

## Multi-Run Generation

- [ ] `{run_count}` genuine runs generated once blockers are resolved (or explicitly scoped to the genuine dimensions, if partial)
- [ ] Every run's native report preserved to a staging directory before the next run overwrote the toolchain's fixed output location

## Completion Criteria

- [ ] Manifest written to `{target_project_root}/.refactor-radar-logs/manifest.json` per the format in `establish-execution-baseline-heuristics.md` — format, run count, preserved file list, excluded dimensions with reasons, fixes applied
- [ ] Owner (or the consuming workflow) told where the manifest and preserved runs are
