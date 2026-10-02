# Detect Tech Versions Heuristics

## What Success Looks Like

The owner learns exactly which technologies are outdated — Java runtime, JUnit, Spring, Selenium, Maven, and npm dependencies — with current vs. latest-safe version, why it matters (CVE, breaking migration debt, or just staleness), and a concrete, minimally-scoped upgrade diff. This axis had zero dedicated detector coverage before this skill: version drift is invisible until a security scan or a forced migration deadline surfaces it, unlike structural smells that show up in code review.

**Modernization focus, not a compliance audit.** This detector is scoped to a curated technology list (Java, JUnit, Spring, Selenium, Maven, npm/Node, and their common test-automation companions), not every transitive dependency in the tree. It exists to answer "what should we upgrade next and why," not to replace a full SCA (software composition analysis) tool.

## Approach

### 1. Manifest-Only Scanning (Not Graph-Indexed)

`pom.xml`, `build.gradle`/`build.gradle.kts`, and `package.json` are configuration files, not code symbols. Read them directly via `Read`/`Grep` (for Maven, `code-graph query maven-deps` also lists the declared dependencies the indexer resolved, which is a useful cross-check). This is the documented fallback case in the Tool Usage Discipline section of `evidence-and-diff-discipline.md` — state it explicitly rather than defaulting to `Read` out of habit, since every other detector's default is graph-first.

### 2. Version Comparison Against a Curated Database

`resources/version-database.csv` (in `rrd-detect-tech-versions`'s own resources; consult that skill's copy) is a static, offline snapshot (not a live API call) covering the well-known versions in a typical Java/Selenium/JUnit test-automation stack. This is a deliberate tradeoff: instant, works offline, no rate limits, no silent failures on an air-gapped Jenkins agent — at the cost of going stale between database refreshes. Treat an artifact not present in the CSV as **out of scope for this run**, not a missing finding — state the skipped count once, don't guess at a version comparison with no reference data.

### 3. Breaking-Change Classification: Hardcoded Rules, Not Changelog Parsing

The following migrations are known breaking boundaries. Any version pair crossing one of these is BREAKING regardless of the CSV's own severity column:

| From → To | Breaking Changes |
|---|---|
| JUnit 4.x → JUnit 5.x (Jupiter) | `@Test(expected=...)` → `assertThrows(...)`; `@Before`/`@After` → `@BeforeEach`/`@AfterEach`; `@RunWith(...)` → `@ExtendWith(...)`; package `org.junit` → `org.junit.jupiter.api` |
| Spring 5.x → Spring 6.x | `javax.*` → `jakarta.*` namespace change across all Spring-managed annotations and APIs |
| Selenium 3.x → Selenium 4.x | `DesiredCapabilities` → browser-specific `Options` classes; `driver.manage().timeouts()` API signature changed; W3C WebDriver protocol replaces JSON Wire Protocol |
| Java 8 → Java 11 | `javax.xml.bind` (JAXB) and other Java EE modules removed from the JDK — needs explicit dependency if used |
| Java 11 → Java 17/21 | Mostly safe (LTS-to-LTS); check for reliance on now-removed deprecated APIs (e.g. `SecurityManager` deprecated for removal in 17+) |
| Maven 3.6.x → Maven 3.9.x | Safe — build tool version bump, no source-level breaking changes for typical projects |

This table is a starting reference, not exhaustive — if a version pair not listed here is encountered, default to MEDIUM severity (not automatically SAFE) and say so explicitly, rather than assuming safety for an unlisted pair.

### 4. CVE Severity Overrides Everything

If the version database's `cve` column is populated for the current version, the finding is CRITICAL regardless of the breaking-change classification or usage count — a low-usage but actively-exploited CVE (e.g. Log4Shell, CVE-2021-44228) still leads the report. Always propose the **minimum patched version** (`cve_fixed_in` column) for the diff, not the latest available version — a minimal, reviewable security fix is safer to ship than a simultaneous security-fix-plus-modernization diff.

### 5. Impact via Usage Count, Not Just Declaration

A dependency declared in the manifest but never imported anywhere (a stray transitive pin, or a leftover from removed code) is lower priority than one imported across dozens of files. Always run the `code-graph query module-usage` usage check before finalizing priority — don't rank purely off severity and version-gap size.

## Calibration Note

The hardcoded migration-rules table and the version database are both point-in-time snapshots. Ray does not carry cross-session calibration memory — treat any owner statement that "we've already assessed this CVE as a false positive for our usage" or "this migration was already evaluated and rejected" as in-session context for that run, not as something to persist automatically. If a version pair's actual breaking-change behavior contradicts this fragment's table (e.g. a patch the owner already knows behaves differently), defer to the owner's direct knowledge and note the discrepancy in the finding rather than silently overriding it.
