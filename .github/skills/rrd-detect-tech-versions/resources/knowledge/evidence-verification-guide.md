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
// Verify an impact count (files importing a dependency's packages):
MATCH (f:CodeNode:File)-[:IMPORTS]->(m:CodeNode:Module) WHERE m.name STARTS WITH 'org.junit'
RETURN m.name, count(DISTINCT f) AS files ORDER BY files DESC;
```

This bypasses `code-graph` entirely. If a number in a report matches what is in the database, it did not come from an invented or hallucinated tool response.

## For this detector specifically: verify against the manifest, not the graph

`detect-tech-versions` reads `pom.xml`/`build.gradle`/`package.json` directly via `Read`, so the primary verification step is simpler than for other detectors: open the manifest file yourself and confirm the version string the report cites is actually present at the stated location. The graph is only used for the *usage/impact count* (`code-graph query module-usage --param module_prefix=<prefix>` or `importers`) and, for Maven, the declared dependencies (`code-graph query maven-deps`). Those can be re-verified the same way as any other detector's evidence.

## Cheap corroborating checks

- **Index freshness.** `code-graph status` shows `indexed_at` and whether the index is `stale` against HEAD. A stale index is a red flag for graph-based impact counts.
- **Re-run the exact query.** Every finding's evidence field should include the literal `code-graph query ...` command used for the impact count. Run it yourself and diff the result against what the report claims.
- **Version database currency.** `resources/version-database.csv` is a static snapshot. Check its date/notes column if a "latest safe version" claim looks suspiciously old.

## What this does NOT verify

Confirming the manifest string and the impact count are real does not confirm the **CVE or breaking-change classification is correct**. That still requires checking the actual CVE database or release notes for the specific version pair, per the calibration note in `detect-tech-versions.md`.
