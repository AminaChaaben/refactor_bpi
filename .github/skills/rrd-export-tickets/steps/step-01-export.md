---
name: 'step-01-export'
description: 'Confirm scope and consent, then create one GitLab issue per top-N opportunity'
nextStepFile: null
---

# Step 1: Export

## SEQUENCE

### 1. Resolve the Source Run

Ask the owner which completed `rrd-audit-all` run to export from, if not already stated (a specific `refactor-radar-audit-*.html` path, or "the most recent for {target_project}" resolved by file mtime). Read that report's `Opportunity[]` data (from the report itself or the underlying JSON if still present).

### 2. Confirm GitLab Tooling Actually Exists in This Environment

Before asking anything else, check whether any GitLab-capable tool is actually available — `glab` on PATH, `gh` configured against a GitLab remote, or a documented GitLab API credential/MCP tool in this session. If none exist, say so plainly and stop here rather than proceeding into the consent questions below for a workflow that has no way to act on a "yes": *"No GitLab CLI or API access is available in this environment (checked for `glab`, `gh`). This workflow can't create issues without one — install/configure `glab` (or an equivalent), or tell me a different way to reach your GitLab instance, and I'll pick back up from here."* Do not fabricate issue URLs or pretend an export happened.

### 3. Confirm Scope and Destination — Two Separate Confirmations

1. Confirm how many opportunities to export (`{top_n}` if given, else `default_top_n`) and which specific opportunities that resolves to, by title — show the list before creating anything.
2. Confirm the destination GitLab project explicitly. Do not assume it matches the local repository name — a project checked out locally as `my-app` may live in GitLab as a differently-named or differently-pathed repo.

**Do not proceed past this point without an explicit yes to both**, per this workflow's stricter consent bar (`SKILL.md`) — this writes to a shared, external system, unlike every other Refactor Radar workflow's local `proposals/` diffs.

### 4. Check for Already-Exported Opportunities

Before creating any issue, search the target GitLab project's existing issues for a reference to each opportunity's ID (e.g. searching issue descriptions for the opportunity ID string). Skip any opportunity that already has a corresponding issue — state which were skipped and why, don't silently re-create.

### 5. Create One Issue Per Remaining Opportunity

For each opportunity to export, create a GitLab issue via the available GitLab tooling (`glab issue create` or the GitLab API, whichever is available in this environment):

- **Title**: the opportunity's title, prefixed with its priority level (e.g. `[Critical] ...`)
- **Body**: problem statement, affected components, links to the specific diff proposal file(s) in `proposals/`, and a link back to the source HTML report
- **Labels**: `{issue_labels}` from config

### 6. Report

List every created issue's URL mapped to its opportunity title, and every skipped opportunity with its reason (already exported / owner excluded it in Step 2).

Workflow complete.
