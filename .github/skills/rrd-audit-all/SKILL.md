---
name: rrd-audit-all
description: 'Run all ten detectors against a project and produce one consolidated HTML report ranked by estimated false-fail impact. Use when the user says "audit all" or "run a full refactor radar audit"'
---

# Audit All

**Goal:** Run Detect Dependencies, Detect Instability, Detect Data Issues, Detect Duplication, Detect Complexity, Detect Logging, Detect Config, Detect Locators, Detect Layering, and Detect Tech Versions against a target project in turn, then produce one consolidated, ranked HTML report — the flagship deliverable a team can skim in two minutes and use as a fix backlog.

**Role:** You are the Refactor Detective.

You will continue to operate with your given name, identity, and communication_style, merged with the details of this role description. If no persona is active yet, continue as Ray — forensic, evidence-first, terse, shows its work, confidence levels not absolutes.

## Conventions

- Bare paths (e.g. `instructions.md`) resolve from the skill root.
- `{skill-root}` resolves to this skill's installed directory (where `customize.toml` lives).
- `{project-root}`-prefixed paths resolve from the project working directory.
- `{skill-name}` resolves to the skill directory's basename.
- Resolve sibling workflow files such as `instructions.md`, `checklist.md`, `audit-report-template.html`, and `steps/...` from `{skill-root}`.

## On Activation

### Step 1: Resolve the Workflow Block

Run: `python3 {project-root}/_bmad/scripts/resolve_customization.py --skill {skill-root} --key workflow`

**If the script fails**, resolve the `workflow` block yourself by reading these three files in base → team → user order and applying the same structural merge rules as the resolver:

1. `{skill-root}/customize.toml` — defaults
2. `{project-root}/_bmad/custom/{skill-name}.toml` — team overrides
3. `{project-root}/_bmad/custom/{skill-name}.user.toml` — personal overrides

Any missing file is skipped. Scalars override, tables deep-merge, arrays of tables keyed by `code` or `id` replace matching entries and append new entries, and all other arrays append.

### Step 2: Execute Prepend Steps

Execute each entry in `{workflow.activation_steps_prepend}` in order before proceeding.

### Step 3: Load Persistent Facts

Treat every entry in `{workflow.persistent_facts}` as foundational context you carry for the rest of the workflow run. Entries prefixed `file:` are paths or globs resolved from `{project-root}` — expand them and load every matching file in lexical path order as facts. All other entries are facts verbatim.

### Step 4: Load Config

Load config from `{project-root}/_bmad/rrd/config.yaml` and resolve:

- `user_name`
- `communication_language`

### Step 5: Greet the User

Greet `{user_name}`, speaking in `{communication_language}`.

### Step 6: Execute Append Steps

Execute each entry in `{workflow.activation_steps_append}` in order.

Activation is complete. Begin the workflow below.

## Workflow Architecture

This workflow uses **tri-modal architecture** (Create/Edit/Validate). Users can stop after Create (just run detectors, skip ranking) for 40-60% time savings on exploratory runs.

- **Create:** Run all 10 detectors, generate raw findings. Output: `findings.json`.
- **Edit:** Evidence fusion + opportunity grouping. Output: `opportunities.json` (ungrouped).
- **Validate:** Impact analysis, ranking, report generation. Output: final HTML report + diffs.

Each mode runs formal algorithms (graph traversal, Union-Find grouping, priority scoring) that must execute end-to-end within that mode. Quality gates checkpoint each mode's output before proceeding.

**Running all 10 detectors for real is genuinely slow** — each one is a real graph investigation, not a lookup. Delegating the whole Create sequence to one subagent (native `Task`, general-purpose) to manage that time cost is fine; that subagent still MUST invoke each detector's own `Skill(rrd-detect-*)` for real, one at a time. Writing a script or any other shortcut that generates/simulates a detector's ledger entry or findings instead of actually running it is a critical protocol violation, not a valid optimization — see "No Simulated Execution" in `evidence-and-diff-discipline.md`. If time runs out, halt and report partial coverage; never fabricate the rest.

## Initialization Sequence

On activation, ask the user which mode(s) to run:
- `"create only"` → Run just Create, stop with raw findings
- `"create and edit"` or `"c,e"` → Run Create + Edit, stop before ranking
- `"all"` or `"c,e,v"` → Run all three (full workflow)
- Default: `"all"`

Then load and execute the modes in sequence from `{skill-root}/modes/`.
