# Detect Instability Validation Checklist

## Prerequisites

- [ ] `code-graph status` run; index confirmed present and not stale (re-indexed first if needed)
- [ ] Target project confirmed indexed
- [ ] Knowledge fragment loaded: `evidence-and-diff-discipline.md`
- [ ] Knowledge fragment loaded: `detect-instability.md`
- [ ] Knowledge fragment loaded: `detect-report-template.md`

**Halt if missing:** target project not indexed.

## Investigation

- [ ] `Grep` run for volatile selectors (auto-generated IDs, positional/index selectors, content-based text)
- [ ] Every `Grep` call used the right `glob`/`type` and `path` scope — pattern and scope re-checked before concluding zero results
- [ ] If `total_grep_matches` > 0, confirmed the expected file wasn't truncated by `limit` before reporting a non-finding
- [ ] Fixed `sleep`/timeout patterns identified instead of explicit waits/polling
- [ ] Unhandled overlays/iframes/native dialogs identified
- [ ] If logs provided: logs read directly and static findings correlated against real failure/rerun evidence
- [ ] Confidence level distinguishes "structurally risky, never fails in practice" from "confirmed by real reruns"

## Findings and Proposals

- [ ] Every finding cites file:line and the query/log evidence
- [ ] Every fix proposes a dynamic-wait or stable-selector replacement, scoped to the fragile line only
- [ ] Every fix is written as a diff to the target project's `proposals/`

## Completion Criteria

- [ ] Findings summary written to `{rrd_artifacts}/detect-instability-{target_project}.md`
- [ ] All diff proposals written to the target project's `proposals/`, listed in the summary
