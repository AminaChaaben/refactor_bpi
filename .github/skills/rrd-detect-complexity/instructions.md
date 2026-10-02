<!-- Powered by BMAD-CORE™ -->

# Detect Complexity

---

## Overview

Finds high-complexity, hard-to-maintain, or hidden-risk functions/methods — via the graph's precomputed complexity properties (cyclomatic, cognitive, loop depth, linear scans in loops, unguarded recursion, deep access chains) — with the metric that flagged it, the confirmed source-level cause, and a concrete simplify/extract proposal. Unlike the other four detectors (tuned for test-suite reliability), this family targets the maintainability of the code itself, so it applies just as well to a non-test codebase as to a test suite.

This workflow applies the shared Investigation Contract, carried locally in this skill's own `./resources/knowledge/evidence-and-diff-discipline.md` (the same fragment Ray's persona references — kept in sync across skills, not a cross-folder pointer): every finding cites evidence, every fix is a diff in the target project's `proposals/`, source is never edited directly.

---

## WORKFLOW ARCHITECTURE

This workflow uses **single-mode step-file architecture**, not the tri-modal Create/Edit/Validate pattern used elsewhere in BMad — a detection run doesn't have a meaningful separate edit/validate mode, so it uses plain sequential steps instead.

---

## INITIALIZATION SEQUENCE

### 1. Configuration Loading

From `workflow.yaml`, resolve: `config_source`, `output_folder`, `rrd_artifacts`, `user_name`, `communication_language`, `document_output_language`, `date`, `complexity_thresholds`.

### 2. First Step

Load, read completely, and execute:
`{skill-root}/steps/step-01-preflight-and-init.md`
