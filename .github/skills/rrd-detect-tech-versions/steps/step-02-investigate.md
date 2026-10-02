---
name: 'step-02-investigate'
description: 'Scan build/dependency manifests for pinning/centralization discipline, cross-reference curated CVE/breaking-migration knowledge, and treat any live version check as an unverified pointer'
nextStepFile: '{skill-root}/steps/step-03-report-and-propose.md'
---

# Step 2: Investigate

## SEQUENCE

### 1. Locate Manifests

Use `Glob`/`Grep` to find, at the target project root:

- `pom.xml` (Maven)
- `build.gradle` / `build.gradle.kts` (Gradle)
- `package.json` (npm/Node)

A project may have more than one of these (e.g. a Java backend + a JS test framework) — scan every manifest found, don't stop at the first match.

### 2. Extract Declared Versions

For each manifest found, read it directly (these are config files, not indexed code symbols — `Read` is correct here; note this explicitly per the Tool Usage Discipline fallback rule) and extract:

- **pom.xml**: `<properties>` (`maven.compiler.source`/`target`, `java.version`), every `<dependency>` group/artifact/version (including versions inherited from a parent POM or a `<dependencyManagement>` block — note when a version is inherited rather than declared locally)
- **build.gradle**: `sourceCompatibility`/`targetCompatibility` (or `toolchain` block for Java version), every `dependencies { }` entry with a resolvable version string
- **package.json**: `engines.node` if present, every entry in `dependencies` and `devDependencies`. Also read the lockfile (`package-lock.json`/`yarn.lock`/`pnpm-lock.yaml`) if present to get each package's **resolved** version — that resolved version, not the caret range, is what step 3b and step 4 check against OSV/audit. Note if no lockfile is committed (a finding — see §3).

### 3. Structural Discipline Checks (Primary, Fully Reliable — No External Call)

Before touching any external source, check what's verifiable from the repo alone:

- **Unpinned version (ecosystem-aware)**: for **Maven/Gradle**, flag `LATEST`, `RELEASE`, an open version range, or an unexplained `-SNAPSHOT` on a dependency or plugin. For **npm**, a `^`/`~` caret/tilde range is *idiomatic and acceptable when a committed lockfile pins the resolved version* — do **not** flag it. Flag npm only for a genuinely floating spec (`*`, `latest`, `x`, an unbounded `>=`), or for any range **when no lockfile is committed**. Report as its own finding — see step 7 for the plugin-specific case.
- **Missing lockfile (npm/Node)**: if `package.json` exists but no `package-lock.json`/`yarn.lock`/`pnpm-lock.yaml` is committed, report a MEDIUM "missing lockfile — non-reproducible installs" finding: every `^`/`~` range then resolves to whatever is newest at install time, so builds aren't reproducible and resolved versions can't be audited reliably.
- **Duplicate version declaration**: the same `(group, artifact)` version set in more than one place — a parent POM property, a BOM import, a `dependencyManagement` entry, and/or a local `<version>` tag. Report as its own LOW/MEDIUM finding ("duplicate version declaration") regardless of whether the value is outdated — per `detect-tech-versions.md` §7. If the duplicates disagree, say so explicitly; that's a real drift risk, not a cosmetic one.

These findings carry full confidence — no caveat needed, no external dependency, nothing to verify further.

### 3b. Live Version & Advisory Check (Online — Primary Source When Network Is Available)

`{network_access}` defaults to `online`. **When online, this step's results are authoritative Tier 1 findings**, not "verify yourself" pointers — because they come from the ecosystem's own tooling and the OSV advisory database, not a single guessed endpoint. When `offline`, skip to the CSV fallback in step 4 and label any version-gap statement best-effort.

Use the ecosystem's own tooling first — it is ground truth, already knows the project's resolved tree, and needs no guessing:

- **npm/Node (`package.json` present):**
  - `npm outdated --json` in the target project → current / wanted / latest per package (authoritative).
  - `npm audit --json` → CVEs from the npm advisory DB with severity and the fix-available version (authoritative).
  - If `node_modules`/lockfile is absent so those can't run, fall back to per-package `https://registry.npmjs.org/{pkg}` (`dist-tags.latest`) for latest, and OSV (below) for CVEs.
- **Maven/Gradle:**
  - `mvn versions:display-dependency-updates` and `versions:display-plugin-updates` → available upgrades (authoritative for the resolved POM).
  - If Maven can't run, fall back to Maven Central `https://search.maven.org/solrsearch/select?q=g:{group}+AND+a:{artifact}&core=gav&rows=1&wt=json` for latest (best-effort — verify), and OSV for CVEs.
- **OSV.dev — cross-ecosystem CVE source (primary online, replaces the CSV):** `POST https://api.osv.dev/v1/query` with `{"package":{"ecosystem":"npm|Maven|PyPI","name":"{name}"},"version":"{resolved_version}"}`. Returns advisories (CVE/GHSA IDs, severity, affected/fixed ranges) for the exact resolved version — use the locked version, not the range.

