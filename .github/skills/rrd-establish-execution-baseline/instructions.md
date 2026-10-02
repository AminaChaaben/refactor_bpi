<!-- Powered by BMAD-CORE™ -->

# Establish Execution Baseline

---

## Overview

Turns "no real execution data yet" into `{run_count}` genuine runs of the target's actual test suite. Extracted from `rrd-analyze-test-reliability`'s original Step 1b after real use across three different projects showed this capability — toolchain detection, compile/discovery/environment blocker diagnosis and fixing, multi-run generation — is bigger than one workflow's prerequisite and is independently reusable.

This workflow applies the shared Investigation Contract, carried locally in this skill's own `./resources/knowledge/evidence-and-diff-discipline.md`. `./resources/knowledge/establish-execution-baseline-heuristics.md` carries the diagnostic order, real examples of each blocker type from actual runs, and the manifest format a consuming workflow reads instead of re-diagnosing the same environment.

---

## WORKFLOW ARCHITECTURE

This workflow uses **single-mode step-file architecture** — plain sequential steps, no separate edit/validate mode. It shares the module's one source-touching exception with `rrd-apply-and-verify`.

---

## INITIALIZATION SEQUENCE

### 1. Configuration Loading

From `workflow.yaml`, resolve: `config_source`, `output_folder`, `rrd_artifacts`, `user_name`, `communication_language`, `document_output_language`, `date`, `target_project_root`, `run_count`.

### 2. First Step

Load, read completely, and execute:
`{skill-root}/steps/step-01-preflight-and-init.md`
