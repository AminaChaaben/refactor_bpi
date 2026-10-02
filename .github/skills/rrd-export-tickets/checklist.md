# Export Tickets Validation Checklist

## Prerequisites

- [ ] GitLab tooling (`glab`/`gh`/API access) confirmed actually present in this environment before proceeding into consent questions — if none exists, said so plainly and stopped, no fabricated export
- [ ] Owner named a specific completed `rrd-audit-all` run (by report path or "the most recent run for {project}") — never inferred silently
- [ ] Owner explicitly confirmed the target GitLab project/repo to create issues in — never assumed from the local repository name alone
- [ ] Owner explicitly confirmed proceeding with issue creation itself, as a separate confirmation from choosing which run to export — per this workflow's stricter consent bar (external, team-shared write)

## Export

- [ ] Exactly the top N opportunities exported (N = owner-specified, or `default_top_n` if not specified) — not silently exporting more or fewer
- [ ] Every issue body links back to the source report and the specific diff proposal file(s) for that opportunity
- [ ] Every issue carries the configured `issue_labels`
- [ ] No issue created for an opportunity already previously exported (check for an existing issue referencing the same opportunity ID before creating a duplicate)

## Completion Criteria

- [ ] Summary lists every created issue's URL, mapped to its opportunity ID/title
- [ ] Any opportunity skipped (e.g. already exported) stated explicitly with the reason
