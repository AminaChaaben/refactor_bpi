# The graph subsystem

A Neo4j traceability graph built from Jira, Jira Xray, Azure DevOps and GitLab —
every issue, its people, its hierarchy, its links, its sprints, its comments, its
history, its tests, steps, preconditions, runs, evidence and defects, and the
pipelines and merge requests that delivered them. Every tracker lands in one shared
vocabulary, so a question asked of the graph is the same question and the same
Cypher whichever system the project actually lives in.

This document explains, in order: where everything lives, how a build actually runs
end to end, what each module does and how, how the scheduler wires the graph in,
how to query the result, and how to stand up a demo from nothing.

## Where everything lives

```
connection_sources/
  graph/
    schema.py           GraphSchema: one graph's marker label, closed vocabularies, constraints
    ontology.py         vocabulary: labels, relationships, per-project aliases (ALM_SCHEMA)
    ontology_defaults.json   the built-in issue-type / link-type / status maps
    aliases.py           reading/writing a project's ontology overrides
    model.py              GraphNode / GraphEdge / GraphBatch, the uid() scheme
    extract_jira.py       Jira issues, links, changelog, remote links, watchers, sprints
    extract_xray.py       the six Xray collections -> graph, tier-agnostic
    xray.py                which Xray tier a site has; the Cloud GraphQL client
    xray_server.py         Xray Server/DC REST, rewritten into the Cloud shapes
    extract_azure.py      Azure DevOps work items, test plans/suites/cases, runs/results
    extract_gitlab.py     GitLab as delivery context (repos, pipelines, MRs, milestones)
    cypher.py              MERGE statements built as text + parameters
    loader.py               sends a batch through a Runner (real session or test double)
    derive.py                coverage / latest-run / hierarchy-depth, computed post-load
    queries.py               the named, read-only questions the graph answers
    project_state.py        one batch projected into a project's whole state as JSON
    config.py                GraphConfig: connection + shape settings for one project
    runner.py                 orchestrates all of the above into one build
    __init__.py                the public surface re-exported as `connection_sources.graph`
  update_state.py         the sync cycle; --graph makes it also rebuild the graph
  schedule.py              registers update_state as a recurring OS task/timer/cron job
  cli.py                    graph-load / graph-state / graph-query / graph-doctor / ...
tests/graph/                one test file per module above, plus test_runner.py
docs/GRAPH.md                this file
```

Every module is intentionally inert except `runner.py`: extractors are pure
functions (payload in, `GraphBatch` out), `cypher.py` only builds statement text,
`loader.py` only writes through a `Runner` it is handed. `runner.py` is the one
piece that actually opens a connection and calls everything else in order — which
is also why it is the one module whose tests fake every I/O boundary wholesale
(see `tests/graph/test_runner.py`) rather than talking to anything real.

## How a build actually runs, end to end

1. **`run_build(project, ...)`** (`runner.py`) loads the project's `sources.json`
   (`load_project`), its `.env` (`load_project_env`) and its `graph` config block
   (`load_graph_config`). A build is stamped with one version number,
   `int(time.time())` — every node and edge this run touches carries it, and that
   is what `--prune` later compares against to find what this run did not confirm.

2. It picks which configured sources to build from — every enabled Jira, Azure
   DevOps or GitLab source, or only the ones named — and **orders trackers before
   delivery sources** (`TRACKER_SERVERS = ("jira", "azuredevops")` before
   `DELIVERY_SERVERS = ("gitlab",)`), because GitLab's contribution is a set of
   issue-key mentions that need to resolve into the namespace the tracker's own
   nodes were just written under. A GitLab-only build still works; its mentions
   just have nothing to attach to yet.

