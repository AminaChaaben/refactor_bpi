---
name: 'step-02-audit-governance'
description: 'Find recurring unwritten-convention patterns and check for repo governance artifacts'
nextStepFile: '{skill-root}/steps/step-03-report-and-propose.md'
---

# Step 2: Audit Governance

## SEQUENCE

### 1. Find Recurring Patterns (the Core Evidence Model)

Read the prior `rrd-audit-all`/detector report(s) located in Step 1. Group findings by gap type across detector families (e.g. "locator-priority inconsistency" from Detect Locators, "hardcoded timeout" from Detect Config, "silent catch" from Detect Logging). For each gap type, count distinct occurrences across distinct files.

**The threshold that matters**: 1-2 occurrences of a gap type is "someone made a mistake, already caught and fixed by the detector that found it" — not a governance finding on its own. **3 or more occurrences of the same gap type across different files** is evidence that no convention currently prevents this class of mistake — that's the governance-layer finding, distinct from (and in addition to) the individual code-level findings the original detector already reported.

### 2. Check Repo Governance Artifacts

Use `Glob` (and `code-graph query tree` for the overall layout) to check for the presence of:

- A contribution guide (`CONTRIBUTING.md` or equivalent)
- `CODEOWNERS`
- Lint/static-analysis config (`.eslintrc*`, `checkstyle.xml`, `.editorconfig`, `pylintrc`, `.rubocop.yml`, etc. — match to the project's actual language, don't check for configs from an unrelated ecosystem)
- Any documented Definition-of-Done for an automated test (search for terms like "definition of done," "test checklist," "PR checklist" in existing docs)
- Any documented convention for locators/waits/logging (a `CONTRIBUTING.md` section, a `docs/conventions.md`, or similar — not just the *code* being consistent, but a *written* rule explaining why)

### 3. Connect the Two

For each recurring pattern found in Step 1, check whether a corresponding written convention already exists (from Step 2) but isn't being followed (a *training/enforcement* gap, different fix) versus no written convention existing at all (a *documentation* gap, the more common case). Report which one it actually is — don't default to "write a doc" if a doc already exists and simply isn't being followed; that finding's fix is enforcement (a lint rule, a PR checklist item), not more documentation.

### 4. Scale to Project Size

A 3-test smoke-suite project failing every governance check here is not itself a finding — recommending a CODEOWNERS file and a formal Definition-of-Done for a tiny, single-maintainer project is disproportionate. Only report governance-artifact absence as a finding when Step 1's recurring-pattern evidence shows the gap is actually being felt (multiple people/files exhibiting the same unwritten-convention violation), or when the project's own scale (multiple contributors, `CODEOWNERS`-worthy directory structure, existing CI) suggests the artifact's absence is already costing something.

### 5. Continue

Load `./step-03-report-and-propose.md`.
