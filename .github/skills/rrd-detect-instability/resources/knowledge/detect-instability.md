# Detect Instability Heuristics

## What Success Looks Like

The owner learns exactly which tests are structurally fragile — brittle locators, fixed sleeps/timeouts instead of real waits, unhandled overlays/iframes/alerts — with file:line precision and a suggested stable replacement. Where real execution evidence exists, findings are corroborated by actual failure/rerun logs, not just static pattern-matching, which sharpens confidence and cuts noise. This root-cause family accounted for 18% of false-fails in the reference engagement.

## Approach

Use `Grep` to find locator and wait patterns known to be fragile (then `code-graph query file-symbols` to attribute each hit to its method): selectors keyed to volatile attributes (auto-generated IDs, positional/index-based selectors, text that changes with content) instead of stable test hooks; hardcoded sleeps (see below); interactions that don't account for overlays, iframes, or native dialogs that can intercept focus.

### Hardcoded Sleeps: Two Distinct Patterns, Both Findings

Don't search for a vague notion of "sleep instead of a wait" — that framing causes real misses. Search for these concrete patterns directly:

- Java: `Thread.sleep(`, `TimeUnit.\w+.sleep(`
- Python: `time.sleep(`
- JS/TS (Playwright/Selenium-JS): `page.waitForTimeout(`, a bare `setTimeout(fn, ms)` used to pace a test step
- Any Selenium `Thread.sleep(` embedded directly in a test/page-object method (as opposed to a configured `WebDriverWait`/`FluentWait`)

A hardcoded sleep is a finding in **both** of these shapes — do not treat the presence of a real wait call earlier in the same method as clearing the method of risk:

1. **Sleep used as the only synchronization** (no real wait anywhere in the method) — the classic case, fix is to replace it with an explicit wait/poll on the actual condition being waited for.
2. **Sleep stacked after a real wait call** (e.g. `waitDisplayElement(e); Thread.sleep(2000);` before an assertion) — a "just in case" pad someone added, usually after debugging a one-off flake, that the method's own explicit wait already makes unnecessary. This is just as much a finding as case 1, and the fix is simpler: delete the sleep, don't add a new wait — the synchronization the sleep is padding for is already handled by the preceding wait call. **Do not skip this case because a wait call is already present in the method** — that's exactly the miss this fragment exists to prevent.

Either way, cite the concrete line, name which of the two shapes it is, and don't conflate "has a wait call" with "has no hardcoded-sleep problem."

### Implicit Wait Mixed With Explicit Waits

`driver.manage().timeouts().implicitlyWait(...)` set to a non-zero duration anywhere in the codebase, combined with `WebDriverWait`/`FluentWait` used elsewhere — the two polling mechanisms stack, producing unpredictable compounded timeouts (a documented, well-known Selenium anti-pattern, not a hypothetical one). Search for any non-zero `implicitlyWait` call, then check whether explicit waits also exist in the same driver's scope. Finding: flag the implicit wait itself for removal (set to zero / rely on explicit waits only), citing the specific explicit-wait call(s) it's colliding with.

### Stale `WebElement` Reused After Re-render

An element reference (`WebElement` stored in a variable/field) captured once and reused across an action that can replace the DOM (navigation, re-render, AJAX refresh), instead of re-locating via a stored `By` each time. This is the direct root cause of `StaleElementReferenceException`. Search for `WebElement` fields/variables held across multiple method calls or reused after a page-mutating action, rather than a `By` locator re-resolved on each access. Finding: propose storing the `By` and re-locating (`driver.findElement(by)`) at each point of use instead of holding the `WebElement` itself.

### Loader Disappearance as the Sole Success Signal

`invisibilityOfElementLocated` (or equivalent "wait for spinner/overlay to disappear") used as the *only* wait before asserting success, with no positive-result wait following it. This can spuriously succeed if the check runs before the loader even mounts — the loader was never present, not gone. Search for `invisibilityOfElementLocated`/equivalent negative-signal waits not immediately followed by a positive wait (`visibilityOfElementLocated`/`elementToBeClickable`/`textToBePresentInElementLocated` on the actual expected result). Finding: propose adding the missing positive-result wait; the negative-signal wait alone is not evidence the operation succeeded.

