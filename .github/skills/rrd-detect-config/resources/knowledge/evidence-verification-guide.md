# Evidence Verification Guide

How an owner independently re-verifies any graph-based finding without trusting Ray's tool-call output. Every detection workflow should be able to point an owner here when asked "how do I know this is real?"

## The index lives in the project's own Neo4j — query it directly

Each project has its own Neo4j instance (StatefulSet `graph-db` in the project's Kubernetes namespace, which is named after the project id). It holds two graphs side by side: the context graph (`:AlmNode`) and the code graph (`:CodeNode`). Every code-graph node's `uid` starts with `code:<site>:<repo path>:`, and `code-graph status` prints that prefix.

From inside the sandbox, `code-graph` already has the connection (`NEO4J_*` environment variables). From the host, open a shell on the database:

```bash
PID=<project id>
PW=$(kubectl -n "$PID" get secret graph-db-auth -o jsonpath='{.data.password}' | base64 -d)
kubectl -n "$PID" exec -it graph-db-0 -- cypher-shell -u neo4j -p "$PW"
```

```cypher
// Verify a SIMILAR_TO (duplication) claim:
MATCH (a:CodeNode)-[r:SIMILAR_TO]->(b:CodeNode) RETURN a.qualified_name, b.qualified_name, r.jaccard, r.same_file;

// Verify a complexity claim (properties live directly on the Function node):
MATCH (n:CodeNode:Function) RETURN n.qualified_name, n.cyclomatic_complexity, n.cognitive, n.transitive_loop_depth
ORDER BY n.cognitive DESC LIMIT 20;
```

This bypasses `code-graph` entirely. If a number in a report matches what is in the database, it did not come from an invented or hallucinated tool response.

## Cheap corroborating checks (before opening the database)

- **Index freshness.** `code-graph status` shows `indexed_at`, the indexed `head_sha` and whether the working tree's HEAD has moved since (`stale`). A report claiming fresh findings against a stale index is a red flag.
- **Re-run the exact query.** Every finding's evidence field should include the literal `code-graph query <name> --param ...` command used. Run it yourself and diff the result against what the report claims.
- **Row counts, not just top rows.** If a report says "N similar pairs found", re-run the query with a large enough `--limit` (or a `MATCH ... RETURN count(*)` in cypher-shell) and check that it matches N exactly, not just plausibly.

## Server-side logging (for deeper debugging)

`kubectl -n <project id> logs graph-db-0` (the database) and the sandbox listener's log (`[code-graph]` and `[graph-load]` lines, which show every index and load run with its node, relationship and prune counts) cover both halves of the stack.

## What this does NOT verify

Querying the raw graph confirms the **graph data is real and matches the report**. It does not confirm the **finding is correct**. That still requires reading the actual source (`code-graph query symbol` or `Read`) and applying judgment, per the rest of the Investigation Contract. A real `jaccard=1.000` edge on two genuinely unrelated one-line boilerplate functions is real data pointing at a weak finding, not a wrong one. Verification and correctness are two different questions, and this guide only answers the first.
