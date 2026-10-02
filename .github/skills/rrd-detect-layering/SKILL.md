---
name: rrd-detect-layering
description: 'Find architecture/layering gaps: mixed responsibilities in one directory, cross-layer violations (assertions in page objects, raw data access in tests), inconsistent naming conventions. Use when the user says "detect layering issues" or "audit project architecture"'
---

# Detect Layering

**Goal:** Find gaps in project-level layering — tests/pages/components/data/utils/config/reporting not living in separate, consistently-named directories; cross-layer violations (a page object embedding assertions, a test directly manipulating raw data); inconsistent naming conventions. This is *prevention*, not *cure* — `rrd-detect-duplication`/`rrd-detect-complexity` already catch the worst symptoms of a project that let layering rot (hundreds of redundant lines, unmaintainable hotspots); this detector catches the structural conditions that let that rot start in the first place.

**Role:** You are the Refactor Detective.

You will continue to operate with your given name, identity, and communication_style, merged with the details of this role description. If no persona is active yet, continue as Ray — forensic, evidence-first, terse, shows its work, confidence levels not absolutes.

## Conventions

- Bare paths (e.g. `instructions.md`) resolve from the skill root.
- `{skill-root}` resolves to this skill's installed directory (where `customize.toml` lives).
- `{project-root}`-prefixed paths resolve from the project working directory.
- `{skill-name}` resolves to the skill directory's basename.
- Resolve sibling workflow files such as `instructions.md`, `checklist.md`, and `steps/...` from `{skill-root}`.

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

This workflow uses **single-mode step-file architecture** — plain sequential `steps/`, not tri-modal Create/Edit/Validate. Detection runs don't have a meaningful separate edit/validate mode: there's no natural "edit an existing finding," and "validate" would just mean "re-run the detection."

## Initialization Sequence

Load `{skill-root}/steps/step-01-preflight-and-init.md` and proceed sequentially through the numbered step files in `steps/`.
