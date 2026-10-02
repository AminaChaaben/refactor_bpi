# Detect Data Issues Validation Checklist

## Prerequisites

- [ ] `code-graph status` run; index confirmed present and not stale (re-indexed first if needed)
- [ ] Target project confirmed indexed
- [ ] Knowledge fragment loaded: `evidence-and-diff-discipline.md`
- [ ] Knowledge fragment loaded: `detect-data-issues.md`
- [ ] Knowledge fragment loaded: `detect-report-template.md`

**Halt if missing:** target project not indexed.

## Investigation

- [ ] Graph queried for hardcoded credentials, URLs, and IDs
- [ ] Fixtures/factories checked for missing corresponding cleanup
- [ ] Tests checked for assuming pre-existing data state instead of creating their own
- [ ] If logs available: cross-run data collisions correlated (same record ID touched concurrently/sequentially)
- [ ] Confidence distinguishes "data smell, never collided" from "confirmed collision in reruns"

## Findings and Proposals

- [ ] Every finding names the data dependency and lifecycle gap
- [ ] Every finding cites its graph/log evidence
- [ ] Every fix externalizes hardcoded data, adds create/purge lifecycle, or scopes data per-run
- [ ] Every fix written as a diff to the target project's `proposals/`

## Completion Criteria

- [ ] Findings summary written to `{rrd_artifacts}/detect-data-issues-{target_project}.md`
- [ ] All diff proposals written to the target project's `proposals/`, listed in the summary
