# code-graph

Builds and queries a project's **code graph**. It lives in the project's own Neo4j, next to the ALM context graph.

- **One Neo4j per project.** The backend creates a `graph-db` StatefulSet in the project's namespace when the project is created, stopped. It starts with the sandbox and stops when the sandbox goes away. The sandbox receives `NEO4J_URI` / `NEO4J_USERNAME` / `NEO4J_PASSWORD` / `NEO4J_DATABASE`.
- **Two graphs, one loader.**
  - The context graph uses `:AlmNode` with uid `<system>:<site>:<scope>:<kind>:<id>` (e.g. `jira:…`).
  - The code graph uses `:CodeNode` with uid `code:<git host>:<project id>-<repo path>:<kind>:<id>`.
  - Both are written by `connection_sources.graph.loader`, each with its own `GraphSchema`: marker label, closed label and relationship sets, and uniqueness constraint (`alm_node_uid`, `code_node_uid`). Neither graph can see or prune the other.
- **CodeGraphContext is only the extractor.**
  - `cgc index` parses the repository into a throwaway embedded Kùzu store, and `cgc bundle export` dumps it.
  - `bundle.py` maps that dump to a `GraphBatch` with repository-relative paths and stable uids.
  - `metrics/` then re-parses the source with tree-sitter and computes the metrics: cyclomatic (overwriting CGC's value), cognitive, loop depth, transitive loop depth, linear scans, allocations and recursion in loops, parameter count, access depth, per-call `in_loop_depth`, and `SIMILAR_TO` near-duplicate edges.
- **No MCP server.** Agents run the CLI through `Bash`. Each query is a named, read-only Cypher statement, scoped to this repository's uid prefix.

## Commands

```bash
code-graph status                 # indexed? stale against HEAD? counts, head_sha, indexed_at
code-graph index --wait 600       # extract, compute metrics, load, prune what disappeared
code-graph query --list           # every named query with its parameters and defaults
code-graph query complexity-hotspots --limit 50
code-graph query callers --param qualified_name=LoginPage.login --param depth=3
code-graph state --out graph.json # extract and compute without Neo4j (debugging)
```

Output is JSON on stdout. Errors are typed JSON on stderr, with the error's exit code, the same contract as `alm-conn`.

Named queries:

| Area | Queries |
|---|---|
| Overview | `status`, `summary`, `labels`, `relationships` |
| Complexity | `complexity-hotspots`, `complexity-by-{cyclomatic,cognitive,loop-depth,tld,params,access-depth}`, `complexity-risks`, `loops` |
| Duplication | `similar-pairs` |
| Calls and impact | `callers`, `callees`, `caller-count`, `calls-matching`, `impact` |
| Dependencies | `importers`, `module-usage`, `maven-deps` |
| Structure | `variables`, `by-decorator`, `tree`, `files`, `file-symbols`, `symbol`, `search-source`, `hierarchy`, `endpoints` |
| Maintenance | `uncalled`, `stale`, `stubs` |

## In the sandbox

- **Image:** the sandbox image installs this package (`/usr/local/bin/code-graph`) and pre-downloads the tree-sitter grammars into `/opt/code-graph/grammars` (`TREE_SITTER_LANGUAGE_PACK_CACHE_DIR`), because the sandbox cannot download them at run time.
- **Listener:** it indexes the repository once the clone exists, and only if `status` says the index is missing or stale. It also reloads the ALM context graph every 15 minutes.
- **Repository:** `--repo`, else `CODE_GRAPH_REPO`, else `/workspace/repo` or `/workspace/tests`, whichever holds a `.git`.
- **Scratch files:** indexing never writes into the working tree. CGC's scratch store lives in a temp directory, and `.cgcignore` / `.codegraphcontext/` are added to `.git/info/exclude`.
- **Concurrency:** one index at a time per repository, enforced by a lock in `CODE_GRAPH_STATE_DIR` (default `<repo parent>/_bmad_state/code-graph`).

## Local development

```bash
cd Talan_Library/Code_Graph_Lib
uv sync
docker run -d --name cg-dev-neo4j -p 127.0.0.1:27687:7687 -e NEO4J_AUTH=neo4j/devpassword1 neo4j:5-community
export NEO4J_URI=bolt://127.0.0.1:27687 NEO4J_USERNAME=neo4j NEO4J_PASSWORD=devpassword1
uv run code-graph index --repo /path/to/repo --wait 120
uv run code-graph query complexity-hotspots --repo /path/to/repo
```

Python 3.11 to 3.13: Kùzu has no 3.14 wheels yet.
