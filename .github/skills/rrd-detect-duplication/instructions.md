<!-- Powered by BMAD-CORE™ -->

# Detect Duplication

---

## Overview

Finds structurally similar or near-duplicate functions, classes, or test blocks — with a similarity score and a factor-out proposal. The lowest-drama detector of the four, but a real reliability cost: duplicated setup logic drifts out of sync and produces false-fails that look unrelated. This root-cause family accounted for 18% of false-fails in the reference engagement.

This workflow applies the shared Investigation Contract, carried locally in this skill's own `./resources/knowledge/evidence-and-diff-discipline.md` (the same fragment Ray's persona references — kept in sync across skills, not a cross-folder pointer): every finding cites evidence, every fix is a diff in the target project's `proposals/`, source is never edited directly.

---

## WORKFLOW ARCHITECTURE

This workflow uses **single-mode step-file architecture**, not the tri-modal Create/Edit/Validate pattern used elsewhere in BMad — a detection run doesn't have a meaningful separate edit/validate mode, so it uses plain sequential steps instead.

---

## INITIALIZATION SEQUENCE

### 1. Configuration Loading

From `workflow.yaml`, resolve: `config_source`, `output_folder`, `rrd_artifacts`, `user_name`, `communication_language`, `document_output_language`, `date`, `similarity_threshold`.

### 2. First Step

Load, read completely, and execute:
`{skill-root}/steps/step-01-preflight-and-init.md`
