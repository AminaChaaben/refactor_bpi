---
name: "rrd-setup"
description: Sets up Refactor Radar module in a project. Use when the user requests to 'install rrd module', 'configure Refactor Radar', or 'setup Refactor Radar'.
---

# Module Setup

## Overview

Installs and configures a BMad module into a project. Module identity (name, code, version) comes from `./assets/module.yaml`. Collects user preferences and writes them to three files:

- **`{project-root}/_bmad/config.yaml`** — shared project config: core settings at root (e.g. `output_folder`, `document_output_language`) plus a section per module with metadata and module-specific values. User-only keys (`user_name`, `communication_language`) are **never** written here.
- **`{project-root}/_bmad/config.user.yaml`** — personal settings intended to be gitignored: `user_name`, `communication_language`, and any module variable marked `user_setting: true` in `./assets/module.yaml`. These values live exclusively here.
- **`{project-root}/_bmad/module-help.csv`** — registers module capabilities for the help system.

Both config scripts use an anti-zombie pattern — existing entries for this module are removed before writing fresh ones, so stale values never persist.

`{project-root}` is a **literal token** in config values — never substitute it with an actual path. It signals to the consuming LLM that the value is relative to the project root, not the skill root.

## On Activation

1. Read `./assets/module.yaml` for module metadata and variable definitions (the `code` field is the module identifier)
2. **Check for a current-generation installer**: if `{project-root}/_bmad/config.toml` exists, this project is running a newer BMAD-METHOD installer (TOML-based, installer-managed, one `[modules.<code>]` section per module) that this module's own config (`_bmad/config.yaml`/`config.user.yaml`, YAML-based) was not built against. Inform the user plainly: this module will still install and run correctly on its own dedicated config file (`_bmad/rrd/config.yaml`), but it registers itself in the older unified-YAML convention, not `config.toml` — so it won't appear in the current installer's own module listing or `[modules.rrd]` section. This is a known versioning gap, not a failure; proceed with setup as documented below unless the user wants to hold off pending a version-alignment decision.
3. Check if `{project-root}/_bmad/config.yaml` exists — if a section matching the module's code is already present, inform the user this is an update
4. Check for per-module configuration at `{project-root}/_bmad/rrd/config.yaml` and `{project-root}/_bmad/core/config.yaml`. If either file exists:
   - If `{project-root}/_bmad/config.yaml` does **not** yet have a section for this module: this is a **fresh install**. Inform the user that installer config was detected and values will be consolidated into the new format.
   - If `{project-root}/_bmad/config.yaml` **already** has a section for this module: this is a **legacy migration**. Inform the user that legacy per-module config was found alongside existing config, and legacy values will be used as fallback defaults.
   - In both cases, per-module config files and directories will be cleaned up after setup.

If the user provides arguments (e.g. `accept all defaults`, `--headless`, or inline values like `user name is BMad, I speak Swahili`), map any provided values to config keys, use defaults for the rest, and skip interactive prompting. Still display the full confirmation summary at the end.

## Collect Configuration

Ask the user for values. Show defaults in brackets. Present all values together so the user can respond once with only the values they want to change (e.g. "change language to Swahili, rest are fine"). Never tell the user to "press enter" or "leave blank" — in a chat interface they must type something to respond.

**Default priority** (highest wins): existing new config values > legacy config values > `./assets/module.yaml` defaults. When legacy configs exist, read them and use matching values as defaults instead of `module.yaml` defaults. Only keys that match the current schema are carried forward — changed or removed keys are ignored.

**Core config** (only if no core keys exist yet): `user_name` (default: BMad), `communication_language` and `document_output_language` (default: English — ask as a single language question, both keys get the same answer), `output_folder` (default: `{project-root}/_bmad-output`). Of these, `user_name` and `communication_language` are written exclusively to `config.user.yaml`. The rest go to `config.yaml` at root and are shared across all modules.

**Module config**: Read each variable in `./assets/module.yaml` that has a `prompt` field. Ask using that prompt with its default value (or legacy value if available).

## Cache Ray's Runtime State Before Running Scripts

If `{project-root}/_bmad/rrd/config.yaml` already exists, read it now and note any `last_category`/`last_code` values present. **Do this before the Write Files step below** — `merge-config.py` treats this same file as a legacy fallback source and deletes it as part of its own cleanup, so by the time the later "Write the Module-Level Config Mirror" step runs, the file may already be gone. The values cached here are what that later step must carry forward.

## Resolve the Python Interpreter

`python3` is not guaranteed to exist on PATH — confirmed absent on at least one real Windows/Git-Bash environment this module was tested in, where only `python`/`py` were available. Before running any script below, resolve the working interpreter once: try `python3 --version`, then `python --version`, then `py --version`, and use the first one that succeeds for every invocation in this skill. Do not assume `python3` and let the command fail silently into a stop — this is a cheap, one-time check, not a per-call retry.

## Write Files

Write a temp JSON file with the collected answers structured as `{"core": {...}, "module": {...}}` (omit `core` if it already exists). Then run both scripts — they can run in parallel since they write to different files (substitute the resolved interpreter from above for `{python}`):

