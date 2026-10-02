---
name: 'create'
description: 'Run all 10 detectors in sequence, generate raw findings. Output: findings.json (user can stop here).'
nextMode: 'edit'
---

# Create: Run All Detectors

## MODE GOAL

Run all 10 structural detectors (Dependencies, Instability, Data Issues, Duplication, Complexity, Logging, Config, Locators, Layering, Tech Versions) in sequence against the target project and collect their findings into a single raw findings JSON file.

User can stop here and work with raw findings without waiting for opportunity grouping/ranking.

## SEQUENCE

**Never delegate any detector run to a background `Agent` tool call
(`run_in_background: true`) or to `ScheduleWakeup`.** A headless, one-shot dispatch
(`usine.py spawn`) has no live process left for a background sub-agent to run inside once
this turn ends — the parent process exits, and an orphaned background task never produces a
result. "I'll check back later" is a promise this runtime cannot keep here: confirmed live
(2026-09-24) — a background 10-detector sweep spawned this way was silently abandoned
mid-detector, twice in a row, across two separate dispatches, producing zero findings after
real cost was spent. Run every detector directly, synchronously, in this same turn, exactly
as instructed below. The spawn's own timeout (`Talan_Library/Orchestrateur_Lib/spawn/
config.py`'s `timeout_seconds`, 2700s/45min by default) is the real budget for a sequential
run — there is no artificial 600s wall on a turn's own natural duration, only on waiting for
a `run_in_background` sub-task, which is exactly why that pattern must not be used here.

### 1. Preflight & Init (from step-01)

- Run `code-graph status` (and `code-graph index --wait 600` if not indexed or stale); halt if indexing fails
- Load knowledge fragments from `resources/`
- Verify output directory exists
- Check `{rrd_artifacts}/audit-history/` for a ledger from an earlier, interrupted dispatch of
  this same run. If one exists with some entries already `completed`, resume from the first
  `failed`/missing entry rather than re-running detectors already done — say so explicitly
  when reporting coverage in step 4.

### 2. Run All 10 Detectors (from step-02)

Run each detector in sequence via its own skill or step-file. Maintain a **detector run ledger** as you go — one entry per detector, status one of `completed` / `failed` / `skipped`, updated immediately after each detector finishes (or errors out), not reconstructed afterward from memory. Write the ledger to `{rrd_artifacts}/audit-history/` on disk after every single detector, not just at the end — that path is on the project's persistent workspace, so if this dispatch itself gets cut short (rate limit, spawn timeout, kill), the *next* dispatch can read it and resume from the first `failed`/unwritten entry instead of restarting all 10 from zero:

```
1. Detect Dependencies (DD)
2. Detect Instability (DI)
3. Detect Data Issues (DT)
4. Detect Duplication (DU)
5. Detect Complexity (DC)
6. Detect Logging (DL)
7. Detect Config (DF)
8. Detect Locators (DO)
9. Detect Layering (DY)
10. Detect Tech Versions (DV)
```

Each detector:
- Queries the codebase graph for its specific finding type
- Produces findings with: id, detector_family, file, line, title, description, evidence, confidence, affected_target, root_cause_signals
- No filtering, no grouping — raw output only

If a detector errors, times out, or returns no result at all (as opposed to a genuine "0 findings" result), mark it `failed` in the ledger with the reason — **do not silently continue as if it produced zero findings.** A detector that never ran and a detector that ran and found nothing are not the same thing, and only the ledger tells them apart.

**A ledger entry may only be marked `completed` if a real `Skill(rrd-detect-*)` invocation for that exact detector actually happened in this session, backed by at least one real `code-graph query` (or `Grep`) call** — never because it seemed faster to infer or template a plausible result. Writing your own orchestrator script, or any other mechanism that produces a ledger entry or findings without that real invocation, is fabrication, not an optimization — forbidden even under time pressure (see "No Simulated Execution" in `evidence-and-diff-discipline.md`).

### 2b. Detector Completion Gate (Hard Stop)

Before pooling anything, check the ledger: all 10 entries must be `completed`.

- **If any entry is `failed` or `skipped`**, halt here. Report to the owner exactly which detector(s) did not complete and why (from the recorded reason), and ask whether to retry just that detector, continue with a partial audit (explicitly labeled as covering N of 10 families), or abort the run entirely. Never proceed to pooling with a gap in the ledger silently absorbed as "0 findings."
- **If running out of time/budget before all 10 are genuinely run**, this is the same case as above — mark the untried detectors `skipped` (not `completed`) and halt with an explicit partial-coverage report. Do not synthesize the remaining entries to reach 10/10.
- **If all 10 are `completed`**, proceed to step 3.
- Carry the ledger forward into the final report (step 7 of Validate mode) so the owner can see, at a glance, that this was a genuine 10/10 run — or exactly which family was skipped/failed and why, if not.

### 3. Pool All Findings

Collect all 10 detectors' outputs (Finding[]) into a single array and write:

```bash
{project-root}/.refactor-radar-work/findings.json
```

Each finding includes its origin detector (detector_family) so Edit mode can trace it back.

### 4. Report Raw Coverage

Before stopping, log:
- Detector run ledger: which of the 10 completed, with any failed/skipped entries and their reasons called out explicitly
- Total findings collected: N across all completed detectors
- Breakdown by detector: DD=X, DI=Y, DT=Z, ... DV=W
- Affected targets (unique files/classes)

**User can stop here.** Remaining modes (Edit, Validate) are optional. If the user stops, they have the raw detector findings to work with — no ranking applied yet, 40-60% faster than full audit.

### 5. Continue to Edit (Optional)

If the user confirms, proceed to the Edit mode. Otherwise, end.