Record the source and date for every online result. If two authoritative sources disagree, say so. Only when **no** authoritative source is reachable (no tooling, no OSV hit — a lone registry/web guess) do you label a result `candidate — verify before applying, per {source}`. This step never blocks or degrades the structural findings from step 3.

### 4. CVE / Advisory Severity (OSV Online → CSV Offline Fallback)

Determine CVE/severity for each `(ecosystem, name, resolved_version)`:

- **Online (default):** trust the OSV.dev result from step 3b — CVE/GHSA ID, severity, and the first fixed version. If OSV (or `npm audit`) reports an advisory affecting the resolved version, this is CRITICAL/HIGH per the advisory's severity, and the proposed fix targets the first fixed version (OSV's fixed range, or `npm audit`'s `fixAvailable`), not a bare "latest."
- **Offline / OSV unreachable:** fall back to `./resources/version-database.csv` (`cve`/`cve_fixed_in`/`severity`) as a point-in-time snapshot — good for the well-known cases (Log4Shell, jackson-databind) but not authoritative for coverage; state that CVE data is from the offline snapshot and may be incomplete.
- Also check whether the pair is a known agent/weaving/toolchain-risk artifact (§6 of `detect-tech-versions.md`).

If neither OSV nor the CSV has data for an artifact, say so — CVE/severity simply isn't available for it (don't guess). State the no-data count once at the end, not per-artifact.

### 5. Classify Breaking-Change Risk

For every outdated match, consult `./resources/knowledge/detect-tech-versions.md`'s hardcoded migration-rules table (JUnit 4→5, Selenium 3→4, Playwright major jumps, Java 8→11/17/21). If the current→target version pair crosses a documented breaking boundary, classify as **HIGH** (breaking) regardless of the CVE/severity column; otherwise use the CSV's declared severity, or MEDIUM by default if the artifact has no CSV entry (per step 4).

### 5b. Toolchain Compatibility Check (Java/JVM Runtime Bumps Only)

Before finalizing a SAFE classification for any Java/JVM runtime version change, grep the manifest(s) and surefire/failsafe plugin config for the agent/weaving/bytecode-manipulation watchlist in `./resources/knowledge/detect-tech-versions.md` §6 (`aspectjweaver`/`aspectj-maven-plugin`, `cglib`/`byte-buddy`/`javassist`, `lombok`, `jacoco-maven-plugin`'s javaagent). If any is present, downgrade the classification to **MEDIUM-RISK (toolchain verification required)** and note in the finding that the agent's own version must be confirmed compatible with the target JDK before the bump is applied standalone. This check exists because a prior run classified a Java 11→21 bump as SAFE on a project using `aspectjweaver`, applied it, and the test suite crashed at class-load time (`VerifyError`) — a failure this table would have caught.

### 5c. Effective Version Resolution

For any dependency whose version isn't a local `<version>` tag on the `<dependency>` itself, note whether it comes from a parent POM, a `dependencyManagement` entry, or a BOM import (per `./resources/knowledge/detect-tech-versions.md` §7). Don't propose a diff at a location that doesn't actually declare the version — target the BOM/property/parent entry that does.

### 6. Determine Impact (Usage Count)

For each outdated artifact, run `code-graph query module-usage --param module_prefix=<prefix>` for its characteristic import/package prefix (e.g. `org.junit` for JUnit 4 vs `org.junit.jupiter` for JUnit 5, `org.openqa.selenium` for Selenium) to count how many files actually use it (`code-graph query importers --param module_prefix=<prefix>` lists the files) — not just declare it as a dependency. An artifact declared but never imported (a stray transitive pin) is lower priority than one imported across 40 test files.

**Exception — agent/reflection-loaded artifacts don't get penalized for a low static-import count.** Load-time weaving agents and `ServiceLoader`-loaded implementations are used without a static `import` anywhere in the codebase by design. For any artifact on the §6 watchlist (or otherwise known to be agent/reflection-loaded), don't treat a 0-or-low import count as "low impact" — note explicitly that usage is structural/agent-based and impact should be judged by whether the agent is active in the build (present in `<argLine>`/plugin config), not by import count.

### 7. Plugin Version Pinning

While scanning, also flag any build-plugin entry (`<plugin>`/`plugins { }`) declared with `LATEST`, `RELEASE`, an open version range, or an unexplained `-SNAPSHOT`. Report as a LOW/MEDIUM "unpinned plugin version" finding per `./resources/knowledge/detect-tech-versions.md` §8 — reproducibility risk, not a CVE or migration issue, so it doesn't go through the severity/breaking-risk formula below; list it separately in the report.

### 8. Compute Priority

```
priority = (severity_weight × impact_count) / (effort_weight × breaking_risk_weight)

severity_weight:  CRITICAL(CVE)=100, HIGH=40, MEDIUM=15, LOW=5
breaking_risk_weight: BREAKING=3, SAFE=1
effort_weight: single-file/manifest-only=1, multi-file mechanical rename=2, requires-code-refactor=4
```

Rank findings by this priority score, descending, within this run only (relative ranking, same convention as `rrd-audit-all`'s opportunity ranking — not an absolute cross-run scale).

### 9. Continue

Load `./step-03-report-and-propose.md`.