```bash
{python} ./scripts/merge-config.py --config-path "{project-root}/_bmad/config.yaml" --user-config-path "{project-root}/_bmad/config.user.yaml" --module-yaml ./assets/module.yaml --answers {temp-file} --legacy-dir "{project-root}/_bmad"
{python} ./scripts/merge-help-csv.py --target "{project-root}/_bmad/module-help.csv" --source ./assets/module-help.csv --legacy-dir "{project-root}/_bmad" --module-code rrd
```

Both scripts output JSON to stdout with results. If either exits non-zero, surface the error and stop. The scripts read legacy config values as fallback defaults, then delete only this module's own legacy file after a successful merge — `{legacy-dir}/core/config.yaml` and `{legacy-dir}/core/module-help.csv` are read for fallback values but deliberately never deleted, since in a current-generation multi-module install "core" is a live sibling module's own file, not necessarily a stale artifact this module owns (a real incident during testing: an earlier version of these scripts deleted a fresh install's `_bmad/core/config.yaml` on first run). Check `legacy_configs_deleted` and `legacy_csvs_deleted` in the output — they should only ever name this module's own path, never a `core/` path.

Run `./scripts/merge-config.py --help` or `./scripts/merge-help-csv.py --help` for full usage.

## Create Output Directories

After writing config, create any output directories that were configured. For filesystem operations only (such as creating directories), resolve the `{project-root}` token to the actual project root and create each path-type value from `config.yaml` that does not yet exist — this includes `output_folder` and any module variable whose value starts with `{project-root}/`. The paths stored in the config files must continue to use the literal `{project-root}` token; only the directories on disk should use the resolved paths. Use `mkdir -p` or equivalent to create the full path.

## Cleanup Legacy Directories

After both merge scripts complete successfully, remove the installer's package directories. Skills and agents in these directories are already installed at `.claude/skills/` — the `_bmad/` directory should only contain config files.

```bash
{python} ./scripts/cleanup-legacy.py --bmad-dir "{project-root}/_bmad" --module-code rrd --also-remove _config --skills-dir "{project-root}/.claude/skills"
```

The script verifies that every skill in the directories being removed exists at `.claude/skills/` before removing anything, and never includes `core` in that removal list automatically (see the note above — `--also-remove` only ever names directories this module's own install actually created, never `core`). Directories without skills (like `_config/`) are removed directly. If the script exits non-zero, surface the error and stop. Missing directories (already cleaned by a prior run) are not errors — the script is idempotent.

Check `directories_removed` and `files_removed_count` in the JSON output for the confirmation step — `directories_removed` should never contain `core` (see above). Run `{python} ./scripts/cleanup-legacy.py --help` for full usage.

## Write the Module-Level Config Mirror

**Deviation note (judgment call):** this generic setup-skill template writes to the newer unified `{project-root}/_bmad/config.yaml` (module section) format. Refactor Radar's five workflow skills (`rrd-detect-*`, `rrd-audit-all`) were built to match the Test Architecture Enterprise module's structural convention exactly, per explicit project decision, which reads its module config from a dedicated **module-level file**, `{config_source}: "{project-root}/_bmad/tea/config.yaml"` — not the unified file. To keep that fidelity without fighting the current tooling, after the merge scripts succeed, also write a thin mirror file at `{project-root}/_bmad/rrd/config.yaml`, built from two template files (kept as separate assets, not inlined here, so the field-level comments stay easy to maintain and don't require editing this instruction file):

1. Read `./assets/rrd-config-template.yaml` and substitute its placeholders: `{{RRD_ARTIFACTS}}`, `{{OUTPUT_FOLDER}}`, `{{USER_NAME}}`, `{{COMMUNICATION_LANGUAGE}}`, `{{DOCUMENT_OUTPUT_LANGUAGE}}` with the resolved values. This always gets written, every setup run — it's the full commented base file.
2. If `last_category`/`last_code` were cached in the "Cache Ray's Runtime State" step above, also read `./assets/rrd-config-runtime-fragment.yaml`, substitute `{{LAST_CATEGORY}}`/`{{LAST_CODE}}` with the cached values, and append it to the base file's content. If neither was cached (first-ever setup, or Ray has never dispatched anything yet), skip this fragment entirely — do not append it with empty/placeholder values.
3. Write the combined result to `{project-root}/_bmad/rrd/config.yaml`.

Create `{project-root}/_bmad/rrd/` if it does not exist. Re-write this mirror file every time setup/configure runs (anti-zombie: overwrite, don't append) — the runtime fragment is the only part that varies per-run based on prior state; the base template is always written fresh from the asset file, so the file stays self-documenting even after a hand-edit strips a comment out.

## Confirm

Use the script JSON output to display what was written — config values set (written to `config.yaml` at root for core, module section for module values), user settings written to `config.user.yaml` (`user_keys` in result), help entries added, fresh install vs update. If legacy files were deleted, mention the migration. If legacy directories were removed, report the count and list (e.g. "Cleaned up 106 installer package files from bmb/, core/, \_config/ — skills are installed at .claude/skills/"). Then display the `module_greeting` from `./assets/module.yaml` to the user.

## Outcome

Once the user's `user_name` and `communication_language` are known (from collected input, arguments, or existing config), use them consistently for the remainder of the session: address the user by their configured name and communicate in their configured `communication_language`.
