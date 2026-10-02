# Detect Logging Validation Checklist

## Prerequisites

- [ ] `code-graph status` run; index confirmed present and not stale (re-indexed first if needed)
- [ ] Target project confirmed indexed
- [ ] Knowledge fragment loaded: `evidence-and-diff-discipline.md`
- [ ] Knowledge fragment loaded: `detect-logging.md`
- [ ] Knowledge fragment loaded: `detect-report-template.md`
- [ ] Scope confirmed with owner (whole codebase, or narrowed to a specific test's code paths)

**Halt if missing:** target project not indexed.

## Investigation

- [ ] `code-graph query calls-matching` run for external-call callee name patterns (request/http/fetch/execute/query/driver/client/connect/open/read/write/send)
- [ ] `code-graph query loops` run (`loop_depth >= 1 OR transitive_loop_depth >= 1`) to locate failure-capable loops
- [ ] `Grep` run for the language's exception-handling syntax (`catch`/`except`/`rescue`/`.catch(`) scoped to the target project
- [ ] Every candidate confirmed by reading actual source (`code-graph query symbol`/`Read`) — the concrete gap (silent catch, dropped exception, unlogged external call, unlogged loop failure path) is named, not just inferred from the query
- [ ] Every confirmed gap tagged with a failure-classification leaning (app-error / env-error / data-error / script-bug), stated as a leaning not a certainty
- [ ] Failure-diagnostics capture (screenshot/trace on failure) checked for the target's test framework — reported as present-and-correct, present-but-gapped, or absent
- [ ] Checked whether each candidate is also flagged by Detect Instability or Detect Dependencies (cross-detector corroboration) before finalizing confidence

## Findings and Proposals

- [ ] Every finding names the function/method, file:line, the confirmed gap type, and its failure-classification tag (where applicable)
- [ ] Every fix proposes the actual logging statement (including the original exception object where one was dropped), not a generic "add logging" note
- [ ] Every fix written as a diff to the target project's `proposals/`
- [ ] Findings inside test methods themselves (as opposed to the application/framework code under test) are flagged as such and deprioritized unless the owner asked otherwise — except failure-diagnostics capture findings, which live in test infrastructure by definition

## Completion Criteria

- [ ] Findings summary written to `{rrd_artifacts}/detect-logging-{target_project}.md`, grouped by gap type
- [ ] All diff proposals written to the target project's `proposals/`, listed in the summary
