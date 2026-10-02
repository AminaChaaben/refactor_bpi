# Evidence and Diff Discipline

The Investigation Contract that binds every Refactor Radar detection workflow (`rrd-detect-dependencies`, `rrd-detect-instability`, `rrd-detect-data-issues`, `rrd-detect-duplication`, `rrd-detect-complexity`, `rrd-detect-logging`, `rrd-analyze-test-reliability`, `rrd-establish-execution-baseline`, `rrd-audit-all`). Stated once here, not repeated per workflow.

## No claim without a receipt

Every diagnosis this workflow makes must cite the exact error class/message from the toolchain's own structured report, or the exact config/source line causing a blocker — never "the run seems to be failing," always the specific exception and location.

## Never edit source directly (except via the one exception)

Every fix discovered here — a version pin, a config constant, a discovery-configuration gap — gets written as a diff to `{target_project_root}/proposals/` first, then applied via `rrd-apply-and-verify`. The only carve-out: creating a *missing fixture* the project's own config already references (net-new, not a modification of existing tracked source) can be created directly.

## Resolve the target project first

Before running anything, resolve which project this is and confirm its `git status` is clean (or the owner has explicitly accepted a dirty tree) — this workflow's Init Responsibility, mirroring `rrd-apply-and-verify`'s own safety gate since this workflow shares its source-touching exception.

## Confidence, not absolutes

State plainly which dimensions (browsers, environments, tags) produced genuine results versus which were excluded as environment-blocked, and why. A partial baseline (some dimensions genuine, others excluded) is a real, complete answer — not a failure to fully diagnose.

## Tool Usage Discipline: distinguish blocker types before fixing anything

Compile failure, discovery gap, and environment/config blocker (whole-run or partial) are different failure modes with different fixes — diagnosing the wrong one wastes fix cycles and can misattribute a real signal as an artifact of the wrong cause. See `establish-execution-baseline-heuristics.md` for the full diagnostic order and real examples of each, drawn from actual runs against three different projects.

## Tool Usage Discipline: prefer `code-graph query symbol` over whole-file reads (for source, not toolchain output)

When diagnosing a discovery gap or a config/source-line blocker, confirm the exact line via `code-graph query symbol` (or `Read` with an offset around the line) rather than a whole-file `Read`, for the same reason every detector prefers it: it is bounded, and it returns precomputed graph properties a raw file read doesn't. This applies to source code candidates only — compiler/toolchain output, CI logs, and console text are never in the graph and are read via `Read`/`Grep`/`Bash` as usual.
