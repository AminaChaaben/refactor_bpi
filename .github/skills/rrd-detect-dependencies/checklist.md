# Detect Dependencies Validation Checklist

## Prerequisites

- [ ] `code-graph status` run; index confirmed present and not stale (re-indexed first if needed)
- [ ] Code graph confirmed indexed and not stale (`code-graph index` run first if it was)
- [ ] Knowledge fragment loaded: `./resources/knowledge/evidence-and-diff-discipline.md`
- [ ] Knowledge fragment loaded: `./resources/knowledge/detect-dependencies.md`
- [ ] Knowledge fragment loaded: `./resources/knowledge/detect-report-template.md`

**Halt if missing:** target project not indexed.

---

## Investigation

- [ ] `code-graph query variables` / `by-decorator` / `caller-count` (plus `Grep` for literals) run to surface shared mutable fixtures, module-level state, singletons, shared live services/databases
- [ ] `code-graph query callers` / `importers` used to confirm real mutable-state coupling, not just shared imports
- [ ] Shared read-only constants excluded from findings (not coupling)
- [ ] Live/shared hosted state weighted heavily even without explicit code-level coupling
- [ ] Each finding classified by coupling shape: shared fixture / order-dependence / shared live resource
- [ ] Each finding assigned a risk level

## Findings and Proposals

- [ ] Every finding cites the exact graph query, trace, or evidence that produced it
- [ ] Every finding has a proposed isolation fix (scope the fixture, inject fresh state, remove ordering assumption)
- [ ] Every fix is written as a diff to the target project's `proposals/` — no direct source edits
- [ ] No fix invented without a concrete graph-backed rationale

## Completion Criteria

- [ ] Findings summary produced (count, risk levels, files/tests involved)
- [ ] Output written to `{rrd_artifacts}/detect-dependencies-{target_project}.md`
- [ ] All diff proposals written to the target project's `proposals/`, listed in the summary
