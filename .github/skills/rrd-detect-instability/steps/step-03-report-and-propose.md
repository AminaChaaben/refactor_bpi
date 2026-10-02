---
name: 'step-03-report-and-propose'
description: 'Write findings summary and diff proposals'
nextStepFile: null
---

# Step 3: Report and Propose

## SEQUENCE

### 1. Write Findings Summary

Write `{rrd_artifacts}/detect-instability-{target_project}.md` following `./resources/knowledge/detect-report-template.md`'s structure: each finding with file:line, confidence level, and evidence citation.

### 2. Write Diff Proposals

For each finding, write a scoped diff limited to the fragile line(s), to `{target_project_root}/proposals/`:

- **Sleep as the only synchronization**: replace with an explicit wait/poll on the actual condition (e.g. `WebDriverWait`/`FluentWait` on the element's real state, not a fixed duration).
- **Sleep stacked after a real wait call**: the fix is deletion, not a new wait — the preceding wait call already handles synchronization. Don't propose adding wait logic where none is needed.
- **Stable-selector fix**: swap the volatile locator for a stable test hook, limited to the fragile line.
- **Implicit wait mixed with explicit waits**: remove/zero the `implicitlyWait` call, citing the colliding explicit wait(s).
- **Stale `WebElement` reuse**: switch to storing a `By` and re-locating at each point of use instead of holding the `WebElement`.
- **Loader-disappearance as sole success signal**: add the missing positive-result wait; do not remove the negative-signal wait, just stop treating it as sufficient on its own.
- **`elementToBeClickable` as overlay guarantee**: same fix as the general overlay finding, but cite the specific clickable-condition limitation.
- **`ignoring(StaleElementReferenceException)` without re-locating**: rewrite the condition to re-run `findElement`/`findElements` by `By` on each poll.
- **Global retry masking real failures**: narrow the retry scope to the specific known-flaky wait, or remove it — do not propose fixing the underlying wait as part of the same diff unless asked.
- **Window-handle count / unrestored context switch**: swap the count comparison for a set difference; wrap the context switch with a `finally` that restores the prior context/handle.

### 3. Summarize to Owner

Report finding count, confidence breakdown, and output/proposal paths, in `{communication_language}`, in Ray's voice.

Workflow complete.
