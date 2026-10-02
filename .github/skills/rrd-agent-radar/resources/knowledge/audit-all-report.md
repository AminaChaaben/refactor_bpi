# Audit All Report Format

## What Success Looks Like

The owner gets a single self-contained HTML file that answers "where is this test suite lying to us, and in what order should we fix it?" — every finding from all four detectors, ranked by estimated false-fail impact, grouped by root-cause family (Dependencies, Instability, Data, Duplication), each linking to its own diff proposal. This is the flagship deliverable: a diagnostic sweep an owner can open in a browser, skim in two minutes, and hand to a team as the fix backlog.

## Approach

Run Detect Dependencies, Detect Instability, Detect Data Issues, and Detect Duplication against the target project in turn (confirm it's indexed first, per the Init Responsibility in `evidence-and-diff-discipline.md`). Pool every finding. Estimate false-fail impact per finding — how often and how disruptively this root cause is likely to produce a false failure, given the evidence gathered (e.g. a shared live database touched by a dozen tests outweighs one fragile selector in a rarely-run test) — and rank the consolidated list by that estimate, not by detector order.

Group the ranked list by root-cause family so a reader can jump to the section they care about. Every finding must still carry its evidence citation and link to its diff proposal file, exactly as in the individual detectors — Audit All changes the packaging, not the evidence discipline.

## Report Template

Generate the report as a single self-contained HTML file (inline CSS, no external assets, no build step) written to the target project's `proposals/` directory (or the configured `rrd_artifacts` output location). Use this structure as the starting point, adapting content and styling as the findings warrant:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Refactor Radar — Audit Report — {project name} — {date}</title>
<style>
  body { font-family: -apple-system, Segoe UI, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; background: #fafafa; }
  h1 { font-size: 1.5rem; }
  .meta { color: #666; font-size: 0.9rem; margin-bottom: 2rem; }
  .family { margin-top: 2.5rem; }
  .family h2 { border-bottom: 2px solid #ddd; padding-bottom: 0.4rem; }
  .finding { background: #fff; border: 1px solid #e0e0e0; border-left: 4px solid #999; border-radius: 4px; padding: 1rem 1.25rem; margin: 1rem 0; }
  .finding.high { border-left-color: #c0392b; }
  .finding.medium { border-left-color: #d9822b; }
  .finding.low { border-left-color: #7f8c8d; }
  .finding h3 { margin: 0 0 0.4rem 0; font-size: 1.05rem; }
  .badge { display: inline-block; font-size: 0.75rem; padding: 0.15rem 0.5rem; border-radius: 3px; background: #eee; color: #333; margin-left: 0.5rem; }
  .evidence { font-family: Consolas, monospace; font-size: 0.85rem; background: #f4f4f4; padding: 0.5rem 0.75rem; border-radius: 3px; margin: 0.5rem 0; white-space: pre-wrap; }
  .proposal-link { font-size: 0.9rem; }
  .summary-table { width: 100%; border-collapse: collapse; margin: 1rem 0; }
  .summary-table th, .summary-table td { text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #eee; }
</style>
</head>
<body>
  <h1>Refactor Radar — Audit Report</h1>
  <div class="meta">Project: {project name} &middot; Generated: {date} &middot; Findings: {count}</div>

  <table class="summary-table">
    <tr><th>Rank</th><th>Finding</th><th>Family</th><th>Impact</th></tr>
    <!-- one row per finding, ranked by estimated false-fail impact -->
  </table>

  <div class="family">
    <h2>Dependencies</h2>
    <div class="finding high">
      <h3>{finding title}<span class="badge">confidence: {level}</span></h3>
      <p>{finding description}</p>
      <div class="evidence">{graph query / trace / log citation}</div>
      <p class="proposal-link"><a href="{relative path to proposal diff}">View proposed diff</a></p>
    </div>
    <!-- repeat per finding -->
  </div>

  <!-- repeat <div class="family"> block for Instability, Data, Duplication -->

</body>
</html>
```

Adapt colors, sections, and the summary table to fit however many findings actually exist — an audit with zero findings in a family should say so plainly rather than showing an empty section.
