<!-- Powered by BMAD-CORE™ -->

# Apply and Verify

---

## Overview

Applies one or more previously-proposed Refactor Radar diffs to the target project's real source, then runs the project's own test suite and reports genuine pass/fail counts from its native test-report format. This is the one workflow in the module that edits source directly — every other detection/audit workflow only ever proposes. It is never invoked implicitly by another workflow; the owner must ask for it by name.

This workflow applies the shared Investigation Contract, carried locally in this skill's own `./resources/knowledge/evidence-and-diff-discipline.md`, with the explicit exception this workflow itself represents. `./resources/knowledge/apply-and-verify-heuristics.md` carries the safety gate, the full-file-read-before-apply rule, and the test-verification discipline specific to this workflow.

---

## WORKFLOW ARCHITECTURE

This workflow uses **single-mode step-file architecture** — plain sequential steps, no separate edit/validate mode.

---

## INITIALIZATION SEQUENCE

### 1. Configuration Loading

From `workflow.yaml`, resolve: `config_source`, `output_folder`, `rrd_artifacts`, `user_name`, `communication_language`, `document_output_language`, `date`, `target_project_root`, `proposal_selection`.

### 2. First Step

Load, read completely, and execute:
`{skill-root}/steps/step-01-preflight-and-init.md`
