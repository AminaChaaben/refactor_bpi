# Detect Layering Validation Checklist

## Prerequisites

- [ ] `code-graph status` run; index confirmed present and not stale (re-indexed first if needed)
- [ ] Target project confirmed indexed
- [ ] Knowledge fragment loaded: `evidence-and-diff-discipline.md`
- [ ] Knowledge fragment loaded: `detect-layering.md`
- [ ] Knowledge fragment loaded: `detect-report-template.md`

**Halt if missing:** target project not indexed.

## Investigation

- [ ] `code-graph query tree` used to enumerate the actual directory structure — not assumed from convention
- [ ] Each of the seven candidate layers (tests, pages, components, data, utils, config, reporting) checked for a dedicated directory vs. mixed-in-with-something-else placement
- [ ] Cross-layer violations checked: page objects containing assertion logic; test files directly manipulating raw data/fixtures instead of going through a data layer
- [ ] Naming-convention consistency checked across files within the same layer (e.g. `PascalCase.java` vs `snake_case.py` mixed in one directory, or `*Page.java` vs `*_page.py` mixed suffix/prefix conventions)
- [ ] Confidence distinguishes "no layer for X exists because the project is small/single-purpose" from "layer exists elsewhere, inconsistently, or mixed with another concern"

## Findings and Proposals

- [ ] Every finding names the specific missing/mixed layer, cross-layer violation, or naming inconsistency, with file:line or directory-level evidence
- [ ] Every finding cites its `code-graph query`/`Grep` evidence
- [ ] Every fix proposes a concrete directory/file move or a specific responsibility extraction, scoped to what was actually found — not a full-project restructure in one diff
- [ ] Every fix written as a diff to the target project's `proposals/`

## Completion Criteria

- [ ] Findings summary written to `{rrd_artifacts}/detect-layering-{target_project}.md`
- [ ] All diff proposals written to the target project's `proposals/`, listed in the summary
