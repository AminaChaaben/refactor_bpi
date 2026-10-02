<!-- Powered by BMAD-CORE™ -->

# Analyze Test Reliability

---

## Overview

Given execution logs from multiple runs of a test suite (JUnit/Surefire XML, Playwright JSON report, Jenkins console text, or any format yielding parseable per-test outcomes), classifies each test as a real failure, a false positive (flaky), a false negative (passes but doesn't really verify anything), or healthy. Reuses the existing Detect Instability/Dependencies/Data Issues heuristics as the explanation layer for false positives, once execution history says where to look.

This workflow applies the shared Investigation Contract, carried locally in this skill's own `./resources/knowledge/evidence-and-diff-discipline.md`. `./resources/knowledge/analyze-test-reliability.md` carries the log-normalization approach, the multi-run consistency classification, and the false-negative ("tests that lie") detection heuristics specific to this workflow — including the note that the code graph does **not** ingest test-run logs; this workflow's parsing is a from-scratch capability built on `Read`/`Grep`/`Bash`.

---

## WORKFLOW ARCHITECTURE

This workflow uses **single-mode step-file architecture** — plain sequential steps, no separate edit/validate mode.

---

## INITIALIZATION SEQUENCE

### 1. Configuration Loading

From `workflow.yaml`, resolve: `config_source`, `output_folder`, `rrd_artifacts`, `user_name`, `communication_language`, `document_output_language`, `date`, `log_sources`, `log_format`, `min_runs_required`.

### 2. First Step

Load, read completely, and execute:
`{skill-root}/steps/step-01-preflight-and-init.md`
