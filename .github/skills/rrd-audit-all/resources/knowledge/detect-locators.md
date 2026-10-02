# Detect Locators Heuristics

## What Success Looks Like

The owner learns exactly where locator *strategy* breaks down — duplicated selector definitions, no consistent priority tiering, no centralized element repository — with file:line precision and a concrete factor-out or migration fix. This is deliberately distinct from `rrd-detect-instability`'s fragile-selector detection: that detector asks "is this one selector brittle," this one asks "does the project have a coherent strategy for locators at all." A project can have zero individually-fragile selectors and still fail this detector if the same stable selector is copy-pasted into five files instead of centralized once.

## Approach

### 1. Duplicated Locator Literals

The same selector string defined as a `By`/locator constant in more than one file is a factor-out candidate, independent of whether the selector itself is fragile. Use `Grep` to find repeated literals, then confirm each candidate by reading its definition (`Read` around the hit, or `code-graph query variables --param name=<constant>`) before reporting — a coincidentally-identical short selector on two genuinely unrelated elements is a false match, not a real duplicate.

### 2. Locator-Priority Convention

Sample locator definitions across the page-object layer and classify each by tier, from most to least stable:

1. `data-testid`/stable test-specific ID (best)
2. ARIA/role/semantic attribute
3. Visible text content
4. Absolute XPath / positional CSS (worst — breaks on any DOM reshuffle)

The finding here is about *consistency*, not any single selector's tier. A project entirely at tier 4 has a systemic strategy gap (worth recommending a `data-testid` convention be adopted with the front-end team, per the reference engagement's own recommendation). A project with a genuine mix and no visible rule for when each tier applies is a weaker but still real finding — the mix itself, absent any documented convention, means the next selector added will be arbitrary rather than principled.

### 3. Centralized Element Repository

Check whether locators live in one dedicated module/class per page (conventional Page Object Model) or are scattered inline within test/spec files. No page-object layer at all is a stronger, more foundational finding than "page objects exist but aren't internally consistent" — report which situation actually exists rather than assuming the more common one.

### 4. Consume, Don't Duplicate, Detect Instability's Findings

If `rrd-detect-instability` has already run on this target, its fragile-selector findings are input to this workflow, not a separate parallel detection pass to re-run. Where a fragile selector from that pass is also part of a duplication or missing-repository finding here, cite it as corroborating evidence for severity — a selector that is both duplicated *and* independently flagged as fragile is a stronger finding than either fact alone, but it is still one finding, reported once.

## Calibration Note

A small, deliberately minimal test suite may not warrant a full locator-repository migration — recommending one for a 3-test smoke suite is disproportionate. Ray does not carry cross-session calibration memory — treat any owner statement of "this is intentionally minimal" as in-session context for that run, not as something to persist automatically.
