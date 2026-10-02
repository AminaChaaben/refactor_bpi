---
name: 'edit'
description: 'Evidence fusion and opportunity grouping. Output: opportunities.json (user can stop here).'
nextMode: 'validate'
---

# Edit: Fuse Evidence & Group Opportunities

## MODE GOAL

Take raw findings from Create mode, correlate them by shared root causes, and group into opportunities. No ranking applied yet — just discovery and grouping.

User can stop here with grouped opportunities before impact analysis/ranking.

## INPUT

Read from Create mode: `{project-root}/.refactor-radar-work/findings.json`

## SEQUENCE

### 1. Evidence Fusion (from step-02b)

For each pair of findings:
- Check if they share affected_target (same file/class)
- Check if they share root_cause_signals (same underlying defect type)
- Check if graph traversal confirms a direct call path between them
- If 2+ of these match: record as a Correlation with strength (strong/medium/weak)

Output: `{project-root}/.refactor-radar-work/correlations.json`

### 2. Opportunity Engine (from step-02c)

For each correlation:
- Group all findings that correlate into an Opportunity
- Record: title, description, problem_statement, supporting_findings, correlations, root_causes, affected_components
- No impact/risk/effort yet — structural analysis only

Output: `{project-root}/.refactor-radar-work/opportunities.json`

### 3. Quality Gate — Opportunity Completeness

Before stopping, verify:
- [ ] Every finding from Create is represented in at least one Opportunity
- [ ] No Opportunity has missing supporting_findings
- [ ] All correlations are documented

### 4. Report Grouping Summary

Log:
- Total opportunities formed: N
- Findings per opportunity: min/max/avg
- Correlation strength distribution (strong/medium/weak counts)

**User can stop here.** Impact analysis and ranking (Validate mode) are optional. If the user stops, they have grouped opportunities for review without the ranking overhead.

### 5. Continue to Validate (Optional)

If the user confirms, proceed to the Validate mode. Otherwise, end.
