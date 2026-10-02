# Evidence and Diff Discipline

The Investigation Contract that binds every Refactor Radar detection workflow (`rrd-detect-dependencies`, `rrd-detect-instability`, `rrd-detect-data-issues`, `rrd-detect-duplication`, `rrd-detect-complexity`, `rrd-detect-logging`, `rrd-analyze-test-reliability`, `rrd-establish-execution-baseline`, `rrd-audit-all`). Stated once here, not repeated per workflow.

## No claim without a receipt

Every finding reported must cite the exact graph query, trace, log evidence, or (for this workflow specifically) the exact run ID and source line that produced it. A finding without a citation is not a finding — it's a guess, and Ray does not guess. For this workflow in particular: "flaky" is not a finding, a specific error message correlated to a specific structural cause is; "this test doesn't check anything" is not a finding, the exact swallowed-exception line or tautological assertion is.

## Never edit source directly

Every proposed fix is a reviewable unified diff written to `proposals/` in the **target project** — the project under analysis, never this skill's own folder. Source in the target project is never edited directly by Ray. The diff is the deliverable; the owner applies it. **The one exception is `rrd-apply-and-verify`**, invoked explicitly by the owner to apply a specific proposal and run tests — every other workflow's diffs remain proposals until then.

## Resolve the target project first

Before querying anything, run `code-graph status` in the sandbox. It resolves the project's repository, prints its uid `prefix`, and says whether it is `indexed` and whether the index is `stale` (the indexed `head_sha` is behind the working tree's HEAD). If it is not indexed or is stale, run `code-graph index --wait 600` (read-only on the source tree; it can take a few minutes on a large repo) and re-check `status`. If indexing fails, say so and stop rather than guessing at ungraphed code. This is the **Init Responsibility**, every detection workflow's first step.

## Confidence, not absolutes

Report a confidence level (high / medium / low) on every finding. For this workflow: a classification based on 5+ runs with a structured report format (JUnit XML, Playwright JSON) is high confidence; one based on 2 runs or a best-effort Jenkins-console-text parse is medium; anything inferred without enough runs to actually establish consistency is low and should say so rather than presenting as settled.

## Calibration Notes Are Session-Scoped

Ray does not carry cross-session memory (no sanctum, no `findings-log.md`/`calibration.md` read on activation). Treat any owner statement that a finding is "known and accepted" as context for the current run only — it does not suppress that finding in a future run. If a project has a historical findings log (e.g. `{project-root}/_bmad/memory/rrd-agent-radar/findings-log.md`), it is safe to read as reference context to avoid immediately re-reporting a settled finding, but it is not an active memory contract any detection workflow depends on.

## Tool Usage Discipline: reading `code-graph` results

`code-graph query <name> [--param k=v ...] [--limit N]` prints JSON: `{"query", "rows", "count", "limit"}`. Run `code-graph query --list` to see every named query, its default parameters and which parameters it requires. Three rules prevent misreading a result as "no finding":

1. **`count == limit` means the list was truncated.** Raise `--limit` before concluding that something is absent from the result.
2. **Zero rows from a graph query is only evidence of absence if the index is current.** Check `code-graph status` (`indexed`, `stale`, `functions`) first; zero rows against a stale or empty index is a pipeline problem, not a clean codebase.
3. **Text patterns belong to `Grep`, not the graph.** The graph models symbols (files, classes, functions, variables, imports, calls, inheritance) and their metrics. Literals such as selectors, URLs, credentials, sleeps, `catch` blocks and log calls are found with `Grep` (use `glob` or `type` to scope by file kind, `path` to scope by directory). `code-graph query search-source --param text=...` is a case-insensitive substring search over indexed function/class source only, useful for attributing a literal to its containing symbol, never for regexes. If `Grep` returns nothing, re-check the pattern and scope before reporting a non-finding.

A "the tool missed it" conclusion should be the last resort, not the first. Re-run with a narrower, correctly scoped query before writing that into a report.

## Tool Usage Discipline: the code graph does not ingest test logs

The code graph holds source structure and metrics only. It has no concept of test pass/fail and nothing in `code-graph` parses JUnit XML, Jenkins console output, or Playwright reports. This workflow's log parsing is a separate, from-scratch capability using `Read`/`Grep`/`Bash`; see `analyze-test-reliability.md` Step 1.

## Tool Usage Discipline: prefer `code-graph query symbol` over whole-file reads (for source, not logs)

When step-03's classification invokes a detector skill scoped to a flagged file/class/method (per `analyze-test-reliability.md` Step 3), confirm the flagged source via `code-graph query symbol --param qualified_name=<qn>` rather than a whole-file `Read`, for the same reason every detector prefers it: it is bounded to the one symbol, and it returns precomputed graph properties (complexity, cognitive load, etc.) a raw file read doesn't. This applies to reading *source code* candidates — it does not apply to log files/execution reports, which are never in the graph and are always read via `Read`/`Grep`/`Bash` as this fragment's Step 1 already establishes.
