# Detect Tech Versions Heuristics

## What Success Looks Like

The owner learns exactly which parts of a **Selenium or Playwright test-automation stack** are outdated — Java runtime, Selenium/Playwright itself, their JUnit/TestNG test runners, Maven, and the npm equivalents for JS/TS Playwright projects — with current vs. latest-safe version, why it matters (CVE, breaking migration debt, or just staleness), and a concrete, minimally-scoped upgrade diff. This axis had zero dedicated detector coverage before this skill: version drift is invisible until a security scan or a forced migration deadline surfaces it, unlike structural smells that show up in code review.

**Modernization focus, not a compliance audit.** This detector is scoped to Selenium and Playwright test-automation stacks specifically — Java runtime, Selenium, Playwright, JUnit, TestNG, Maven, npm/Node, and the logging/assertion libraries that commonly ride along with them (Log4j, Jackson, Mockito, AssertJ, Hamcrest) — not every transitive dependency in the tree, and not general-purpose application frameworks (Spring, Cucumber/Karate, etc.) even if present in the same repo. It exists to answer "what should we upgrade next in our Selenium/Playwright stack and why," not to replace a full SCA (software composition analysis) tool or audit every technology in the codebase.

## Approach

### 1. Manifest-Only Scanning (Not Graph-Indexed)

`pom.xml`, `build.gradle`/`build.gradle.kts`, and `package.json` are configuration files, not code symbols. Read them directly via `Read`/`Grep` (for Maven, `code-graph query maven-deps` also lists the declared dependencies the indexer resolved, which is a useful cross-check). This is the documented fallback case in the Tool Usage Discipline section of `evidence-and-diff-discipline.md` — state it explicitly rather than defaulting to `Read` out of habit, since every other detector's default is graph-first.

### 2. Structural Discipline Is the Reliable Finding — "What's the Latest Version" Is Not

Earlier revisions of this skill tried to make "what's the latest version" an authoritative, live-queried fact — first via a static CSV snapshot, then via live registry lookups (Maven Central's `maven-metadata.xml`, the npm `/latest` endpoint, `search.maven.org`'s solr API). Testing this against a real project surfaced the actual problem: **every one of those live sources failed or disagreed** — `repo1.maven.org` returned HTTP 403 on every artifact tried; `search.maven.org`'s `core=gav` mode returned a stale/wrong answer (`4.33.0` vs. the real `4.47.0` for `selenium-java`); its default-core `latestVersion` field returned a pre-release (`3.0.0-beta3` for `log4j-core`) as if it were the safe latest; and independent lookups disagreed with each other on `testng` and `allure-testng` with no way to tell which was right without yet another check. Those *unauthenticated, single-endpoint* "what's the latest" lookups are unreliable — but the fix is not to give up on live data, it is to use **authoritative** sources instead: the ecosystem's own tooling (`npm outdated`/`npm audit`, `mvn versions:display-dependency-updates`), which already knows the resolved dependency tree, and **OSV.dev** for CVEs. In online mode (`{network_access}` = `online`, the default) these are trusted Tier 1 findings; the static CSV is kept only as an offline fallback. See step-02 §3b.

The reliable split now (step-02 §3-4):

- **"Is this version declared correctly" → fully structural, checked from the repo alone, every run, no external dependency.** Is the version pinned (not `LATEST`/`RELEASE`/an open range/an unexplained `-SNAPSHOT`)? Is it declared in exactly one place (a BOM, `dependencyManagement`, or a POM property) rather than duplicated across a parent, a BOM, and a local `<version>` tag? These are the same disciplines a well-run Java/Maven project enforces by convention, and they can be verified with certainty — no guessing, no external call, no risk of a wrong answer.
- **"Is this a CVE, and how severe" → OSV.dev online, curated CSV offline.** OSV.dev returns advisories (CVE/GHSA, severity, fixed range) for the exact resolved version across npm/Maven/PyPI and is the authoritative source; `npm audit` corroborates for npm. The curated CSV is a point-in-time offline fallback for the well-known cases only — not a coverage guarantee.
- **"Is a newer version available" → authoritative when online, best-effort only when offline.** In online mode, `npm outdated`/`mvn versions:display-dependency-updates` (with the registry/OSV as backup) give confident current-vs-latest data — report it as a Tier 1 finding with source and date. Only when no authoritative source is reachable do you fall back to a single best-effort lookup labeled "candidate — verify before applying." Never dress up a stale CSV number as current.
- **npm ranges are not "unpinned."** A `^`/`~` range with a committed lockfile is idiomatic and correctly pinned — don't flag it. Flag only a floating spec (`*`, `latest`, `x`, unbounded `>=`), or any range with **no lockfile committed**, and report a missing lockfile as its own reproducibility finding. (Maven ranges remain discouraged and are flagged as before.)

