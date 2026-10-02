---
name: 'step-03-report-and-propose'
description: 'Write findings summary and diff proposals for modernization upgrades'
nextStepFile: null
---

# Step 3: Report and Propose

## SEQUENCE

### 1. Write Findings Summary

Write `{rrd_artifacts}/detect-tech-versions-{target_project}.md` (following `./resources/knowledge/detect-report-template.md`'s structure), structured in two tiers:

**Tier 1 — Confident findings (structural + authoritative online):** unpinned versions, missing lockfile, and duplicate version declarations (step-02 §3), CVE findings from OSV.dev online or the curated CSV offline (step-02 §4), version-gap findings from `npm outdated`/`mvn versions` online (step-02 §3b), breaking-migration classifications (step-02 §5), toolchain-risk flags (step-02 §5b), and effective-version-source notes (step-02 §5c). For each: technology name, current version, severity (CRITICAL/HIGH/MEDIUM/LOW/MEDIUM-RISK-TOOLCHAIN), CVE ID if applicable, breaking/safe/toolchain-risk classification, effective-version source (local/parent/BOM), impact (file count using it, or "structural/agent-based — see §6 watchlist" per step-02 §6), effort estimate, and priority rank.

**Tier 2 — best-effort, unverified pointers only (step-02 §3b fallback):** used *only* when no authoritative source was reachable — offline mode, or a lone registry/web guess with no OSV/tooling corroboration. List separately, each labeled `candidate — verify before applying, per {source}, checked {date}`. Never merge these into Tier 1's severity/priority scoring — they are a pointer for the owner to check, not a finding this skill is confident in.

State the not-in-CSV artifact count (from step-02 §4) once at the top. List unpinned-plugin findings (step-02 §7) in a separate small section under Tier 1 — they don't carry a priority score.

### 2. Write Diff Proposals

For each Tier 1 finding, write a diff to `{target_project_root}/proposals/`. Tier 2 candidates never get a diff on their own — they're a pointer, not a confirmed target; only fold one into a diff after the owner has confirmed it, at which point it becomes a manifest-only upgrade below.

- **Unpinned version / duplicate version declaration**: propose pinning to the current effective resolved version (single source of truth), or consolidating duplicate declarations into one location (the BOM/property/parent entry, per `detect-tech-versions.md` §7) — a minimal, mechanical diff, not a modernization.
- **Manifest-only upgrade** (safe, no breaking change): bump the version string in `pom.xml`/`build.gradle`/`package.json` directly.
- **CVE fix**: bump to the minimum patched version cited in `./resources/version-database.csv`'s `cve_fixed_in` column — not necessarily the latest version — to keep the fix minimal and reviewable, per the same "smallest safe diff" principle as every other detector.
- **Breaking migration (e.g. JUnit 4→5)**: propose the manifest version bump plus, where the migration is mechanical (e.g. `@Before`→`@BeforeEach` renames), a representative diff for the first 1-2 affected files with a note that the same rename applies across the full impacted file list (do not attempt to generate a diff per file for large migrations — cite the file count and pattern instead, and let `rrd-apply-and-verify` or the owner extend it).
- **Mixed-version conflict** (same framework at two versions via transitive deps): propose either an explicit exclusion in the manifest or an upgrade of the outdated direct dependency, whichever resolves the conflict with a smaller diff.
- **MEDIUM-RISK (toolchain verification required)** (step-02 §5b): do **not** write a standalone one-line version-bump diff. Instead, write the finding as a flagged recommendation only — name the specific agent/weaving dependency at risk, state that its own version must be confirmed compatible with the target JDK first, and offer to draft a paired diff (JDK property + agent version bump together) only once the owner confirms compatibility. Shipping an unverified "safe" bump here is exactly the mistake this check exists to prevent.
- **Unpinned plugin version** (step-02 §7): propose pinning the plugin to its current effective resolved version (or the CSV's `latest_safe_version` if the plugin is in the database) — a minimal, mechanical diff, not a modernization.

### 3. Summarize to Owner

Report finding count (with breakdown by severity), the skipped-artifact count, and output/proposal paths, in `{communication_language}`, in Ray's voice. Lead with any CRITICAL (CVE) findings first regardless of overall rank order — a security fix should never be buried under a lower-severity item that happened to score higher on impact alone; state explicitly if a CVE finding is not rank #1 and why (e.g. very low usage count reduced its computed priority, but severity still warrants leading with it).

Workflow complete.