3. For each source, **`build_batch()`** dispatches by `source.server` to
   `_build_jira` / `_build_azure` / `_build_gitlab`, each of which:
   - opens a live client (`operations.open_client`),
   - reads whatever the project's `graph.include` config asked for (comments,
     attachments, changelog, remote links, watchers, sprints, xray, test plans,
     executions, delivery — see `GraphConfig.wants()`),
   - calls the matching `extract_*` function(s) to turn the raw payload into a
     `GraphBatch`,
   - and returns both the batch and a **report** (`issues_read`, per-collection
     counts, the Xray tier detected, any per-call errors).

   Every optional read is wrapped in `_guarded()`: a `ConnectionSourceError` from
   one call (a token without test-plan scope, pipelines disabled on the project)
   is recorded against the report and the build carries on — a graph missing its
   pipelines still answers every coverage question; losing the whole build over
   one unreadable collection would be the wrong trade every time.

4. The per-source batches are merged into one `GraphBatch`; **`tracker_system`
   /`tracker_site`** are captured from the first tracker source built and threaded
   into every later `build_batch()` call, which is what lets a GitLab pipeline's
   `MENTIONS` edge land on `jira:example.atlassian.net:issue:DEM-1` instead of
   inventing a GitLab-namespaced stub nobody else's data will ever point at.

5. Unless `dry_run`, the merged batch is sent through **`loader.load_batch()`**
   against a real Neo4j session (`loader.open_session`) — schema first, then
   nodes, then relationships, batched by Cypher label/type since neither can come
   from a parameter. If `--graph-prune` was asked for, **`loader.prune()`** sweeps
   away anything under this project's namespace that the just-finished build did
   not stamp with its version. Then **`derive.run_derivations()`** computes
   coverage status, latest-run pointers and hierarchy depth directly in the
   database, once per namespace.

6. The result is a `BuildResult` (`runner.BuildResult.to_dict()`): what was read,
   what was written, what Xray tier each site had, what failed and why. That, or
   the state document built from it (see below), is what every CLI command and
   the scheduler's `--graph` flag ultimately print.

## The ontology: how any tracker's own words become one vocabulary

`ontology.py` defines two **closed** sets — `LABELS` and `RELATIONSHIPS` — the
only Cypher fragments in the whole package that are ever built by string
interpolation rather than parameterized. Nothing else may write a label or
relationship type that is not in one of these sets; an unmapped issue type falls
back to `:Issue`, an unmapped link type falls back to `:LINKED_TO`. This is a
closed-set-by-design choice, not an oversight — it is what keeps a link-type typo
in Jira from ever becoming a new relationship type nobody can query for.

Each project maps its tracker's own type/link/status names onto that closed
vocabulary via `Ontology` (`ontology_defaults.json` plus a project's own
`graph.ontology` overrides in `sources.json`, editable live with
`graph-alias-add`/`graph-link-alias-add`). A Story, a User Story and a Product
Backlog Item all become `:Story`; "is validated by" and Azure's
`Microsoft.VSTS.Common.TestedBy` both become `COVERS`.

## Jira and Jira Xray — the deepest-mapped tracker

Xray exists in three shapes, detected **per site**, never configured
(`xray.detect_tier`):

- **Cloud** — a GraphQL API, numeric issue ids. `xray.XrayCloudClient` reads the
  six collections (tests, preconditions, test sets, test plans, executions, runs)
  directly in that shape.
- **Server/DC** — a REST plugin (`/rest/raven`) keyed by issue key, not id.
  `xray_server.py` classifies the already-read Jira issues by type (test,
  precondition, test set, test plan, execution), reads runs/steps/results through
  `/rest/raven`, and **rewrites the result into the exact shape the Cloud client
  would have produced** — declared custom fields (test type, precondition
  definition, execution environments) are read the same way regardless of tier, so
  `extract_xray.py` is reused byte-for-byte by both tiers.
