---
name: 'create'
description: 'Scan logs, parse runs, aggregate per-test history. Output: raw classification data (user can stop here).'
nextMode: 'edit'
---

# Create: Scan Logs & Parse Runs

## MODE GOAL

Turn every log source into normalized TestRun records, aggregate into per-test history spanning all runs, and produce raw classification data that the user can review before committing to full analysis.

## SEQUENCE

### 1. Resolve Target Project

Run `code-graph status`. If it reports `indexed: false` or `stale: true`, run `code-graph index --wait 600` now (read-only, no source touched): the Edit mode's false-positive/false-negative explanation depends on the graph. Halt if indexing fails, and say why.

### 2. Resolve Log Sources and Format

Ask the owner for the path(s) to their execution logs, if not already provided. Identify the format:

- A directory of `TEST-*.xml` files or a `testng-results.xml` → JUnit/Surefire/TestNG XML (per run, expect one such file/set per run)
- A `test-results.json` (or similarly-named) file → Playwright JSON report
- Plain text file(s) from a CI system → Jenkins console text (best-effort, lower confidence — say so to the owner now)
- Anything else → ask the owner to describe the format, or point at one example file to inspect before committing to a parsing approach

### 3. Confirm Minimum Run Count

Count how many separate runs are represented across the provided log sources.

- If 2 or more already exist: continue to step 4.
- If fewer exist (including zero): suggest running `rrd-establish-execution-baseline` to generate runs, or ask the owner to supply existing logs.
- Only skip log generation and proceed with what exists if the owner explicitly says not to generate more — in which case every finding in the final report must say so and confidence is capped at low.

### 4. Load Knowledge Fragments

Consult `./resources/rrd-index.csv`, then load:

- `./resources/knowledge/evidence-and-diff-discipline.md`
- `./resources/knowledge/analyze-test-reliability.md`

### 5. Parse Each Source with the Bundled Script

Run `{skill-root}/scripts/parse_test_results.py` against the log source(s) rather than hand-writing a one-off parser — real use across multiple projects found hand-written inline parsing (especially XML, and especially through shell quoting) is a real, recurring source of avoidable bugs. The script auto-detects JUnit XML, TestNG XML (a distinct structure — nested `test-method` under `class` under `test` under `suite`, with the parent `<test name="...">` often carrying dimension info like a browser tag), and Playwright JSON, and normalizes all of them into the same `TestRun` record shape:

```bash
python "{skill-root}/scripts/parse_test_results.py" {log_sources_dir_or_files} > /tmp/parsed.json
```

For Jenkins console text (no bundled parser — too free-form to generalize): grep for the embedded test-runner's own summary/per-test lines (Surefire's `Tests run: X, Failures: Y...` per class, or Playwright list-reporter `✓`/`✘` lines) rather than trying to parse arbitrary console prose. Mark every record sourced this way as lower confidence.

### 6. Assign Run IDs

The script derives `run_id` from each source filename by default. Use a stable identifier either way (filename, timestamp, build number if visible in the log) — this is what Edit mode's consistency check groups by.

### 7. Discovery Gap Check — Unconditional, Even for Owner-Supplied Logs

Before aggregating, cross-check the parsed test count against the real number of `@Test`-annotated methods in source (`code-graph query by-decorator --param decorator=Test` or `Grep`, independent of what the logs claim) — **do this even if the owner supplied their own logs and no baseline-generation step ran**. The same naming-convention discovery gap that skill checks for when generating logs can just as easily already be baked into logs the owner brought themselves. If the counts don't match, say so before proceeding — don't silently classify a partial, gapped data set as if it were complete.

### 8. Aggregate Per Test

Group all `TestRun` records by `test_id` into a history list, sorted by run order if determinable. Note the total run count actually available per test — not every test necessarily appears in every run (e.g. a test added mid-way, one that only runs under a certain tag/profile, or one excluded via an environment dimension).

### 9. Report Parsing Coverage

Before proceeding, state how many runs were parsed, how many distinct tests were found (and how that compares to the real `@Test` count), and flag any log source that failed to parse cleanly (don't silently drop it — say what went wrong).

### 10. Output: Raw Classification Data

Write `{project-root}/.refactor-radar-work/test-runs-parsed-{target_project}.json` containing:
- All `TestRun[]` records
- Per-test histories and run counts
- Parsing coverage summary

**User can stop here.** Remaining modes (Edit, Validate) are optional. If the user stops, they have the raw data to work with.

### 11. Continue to Edit (Optional)

If the user confirms, proceed to the Edit mode. Otherwise, end.
