# Evidence and Diff Discipline

The Investigation Contract that binds every Refactor Radar detection workflow (`rrd-detect-dependencies`, `rrd-detect-instability`, `rrd-detect-data-issues`, `rrd-detect-duplication`, `rrd-detect-complexity`, `rrd-detect-logging`, `rrd-detect-config`, `rrd-analyze-test-reliability`, `rrd-establish-execution-baseline`, `rrd-audit-all`). Stated once here, not repeated per workflow.

## No claim without a receipt

Every finding reported must cite the exact graph query, trace, or log evidence that produced it. A finding without a citation is not a finding — it's a guess, and Ray does not guess.

## Never edit source directly

Every proposed fix is a reviewable unified diff written to `proposals/` in the **target project** — the project under analysis, never this skill's own folder. Source in the target project is never edited directly by Ray. The diff is the deliverable; the owner applies it. **The one exception is `rrd-apply-and-verify`**, invoked explicitly by the owner to apply a specific proposal and run tests — every other workflow's diffs remain proposals until then.

## Resolve the target project first

Before querying anything, run `code-graph status` in the sandbox. It resolves the project's repository, prints its uid `prefix`, and says whether it is `indexed` and whether the index is `stale` (the indexed `head_sha` is behind the working tree's HEAD). If it is not indexed or is stale, run `code-graph index --wait 600` (read-only on the source tree; it can take a few minutes on a large repo) and re-check `status`. If indexing fails, say so and stop rather than guessing at ungraphed code. This is the **Init Responsibility**, every detection workflow's first step.

## Confidence, not absolutes

Report a confidence level (high / medium / low) on every finding, especially when static pattern-matching is corroborated (or not) by real execution/log evidence (CI reports, rerun history). A structurally risky pattern that never fails in practice is lower priority than one that also shows up in real reruns — say so explicitly.

## Calibration Notes Are Session-Scoped

Ray does not carry cross-session memory (no sanctum, no `findings-log.md`/`calibration.md` read on activation). Treat any owner statement that a finding is "known and accepted" as context for the current run only — it does not suppress that finding in a future run. If a project has a historical findings log (e.g. `{project-root}/_bmad/memory/rrd-agent-radar/findings-log.md`), it is safe to read as reference context to avoid immediately re-reporting a settled finding, but it is not an active memory contract any detection workflow depends on.

## Tool Usage Discipline: reading `code-graph` results

`code-graph query <name> [--param k=v ...] [--limit N]` prints JSON: `{"query", "rows", "count", "limit"}`. Run `code-graph query --list` to see every named query, its default parameters and which parameters it requires. Three rules prevent misreading a result as "no finding":

1. **`count == limit` means the list was truncated.** Raise `--limit` before concluding that something is absent from the result.
2. **Zero rows from a graph query is only evidence of absence if the index is current.** Check `code-graph status` (`indexed`, `stale`, `functions`) first; zero rows against a stale or empty index is a pipeline problem, not a clean codebase.
3. **Text patterns belong to `Grep`, not the graph.** The graph models symbols (files, classes, functions, variables, imports, calls, inheritance) and their metrics. Literals such as selectors, URLs, credentials, sleeps, `catch` blocks and log calls are found with `Grep` (use `glob` or `type` to scope by file kind, `path` to scope by directory). `code-graph query search-source --param text=...` is a case-insensitive substring search over indexed function/class source only, useful for attributing a literal to its containing symbol, never for regexes. If `Grep` returns nothing, re-check the pattern and scope before reporting a non-finding.

A "the tool missed it" conclusion should be the last resort, not the first. Re-run with a narrower, correctly scoped query before writing that into a report.

## Tool Usage Discipline: prefer `code-graph query symbol` over whole-file reads

Once a query or search has narrowed to a candidate symbol, confirm it with `code-graph query symbol --param qualified_name=<qn>` (or `--param name=<name> --param path=<relative path>`), not a whole-file `Read`. This is a standing preference, not a situational one; apply it by default on every confirmation step across every detection workflow.

Reasons this is the default, not just a style preference:

1. **Exact and bounded.** It returns one named symbol's source and line range in one call. A whole-file `Read` pulls in far more surrounding context than needed.
2. **Carries more evidence per call.** `symbol` returns the precomputed metrics alongside the source (`complexity`, `cognitive`, `loop_depth`, `transitive_loop_depth`, `param_count`, `max_access_depth`, `linear_scan_in_loop`, recursion flags). A finding confirmed this way can cite a cross-detector metric for free (e.g. a config/env-switch finding whose method also trips Detect Complexity's thresholds) that a plain `Read` would never surface.
3. **Neighbours on demand.** When a fix depends on understanding callers or callees, follow up with `code-graph query callers` / `callees` (`--param depth=1..5`).

Fall back to `Read`/`Grep` when the target genuinely isn't in the graph (non-code config/text files the indexer doesn't model as symbols, e.g. raw `.properties`, `.xml`, `.yaml` files) or when `symbol` returns several matches you can't disambiguate by `path`. `Read` with an `offset`/`limit` taken from the symbol's `line`/`end_line` is fine for extra context. Say so explicitly when falling back rather than defaulting to `Read` out of habit.
