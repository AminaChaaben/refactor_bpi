---
name: 'step-02-investigate'
description: 'Find data lifecycle gaps and correlate collisions across runs'
nextStepFile: '{skill-root}/steps/step-03-report-and-propose.md'
---

# Step 2: Investigate

## SEQUENCE

### 1. Structural Search

Use `Grep` for hardcoded credentials/URLs/IDs; `code-graph query by-decorator` (setup/teardown annotations and fixtures) plus `callees` for fixtures/factories creating records without matching cleanup; and `code-graph query symbol` on tests that look like they assume pre-existing data rather than creating their own.

### 2. Cross-Run Correlation (If Logs Available)

If execution logs are available, read them directly (`Read`/`Grep`, matching the log's real format) and correlate for the same record ID touched by concurrent or sequential tests — a real collision, not just a static suspicion (the graph holds no execution history; see `detect-data-issues.md`'s Tool Note).

### 3. Continue

Load `./step-03-report-and-propose.md`.
