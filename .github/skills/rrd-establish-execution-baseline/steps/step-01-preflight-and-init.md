---
name: 'step-01-preflight-and-init'
description: 'Resolve target project, safety gate, detect toolchain, load knowledge fragments'
nextStepFile: '{skill-root}/steps/step-02-diagnose-and-fix.md'
---

# Step 1: Preflight & Init

## SEQUENCE

### 1. Resolve Target Project

Run `code-graph status`. If it reports `indexed: false` or `stale: true`, run `code-graph index --wait 600` now (read-only, no source touched).

### 2. Safety Gate — git status

Run `git status` on `{target_project_root}`.

- Not a git repo: tell the owner there is no version-control safety net, ask them to explicitly confirm before proceeding.
- Dirty tree (uncommitted changes unrelated to this workflow's own prior runs): show the dirty files, ask the owner to confirm before proceeding.
- Clean (or only this module's own prior output): proceed without prompting further.

### 3. Detect the Toolchain

Look for marker files in `{target_project_root}`:

| Marker | Toolchain | Typical test command |
|---|---|---|
| `mvnw`/`pom.xml` | Maven | Check the README first — some projects bind Surefire to `verify` (`mvn clean verify`), not `test` |
| `gradlew`/`build.gradle*` | Gradle | `./gradlew test` |
| `package.json` with a `test` script | npm/yarn/pnpm | Check `scripts.test` for the real runner |
| `pytest.ini`/`pyproject.toml` + `tests/` | pytest | `pytest <path>` |
| `go.mod` | Go | `go test ./...` |

If ambiguous, ask the owner.

### 4. Load Knowledge Fragments

Consult `./resources/rrd-index.csv`, then load:

- `./resources/knowledge/evidence-and-diff-discipline.md`
- `./resources/knowledge/establish-execution-baseline-heuristics.md`

### 5. Continue

Load `./step-02-diagnose-and-fix.md`.
