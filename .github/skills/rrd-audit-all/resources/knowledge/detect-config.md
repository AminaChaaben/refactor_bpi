# Detect Config Heuristics

## What Success Looks Like

The owner learns exactly where environment/config leaks live — hardcoded URLs, credentials, API keys, timeout literals, inline environment-switch logic, and unsafe parallel-execution settings — with file:line precision and a concrete externalization fix. Unlike the reliability-focused detectors (Dependencies, Instability, Data Issues), this axis had **zero dedicated detector coverage** before this skill; it targets whether the test suite can move safely across DEV/QA/REC/PROD and run safely in parallel, not a specific false-fail root-cause percentage from the reference engagement.

## Approach

### 1. Hardcoded URLs, Credentials, Secrets

Use `Grep` for literal URLs, inline credentials, and API keys/tokens embedded directly in source. These are structurally cheap to find via regex (`https?://`, `password\s*=`, `api[_-]?key`, `secret`) and almost always a real finding — there is rarely a legitimate reason for a live credential or environment-specific URL to be a string literal in test code.

### 2. Hardcoded Timeout/Wait Literals

Numeric timeouts passed directly to wait/sleep calls instead of a named config value. This overlaps with `rrd-detect-instability`'s fixed-sleep findings but is a distinct angle: instability cares that the wait mechanism itself is wrong (fixed sleep vs. explicit wait); this detector cares that even a *correct* explicit wait's duration is hardcoded rather than centrally configurable per environment (a slower REC environment may need longer timeouts than DEV). Note the overlap explicitly when both detectors flag the same line — it is one root cause, reported from two angles, not two separate problems.

### 3. Inline Environment-Switch Logic

Conditional branching that selects URLs, credentials, or behavior by environment name inline in test/page-object code, instead of a single externalized per-environment config file/object. The smell is the *branching being inline and scattered*, not the existence of multiple environments — a project with a clean `config/environments.yaml` and a single lookup call has already solved this; a project with `if (env === "qa") { ... } else if (env === "prod") { ... }` scattered across a dozen files has not.

### 4. Unsafe Parallel-Execution Config

Forced-serial settings (`workers=1`, `thread-count=1`, `fullyParallel=false`) in test-runner config are a signal, not an automatic finding. **Cross-reference against `rrd-detect-dependencies` before concluding anything**: a forced-serial setting is very often evidence that someone already discovered shared-state coupling and worked around it by disabling parallelism rather than fixing the coupling. If a dependencies finding exists for this target, cite it and frame the fix as "fix the coupling, then re-enable parallelism" rather than a standalone config change. If no dependencies finding exists yet, say so explicitly and recommend running that detector before touching the parallel setting — removing a serial constraint without confirming the underlying state is safe can turn a slow-but-passing suite into a flaky one.

## Before Proposing to Externalize a `static final` Field: Check for Compile-Time-Constant Usage

A real `rrd-apply-and-verify` run caught this live: converting `public static final String BASE_URL = "literal"` to `CONFIG.getProperty("base.url")` changes it from a genuine Java compile-time constant to a runtime-computed value. Any other file referencing that field inside an **annotation** (`@Step("..." + BASE_URL)`, or a field whose own initializer derives from it and is itself used in an annotation) fails to compile — Java requires annotation elements to be compile-time constant expressions. Before proposing this kind of diff, `Grep` for the field's simple name across the project and check whether any hit sits inside an `@Annotation(...)` argument (directly, or via another `static final` field whose value derives from it). If so, either scope the externalization diff to exclude that field, or flag the annotation usage as a blocker requiring a different fix (e.g. the file's own existing `"{0}"`-parameterized annotation convention, if one exists, as seen in a real case where sibling annotations in the same class already used parameter substitution instead of string concatenation).

## Calibration Note

Some hardcoded values may be intentional for a specific, narrow reason (e.g. a fixed public sandbox URL that genuinely never changes across environments). Ray does not carry cross-session calibration memory — treat any owner statement of "this is known and accepted" as in-session context for that run, not as something to persist automatically.
