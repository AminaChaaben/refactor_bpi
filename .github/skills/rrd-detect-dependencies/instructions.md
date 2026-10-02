<!-- Powered by BMAD-CORE™ -->

# Detect Dependencies

---

## Overview

Finds test-to-test coupling and cascade risk — shared mutable fixtures, shared globals, order-dependence, shared live app/data state — before that coupling causes a cascade of unrelated false-fails. This is the single highest-impact detector in the Refactor Radar module: in the reference engagement, this root-cause family accounted for 47% of all false-fails, so this workflow errs toward thoroughness.

This workflow applies the shared Investigation Contract, carried locally in this skill's own `./resources/knowledge/evidence-and-diff-discipline.md` (the same fragment Ray's persona references — kept in sync across skills, not a cross-folder pointer): every finding cites evidence, every fix is a diff in the target project's `proposals/`, source is never edited directly.

---

## WORKFLOW ARCHITECTURE

This workflow uses **single-mode step-file architecture**, not the tri-modal Create/Edit/Validate pattern used elsewhere in BMad — a detection run doesn't have a meaningful separate edit/validate mode, so it uses plain sequential steps instead:

- **Micro-file Design**: Each step is self-contained
- **JIT Loading**: Only the current step file is in memory
- **Sequential Enforcement**: Execute steps in order without skipping

---

## INITIALIZATION SEQUENCE

### 1. Configuration Loading

From `workflow.yaml`, resolve: `config_source`, `output_folder`, `rrd_artifacts`, `user_name`, `communication_language`, `document_output_language`, `date`.

### 2. First Step

Load, read completely, and execute:
`{skill-root}/steps/step-01-preflight-and-init.md`
