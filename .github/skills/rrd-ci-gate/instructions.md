<!-- Powered by BMAD-CORE™ -->

# CI Gate

---

## Overview

Invokes `rrd-audit-all` (full or incremental, per that workflow's own Step 1 logic — this wrapper doesn't override that choice) and converts its trend output into a pass/fail verdict: fail if any opportunity at or above `gate_fails_at_or_above` (default: Critical) is **new since the last tracked run**. A pre-existing Critical that was already there before this run is not a new gate failure — it's backlog, and this gate's job is to stop new debt, not to block every pipeline run on debt that already existed.

This is intentionally the thinnest possible wrapper. It does not re-implement detection, ranking, or reporting — all of that is `rrd-audit-all`'s responsibility, invoked here as a sub-step.

---

## INITIALIZATION SEQUENCE

Load `{skill-root}/steps/step-01-run-and-gate.md` directly — no separate config-loading step beyond what activation already resolved.
