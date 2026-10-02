---
name: 'step-02-apply-diff'
description: 'Read the full current file(s) each diff touches, apply or skip each with reason'
nextStepFile: '{skill-root}/steps/step-03-run-tests.md'
---

# Step 2: Apply Diff(s)

## STEP GOAL

For each selected proposal, apply it for real — grounded in the actual current file content, not a blind mechanical patch — or skip it with a stated reason if the full file reveals the plan no longer holds.

## SEQUENCE

### 1. Read the Patch's Rationale and Intended End State

Read the `.patch` file's rationale header and diff body to understand what it's trying to achieve and why.

### 2. Read the Real Current File(s) in Full

For every file the diff touches, read the **entire current file** — not an excerpt. This is the critical discipline this workflow exists to enforce: a diff was written against a snapshot at proposal time, and either the file has since changed, or fuller context reveals something the excerpt didn't show (see `apply-and-verify-heuristics.md` for the real example that motivated this rule).

### 3. Decide: Apply or Skip

- If the real file still matches the diff's assumptions and the plan still makes sense: proceed to apply.
- If the file has changed since the proposal was written: re-derive the equivalent edit against current content — do not force a stale patch.
- If reading the full file reveals the plan is no longer a good idea (breaks an intentional structure, the assumption it relied on isn't true, etc.): **skip this diff**, state the specific reason, and move to the next one. Do not force it through because "the diff said so."

### 4. Apply

Make the edit directly (not via blind `git apply`/`patch` on hand-written diff text, which often won't apply mechanically clean). Match the diff's intent, adjusted for the file's real current imports/structure/conventions. Keep changes minimal and consistent with the rest of the file's existing style.

### 5. Run the Target Project's Own Formatter/Linter, If One Exists

Check for a formatting/linting step in the project's own build config (e.g. a Maven formatter plugin, `prettier`, `black`, `gofmt`) and run it now, before testing — catching a style violation here is faster feedback than discovering it at build time.

### 6. Record the Outcome Per Diff

For each proposal processed this step, record: applied / skipped, and if skipped, the specific reason discovered.

### 7. Continue

Load `./step-03-run-tests.md`.
