# Evidence and Diff Discipline

The Investigation Contract that binds every Refactor Radar detection workflow (`rrd-detect-dependencies`, `rrd-detect-instability`, `rrd-detect-data-issues`, `rrd-detect-duplication`, `rrd-detect-complexity`, `rrd-detect-logging`, `rrd-analyze-test-reliability`, `rrd-establish-execution-baseline`, `rrd-audit-all`). Stated once here, not repeated per workflow. `rrd-apply-and-verify` is the one workflow that deliberately breaks the "never edit source directly" rule below — see `apply-and-verify-heuristics.md` for how that exception is scoped and safeguarded. `rrd-establish-execution-baseline` shares this same exception path (it applies fixes *through* `rrd-apply-and-verify`, not around it) rather than opening a second source-touching route.

## No claim without a receipt

Every finding reported must cite the exact graph query, trace, or log evidence that produced it. A finding without a citation is not a finding — it's a guess, and Ray does not guess.

## Never edit source directly (except here, explicitly)

Every other workflow's proposed fix is a reviewable unified diff written to `proposals/` in the **target project** and never applied automatically. `rrd-apply-and-verify` is the sole, explicit exception: the owner invokes it by name specifically to apply a chosen proposal and run tests. It is never invoked implicitly by another workflow, and it never commits on the owner's behalf — applying and testing are this workflow's job; `git add`/`git commit` remain the owner's decision.

## Resolve the target project first

Before touching anything, resolve which project is the target and confirm its working tree is clean (or that any dirty state is understood and accepted by the owner) via `git status`. This is the **Init Responsibility** for this workflow — analogous to the other workflows' `code-graph status` check, but for filesystem safety instead of graph availability.

## Confidence, not absolutes

Report real pass/fail counts pulled from the target project's own test-report format, not console text or exit codes alone. A test that "probably passed" is not verified.

## Tool Usage Discipline: read the full file before applying a diff

A diff was written against excerpts and understanding at proposal time. Always read the full current file(s) before applying — see `apply-and-verify-heuristics.md` for the real example (a proposed test merge that turned out to break a deliberate `@Nested` grouping once the whole file was read) that motivated this rule.

**This is the one detector-family workflow where a plain `Read` of the full target file is correct, not a fallback** — the point here is deliberately reading the *entire current file* on disk immediately before a filesystem write, not confirming a graph-indexed symbol. `code-graph query symbol` returns a symbol's source as of the last index run, which may predate the exact pre-apply state this rule requires. Every other Refactor Radar workflow's *investigation* phase should still prefer `code-graph query symbol` over `Read` when confirming a candidate finding — this exception is specific to the immediate pre-apply full-file read.
