# Evidence Fusion Heuristics

How to detect when multiple findings represent the same underlying problem, organized by correlation strength.

---

## Strong Correlation Detection

### Rule 1: Same Target + Graph Relationship

**Condition:**
- Two findings have the same `affected_target` (same file, class, or component)
- Query the graph: do they share a direct call path or data dependency?

**Example:**
```
DI-014: LoginPage.java, line 47
  title: "Duplicated wait-for-element"
  affected_target: "LoginPage.java"
  
DU-032: LoginPage.java, line 82
  title: "Duplicated interaction logic"
  affected_target: "LoginPage.java"

graph_relationship: 
  code-graph query callees --param qualified_name=LoginPage.login --param depth=3   # authenticateUser in the result
  = true (direct call)

Result: STRONG correlation
Evidence: "Same target LoginPage.java + direct call relationship"
```

**Implementation:**
1. For each finding pair, check if `affected_target` matches exactly.
2. If yes, run `code-graph query symbol --param qualified_name={affected_target}` to confirm it's a single entity in the graph.
3. Run `code-graph query callers` / `callees` on `{affected_target}` (`--param depth=3`) to check if both findings are in the same call chain.
4. If call chain exists, mark as Strong.

---

### Rule 2: Same Target + Same Root Cause Signal

**Condition:**
- Two findings have the same `affected_target`
- Their `root_cause_signals` arrays contain a common entry

**Example:**
```
DI-047: FragileSelector.java
  root_cause_signals: ["fragile_selector", "timing_dependency"]
  
DI-089: FragileSelector.java
  root_cause_signals: ["timing_dependency", "missing_wait"]

overlap: "timing_dependency"

Result: STRONG correlation
Evidence: "Same target + shared root cause: timing_dependency"
```

**Implementation:**
1. After finding same target, check `root_cause_signals` intersection.
2. If non-empty, mark as Strong.
3. Record the shared signal(s) in the Correlation evidence.

---

### Rule 4: Same Exact Entity, Different Detector Families (Cross-Family Corroboration)

**Condition:**
- Two findings have the *exact same* `affected_target` (the identical function/method/class, not just the same file or component)
- The findings come from **different `detector_family` values** (e.g. one from `dependencies`, one from `complexity`)

**Example:**
```
DI-002: agents/base.py, TestAgent.generate_detailed_test_cases
  detector_family: "dependencies"
  evidence: confirmed caller of FileOnlyLogger.write (code-graph query callers)

DC-005: agents/base.py, TestAgent.generate_detailed_test_cases
  detector_family: "complexity"
  evidence: cognitive=41, confirmed via code-graph query symbol

Result: STRONG correlation
Evidence: "Same exact function independently flagged by two different detection methodologies"
```

**Implementation:**
1. Group findings by exact `affected_target` (not just file/component — the identical qualified name).
2. If two findings on the same exact target come from different `detector_family` values, mark as Strong regardless of whether they share a `root_cause_signal` — two independent detection methods agreeing is stronger evidence than a shared tag would be.
3. Record both findings' evidence verbatim in the correlation so a reviewer sees both signals side by side.

**Rationale:** Unlike Rule 1 (same target + graph relationship between *different* lines/entities) or Rule 2 (same target + shared signal), this rule fires when the target is *identical* and the signals are *methodologically independent* — a dependency-coupling detector and a complexity detector reaching the same target by completely different evidence paths is a materially stronger corroboration than either finding alone, and stronger than two findings from the *same* family sharing a tag. This is the most common source of Strong correlations once Detect Complexity is in the mix, since it's the one detector whose evidence (graph-computed metrics) is structurally unrelated to the other four's (pattern/coupling/lifecycle matching).

---

### Rule 5: Same Code Path + Execution Evidence

**Condition:**
- Two findings are in the same `file`
- Execution logs show both involved in the same failure
- OR test evidence shows both triggered by the same test run

**Example:**
```
DI-014: tests/LoginTest.java, line 47
DI-089: tests/LoginTest.java, line 71

execution_logs contain:
  2026-08-10 14:15:32 [LoginTest.run()] FAIL: timeout at wait-element (line 47)
  2026-08-10 14:15:33 [LoginTest.run()] FAIL: missing-element (line 71)

Result: STRONG correlation
Evidence: "Same test run failed at both lines; likely cascade"
```

