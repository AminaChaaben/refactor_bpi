<!-- Powered by BMAD-CORE™ -->

# Detect Logging

---

## Overview

Finds diagnosability gaps — catch blocks, external-call sites, and failure-capable loop bodies with no logging signal, or that log but drop the original exception — with file:line precision and a concrete logging-statement proposal. Unlike the other detectors, this axis isn't about test-suite reliability or code maintainability directly: it exists so that **Category 1's log-driven analysis** (`rrd-analyze-test-reliability`) has a real signal to point at later, instead of a stack trace and a shrug. Fixing a logging gap today is what turns tomorrow's flaky-test investigation from a guess into a receipt.

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
