<!-- Powered by BMAD-CORE™ -->

# Audit All

---

## Overview

Runs Detect Dependencies, Detect Instability, Detect Data Issues, Detect Duplication, Detect Complexity, and Detect Logging against the target project in turn, pools every finding, ranks the pooled list by estimated false-fail impact, and produces one self-contained ranked HTML report grouped by root-cause family — the flagship deliverable a team can skim in two minutes and use as a fix backlog.

This workflow applies the shared Investigation Contract, carried locally in this skill's own `./resources/knowledge/evidence-and-diff-discipline.md` (the same fragment Ray's persona references — kept in sync across skills, not a cross-folder pointer): every finding cites evidence, every fix is a diff in the target project's `proposals/`, source is never edited directly. Audit All changes the packaging of findings, not the evidence discipline.

---

## WORKFLOW ARCHITECTURE

This workflow uses **tri-modal Create/Edit/Validate architecture** (matching `SKILL.md` and `checklist.md` — this file previously described a stale single-mode/`steps/` shape that never existed on disk for this skill; corrected 2026-09-01). There is no `steps/` folder here — sequencing lives in `{skill-root}/modes/create.md`, `modes/edit.md`, `modes/validate.md`.

---

## INITIALIZATION SEQUENCE

### 1. Configuration Loading

From `workflow.yaml`, resolve: `config_source`, `output_folder`, `rrd_artifacts`, `user_name`, `communication_language`, `document_output_language`, `date`, `template`.

### 2. First Step

Follow `SKILL.md`'s own "On Activation" / "Initialization Sequence" exactly: ask the user (or honor a pre-answered choice already in the initial message) which mode(s) to run, then load and execute `{skill-root}/modes/create.md`, `modes/edit.md`, and `modes/validate.md` in sequence as chosen. Do not look for or invent a `steps/step-01-*.md` file — it does not exist for this skill.

### 3. No Delegation Shortcuts

Running all 10 detectors for real is slow (each is a genuine graph investigation, not a lookup). That cost is real and expected — it is never a reason to fabricate. See "No Simulated Execution" in `./resources/knowledge/evidence-and-diff-discipline.md`, binding on this workflow like every other rule in that file.