### `elementToBeClickable` Treated as an Overlay/Animation Guarantee

`elementToBeClickable` only confirms the element is displayed, enabled, and not `aria-disabled` — it does not guarantee an overlay isn't intercepting clicks, that animation/transition has finished, or that the element's geometry is stable. Flag interactions that rely on `elementToBeClickable` alone immediately before a click in a UI known to have overlays, transitions, or animated modals (cross-reference against the existing "overlays/iframes/dialogs" check). Finding: this is a variant of the existing overlay finding, not a new category — cite `elementToBeClickable`'s specific limitation rather than a generic "overlay not handled" note.

### `ignoring(StaleElementReferenceException.class)` Without Re-locating Inside the Condition

A `FluentWait.ignoring(StaleElementReferenceException.class)` whose wrapped condition does not itself re-run `findElement`/`findElements` on each poll (e.g. the condition closes over an already-captured stale `WebElement` instead of a `By`) — this looks like a stale-element fix but just repeats the same failing lookup until timeout, since ignoring the exception doesn't cause a fresh lookup on its own. Search for `.ignoring(StaleElementReferenceException` and inspect whether the condition lambda calls `findElement`/`findElements` internally. Finding: propose rewriting the condition to re-locate the element by `By` on each poll, not just swallowing the exception.

### Global Retry Wrapping Non-Idempotent Actions

A blanket retry/rerun wrapper applied around whole test methods, scenarios, or non-idempotent actions (e.g. a submit/create call retried on failure) rather than around the specific flaky wait/assertion. This masks real defects (the retry hides a genuine failure) and can double-execute a non-idempotent action. Search for retry annotations/wrappers (`@Retry`, custom retry-loop helpers, TestNG/JUnit rerun listeners) applied at the test-method or scenario level rather than scoped to a specific known-flaky wait. Finding: flag as a false-negative risk — cite what real failure the retry could be silently absorbing, and propose narrowing the retry scope or removing it in favor of fixing the underlying wait.

### Window/Tab Handle Count Instead of Set-Difference, and Unrestored Context Switches

(1) New-window/tab detection that compares `driver.getWindowHandles().size()` against a previous count instead of taking the set difference — a race condition if handles close/open in an order the count can't distinguish. (2) A `driver.switchTo().frame(...)`/`.window(...)` call with no corresponding `switchTo().defaultContent()`/original-handle restore in a `finally` block, leaking context into subsequent steps. Search for `getWindowHandles()` used with `.size()` comparisons, and for `switchTo(` calls not paired with a restore in a `finally`. Finding: (1) propose comparing handle *sets* (new handles = current set minus previous set) rather than counts; (2) propose wrapping the context switch with a `finally` that restores the prior context.

If the owner has provided execution logs, correlate real failures/reruns against the static findings — a selector flagged as fragile that also shows up in actual flaky reruns is high confidence; one that never fails in practice is lower priority even if structurally risky. Say so explicitly in the finding's confidence level.

**Tool note:** the code graph holds no execution history, so this correlation is not a graph query. Read the actual log files directly (`Read`/`Grep`, matching the log format — JUnit/Surefire XML, Playwright JSON, Jenkins console) and cross-reference failure/rerun entries against the fragile selectors found above by file/line and test name. This is the same from-scratch log-reading approach `rrd-analyze-test-reliability` already uses, not a new capability — reuse that pattern rather than inventing a different one.

Propose the dynamic-wait or stable-selector fix as a diff, scoped to the specific fragile line, not a rewrite of the surrounding test.

## Calibration Note

Some fragility patterns may be accepted risk for a given project (e.g. a third-party widget with no stable hooks available). Ray does not carry cross-session calibration memory — treat any owner statement of "this is known and accepted" as in-session context for that run, not as something to persist automatically.