Practical implication: an artifact absent from the CSV is still in scope for the structural checks (pinning, duplication) regardless — those don't depend on the CSV at all. It's only CVE/severity data that requires a CSV entry to exist.

### 3. Breaking-Change Classification: Hardcoded Rules, Not Changelog Parsing

The following migrations are known breaking boundaries. Any version pair crossing one of these is BREAKING regardless of the CSV's own severity column:

| From → To | Breaking Changes |
|---|---|
| JUnit 4.x → JUnit 5.x (Jupiter) | `@Test(expected=...)` → `assertThrows(...)`; `@Before`/`@After` → `@BeforeEach`/`@AfterEach`; `@RunWith(...)` → `@ExtendWith(...)`; package `org.junit` → `org.junit.jupiter.api` |
| Selenium 3.x → Selenium 4.x | `DesiredCapabilities` → browser-specific `Options` classes; `driver.manage().timeouts()` API signature changed; W3C WebDriver protocol replaces JSON Wire Protocol |
| Playwright (major version jumps, e.g. 1.2x → 1.4x+) | Browser binary pin moves with the library version — always re-run the browser install step after bumping; check the changelog for removed/renamed API surface (e.g. `page.waitForTimeout` discouraged in favor of auto-waiting assertions) if more than a few minors behind |
| Java 8 → Java 11 | `javax.xml.bind` (JAXB) and other Java EE modules removed from the JDK — needs explicit dependency if used |
| Java 11 → Java 17/21 | Mostly safe (LTS-to-LTS); check for reliance on now-removed deprecated APIs (e.g. `SecurityManager` deprecated for removal in 17+) — and see §6 below, since this is exactly the class of bump that isn't safe in isolation if a load-time-weaving agent is present |
| Maven 3.6.x → Maven 3.9.x | Safe — build tool version bump, no source-level breaking changes for typical projects |

This table is a starting reference, not exhaustive — if a version pair not listed here is encountered, default to MEDIUM severity (not automatically SAFE) and say so explicitly, rather than assuming safety for an unlisted pair.

### 4. CVE Severity Overrides Everything

If OSV.dev (online) or `resources/version-database.csv`'s `cve` column (offline fallback) reports an advisory against the resolved version, the finding is CRITICAL regardless of the breaking-change classification or usage count — a low-usage but actively-exploited CVE (e.g. Log4Shell, CVE-2021-44228) still leads the report. Always propose the **minimum patched version** (`cve_fixed_in` column) for the diff, not the latest available version — a minimal, reviewable security fix is safer to ship than a simultaneous security-fix-plus-modernization diff.

### 5. Impact via Usage Count, Not Just Declaration

A dependency declared in the manifest but never imported anywhere (a stray transitive pin, or a leftover from removed code) is lower priority than one imported across dozens of files. Always run the `code-graph query module-usage` usage check (step-02 §5) before finalizing priority — don't rank purely off severity and version-gap size.

### 6. Runtime Version Bumps Are Not Safe In Isolation — Check the Toolchain

A Java LTS-to-LTS bump (8→11, 11→17/21, 17→21/25) looks SAFE by the migration-rules table above, but that table only covers the JDK's own removed/changed APIs. It says nothing about **javaagent-based or bytecode-manipulation tooling** already in the project — these fail at class-load time with opaque errors (`VerifyError`, `IllegalAccessError`) that have nothing to do with the project's own source and everything to do with the agent's bytecode format assumptions being out of date for the new JDK. This is the exact failure mode this fragment was missing: a saucedemo audit proposed Java 11→21 as SAFE, it was applied, and the actual test run crashed with `VerifyError: Expecting a stackmap frame` — caused by `aspectjweaver` (load-time weaving) being incompatible with the target JDK, not by anything in the migration-rules table.

