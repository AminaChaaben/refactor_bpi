---
name: 'step-02-investigate'
description: 'Enumerate the directory structure, check layer separation, cross-layer violations, and naming consistency'
nextStepFile: '{skill-root}/steps/step-03-report-and-propose.md'
---

# Step 2: Investigate

## SEQUENCE

### 1. Enumerate the Actual Structure

Run `code-graph query tree --limit 500` (and `code-graph query files` for how many functions and classes each file holds) to get the real directory layout — do not assume a layer exists or is separated based on convention or the project's framework defaults. Map what's actually there against the seven candidate layers: tests, pages (page objects/screen objects), components (shared UI-interaction fragments), data (fixtures/factories/test-data), utils (technical helpers), config (environment/settings), reporting (report generation/formatting).

### 2. Layer Separation Check

For each of the seven layers, determine one of three states:

- **Present, dedicated directory** — no finding.
- **Present, but mixed with another concern** — e.g. data fixtures defined inline inside test files rather than a `data/`/`fixtures/` directory; report as a finding with both concerns named.
- **Absent entirely** — only a finding if the project's actual size/scope suggests the layer's function is needed somewhere (a project with zero test data anywhere isn't missing a "data layer," it just doesn't have data-dependent tests; a project with hardcoded values scattered through test bodies but no data layer *is* missing one). Distinguish "doesn't need this layer" from "needs it and doesn't have it" explicitly — don't flag every absent directory reflexively.

### 3. Cross-Layer Violation Check

Search page-object/screen-object files (via `Grep`, scoped with `path` to the pages/components layer identified in Step 1, and `code-graph query file-symbols` to attribute each hit to its method) for assertion library calls (`assert*`, `expect(`, `Assert.`, `should.`) embedded directly in interaction methods — a page object's job is interaction, not verification; assertions belong in the test layer.

Search test files for direct raw-data manipulation (inline SQL, direct file I/O against fixture files, hardcoded record construction) that bypasses whatever data layer exists elsewhere in the project — if a data layer exists, a test bypassing it to hand-roll its own data setup is the violation; if no data layer exists at all, that's Step 2's finding instead, not this one.

### 4. Naming-Convention Consistency Check

Within each identified layer, sample file names and check for a single consistent convention (case style, suffix/prefix pattern — e.g. `LoginPage.java`/`DashboardPage.java` vs. a stray `checkout_page.py` or `PageCheckout.java` in the same directory). Flag genuine inconsistency within one layer's own directory; a difference *between* layers (pages using `*Page` suffix, utils using no consistent suffix) is not itself a finding unless the owner asked for a single project-wide convention.

### 5. Continue

Load `./step-03-report-and-propose.md`.
