# Detect Tech Versions Validation Checklist

## Prerequisites

- [ ] `code-graph status` run; index confirmed present and not stale (re-indexed first if needed)
- [ ] Target project confirmed indexed
- [ ] Knowledge fragment loaded: `evidence-and-diff-discipline.md`
- [ ] Knowledge fragment loaded: `detect-tech-versions.md`
- [ ] Knowledge fragment loaded: `detect-report-template.md`
- [ ] Version database loaded: `resources/version-database.csv`

**Halt if missing:** target project not indexed.

## Investigation

- [ ] All manifests found (`pom.xml`, `build.gradle`/`build.gradle.kts`, `package.json`) — not just the first match
- [ ] Every dependency's declared version extracted, plus the resolved version from the lockfile where present
- [ ] npm caret/tilde ranges NOT flagged as unpinned when a lockfile is committed; a missing lockfile reported as its own finding
- [ ] Online mode: latest/CVE data taken from authoritative sources (`npm outdated`/`npm audit`, `mvn versions:display-dependency-updates`, OSV.dev), with source and date recorded
- [ ] Offline / source unreachable: CVE data taken from `version-database.csv` fallback and labeled a point-in-time snapshot; no-data artifacts counted, not silently dropped
- [ ] Every outdated match classified as BREAKING or SAFE using the hardcoded migration-rules table, not assumed
- [ ] Usage/impact count computed via `code-graph query module-usage` / `importers`, not estimated
- [ ] Priority computed with the documented formula (severity × impact / effort × breaking_risk), not eyeballed

## Findings and Proposals

- [ ] Every finding names current version, latest safe version, severity, and (if applicable) CVE ID
- [ ] Every finding cites its evidence (manifest location + usage search)
- [ ] CVE fixes propose the minimum patched version (OSV fixed range / `npm audit` fixAvailable online, or `cve_fixed_in` offline), not necessarily the latest
- [ ] Breaking migrations are flagged as such, with a representative diff (not a diff attempted per file for large migrations)
- [ ] Every fix written as a diff to the target project's `proposals/`
- [ ] CRITICAL (CVE) findings are surfaced first in the summary regardless of computed rank order

## Completion Criteria

- [ ] Findings summary written to `{rrd_artifacts}/detect-tech-versions-{target_project}.md`
- [ ] All diff proposals written to the target project's `proposals/`, listed in the summary
- [ ] Skipped-artifact count stated once, up front
