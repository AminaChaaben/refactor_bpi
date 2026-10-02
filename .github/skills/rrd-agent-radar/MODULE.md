# Refactor Radar (`rrd`)

A BMad module that turns a codebase knowledge graph (the project's code graph, queried with the `code-graph` CLI) plus execution/test logs into concrete, evidence-backed refactor proposals. It generalizes a proven prototype — `kb_agent.py` at `C:\Users\achaabane\Desktop\refactor_module\` — into BMad-native skills: a stateless persona/router agent (Ray) plus 5 sibling detection workflow skills.

- **Module name:** Refactor Radar
- **Module code:** `rrd`
- **Skills built:** `rrd-agent-radar` (Ray, persona/router), `rrd-detect-dependencies`, `rrd-detect-instability`, `rrd-detect-data-issues`, `rrd-detect-duplication`, `rrd-audit-all` (5 sibling workflow skills), `rrd-setup` (module setup skill)
- **Location:** `C:\Users\achaabane\Desktop\BMAD_cursor\skills\` (one folder per skill, siblings of `rrd-agent-radar`)
- **Plan document:** `C:\Users\achaabane\Desktop\BMAD_cursor\skills\reports\rrd-module-plan.md`

## Why it exists

The root-cause taxonomy driving this module's design comes from a real TNR (non-regression test) reliability engagement: of 231 qualified false-fails across 858 tests (~27% false-fail rate), causes broke down as:

| Root-cause family | Share of false-fails | Refactor Radar workflow |
|---|---|---|
| Test dependencies / cascades | 47% | `rrd-detect-dependencies` |
| Instability (selectors, waits, overlays) | 18% | `rrd-detect-instability` |
| Data lifecycle issues | 17% | `rrd-detect-data-issues` |
| Maintenance / code duplication | 18% | `rrd-detect-duplication` |

Refactor Radar automates the manual "qualify each false-fail into a root cause" step that a human team would otherwise do one failure at a time — generalized so it isn't tied to any one client's codebase or stack.

## Architecture — restructured for exact TEA fidelity

**This module was rebuilt** (see `rrd-module-plan.md`'s "Ideas Captured" for the full history) to match the real, on-disk structure of the installed Test Architecture Enterprise (TEA) module exactly, superseding the original "single agent, four capabilities" memory-agent design.

**Current shape:** a **stateless persona/router agent**, Ray, exactly like `bmad-tea`'s Murat — no sanctum, no First Breath, no PULSE, no cross-session memory — dispatching via its `customize.toml` menu to **5 sibling workflow skills**, one per detection capability, exactly like Murat dispatches to `bmad-testarch-atdd`, `bmad-testarch-framework`, etc.

Why the change: TEA structural fidelity was an explicit user requirement that was decided to override the earlier memory-agent design. The memory feature (sanctum, `findings-log.md`/`calibration.md`, First Breath) was deliberately retired, not merely deprioritized.

### Persona

**Ray** — a forensic, evidence-first refactor detective. Terse, shows its work, never claims a fix without proposing a diff. Calm and precise, not alarmist: reports confidence levels, not absolutes.

**Mission:** *"Find the coupling, fragility, and duplication that make test suites lie about failures — and never touch source without a reviewable diff."*

**The Investigation Contract** — now a shared knowledge fragment (`rrd-agent-radar/resources/knowledge/evidence-and-diff-discipline.md`), referenced by every workflow rather than repeated inline:

- Every finding must cite the exact graph query, trace, or log evidence that produced it. No claim without a receipt.
- Every proposed fix is a reviewable unified diff written to `proposals/` **in the target project** (the project under analysis — never this skill's own folder). Source is never edited directly.
- The code graph is checked with `code-graph status` (and re-indexed if missing or stale) before anything is queried.

### Persona inheritance in the 5 workflow skills

Each workflow skill's `SKILL.md` follows `bmad-testarch-atdd`'s pattern: it states the role briefly and says it continues to operate with whatever persona is already active (Ray), rather than re-declaring Ray's full personality 5 times.

### Workflow-skill step architecture: single-mode, not tri-modal

TEA's workflows use tri-modal Create/Edit/Validate (`steps-c/`, `steps-e/`, `steps-v/`) because they produce a durable, editable artifact. A detection run doesn't have that shape — there's no natural "edit an existing finding," and "validate" would just mean "re-run." So each of the 5 workflow skills uses **plain sequential `steps/`** (mirroring `bmad-create-architecture`'s convention), with the deviation explained in each `instructions.md`.

### Module-level config

`{project-root}/_bmad/rrd/config.yaml`, matching TEA's `{project-root}/_bmad/tea/config.yaml` convention. Holds `rrd_artifacts` (findings/audit-report output folder — **not** diff proposals, which always go to the target project's own `proposals/`) plus core vars (`user_name`, `communication_language`, `document_output_language`, `output_folder`). Written by `rrd-setup` as a mirror of the unified `{project-root}/_bmad/config.yaml` — see `rrd-setup/SKILL.md` for the judgment call behind that mirror step.

### Multi-skill module via `rrd-setup`

Replaces the old standalone self-registration design. Scaffolded with `bmad-module-builder/scripts/scaffold-setup-skill.py`. `assets/module.yaml`'s `agents:` roster lists only `rrd-agent-radar` (Ray) — the 5 workflow skills are not agents and don't get roster entries, matching how TEA's workflow skills don't appear in an `agents:` list either. `assets/module-help.csv` has 7 rows (setup + Ray + 5 workflows) using the real installed `preceded-by`/`followed-by` column convention.

## Capabilities (now standalone workflow skills)

| Code | Skill | Outcome | Key tools |
|---|---|---|---|
| DD | `rrd-detect-dependencies` | Finds test-to-test coupling/cascade risk: shared mutable fixtures/globals, order-dependence, shared live app/data state. Highest-impact detector (47% of reference false-fails) — err toward thoroughness here. | `code-graph query variables`/`by-decorator`/`caller-count`/`callers`/`importers`, `Grep` |
| DI | `rrd-detect-instability` | Finds fragile selectors, fixed waits/timeouts, unhandled overlays/iframes/alerts; corroborated against real failure/rerun logs when available. | `Grep`, `code-graph query file-symbols`, logs via `Read`/`Grep`/`Bash` (the graph holds no execution history) |
| DT | `rrd-detect-data-issues` | Finds shared/non-reusable test data, missing setup/teardown, hardcoded users/URLs; correlates data collisions across runs via logs. | `Grep`, `code-graph query by-decorator`/`callees`, logs via `Read`/`Grep`/`Bash` (the graph holds no execution history) |
| DU | `rrd-detect-duplication` | Finds redundant/near-duplicate code via structural graph similarity. | `code-graph query similar-pairs`/`symbol` |
| AA | `rrd-audit-all` | Runs all four detectors against one project and produces a single self-contained HTML report, ranked by estimated false-fail impact and grouped by root-cause family, each entry linking to its diff proposal. | All of the above |

Each workflow's `instructions.md`/`checklist.md` was migrated from the corresponding old `rrd-agent-radar/references/detect-*.md`/`audit-all.md` capability file — the detection heuristics and quantified root-cause percentages are preserved, not rewritten from scratch. `rrd-audit-all`'s inline HTML report template migrated into its own `audit-report-template.html`, referenced by `workflow.yaml`'s `template:` pointer (mirroring `bmad-testarch-atdd/workflow.yaml`'s `template: "./atdd-checklist-template.md"`).

## Tool dependencies

The `code-graph` command-line tool, run through `Bash` inside the project's sandbox (no MCP server and no custom Python client loop like `kb_agent.py`'s). It reads and writes the code graph in the project's own Neo4j, next to the ALM context graph, using the `NEO4J_*` connection the sandbox is given:

`code-graph status` · `code-graph index` · `code-graph query --list` · `code-graph query <name> --param k=v --limit N`

Named queries (read-only, all scoped to this project's code graph): `summary`, `labels`, `relationships`, `complexity-hotspots`, `complexity-by-{cyclomatic,cognitive,loop-depth,tld,params,access-depth}`, `complexity-risks`, `loops`, `similar-pairs`, `callers`, `callees`, `caller-count`, `calls-matching`, `impact`, `importers`, `module-usage`, `variables`, `by-decorator`, `tree`, `files`, `file-symbols`, `symbol`, `search-source`, `hierarchy`, `endpoints`, `maven-deps`, `uncalled`, `stale`, `stubs`. Text patterns (selectors, literals, sleeps, catch blocks, log calls) use `Grep`; config files and manifests use `Read`.

(`code-graph` uses CodeGraphContext only as an offline extractor, then loads the result with the same loader as the context graph and computes the complexity and similarity metrics itself. There is no call-frequency trace ingestion; see `rrd-analyze-test-reliability`'s `analyze-test-reliability.md` for the one place this mattered.)

Diff proposals are written with each workflow's own **Write** tool directly to the target project's `proposals/` folder — reusing the same "never touch source, always emit a reviewable patch" contract that `kb_agent.py`'s `propose_refactor` function already implemented standalone.

**Init responsibility:** each workflow's `step-01-preflight-and-init.md` runs `code-graph status` before anything else and `code-graph index` if the index is missing or stale, rather than guessing at ungraphed code.

## Reference Finding — Preserved on Disk

One real finding from analyzing this project's own CRM Playwright suite, and two real diff proposals, are preserved on disk as a worked example (not an active memory contract any workflow depends on):

- `C:\Users\achaabane\Desktop\BMAD_cursor\_bmad\memory\rrd-agent-radar\findings-log.md` — the finding (shared live `crmPage` fixture across 5 CRM spec files; a delete-all test on that shared instance)
- `C:\Users\achaabane\Desktop\BMAD_cursor\proposals\tests__e2e__crm-workspace__settings.spec.ts.1.patch`
- `C:\Users\achaabane\Desktop\BMAD_cursor\proposals\playwright.config.ts.1.patch`

The finding's *content* is also captured as a worked "Reference Example" in `rrd-agent-radar/resources/knowledge/detect-dependencies.md`.

## File tree

```
skills/
├── rrd-agent-radar/                    # Ray — stateless persona/router agent
│   ├── SKILL.md                        # Murat-style activation: resolve customization, persona, config, greet, menu
│   ├── customize.toml                  # icon, role/identity/communication_style/principles, [[agent.menu]] → 5 workflows
│   ├── MODULE.md                       # this file
│   └── resources/
│       ├── rrd-index.csv               # id,name,description,tags,tier,fragment_file
│       └── knowledge/
│           ├── evidence-and-diff-discipline.md   # shared Investigation Contract
│           ├── detect-dependencies.md            # DD heuristics + the historical Reference Example
│           ├── detect-instability.md             # DI heuristics
│           ├── detect-data-issues.md             # DT heuristics
│           ├── detect-duplication.md             # DU heuristics
│           └── audit-all-report.md               # AA report format
├── rrd-detect-dependencies/            # workflow skill, single-mode steps/
│   ├── SKILL.md  customize.toml  workflow.yaml  instructions.md  checklist.md
│   └── steps/step-01-preflight-and-init.md, step-02-investigate.md, step-03-report-and-propose.md
├── rrd-detect-instability/             # same shape
├── rrd-detect-data-issues/             # same shape
├── rrd-detect-duplication/             # same shape
├── rrd-audit-all/                      # workflow skill, single-mode steps/ + template
│   ├── SKILL.md  customize.toml  workflow.yaml  instructions.md  checklist.md
│   ├── audit-report-template.html
│   └── steps/step-01-preflight-and-init.md, step-02-run-detectors.md, step-03-rank-and-report.md
└── rrd-setup/                          # module setup skill (scaffolded)
    ├── SKILL.md                        # config collection/writing + rrd/config.yaml mirror step
    ├── assets/module.yaml              # agents: [rrd-agent-radar] only; variables: rrd_artifacts
    ├── assets/module-help.csv          # 7 rows, preceded-by/followed-by columns
    └── scripts/merge-config.py, merge-help-csv.py, cleanup-legacy.py
```

## How this differs from `kb_agent.py`

`kb_agent.py` (`C:\Users\achaabane\Desktop\refactor_module\kb_agent.py`) is a standalone Python CLI with its own Anthropic client and hand-rolled tool loop, because it runs outside Claude Code — it was the proof of concept that the "explore the graph, propose a diff, never edit" pattern works, validated against a small Java Selenium POM project with real proposal patches. Refactor Radar keeps that same investigative contract but:

- runs natively as BMad skills inside Claude Code (no separate API client or tool-loop plumbing needed),
- specializes the one generic prototype into 5 named, independently invocable skills grounded in a quantified root-cause taxonomy,
- structures itself exactly like TEA (persona/router + workflow skills) rather than as a monolithic memory agent,
- adds a consolidated, ranked HTML audit report (`rrd-audit-all`) as a structured deliverable, rather than free-text chat output.

## Status / next steps

- ✅ Module plan revised for TEA-fidelity restructure (`skills/reports/rrd-module-plan.md`)
- ✅ Ray rebuilt as a stateless persona/router agent
- ✅ 5 sibling workflow skills built (`rrd-detect-dependencies`, `rrd-detect-instability`, `rrd-detect-data-issues`, `rrd-detect-duplication`, `rrd-audit-all`)
- ✅ `rrd-setup` scaffolded via `scaffold-setup-skill.py`; old standalone artifacts retired
- ✅ Old sanctum scaffolding, First Breath, and the 5 old `references/detect-*.md`/`audit-all.md` capability files removed from `rrd-agent-radar/` once their content was confirmed migrated
- ⬜ First real run of the restructured module: run `rrd-setup`, activate Ray, try `rrd-detect-dependencies` against an indexed project
- ⬜ Longer-term: this module is designed to sit as a peer inside a larger orchestrated BMad-based "testing factory" — a future fix-execution or CI-gating module can consume each workflow's findings summaries or the `rrd-audit-all` HTML report as its input contract
