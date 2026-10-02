# Detect Layering Heuristics

## What Success Looks Like

The owner learns exactly where project-level layering breaks down — a layer missing or mixed with another concern, a cross-layer responsibility violation (assertions in a page object, raw data access in a test), or inconsistent naming within a layer — with directory/file:line precision and a scoped fix. This detector is deliberately positioned as **prevention, not cure**: `rrd-detect-duplication` and `rrd-detect-complexity` already catch the worst downstream symptoms of layering rot (hundreds of redundant lines, unmaintainable hotspots). This detector catches the structural conditions that let that rot start — lower priority than the detectors that catch symptoms already causing false-fails, but valuable for a project trying to stay maintainable rather than just recover from having stopped being maintainable.

## The Seven Candidate Layers

Tests/scenarios, pages (page objects/screen objects), components (shared UI-interaction fragments), data (fixtures/factories/test-data), utils (technical helpers), config (environment/settings — note the overlap with `rrd-detect-config`, which owns the *content* of config, e.g. hardcoded values; this detector owns whether config *lives in its own layer* structurally), reporting (report generation/formatting).

## Approach

### 1. Enumerate, Don't Assume

Use `code-graph query tree` (directories and what each contains; `--param path=<dir>` to zoom in) and `code-graph query files` (functions and classes per file) to see the actual directory layout. Never assume a layer exists or is separated based on the project's framework defaults or naming convention alone — a project using a popular test framework doesn't automatically have clean layering just because the framework encourages it.

### 2. Layer Separation — Three States, Not Two

For each of the seven layers:

- **Present, dedicated directory** — no finding, say so explicitly.
- **Present, but mixed with another concern** — the actual finding shape most worth reporting. E.g. test data hardcoded inline inside test method bodies rather than in a `data/`/`fixtures/` directory.
- **Absent entirely** — only a finding if the project's actual scope suggests the function is needed and being handled ad-hoc elsewhere. A project with zero test data anywhere genuinely doesn't need a data layer; a project with hardcoded values scattered through a dozen test bodies but no data layer *does* need one — the second case is a finding, the first is not. Don't flag every empty-looking layer reflexively; that produces noise, not signal.

### 3. Cross-Layer Violations — Two Concrete Shapes

**Assertions in interaction code**: a page/screen object's job is interaction (click, type, navigate), not verification. Search for assertion-library calls (`assert*`, `expect(`, `Assert.`, `should.`) embedded inside page-object methods. A page object with an assertion inside a method meant to return a value or perform an action is mixing "does this element exist and is interactable" (a legitimate interaction-layer concern) with "should this value equal X" (a test-layer concern) — the second belongs one layer up, in the calling test.

**Raw data access in tests**: a test directly doing inline SQL, raw file I/O against a fixture file, or hand-rolled record construction *when a data layer already exists elsewhere in the project* is bypassing that layer, which is the actual violation — the fix is routing through the existing layer, not creating a new one. If no data layer exists at all, that's a Layer Separation finding (above), not this one — don't double-report the same root cause as both a missing layer and a cross-layer violation.

### 4. Naming-Convention Consistency — Within a Layer, Not Across Layers

Sample file names within one layer's directory and check for a single consistent case style and suffix/prefix pattern. `LoginPage.java`, `DashboardPage.java`, and a stray `checkout_page.py` or `PageCheckout.java` in the same `pages/` directory is a real finding — the inconsistency makes it harder to predict where to find or add a new page object. A difference *between* layers (pages consistently using a `*Page` suffix, utils having no consistent suffix at all) is not itself a finding — different layers commonly have different internal conventions, and forcing one project-wide convention across structurally different concerns is a much larger, more disruptive change than this detector should propose unprompted.

## What NOT to Flag

- A small, genuinely single-purpose project (e.g. a 3-test smoke suite) doesn't need all seven layers as separate directories — recommending a full component/data/reporting split for a project that size is disproportionate. State this explicitly when scope doesn't warrant separation, rather than flagging every absent layer as a gap.
- Config's *content* (hardcoded values, inline env-switch logic) is `rrd-detect-config`'s finding, not this detector's — this detector only cares whether config lives in its own structural layer at all, not what's inside it. Don't duplicate a `rrd-detect-config` finding here; cross-reference it instead if both are relevant to the same file.
- A layer that exists but isn't *maximally* separated (e.g. `pages/components/` nested under `pages/` rather than as a sibling directory) is a matter of taste, not a finding, unless it actually causes one of the two concrete cross-layer violations above.

## Calibration Note

An owner may have a documented, deliberate reason for an unconventional structure (e.g. a monorepo convention shared across many projects that this project correctly follows even though it looks unusual in isolation). Ray does not carry cross-session calibration memory — treat any owner statement of "this is our monorepo's established convention" as in-session context for that run, not as something to persist automatically.
