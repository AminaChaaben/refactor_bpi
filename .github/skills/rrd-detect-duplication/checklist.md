# Detect Duplication Validation Checklist

## Prerequisites

- [ ] `code-graph status` run; index confirmed present and not stale (re-indexed first if needed)
- [ ] Target project confirmed indexed
- [ ] Knowledge fragment loaded: `evidence-and-diff-discipline.md`
- [ ] Knowledge fragment loaded: `detect-duplication.md`
- [ ] Knowledge fragment loaded: `detect-report-template.md`
- [ ] Similarity threshold confirmed with owner (or default used)

**Halt if missing:** target project not indexed.

## Investigation

- [ ] `code-graph query similar-pairs` run for `SIMILAR_TO` edges (jaccard score) as the primary similarity source
- [ ] `callees`/`file-symbols`/`Grep` fallback run only for areas with no `SIMILAR_TO` coverage
- [ ] Every candidate group confirmed by reading actual source (`code-graph query symbol`), not just the similarity score
- [ ] Similarity score (jaccard if available, otherwise estimated) recorded per duplicate group
- [ ] Files/symbols involved in each group recorded

## Findings and Proposals

- [ ] Every finding names the duplicate group, similarity score, and files/symbols
- [ ] Every fix proposes a factor-out (shared logic extracted, duplicates pointed at it)
- [ ] Every fix written as a diff to the target project's `proposals/`

## Completion Criteria

- [ ] Findings summary written to `{rrd_artifacts}/detect-duplication-{target_project}.md`
- [ ] All diff proposals written to the target project's `proposals/`, listed in the summary
