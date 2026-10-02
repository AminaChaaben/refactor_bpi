---
name: 'step-02-investigate'
description: 'Find fragile patterns and correlate against logs if available'
nextStepFile: '{skill-root}/steps/step-03-report-and-propose.md'
---

# Step 2: Investigate

## SEQUENCE

### 1. Static Pattern Search

Use `Grep` (scoped with `glob`/`type` and `path`) to find:

- Selectors keyed to volatile attributes (auto-generated IDs, positional/index selectors, content-derived text)
- Hardcoded sleeps — search these concrete patterns directly, don't rely on a vague "sleep instead of a wait" search: `Thread.sleep(`, `TimeUnit.\w+.sleep(` (Java), `time.sleep(` (Python), `page.waitForTimeout(` / bare `setTimeout(fn, ms)` (JS/TS). Flag **every** match as a finding — including a sleep that appears *after* a real wait call earlier in the same method (e.g. `waitDisplayElement(e); Thread.sleep(2000);`). Do not clear a method of this finding just because it also contains a legitimate wait call; per `detect-instability.md`'s two-shape breakdown, a sleep stacked after a real wait is its own finding (fix: delete the sleep) distinct from a sleep used as the only synchronization (fix: replace with a real wait)
- Interactions that don't account for overlays, iframes, or native dialogs
- Non-zero `implicitlyWait(...)` combined with `WebDriverWait`/`FluentWait` used elsewhere on the same driver — the compounded-timeout anti-pattern
- `WebElement` references stored/reused across a page-mutating action instead of re-locating via `By` — root cause of `StaleElementReferenceException`
- `invisibilityOfElementLocated` (or equivalent loader-disappearance wait) used as the sole success signal, with no positive-result wait following it
- `elementToBeClickable` relied on alone immediately before a click in a UI with known overlays/animations/modals — it doesn't guarantee any of those are clear
- `.ignoring(StaleElementReferenceException.class)` on a `FluentWait` whose condition doesn't itself re-run `findElement`/`findElements` each poll
- Blanket retry/rerun wrappers (`@Retry`, custom retry loops, rerun listeners) applied at the test-method/scenario level around non-idempotent actions, rather than scoped to one known-flaky wait
- `driver.getWindowHandles().size()` compared against a previous count instead of a set difference; `switchTo().frame(...)`/`.window(...)` with no restore in a `finally`

### 2. Log Correlation (If Available)

If the owner provided execution logs, read them directly (`Read`/`Grep`, matching the log's real format) and cross-check each static candidate against real failure/rerun evidence by file/line and test name (the graph holds no execution history; see `detect-instability.md`'s Tool Note). Mark corroborated findings high confidence; uncorroborated but structurally risky findings lower priority — say so explicitly.

### 3. Continue

Load `./step-03-report-and-propose.md`.
