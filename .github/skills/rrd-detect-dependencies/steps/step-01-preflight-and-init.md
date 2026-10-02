---
name: 'step-01-preflight-and-init'
description: 'Resolve target project, verify it is indexed, load knowledge fragments'
nextStepFile: '{skill-root}/steps/step-02-investigate.md'
---

# Step 1: Preflight & Init

## STEP GOAL

Resolve which project to investigate, confirm its code graph is indexed and current, and load the knowledge fragments this detector depends on.

## MANDATORY EXECUTION RULES

- 📖 Read the entire step file before acting
- ✅ Speak in `{communication_language}`
- 🚫 Halt if the target project is not indexed

## SEQUENCE

### 1. Resolve Target Project

- The target is the project's repository in the sandbox (`code-graph` finds it; `--repo <path>` overrides).
- Run `code-graph status`.
- **If `indexed` is false or `stale` is true:** run `code-graph index --wait 600` and check `status` again. If indexing fails, halt and say why. Do not guess at ungraphed code.
- Store the resolved project as `{target_project}`.

### 2. Load Knowledge Fragments

Consult `./resources/rrd-index.csv`, then load in order:

- `./resources/knowledge/evidence-and-diff-discipline.md` (binding Investigation Contract — cite evidence, diff-only, resolve target first)
- `./resources/knowledge/detect-dependencies.md` (coupling heuristics and the reference example)

### 3. Report and Continue

Tell the owner: target project resolved, indexed, ready to investigate. Then load `./step-02-investigate.md`.
