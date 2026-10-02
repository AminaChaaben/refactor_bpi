<!-- Powered by BMAD-CORE™ -->

# Detect Layering

---

## Overview

Finds project-level layering gaps — tests/pages/components/data/utils/config/reporting not living in separate, consistently-named directories; cross-layer violations (a page object embedding assertions, a test directly manipulating raw data); inconsistent naming conventions within a layer. This is deliberately positioned as *prevention*: `rrd-detect-duplication` and `rrd-detect-complexity` already catch the worst downstream symptoms of layering rot (hundreds of redundant lines, unmaintainable hotspots) — this detector catches the structural conditions that let that rot start, which is why it's a lower-priority, complementary pass rather than a replacement for either.

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
