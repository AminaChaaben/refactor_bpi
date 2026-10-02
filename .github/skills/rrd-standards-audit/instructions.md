<!-- Powered by BMAD-CORE™ -->

# Standards Audit

---

## Overview

Finds governance gaps — missing/unwritten conventions, no Definition-of-Done, no contribution guide, no lint config, no CODEOWNERS — using a completed `rrd-audit-all` run's findings as the evidence base. This is deliberately the odd one out among Refactor Radar workflows: every other detector scans code directly; this one scans *the pattern of findings other detectors already produced* for evidence that a convention needs to be written down. A single isolated finding from `rrd-detect-locators` doesn't justify a governance recommendation; the same gap type recurring three or more times across the codebase does — that's the difference between "one mistake" and "no convention exists to prevent this class of mistake."

This workflow applies the shared Investigation Contract, carried locally in this skill's own `./resources/knowledge/evidence-and-diff-discipline.md` (the same fragment Ray's persona references — kept in sync across skills, not a cross-folder pointer): every finding cites evidence, every fix is a diff in the target project's `proposals/`, source is never edited directly.

---

## WORKFLOW ARCHITECTURE

This workflow uses **single-mode step-file architecture**, not the tri-modal Create/Edit/Validate pattern used elsewhere in BMad — a governance audit doesn't have a meaningful separate edit/validate mode, so it uses plain sequential steps instead.

---

## INITIALIZATION SEQUENCE

### 1. Configuration Loading

From `workflow.yaml`, resolve: `config_source`, `output_folder`, `rrd_artifacts`, `user_name`, `communication_language`, `document_output_language`, `date`.

### 2. First Step

Load, read completely, and execute:
`{skill-root}/steps/step-01-preflight-and-init.md`
