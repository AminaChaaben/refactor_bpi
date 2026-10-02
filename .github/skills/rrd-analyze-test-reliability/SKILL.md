---
name: rrd-analyze-test-reliability
description: 'Analyze execution logs across multiple test runs (JUnit/Surefire XML, Playwright JSON, Jenkins console) to classify false positives (flaky tests) and false negatives (tests that pass without really checking anything). Use when the user says "analyze test reliability", "find flaky tests from logs", or "find false positives/negatives"'
---

# Analyze Test Reliability

**Goal:** Given execution logs from multiple runs of a test suite, classify each test as a real failure, a false positive (flaky — code is fine, the test lied about failing), a false negative (the test passes but doesn't really verify anything), or healthy. Generalizes across stacks — Java/Selenium with JUnit/Surefire XML, TypeScript/Playwright with its own JSON report, Jenkins console logs, or any format that yields a parseable per-test outcome.

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

This workflow uses **tri-modal architecture** (Create/Edit/Validate). It is log-first and multi-run by nature, letting you stop after Create (just scan logs) or after Edit (just classify), before committing to the full validated report.

- **Create:** Scan logs, parse runs, aggregate per-test history. Output: raw classification data.
- **Edit:** Manually review and correct auto-classifications, invoke detectors for structural confirmation. Output: curated findings.
- **Validate:** Verify curated findings, generate final report with diffs, check quality gates. Output: findings summary.

## Initialization Sequence

On activation, ask the user which mode(s) to run. Accept these shortcuts:
- `"create only"` → Run just Create, stop with raw data
- `"create and edit"` or `"c,e"` → Run Create + Edit, stop before validation
- `"all"` or `"c,e,v"` → Run all three (full workflow)
- Default: `"all"`

Then load and execute the modes in sequence from `{skill-root}/modes/`.
