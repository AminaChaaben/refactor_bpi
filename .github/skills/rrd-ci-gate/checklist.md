# CI Gate Validation Checklist

## Prerequisites

- [ ] `code-graph status` run; index confirmed present and not stale (re-indexed first if needed)
- [ ] `rrd-audit-all` invoked as a sub-step — not reimplemented

## Gate Decision

- [ ] Verdict based on **new-since-last-run** opportunities at or above `gate_fails_at_or_above`, not total count of opportunities at that level
- [ ] A pre-existing Critical (present in both the prior and current snapshot) does not by itself fail the gate
- [ ] First-run-ever behavior follows `fail_on_first_run_if_critical_exists` exactly — pass-by-default unless the owner explicitly flipped it
- [ ] Verdict is unambiguous: PASS or FAIL, with the specific new opportunity ID(s)/title(s) that caused a FAIL, not just a count

## Completion Criteria

- [ ] Verdict reported plainly (PASS/FAIL) before any other summary detail, since that's what a calling pipeline script needs first
- [ ] Underlying `rrd-audit-all` report/proposal paths still surfaced for a human reading the same output
