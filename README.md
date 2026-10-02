# bpi_refactor — Ray (Refactor Detective) for GitHub Copilot

Extracted from Usine_Agentique_Testing/Talan_Factory (Refactor Radar, module `rrd`).
Open this folder as the workspace root: skills use `{project-root}` paths.

## Layout
- `.github/skills/rrd-*` — 19 skills. Entry point: `rrd-agent-radar` (Ray); it dispatches to the other 18
  (detect-*, audit-all, analyze-test-reliability, establish-execution-baseline, apply-and-verify, ci-gate, export-tickets, standards-audit, setup).
- `_bmad/rrd/config.yaml` — module config (language, output folders).
- `_bmad/scripts/resolve_customization.py` + `config_utils.py` — persona/menu resolver called on activation (python 3.11+).
- `_bmad/custom/` — optional team/user overrides (`rrd-agent-radar.toml`).
- `tools/Code_Graph_Lib` + `tools/Connection_Sources_Lib` — the `code-graph` CLI the detectors query (needs Neo4j).

## Setup
1. `cd tools/Code_Graph_Lib && uv sync` (Python >=3.11,<3.14), then run `code-graph` via `uv run code-graph status`
   or put its venv Scripts dir on PATH.
2. Neo4j connection settings: copy `tools/Connection_Sources_Lib/.env.example` to `.env` and fill in (credentials were NOT copied).
3. In Copilot (VS Code agent mode), invoke the `rrd-agent-radar` skill.

## Notes
- Skills were adapted from BMad/Claude Code; they run Python via `python3` and the `code-graph` CLI — make sure both resolve.
- Neo4j instance with the target project indexed (`code-graph index --wait 600`) is required for detectors.
- Optional BMad skills mentioned in text (`bmad-help`, module builders) are not included; they aren't needed to run Ray.
