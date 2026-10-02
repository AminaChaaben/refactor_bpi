# Standards Audit Validation Checklist

## Prerequisites

- [ ] `code-graph status` run; index confirmed present and not stale (re-indexed first if needed)
- [ ] Target project confirmed indexed
- [ ] Knowledge fragment loaded: `evidence-and-diff-discipline.md`
- [ ] Knowledge fragment loaded: `standards-audit.md`
- [ ] A completed `rrd-audit-all` run's report located for this target — if none exists, the owner told explicitly to run `rrd-audit-all` first rather than this workflow inventing findings from a fresh, uncoordinated code scan

**Halt if missing:** target project not indexed, or no prior `rrd-audit-all` report exists and the owner hasn't explicitly opted into this workflow reading raw per-detector reports instead.

## Investigation

- [ ] Prior `rrd-audit-all`/per-detector findings read and pattern-matched for recurring violations of an *unwritten* convention (same gap type appearing 3+ times across different files/detectors is treated as evidence a convention is needed, not itself re-reported as a new finding)
- [ ] Repo metadata checked for presence: `CONTRIBUTING.md`/equivalent, `CODEOWNERS`, lint/static-analysis config (`.eslintrc*`, `checkstyle.xml`, `.editorconfig`, `pylintrc`, etc.), a documented Definition-of-Done for an automated test
- [ ] Each governance artifact's *absence* is only reported as a finding when this run's evidence (recurring detector findings) shows the gap is actually being felt, not flagged reflexively for every project regardless of size/maturity

## Findings and Proposals

- [ ] Every finding cites the specific recurring pattern (with counts/examples) or the specific missing repo-metadata file that justifies it
- [ ] Every fix proposes a minimal, concrete starting artifact (a convention doc stub naming the actual pattern found, a lint config seeded with the actual violation category) — not a generic "add documentation" note
- [ ] Every fix written as a diff to the target project's `proposals/`

## Completion Criteria

- [ ] Findings summary written to `{rrd_artifacts}/standards-audit-{target_project}-{date}.md`
- [ ] All diff proposals written to the target project's `proposals/`, listed in the summary
