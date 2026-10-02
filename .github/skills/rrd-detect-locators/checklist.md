# Detect Locators Validation Checklist

## Prerequisites

- [ ] `code-graph status` run; index confirmed present and not stale (re-indexed first if needed)
- [ ] Target project confirmed indexed
- [ ] Knowledge fragment loaded: `evidence-and-diff-discipline.md`
- [ ] Knowledge fragment loaded: `detect-locators.md`
- [ ] Knowledge fragment loaded: `detect-report-template.md`
- [ ] Checked whether `rrd-detect-instability` has already run on this target — if so, its fragile-selector findings are consumed as input, not re-detected

**Halt if missing:** target project not indexed.

## Investigation

- [ ] Selector literals compared across page-object files for duplication (same locator string defined more than once)
- [ ] Locator-priority convention checked: is there a consistent tiering (`data-testid`/stable ID > ARIA/role > semantic text > absolute XPath/CSS), or ad-hoc per-file choices?
- [ ] Centralized element/locator repository pattern checked for existence (a single module/class page objects reference, vs. locators scattered inline per page object)
- [ ] Cross-referenced `rrd-detect-instability` findings (if available) rather than re-flagging the same fragile selector as a second, independent finding
- [ ] Confidence distinguishes "no strategy documented, but consistent in practice" from "genuinely inconsistent/duplicated across files"

## Findings and Proposals

- [ ] Every finding names the duplicated locator or the specific strategy gap, with file:line
- [ ] Every finding cites its graph/code-search evidence
- [ ] Every fix centralizes duplicated locators into one repository module, or proposes a priority-tier convention with a concrete example migration
- [ ] Every fix written as a diff to the target project's `proposals/`

## Completion Criteria

- [ ] Findings summary written to `{rrd_artifacts}/detect-locators-{target_project}.md`
- [ ] All diff proposals written to the target project's `proposals/`, listed in the summary
