# Finding and Correlation Data Model

Definitions for Phase 1 (Data Model) and Phase 2 (Evidence Fusion) of the Opportunity Engine pipeline.

---

## Finding (Raw Output from Detectors)

Produced by DD/DI/DT/DU detectors, **never modified**.

```
Finding {
  id: string                    // unique per detector run, e.g. "DI-014"
  detector_family: string       // "dependencies" | "instability" | "data_issues" | "duplication" | "complexity"
  file: string                  // absolute or relative path
  line: number                  // start line (optional, -1 if not applicable)
  title: string                 // one-sentence finding title
  description: string           // paragraph or short prose
  evidence: string              // citation of graph query/log/code evidence
  confidence: string            // "high" | "medium" | "low"
  recommendation: string        // proposed fix
  affected_target: string       // primary code entity (class, function, file, component)
  root_cause_signals: string[]  // classifier tags, e.g. ["shared_state", "fragile_selector", "lifecycle_gap", "redundancy"]
}
```

### Example: DI-014 (Detect Instability)
```json
{
  "id": "DI-014",
  "detector_family": "instability",
  "file": "tests/e2e/LoginPage.java",
  "line": 47,
  "title": "Duplicated wait-for-element in login interaction",
  "description": "LoginPage.login() waits for '.username-field' using a fixed 2-second timeout. The same wait is repeated in authenticateUser() at line 82, and both assume the field appears within 2 seconds. This is fragile and duplicated.",
  "evidence": "Grep 'wait.*username-field' found 3 matches in LoginPage.java; code-graph query callees (LoginPage.login, depth 3) confirms all three in the login flow",
  "confidence": "high",
  "recommendation": "Extract wait logic into a helper, e.g., LoginPage.waitForUsernameField(), and use in both places.",
  "affected_target": "LoginPage.java",
  "root_cause_signals": ["fragile_selector", "duplicated_logic", "fixed_timeout"]
}
```

---

## Correlation (Metadata Linking Multiple Findings)

Produced by **Evidence Fusion phase only**. Explicitly links findings that appear to represent the same underlying problem.

```
Correlation {
  finding_ids: string[]         // e.g., ["DI-014", "DU-032"]
  strength: string              // "strong" | "medium" | "weak"
  
  evidence: {
    shared_target: string|null  // common file/class/component, e.g., "LoginPage.java"
    graph_relationships: string[] // e.g., ["LoginPage → AuthHelper (direct call)"]
    root_cause_overlap: string|null // shared root-cause signal, e.g., "duplicated_logic"
    code_path_intersection: string|null // e.g., "both in login() call chain"
    execution_evidence: string|null // log/trace reference if available
    semantic_relationship: string|null // similarity score or conceptual link
    cross_family_corroboration: string[]|null // detector_family values, e.g. ["dependencies", "complexity"], when the SAME exact target was independently flagged by different families (evidence-fusion-heuristics.md Strong Rule 4)
  }
  
  reasoning: string             // one-sentence explanation of why these are correlated
  
  metadata: {
    fusion_phase_run: string    // ISO timestamp
    confidence_justification: string // why this strength, not higher/lower
  }
}
```

### Example: Correlation DI-014 ↔ DU-032
```json
{
  "finding_ids": ["DI-014", "DU-032"],
  "strength": "strong",
  "evidence": {
    "shared_target": "LoginPage.java",
    "graph_relationships": [
      "LoginPage contains both wait logic (DI-014) and interaction duplication (DU-032)"
    ],
    "root_cause_overlap": "duplicated_logic",
    "code_path_intersection": "both in LoginPage.login() → authenticateUser()",
    "execution_evidence": null,
    "semantic_relationship": null
  },
  "reasoning": "Both findings identify overlapping duplicated logic in the same class; graph analysis shows they manifest in the same code path.",
  "metadata": {
    "fusion_phase_run": "2026-08-10T14:30:00Z",
    "confidence_justification": "Strong because both shared_target AND root_cause_overlap AND code_path conditions met; semantic similarity was not needed."
  }
}
```

---

## Correlation Confidence Hierarchy

### Strong Correlation
Findings represent the **same underlying problem**.

Triggered when **all of**:
- Same affected target (file, class, component)
- **AND** one of:
  - Same code path in graph (both reachable through direct call sequence)
  - Same root-cause signal (shared `root_cause_signals` entry)
  - Execution evidence shows both involved in same failure
  - **Different `detector_family` values independently flagging the exact same entity** (e.g. Detect Dependencies and Detect Complexity both landing on the same function) — see evidence-fusion-heuristics.md Strong Rule 4. This one does NOT require a shared `root_cause_signals` entry; the methodological independence of the two detectors is itself the evidence.

**Reasoning:** The findings are not independent; fixing one likely fixes the other. For the cross-family case specifically: two structurally unrelated detection methods reaching the same conclusion is stronger corroboration than either alone, even without a shared tag.

### Medium Correlation
Findings represent **related problems in the same area**.

Triggered when:
- Same component **OR**
- Graph neighbor (direct dependency)
- Same root-cause family (both DI, both DU, etc.)

**Reasoning:** Likely part of the same refactoring scope, but not certain to be identical.

### Weak Correlation
Findings have **conceptual or semantic similarity only**.

Triggered when:
- Semantic similarity score > threshold (e.g., embedding distance)
- **Only supported by other correlations**, never creates one by itself

**Reasoning:** Semantic similarity may indicate related problems, but without structural evidence it is not sufficient. Weak correlations require at least one Medium or Strong correlation to be included in an opportunity group.

---

## Correlation Constraints (Conservative Design)

1. **Semantic similarity does not create correlations.** It may strengthen existing Strong/Medium correlations, but a finding with only semantic similarity to others is not automatically grouped.

2. **False correlations are preferable to aggressive grouping.** If uncertain, do not correlate. Better to have 10 standalone findings than 1 incorrect opportunity grouping.

3. **Raw findings are immutable.** Correlation metadata is separate and can be discarded without loss of detector output integrity.

4. **Each correlation must be explainable.** A human reviewer should be able to read the `evidence` and `reasoning` fields and understand why the findings were grouped.

5. **Root-cause overlap is not sufficient alone.** "Both DU" is a correlation trigger only if they share a target or graph relationship. DU findings in unrelated components do not correlate just because they are both duplication.

---

## Downstream: Correlation Groups

After Evidence Fusion produces Correlation[], the Opportunity Engine (Phase 3) will consume both:

- Raw Finding[]
- Correlation[]

And produce:

- Opportunity[] (one or more findings grouped by correlation strength and graph distance)

Example:

```
Finding: DI-014, DU-032, DD-008
Correlation: DI-014 ↔ DU-032 (strong), DU-032 ↔ DD-008 (medium)
Grouping: {DI-014, DU-032, DD-008} → Opportunity "Stabilize LoginPage interaction layer"
```

The Opportunity Engine will use correlation metadata to decide grouping, but also consider:
- Graph reachability
- Blast radius (phase 4)
- Semantic convergence

For now (Phase 1+2), the Correlation[] is the output. The Opportunity Engine is not built yet.
