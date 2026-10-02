<!-- Powered by BMAD-CORE™ -->

# Detect Locators

---

## Overview

Finds gaps in locator *strategy* — duplicated selector definitions across page objects, no consistent priority tiering, and no centralized element repository. This is deliberately narrower than `rrd-detect-instability`'s fragile-selector detection: instability asks "is this individual selector brittle" (auto-generated IDs, absolute XPath, text that changes with content); this detector asks "does the project have a consistent, centralized strategy for locators at all," which is a project-structure question, not a per-selector one. The two overlap at the edges — a duplicated fragile selector is both problems at once — so this workflow consumes `rrd-detect-instability`'s findings as input where available rather than re-running fragility detection from scratch.

This workflow applies the shared Investigation Contract, carried locally in this skill's own `./resources/knowledge/evidence-and-diff-discipline.md` (the same fragment Ray's persona references — kept in sync across skills, not a cross-folder pointer): every finding cites evidence, every fix is a diff in the target project's `proposals/`, source is never edited directly.

---

## WORKFLOW ARCHITECTURE

This workflow uses **single-mode step-file architecture**, not the tri-modal Create/Edit/Validate pattern used elsewhere in BMad — a detection run doesn't have a meaningful separate edit/validate mode, so it uses plain sequential steps instead.

---

## INITIALIZATION SEQUENCE

### 1. Configuration Loading

From `workflow.yaml`, resolve: `config_source`, `output_folder`, `rrd_artifacts`, `user_name`, `communication_language`, `document_output_language`, `date`.

### 2. First Step

Load, read completely, and execute:
`{skill-root}/steps/step-01-preflight-and-init.md`
