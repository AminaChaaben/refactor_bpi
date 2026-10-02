# Detect Report Template

Shared structure for every `rrd-detect-*` findings report. Fill in `{...}` placeholders; keep section order and heading levels as-is so reports stay comparable across detectors and across runs.

```markdown
# Detect <Category> Report — {target_project}

**Confidence key:** High / Medium / Low

## Summary

- Findings: {count} ({high} high, {medium} medium, {low} low confidence)
- Proposal paths: `{target_project_root}/proposals/`

## Findings

### [{confidence}] {short title} — {file}:{line}

- **Evidence:** {graph query / log citation / grep match}
- **Why this is a finding:** {one or two sentences}
- **Proposed fix:** {diff summary or "see proposals/<file>.diff"}

(repeat per finding, highest confidence first)

## Calibration Notes

{any owner-confirmed accepted-risk items, stated as in-session context only — not persisted across sessions}
```

Notes:

- No date in the report title or filename — each detector's report is a single, overwritten-per-run snapshot (`detect-<name>-{target_project}.md`), not a history log. `rrd-audit-all` is the one exception, since it deliberately keeps a dated trend history.
- Skip the "Calibration Notes" section entirely if nothing was flagged as accepted risk during the run — don't leave it as an empty heading.
- If a detector's findings naturally group by category (e.g. gap type, coupling shape), group under `## Findings` with a `###` subheading per category, each still following the per-finding structure above.