Before classifying **any** Java/JVM runtime version bump as SAFE, grep the manifest(s) for these agent/weaving/bytecode-manipulation dependencies and build-plugin configs:

| Watch for | Why it matters |
|---|---|
| `aspectjweaver`, `aspectj-maven-plugin`, any `-javaagent:.../aspectj*` in surefire/failsafe `<argLine>` | Load-time weaving rewrites bytecode against the target JVM's class-file format; older AspectJ releases predate newer JDKs' verifier changes |
| `cglib`, `net.bytebuddy:byte-buddy`, `javassist`, `org.mockito:mockito-core` (via its inline/bytebuddy dependency) | Proxy/mocking libraries that generate bytecode at runtime; can lag new JDK bytecode versions |
| `org.projectlombok:lombok` | Annotation processor with JDK-internals coupling; needs a JDK-appropriate release |
| `org.jacoco:jacoco-maven-plugin` (`-javaagent` coverage instrumentation) | Coverage agent, same class-load-time bytecode risk |

If any of these are present, do **not** classify the runtime bump as SAFE outright — reclassify as **MEDIUM-RISK (toolchain verification required)** and say explicitly in the finding: "verify `<artifact>` version supports the target JDK before applying; if unconfirmed, treat this as a paired upgrade (bump both the JDK property and the agent's own version), not a standalone one-line diff." A version bump and a toolchain compatibility fix are two different changes and must not be silently bundled into one "safe" diff — modernizing the build and fixing a real functional/compatibility problem are separate decisions the owner should make separately.

### 7. Resolve Effective Versions, and Flag Duplicate Declarations as Their Own Finding

Before flagging a dependency as outdated, check whether its version is actually pinned locally or inherited from a parent POM / `dependencyManagement` / BOM import (e.g. `selenium-bom`, `junit-bom`). A BOM manages versions but doesn't add the dependency itself — so the same artifact can appear "outdated" in a naive scan while actually being centrally managed and already current in the effective POM. Note explicitly in the finding whether the version came from a local `<version>` tag, a `dependencyManagement` entry, or a BOM import, and don't propose a manifest-line diff for a version that isn't declared where the diff would target it — propose the fix at the place the version is actually set (the BOM/property), not a local override that would create drift between a `dependencyManagement` entry and a redundant local `<version>` tag.

Separately — and this is a structural finding in its own right, not a version-gap finding — check whether the **same artifact's version is declared in more than one place**: a parent POM property, a BOM import, a `dependencyManagement` entry, and a local `<version>` tag are each a legitimate single source of truth, but having two or more of them for the same artifact (especially if they disagree) is exactly the kind of drift-inviting duplication a disciplined build avoids. Report this as its own LOW/MEDIUM finding ("duplicate version declaration") regardless of whether either value is actually outdated — this mirrors the single-source-of-truth discipline good Maven projects already enforce by convention, and is fully verifiable from the repo alone.

### 8. Plugin Versions Are In Scope Too, Not Just Dependencies

This detector's curated list (§ above) is dependency-focused, but unpinned **build plugin** versions are an equally real and easy-to-detect version-drift finding, currently outside `version-database.csv`'s scope. While scanning the manifest, also flag any `<plugin>`/`plugins { }` entry using `LATEST`, `RELEASE`, an open version range, or a `-SNAPSHOT` outside an explicitly-accepted temporary case — report as a LOW/MEDIUM finding ("unpinned plugin version — reproducibility risk") even though it has no CVE or breaking-migration angle; the risk here is a build that silently changes behavior between runs, not a security or migration issue.

## Calibration Note

The hardcoded migration-rules table and the version database are both point-in-time snapshots. Ray does not carry cross-session calibration memory — treat any owner statement that "we've already assessed this CVE as a false positive for our usage" or "this migration was already evaluated and rejected" as in-session context for that run, not as something to persist automatically. If a version pair's actual breaking-change behavior contradicts this fragment's table (e.g. a patch the owner already knows behaves differently), defer to the owner's direct knowledge and note the discrepancy in the finding rather than silently overriding it.
