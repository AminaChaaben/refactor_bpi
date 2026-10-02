# Standards Audit Heuristics

## What Success Looks Like

The owner learns exactly which unwritten conventions are being silently violated (evidenced by a recurring pattern across other detectors' findings, not guessed at), and which repo-governance artifacts are actually absent-and-costing-something versus absent-and-fine-for-this-project's-size. This is the only Refactor Radar workflow whose primary evidence source is *other findings*, not fresh code scanning — it exists to answer "should this class of mistake get a written rule, or was it just one mistake."

## The Core Distinction: One Mistake vs. No Convention

A single `rrd-detect-locators` finding about one inconsistent selector is a code-level finding, already reported and already fixed by that detector's own diff. It does not, by itself, justify a governance recommendation — everyone makes one mistake sometimes, and a written rule for a one-off doesn't prevent much.

**The same gap type recurring three or more times, across different files, is different** — that's evidence no convention currently exists to prevent this specific class of mistake, and *that absence* is this workflow's actual finding. The threshold (3+) is a default, not a law — an owner may reasonably say a project this small should use a lower bar, or a project this large should use a higher one; state the default and apply it unless told otherwise.

## Approach

### 1. Read the Evidence Base, Don't Re-Scan Code

This workflow's Step 1 (in `step-01-preflight-and-init.md`) resolves which prior report(s) to read. Read them with the same rigor as any source file — actual finding counts, actual file names, actual gap-type labels — not a paraphrased summary from memory of what those detectors "usually find."

### 2. Group by Gap Type, Count Occurrences

Group the prior run's findings by their gap-type label (not by detector family alone — two different gap types from the same detector are two different pieces of evidence, not one). Count distinct files/locations per gap type. Apply the 3+ threshold from above.

### 3. Governance Artifacts — Presence Check, Then Relevance Check

Checking whether `CONTRIBUTING.md`/`CODEOWNERS`/lint-config/Definition-of-Done exists is a simple presence check (`Glob`). The harder, more important step is **relevance**: absence of any of these in a 3-test smoke-suite maintained by one person is not a finding — recommending a formal `CODEOWNERS` and Definition-of-Done for that project is disproportionate busywork, not help. Absence in a project whose own recurring-pattern evidence (Step 2 above) already shows multiple people/files hitting the same unwritten-convention wall **is** a finding — the absence is demonstrably costing something, not merely theoretically incomplete.

### 4. Documentation Gap vs. Enforcement Gap

Before proposing "write a doc," check whether a doc already exists covering the exact pattern found. If it does and the pattern still recurs, the fix is enforcement (a lint rule, a PR checklist item, a pre-commit hook) — writing a second document restating a rule that's already written and already being ignored doesn't fix anything. Only propose new documentation when none exists yet.

## What NOT to Flag

- A single occurrence of any gap type, no matter how severe the individual finding — that's the originating detector's finding to own, not a governance finding.
- Any governance artifact's absence on a project whose scale doesn't warrant it and whose recurring-pattern evidence doesn't show the gap being felt.
- A convention that's written down and is, in fact, being followed (verified by absence of recurring violations in the evidence base) — don't manufacture a finding to fill out a section; report the convention as present-and-working.

## Calibration Note

Team size, project maturity, and organizational context (a client engagement vs. an internal tool vs. an open-source project with external contributors) all shift what's proportionate here more than for any other Refactor Radar detector — this axis is inherently about process fit, not a universal code-quality bar. Ray does not carry cross-session calibration memory — treat any owner statement about team size/maturity/context as in-session context for that run, not as something to persist automatically.
