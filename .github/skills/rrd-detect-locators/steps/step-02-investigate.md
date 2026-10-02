---
name: 'step-02-investigate'
description: 'Find duplicated locators, missing priority convention, and missing centralized element repository'
nextStepFile: '{skill-root}/steps/step-03-report-and-propose.md'
---

# Step 2: Investigate

## SEQUENCE

### 1. Duplicated Locator Literals

Use `Grep` to find the same selector string (CSS/XPath literal) defined as a `By`/locator constant in more than one file. A locator repeated verbatim across two or more page objects is a factor-out candidate regardless of whether it's fragile — confirm each candidate by reading its definition before reporting.

### 2. Locator-Priority Convention Check

Sample locator definitions across the page-object layer (via `code-graph query files --param path=<pages dir>` to enumerate page-object files, then `code-graph query variables --param path=<file>` or `Grep` on a representative sample) and classify each by tier: `data-testid`/stable-ID (best) > ARIA/role/semantic attribute > visible text > absolute XPath/positional CSS (worst). Report whether the project is consistently near the top of this hierarchy, inconsistent (mixed tiers with no visible rule), or consistently low-tier (systemic risk, not just isolated fragility).

### 3. Centralized Element Repository Check

Check whether locators live in a single dedicated module/class per page (a conventional Page Object Model) or are scattered inline within test/spec files themselves. Absence of *any* page-object layer is a stronger finding than "page objects exist but locators aren't tiered consistently" — report which situation is actually present.

### 4. Cross-Reference Detect Instability

If Detect Instability findings were loaded in Step 1, do not re-report the same fragile selector as a new, independent finding. Instead, where a fragile selector from that pass is also part of a duplication or missing-repository finding here, cite it as corroborating evidence for *this* finding's severity, not as a separate line item.

### 5. Continue

Load `./step-03-report-and-propose.md`.