**Implementation:**
1. The code graph has no trace ingestion (the earlier backend's `ingest_traces`
   has no replacement -- see rrd-analyze-test-reliability's analyze-test-reliability.md).
   This rule instead reads execution logs directly (Read/Grep/Bash, same as that
   workflow's own log parsing) rather than via any graph tool call:
   - Parse logs for failure co-occurrence (same test, same run, time-adjacent).
   - If found, mark as Strong.
2. Otherwise, this rule does not trigger.

---

## Medium Correlation Detection

### Rule 1: Same Component (Direct)

**Condition:**
- Two findings affect the same file, class, or module
- No graph relationship required (they may not call each other directly)

**Example:**
```
DI-014: LoginPage.java, line 47
DU-032: LoginPage.java, line 82

Same target, but no direct call between the two lines.

Result: MEDIUM correlation
Evidence: "Same component LoginPage.java, but lines are not in same call chain"
```

**Implementation:**
1. Extract the component name from `affected_target` (e.g., "LoginPage.java" → "LoginPage").
2. If two findings have the same component, mark as Medium.
3. Note in evidence: "Same component, but no direct graph relationship confirmed."

---

### Rule 2: Graph Neighbors

**Condition:**
- Two findings are in different components
- The graph shows a direct edge between them (caller/callee, import, data flow)

**Example:**
```
DI-014: LoginPage.java
DU-032: AuthHelper.java

code-graph query confirms:
  LoginPage → AuthHelper (direct import)

Result: MEDIUM correlation
Evidence: "Different components, but LoginPage directly depends on AuthHelper"
```

**Implementation:**
1. For each finding pair in different targets:
   - Extract qualified names from the graph.
   - Run `code-graph query callees --param qualified_name={source} --param depth=5` and check `{destination}` is in the result.
   - If direct edge found, mark as Medium.
2. Record the edge type (call, import, data flow) in evidence.

---

### Rule 3: Same Root-Cause Family

**Condition:**
- Two findings are from the same `detector_family` (both DI, both DU, etc.)
- Different targets, no graph relationship
- **But:** their root-cause signals suggest they are manifestations of the same class of problem

**Example:**
```
DI-014: LoginPage.java
  root_cause_signals: ["fragile_selector"]

DI-089: RegistrationPage.java
  root_cause_signals: ["fragile_selector"]

Result: MEDIUM correlation
Evidence: "Both DI findings with fragile_selector signal; suggests systemic selector instability"
```

**Implementation:**
1. Group findings by `detector_family`.
2. Within each family, cluster by `root_cause_signals` overlap.
3. If two findings in the same family share a root-cause signal, mark as Medium.
4. Note: This triggers only within a detector family, not across families.

---

## Weak Correlation Detection

### Rule 1: Semantic Similarity (Requires Other Correlations to Activate)

**Condition:**
- Embedding-based or text similarity score between finding descriptions > threshold (e.g., cosine > 0.7)
- **No other correlation rule has triggered**
- Finding is NOT marked Strong or Medium by any other rule

**Example:**
```
DI-014: "Duplicated wait-for-element in login"
DI-089: "Repeated timeout check in authentication"

Semantic similarity: 0.82 (high)
No shared target, no family overlap, no graph relationship.

Result: WEAK correlation
Evidence: "Semantic similarity 0.82, but no structural evidence"
Note: This finding remains solo; weak correlations do not create groups.
```

**Implementation:**
1. Compute embeddings or use string similarity (e.g., Levenshtein distance normalized).
2. If similarity > threshold and no Strong/Medium correlation exists:
   - Record as Weak.
   - **Do not group into an opportunity.**
   - Opportunity Engine (Phase 3) may use this as supporting evidence if the findings end up in the same opportunity for other reasons.

---

## Constraints and Edge Cases

### No Cascading Grouping

```
A ↔ B (medium)
B ↔ C (medium)

Constraint: Do NOT infer A ↔ C.

Instead, record:
- Correlation(A, B)
- Correlation(B, C)
- No Correlation(A, C) unless A and C satisfy a direct rule.

The Opportunity Engine (Phase 3) will decide grouping transitively.
```

**Rationale:** Avoid false correlations through transitivity. Let the Opportunity Engine handle grouping logic.

---

### Root-Cause Family is Not Sufficient Alone

```
DU-014: LoginPage.java, "duplicated interaction"
DU-032: AuthHelper.java, "duplicated validation"

Both DU, but different files/components and no graph edge.

Result: NO correlation.
Note: "DU + DU" is not a trigger unless they share a target or graph relationship.
```

**Rationale:** Too aggressive otherwise; would group all duplication findings together.

---

### Execution Evidence is the Strongest Signal

If execution logs show two findings co-occur in the same failure, that is Strong correlation even without structural evidence.

```
DI-014: fragile selector
DD-089: order dependency

Both involved in test flake "TestSuite.run() FAIL 2026-08-10 14:15:32"
timing: 1 second apart in logs, same test case

Result: STRONG correlation
Evidence: "Co-occurrence in execution logs"
Reasoning: "Both triggered in same test failure; likely cascade effect"
```

**Implementation:** Highest priority rule. Check execution logs first.

---

## Correlation Output Format

For each correlation detected, record:

```json
{
  "finding_ids": ["DI-014", "DU-032"],
  "strength": "strong",
  "evidence": {
    "shared_target": "LoginPage.java",
    "graph_relationships": ["LoginPage.login() → LoginPage.authenticateUser()"],
    "root_cause_overlap": "duplicated_logic",
    "code_path_intersection": "both in login flow",
    "execution_evidence": null,
    "semantic_relationship": null
  },
  "reasoning": "Same class with overlapping duplication and fragility; refactoring LoginPage's interaction layer will address both.",
  "metadata": {
    "fusion_phase_run": "2026-08-10T14:30:00Z",
    "triggered_rules": ["Same Target + Graph Relationship", "Same Target + Root Cause Overlap"],
    "confidence_justification": "Strong because multiple rules fired independently."
  }
}
```

---

## Guiding Principle: Conservative Over Aggressive

- **False positive correlation:** Findings are grouped when they should be separate. Risk: Opportunity misdirection.
- **False negative correlation:** Findings are separate when they should be grouped. Risk: Missed opportunity to recognize scope.

**Design choice:** Accept false negatives. It is better to have 10 separate findings than 1 incorrect opportunity grouping. The Opportunity Engine can learn to merge related findings; false groupings are harder to disentangle.

Always prefer reporting a weak/no correlation to reporting false strong/medium correlations.
