# Data Flow Contract: Refactor Radar Audit-All Pipeline

## Overview

Each step reads from its predecessor's output, processes, and writes structured JSON to pass forward.

All interchange files stored in: `{project-root}/.refactor-radar-work/`

---

## Data Flow

```
step-02: Run Detectors
  → writes: findings.json (Finding[])

step-02b: Evidence Fusion
  → reads: findings.json
  → writes: correlations.json (Correlation[])

step-02c: Opportunity Engine
  → reads: findings.json + correlations.json
  → writes: opportunities.json (Opportunity[])

step-02d: Impact Analysis
  → reads: opportunities.json
  → writes: opportunities.json (updated with impact metrics)

step-02e: Ranking
  → reads: opportunities.json
  → writes: opportunities.json (updated with priority scores)

step-03: Report Rendering
  → reads: findings.json + opportunities.json
  → writes: HTML report + diffs to {project-root}/proposals/
```

---

## Data Structures

### Finding (step-02 output)

```json
{
  "id": "DI-014",
  "detector_family": "dependencies",
  "file": "LoginPage.java",
  "line": 42,
  "title": "Duplicated wait-for-element in login interaction",
  "description": "Multiple waits are duplicated across login(), authenticateUser(), and validateAuth().",
  "evidence": {
    "grep_pattern": "wait.*username-field",
    "matches_found": 3,
    "files_affected": ["LoginPage.java"],
    "code_graph_callees": "LoginPage.login → wait → authenticateUser → wait"
  },
  "confidence": 0.85,
  "affected_target": "LoginPage.java",
  "root_cause_signals": ["duplication", "timing-sensitivity"]
}
```

### Correlation (step-02b output)

```json
{
  "finding_ids": ["DI-014", "DU-032"],
  "strength": "strong",
  "evidence": {
    "same_target": "LoginPage.java",
    "graph_relationships": ["direct call path"],
    "root_cause_overlap": ["duplication"],
    "execution_evidence": null
  },
  "reasoning": "Same target + graph relationship + shared root cause",
  "metadata": {
    "correlation_type": "strong_coupling"
  }
}
```

### Opportunity (step-02c output, enriched by 02d, ranked by 02e)

```json
{
  "id": "OPP-001",
  "title": "Stabilize LoginPage interaction layer",
  "description": "Duplicated interaction logic and fragile selectors creating systemic test instability",
  "problem_statement": "LoginPage has duplicated interaction logic and fragile selectors, creating systemic test instability",
  "supporting_findings": ["DI-014", "DU-032", "DI-089"],
  "correlations": [
    {"finding_ids": ["DI-014", "DU-032"], "strength": "strong"},
    {"finding_ids": ["DU-032", "DI-089"], "strength": "medium"}
  ],
  "root_causes": ["duplication", "fragile selectors"],
  "affected_components": {
    "files": ["LoginPage.java", "AuthHelper.java"],
    "classes": 3,
    "methods": 5,
    "tests": 7
  },
  "impact": {
    "direct": {"count": 3},
    "transitive": {"weighted_high_confidence": 24},
    "hop_breakdown": {"hop_1": 8, "hop_2": 15, "hop_3": 10}
  },
  "risk": {
    "blast_radius": "medium",
    "complexity": "medium",
    "level": "medium"
  },
  "confidence": {
    "finding_consensus": 0.85,
    "evidence_strength": 0.89,
    "execution_corroboration": 0.82,
    "overall": 0.86
  },
  "effort": {
    "strategy": "A",
    "strategy_reason": "Recommendation rewrites LoginPage.java's interaction methods wholesale (Strategy A, whole-file-scope), not a handful of isolated call sites — see opportunity-grouping-and-impact-heuristics.md Part 5.",
    "estimated_lines": 150,
    "scope": "moderate",
    "regression_risk": 0.6
  },
  "priority": {
    "raw_score": 5.16,
    "run_min_raw": 0.09,
    "run_max_raw": 5.16,
    "score": 100,
    "level": "critical",
    "justification": "impact_score=(3 direct + 24 high-confidence transitive) x 1.0 execution weight [3 flaky tests corroborated] = 27; x confidence_ratio 0.86 / (effort_score 3.0 x risk_factor 1.5) = raw_score 5.16. Normalized against this run's range (0.09-5.16) -> priority_score=100 -> Critical, rank #1. Score is run-relative, not an absolute threshold — see opportunity-grouping-and-impact-heuristics.md Part 6."
  },
  "recommendation": "Extract LoginPage.waitForElement(selector, timeout) helper; consolidate duplicated implementations",
  "metadata": {
    "rank": 1,
    "created_by": "step-02c",
    "updated_by": "step-02d, step-02e"
  }
}
```

---

## Step Integration Points

### Step-02 (Run Detectors) → Finding[]

**Output:**
```bash
write {project-root}/.refactor-radar-work/findings.json
```

Format: JSON array of Finding objects. One per detector output.

### Step-02b (Evidence Fusion) → Correlation[]

**Input:**
```bash
read {project-root}/.refactor-radar-work/findings.json
```

**Output:**
```bash
write {project-root}/.refactor-radar-work/correlations.json
```

### Step-02c (Opportunity Engine) → Opportunity[]

**Input:**
```bash
read {project-root}/.refactor-radar-work/findings.json
read {project-root}/.refactor-radar-work/correlations.json
```

**Output:**
```bash
write {project-root}/.refactor-radar-work/opportunities.json
```

### Step-02d (Impact Analysis) → Opportunity[] (enriched)

**Input:**
```bash
read {project-root}/.refactor-radar-work/opportunities.json
```

**Output:**
```bash
write {project-root}/.refactor-radar-work/opportunities.json (overwrite)
```

### Step-02e (Ranking) → Opportunity[] (sorted)

**Input:**
```bash
read {project-root}/.refactor-radar-work/opportunities.json
```

**Output:**
```bash
write {project-root}/.refactor-radar-work/opportunities.json (overwrite)
```

Opportunities sorted by priority.score (descending).

**Note:** `priority.score` is computed by normalizing `priority.raw_score` against `run_min_raw`/`run_max_raw` across every opportunity produced *in this run* — it is not an absolute score. Comparing a 0-100 confidence value directly against small effort/risk numbers saturates almost every real opportunity to 100/Critical (see opportunity-grouping-and-impact-heuristics.md Part 6). Always read `raw_score` + the run's min/max alongside `score` when auditing a report.

### Step-03 (Report Rendering) → HTML + Diffs

**Input:**
```bash
read {project-root}/.refactor-radar-work/findings.json
read {project-root}/.refactor-radar-work/opportunities.json
```

**Output:**
```bash
write {project-root}/proposals/refactor-radar-audit-{project}-{date}.html
write {project-root}/proposals/{target}.{finding_id}.patch (per finding)
```

---

## Cleanup

After step-03 completes, `.refactor-radar-work/` can be deleted or kept for audit/debugging.

```bash
rm -rf {project-root}/.refactor-radar-work/
```

---

## Error Handling

If any step fails to read expected input:
1. Emit clear error: "Missing input: {file} required by {step}"
2. Halt and do not proceed
3. User must re-run step that produces the missing output

If any step fails to write output:
1. Emit clear error: "Failed to write output: {file} in step {step}"
2. Halt and do not proceed
3. User can investigate or retry
