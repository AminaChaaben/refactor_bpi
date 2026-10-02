<!-- Powered by BMAD-CORE™ -->

# Detect Config

---

## Overview

Finds hardcoded environment/config leaks — URLs, credentials, API keys, timeout literals, inline environment-switch logic, and unsafe parallel-execution settings — that keep a test suite from being safely portable across DEV/QA/REC/PROD and safely parallelizable. This axis had zero dedicated detector coverage before this skill: config leaks are structurally cheap to find and usually a one-line fix, making this a fast-win detector.

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
