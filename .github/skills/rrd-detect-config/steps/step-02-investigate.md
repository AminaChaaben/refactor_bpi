---
name: 'step-02-investigate'
description: 'Find hardcoded config, inline env-switch logic, and unsafe parallel-execution settings'
nextStepFile: '{skill-root}/steps/step-03-report-and-propose.md'
---

# Step 2: Investigate

## SEQUENCE

### 1. Hardcoded URLs, Credentials, Secrets

Use `Grep` for literal URLs (`https?://`), inline credentials (`password\s*=`, `user(name)?\s*=\s*['"]`), and API keys/tokens (`api[_-]?key`, `token\s*=`, `secret`) embedded directly in source rather than pulled from config or environment variables.

### 2. Hardcoded Timeout/Wait Literals

Use `Grep` for numeric timeout/wait literals passed directly to wait/sleep/timeout calls instead of a named config value. Cross-reference against `rrd-detect-instability` findings where available — a fixed-sleep finding there and a hardcoded-timeout finding here are often the same root cause seen from two angles; note the overlap rather than double-counting it as two independent problems.

### 3. Inline Environment-Switch Logic

Search for conditional branching that selects behavior by environment (DEV/QA/REC/PROD) inline in test or page-object code — `if (env == "qa")`-style logic — rather than through a single externalized config file/object per environment.

### 4. Unsafe Parallel-Execution Config

Inspect test-runner config (e.g. `testng.xml`, `playwright.config.ts`, `jest.config.js`) for forced-serial settings (`workers=1`, `thread-count=1`, `fullyParallel=false`) and treat a forced-serial setting as a signal worth investigating, not an automatic finding on its own.

- If `rrd-detect-dependencies` has already run on this target and reported shared-fixture/shared-live-state coupling, cite that finding as the likely reason serialization was forced, and frame the config fix as secondary to fixing the underlying coupling.
- If no dependency finding exists yet, report the forced-serial setting as a standalone config finding, but flag explicitly that the root cause may be an unaudited coupling issue — recommend running `rrd-detect-dependencies` on this target before removing the serial constraint.

### 5. Continue

Load `./step-03-report-and-propose.md`.