- **None** — the site has no Xray at all. Its "tests" are just ordinary Jira
  issues, already fully extracted by the plain Jira pass. Since v0.2 the **plain
  tier** (`plain.py`) still answers the questions a real Xray would: it classifies
  the already-read issues by ontology label, reads container membership from the
  "Test" issue links, Gherkin steps and `Preconditions:` sections from the test's
  description, and run results from `run:<test-key>:<state>` labels on the
  execution (with the test's own `state:` label as fallback), then rewrites
  everything into the Cloud-shaped payloads `extract_xray` already consumes — so a
  site with no Xray at all still produces `:TestPlan`, `:TestSet`,
  `:TestExecution`, `:TestRun`, `:TestStep` and `:Precondition` nodes. A site where
  a "Test Plan" issue type cannot exist models the plan as a Story carrying the
  label `test-plan`; `plain.classify` recognises it.

`extract_xray.extract_xray()` is the one function all tiers converge on. It
builds: `:Test` nodes with their steps (`:TestStep`, ordered), `:Precondition`
nodes (inline or standalone), `:TestSet`/`:TestPlan` containers (disambiguated by
label, not by relationship — a `CONTAINS` edge means something different coming
off a `:TestSet` than off a `:TestExecution`), `:TestExecution` nodes holding one
`:TestRun` per case (`:TestRunStep` per step result), `COVERS` edges from tests to
the requirements they cover, and `FOUND` edges from a failing run to the defect it
raised. An `IssueIndex` resolves numeric-id references (Cloud's native shape) back
to issue keys as records are learned, so a reference that arrives before its
target has been read still lands as a correctly-keyed stub that merges once the
real node shows up.

## Azure DevOps and GitLab, mapped onto the same vocabulary

**Azure** (`extract_azure.py`): work items are re-read through `graph_batch` (not
`search`, which cannot return relations alongside a named field list) so
parent/child and tested-by links survive. A Product Backlog Item becomes `:Story`,
an area path becomes a whole `:Component` (kept intact, never split on `\`), an
iteration path becomes `:Sprint`, a test suite becomes `:TestSet`, a test run
becomes `:TestExecution` holding one `:TestRun` per result — the same shape Xray
produces, so the same Cypher answers coverage questions on either tracker. Azure
reports both halves of every link (`-Forward` on one side, `-Reverse` on the
other); the ontology's own declared `reverse` flag composes with that suffix so
both halves converge onto one stored edge in one canonical direction regardless of
which side's read produced it. HTML fields (descriptions, step text) are decoded
with `html.unescape()` **before** tags are stripped, because Azure's test-step XML
is HTML that has been escaped a second time to survive living inside an XML
element (`&lt;P&gt;text&lt;/P&gt;`) — stripping first would leave the escaped tags
as literal text in the stored property.

**GitLab** (`extract_gitlab.py`) is deliberately never a second requirement model —
it is delivery context, joined onto the tracker's own graph by one
weak-but-precise mechanism: `issue_keys_in()` scans pipeline refs, merge-request
titles/branches/descriptions and issue text for tracker-shaped keys
(`[A-Z][A-Z0-9]{1,9}-\d{1,7}`, case-sensitive, not embedded in a longer token,
2+ leading letters so `a-1` in prose never matches) and writes a `MENTIONS` edge
whose target uid is built in the **tracker's** own uid namespace
(`tracker_system:tracker_site:issue:KEY`), not GitLab's. That is the entire join:
the edge points at a stub the moment GitLab is read, and the stub silently merges
with the real, richly-populated node once the tracker's own pass writes it —
`GraphNode.merged_with()` lets the rich side win whichever order the two builds
ran in.

## Loading, deriving, and what the database ends up holding

`loader.py` turns a `GraphBatch` into MERGE statements (`cypher.py`) grouped by
*signature* — a Cypher label or relationship type cannot come from a parameter, so
rows are grouped by their exact label set / relationship type first and then
chunked within each group (`batch_size`, default 500) — schema (one uniqueness
constraint) first, then nodes, then relationships, so a relationship never has to
create the stub node the node pass was about to fill in properly. `prune()` pages
through everything under a namespace prefix that this run's version did not touch
and deletes it — nodes deleted only after any relationship-pruning pass, and only
up to `max_pages` per call so a broken delete statement cannot spin against a live
database forever. `derive.py` then runs three post-load passes purely in Cypher,
using the exact same status tuples (`FAILING_STATUSES`, `PASSING_STATUSES`,
`REQUIREMENT_LABELS`) that `project_state.py`'s Python-side projection uses, so the
database's own coverage rule and the state document's coverage rule can never
silently disagree:

- **coverage** — each requirement gets a `coverage_status` property
  (`UNCOVERED`/`FAIL`/`OK`) from the tests that `COVERS` it.
- **latest-run** — each test's most recent `:TestRun` is marked, so "what does this
  test currently say" is an indexed property lookup, not a walk over history.
- **hierarchy depth** — computed from `CHILD_OF` chains, for workload/rollup
  queries that need to know how deep an item sits under its epic.

Nothing here is committed to a real Neo4j session by contract — `loader.Runner` is
just `Callable[[str, Mapping], list[dict]]`; tests pass a recorder that applies
MERGE semantics to a plain dict, which is the entire reason the load and prune
paths are verified with no database anywhere near the test machine
(`tests/graph/test_loader.py`, `tests/graph/test_derive.py`).

## Two graphs in one database: `GraphSchema`

Each platform project has its own Neo4j (a `graph-db` StatefulSet in the project's
Kubernetes namespace, created with the project, running while its sandbox runs). That
one database holds two graphs: this ALM **context graph**, and the project's **code
graph**, built by `Talan_Library/Code_Graph_Lib` (`code-graph index`). Neo4j Community
has a single database and no role-based access, so the two are kept apart by
construction rather than by the server:

- **A marker label per graph.** Every context-graph node carries `:AlmNode`, every
  code-graph node `:CodeNode`, and every MERGE, prune, derive and named query
  matches on its own marker.
- **A uid prefix per graph.** Context uids are `<system>:<site>:<scope>:<kind>:<id>`
  (e.g. `jira:…`); code uids are `code:<git host>:<project id>-<repo path>:<kind>:<id>`.
  `prune()` sweeps nodes by marker *and* prefix, and relationships by the prefix
  of their start node; the two graphs’ prefixes never overlap (`code` is not a
  tracker system).
- **A constraint per graph.** `alm_node_uid` and `code_node_uid`, each on its own
  marker, so one graph's schema pass cannot drop or clash with the other's.

`schema.py`'s `GraphSchema` carries all of that for one graph: `name`, `marker`,
the closed `labels` and `relationships` sets, the `fallback_label` /
`fallback_relationship` an unknown value degrades to, the constraint/index
`index_prefix`, and the `indexed_props`. `ontology.ALM_SCHEMA` is the context graph's
(marker `AlmNode`, fallbacks `Issue` / `LINKED_TO`), and it is the default everywhere:
`GraphNode` / `GraphEdge` validate their labels through their `schema`, and every
`cypher.py` builder plus `loader.plan_statements` / `load_batch` / `prune` take
`schema=ALM_SCHEMA`. So every ALM call site is unchanged and its statements are
byte-identical to before the parameter existed. `code_graph.schema.CODE_SCHEMA` is the
code graph's; `merged_with` refuses to merge nodes of two different schemas, and the
loader refuses a batch whose nodes belong to another schema than the one it was given.

The context graph's GitLab repository node is labelled `GitRepository`, so it can't
be confused with the code graph's `Repository` (`:CodeNode:Repository`, one per
indexed checkout).

In a sandbox, the listener reloads the context graph (`alm-conn graph-load --prune`)
after the first ALM sync and then every 15 minutes, and indexes the code graph once
the repository is cloned. `graph-query --scope` is prefixed with `PROJECT_ID` there,
the same way the loader prefixes the uids it writes.

## `project_state.py`: the graph, projected back into one JSON document

`build_state()` walks one already-loaded `GraphBatch` (or a batch just read back
from the database) and produces a single JSON document shaped for a person or an
agent to read directly, without writing Cypher: requirements with their coverage
verdict, tests with their latest run status, executions, an inline-vs-standalone
precondition distinction, stub counts (counted but never listed individually),
and named-but-unresolved references. Its coverage rule is computed **once per
test** and shared between the `tests[]` listing and every `requirements[].coverage`
entry that test contributes to — the same fix that closed a real bug found while
writing its tests: a requirement's coverage used to be derived from a test's
*declared* status while the test's own listing used its *actual* run history,
so the same document could show a requirement as not-failing while its own listed
covering test showed `run_status: "FAIL"`. `graph.state_of(result, batch)` wraps
`build_state()`'s output with the `BuildResult` metadata (version, sources, xray
tier per site) around it — this is what `graph-state`/`update_state --graph-state`
write to disk.

## The scheduler, and how `--graph` plugs into it

`schedule.py` registers `update_state` as a recurring OS-native job — Windows
Task Scheduler (`schtasks`, falling back from an XML task definition to a flag
form if the XML is refused), systemd user timers on Linux when available,
cron otherwise. Every process call goes through an injected `runner` callable, so
the exact argv handed to `schtasks`/`systemctl`/`crontab` and the exact text
written into a unit file or the crontab are what tests assert against, on a
machine that runs neither backend (`tests/test_schedule.py`). Two properties
matter for a poller specifically:

- **The task must run on battery and survive a missed wake** (a laptop asleep at
  the scheduled minute) — `DisallowStartIfOnBatteries=false`,
  `StartWhenAvailable=true` on Windows; `Persistent=true` on a systemd timer.
- **A slow cycle must not stack a second one behind it** — `IgnoreNew` on Windows,
  `OnUnitActiveSec` (measured from the *end* of the last run, not a fixed clock)
  on systemd — both because concurrent cycles would contend for the same
  `state.json` lock.

`schedule install --graph [--graph-prune] [--graph-state PATH]` and `cli.py`'s
equivalent flags append the matching `--graph`/`--graph-prune`/`--graph-state`
arguments onto the generated launcher's `update_state` invocation, so every
scheduled cycle runs the sync **and then** (never instead of) rebuilds the graph —
`update_state.run()`'s own docstring is explicit that the graph is a second
consumer of the same read, and a Neo4j that is down for maintenance must never
cost the project its change-detection history. `_build_graph()` in
`update_state.py` never lets a graph failure raise past the sync cycle itself: it
is caught and folded into the printed line as `{"ok": false, ...}`, and the
process exit code drops to *partial*, not *failed* — the sync half already
succeeded and wrote state.

## Querying the graph

`connection_sources/graph/queries.py` holds a closed catalogue of named,
parameterized, read-only Cypher — no ad-hoc Cypher is ever built from user input.
Current names: `summary`, `labels`, `relationships`, `uncovered`, `coverage`,
`traceability`, `hierarchy`, `links`, `workload`, `stale`, `stubs`, `history`.

```bash
# see what's answerable, and what each one answers
python -m connection_sources.cli graph-query --list

# run one against a live project
python -m connection_sources.cli graph-query uncovered --project talan_usine_config
python -m connection_sources.cli graph-query coverage --project talan_usine_config --limit 100
```

Each entry is a `Query(name, description, cypher)`; `graph.build_query(name,
prefix, **params)` fills in the project's own `system:site:` namespace prefix so
the same named query works unmodified across every project the graph has ever
been built for.

## Building and inspecting the graph from the CLI

```bash
# what config, credentials and Xray tier a project actually has, no writes
python -m connection_sources.cli graph-doctor --project talan_usine_config

# a real build against Neo4j (needs NEO4J_URI/NEO4J_USERNAME/NEO4J_PASSWORD in .env)
python -m connection_sources.cli graph-load --project talan_usine_config

# same build, but pruning what this run did not confirm, and writing a state document
python -m connection_sources.cli graph-load --project talan_usine_config \
    --prune --state _bmad_state/graph-state.json

# the state document alone -- no database needed, works on a machine with no Neo4j at all
python -m connection_sources.cli graph-state --project talan_usine_config --out state.json

# closed vocabularies, and per-project overrides
python -m connection_sources.cli graph-labels
python -m connection_sources.cli graph-relationships
python -m connection_sources.cli graph-alias-list --project talan_usine_config
python -m connection_sources.cli graph-alias-add --project talan_usine_config "User Story" Story
```

## Building a demo

The fastest path to something worth showing someone is: a project with real-ish
Jira/Xray shape, a local Neo4j, one `graph-load`, then either `graph-query` or
Neo4j Browser.

1. **Get a database.** Docker is the least setup:
   ```bash
   docker run -d --name neo4j-alm -p 7474:7474 -p 7687:7687 \
       -e NEO4J_AUTH=neo4j/<a-password> neo4j:5
   ```
   Neo4j Browser is then at `http://localhost:7474`.

2. **Point a project's `.env` at it** (`clients/<client>/<project>/.env`):
   ```
   NEO4J_URI=bolt://localhost:7687
   NEO4J_USERNAME=neo4j
   NEO4J_PASSWORD=<a-password>
   ```
   plus that project's normal Jira (and Xray, if any) credentials — see
   `.env.example` in the same folder for every variable name the client registry
   recognizes.

3. **Confirm before writing anything:**
   ```bash
   python -m connection_sources.cli graph-doctor --project clients/<client>/<project>
   ```
   This reports the Xray tier detected for the site and whether Neo4j is reachable,
   without writing a single node.

4. **Build the graph:**
   ```bash
   python -m connection_sources.cli graph-load --project clients/<client>/<project>
   ```

5. **Show it.** Either run a named query from the CLI (`graph-query summary`,
   `graph-query traceability`, `graph-query coverage`), or open Neo4j Browser and
   run raw Cypher against the loaded nodes/relationships directly — every label
   and relationship type is one of the closed sets from `graph-labels` /
   `graph-relationships`, so `MATCH (t:Test)-[:COVERS]->(r) RETURN t, r LIMIT 25`
   and similar exploratory queries work with no further setup.

6. **Wire it into the schedule**, so the demo also shows the automation story, not
   just one manual build:
   ```bash
   python -m connection_sources.cli schedule install --project clients/<client>/<project> \
       --interval 15 --graph --graph-prune --graph-state _bmad_state/graph-state.json
   python -m connection_sources.cli schedule status --project clients/<client>/<project>
   ```

## Testing

Every module's test file lives flat under `tests/`, named `test_graph_<module>.py`
after the module it covers — the same convention the rest of this package's tests
already use (`tests/test_schedule.py`, `tests/test_update_state.py`). Each one is
hand-built payload fixtures in, `GraphBatch` or a plain dict out, asserted against
directly — no live Jira, Azure, GitLab or Neo4j anywhere in the suite.

```
tests/test_graph_model.py               Namespace, uid(), site_of(), GraphNode/Edge/Batch
tests/test_graph_loader.py              batching, load ordering, prune scope refusal
tests/test_graph_extract_jira.py        Jira issue/link/changelog/sprint extraction
tests/test_graph_extract_azure.py       Azure work items, status_category, test plans/runs
tests/test_graph_extract_xray.py        the six Xray collections, nested-connection truncation
tests/test_graph_extract_confluence.py  page shape, tracker-namespace author/mention join
tests/test_graph_extract_gitlab.py      issue-key matching rules, MENTIONS namespace join
tests/test_graph_runner.py              run_build/build_batch orchestration, every I/O boundary faked
tests/test_schedule.py                  schtasks/systemd/cron argv and file contents
tests/test_update_state.py              the sync cycle, and its --graph wiring
```

Not every module has a dedicated file yet; a module with no listed file is covered
indirectly through `test_graph_runner.py`'s orchestration fakes until it earns its
own.

Run everything:

```bash
python -m pytest -q
```

Run just the graph package:

```bash
python -m pytest tests/graph -q
```

`tests/graph/test_runner.py` is the one file worth reading before extending
`runner.py` itself: it fakes `operations.open_client` (per-server fake clients),
`loader.open_session`/`session_runner`/`load_batch`/`prune`, and
`derive.run_derivations` wholesale via `monkeypatch`, then asserts on the real
orchestration behavior — tracker-before-delivery ordering regardless of declared
order, tracker site propagating into a GitLab build's `MENTIONS` edges, one
source's failure never stopping the others, `dry_run` and an empty source list
both short-circuiting before the loader is ever touched, and the exact arguments
`load_batch`/`prune`/`run_derivations` are called with.
