<!-- Powered by BMAD-CORE™ -->

# Detect Tech Versions

---

## Overview

Finds version-management problems in **Selenium and Playwright test-automation stacks** — Java runtime, Selenium, Playwright, JUnit/TestNG, Maven, and their npm/TS equivalents — declared in `pom.xml`, `build.gradle`/`build.gradle.kts`, and `package.json`.

This detector's primary, fully-reliable findings are **structural discipline**, not "is there a newer version out there": unpinned versions (Maven `LATEST`/`RELEASE`/open ranges/unexplained `-SNAPSHOT`, or npm ranges with no committed lockfile), a missing lockfile, and the same artifact's version duplicated across a parent POM/BOM/`dependencyManagement`/local `<version>` tag instead of a single source of truth. These are verifiable from the repo alone — no external call, no flakiness. Known CVEs are added on top from OSV.dev (online) or the curated snapshot (offline).

"Is a newer version available" and "is this a known CVE" are answered from **authoritative online sources** when network access is available (`network_access: online`, the default): the ecosystem's own tooling (`npm outdated`/`npm audit`, `mvn versions:display-dependency-updates`) and **OSV.dev** for advisories — reported as confident findings with source and date. When offline, latest-version claims degrade to a best-effort, explicitly-unverified pointer and CVE data falls back to the curated `version-database.csv` snapshot. npm `^`/`~` ranges with a committed lockfile are treated as correctly pinned (not flagged); a missing lockfile is its own reproducibility finding. It also flags breaking framework migrations like JUnit 4→5 or Selenium 3→4 (high), and toolchain-incompatible runtime bumps (e.g. a Java version bump that breaks a load-time-weaving agent already in the project — medium-risk, not safe-by-default).

This workflow applies the shared Investigation Contract, carried locally in this skill's own `./resources/knowledge/evidence-and-diff-discipline.md` (the same fragment Ray's persona references — kept in sync across skills, not a cross-folder pointer): every finding cites evidence, every fix is a diff in the target project's `proposals/`, source is never edited directly.

---

## WORKFLOW ARCHITECTURE

This workflow uses **single-mode step-file architecture**, not the tri-modal Create/Edit/Validate pattern used elsewhere in BMad — a detection run doesn't have a meaningful separate edit/validate mode, so it uses plain sequential steps instead.

---

## INITIALIZATION SEQUENCE

### 1. Configuration Loading

From `workflow.yaml`, resolve: `config_source`, `output_folder`, `rrd_artifacts`, `user_name`, `communication_language`, `document_output_language`, `date`.

### 2. First Step

Load, read completely, and execute:
`{skill-root}/steps/step-01-preflight-and-init.md`
