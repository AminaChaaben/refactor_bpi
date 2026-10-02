# Opportunity and Impact Data Model

Definitions for Phase 3 (Opportunity Engine + Impact Analysis).

---

## Opportunity (Primary Reporting Unit)

Produced by the Opportunity Engine, enriched by Impact Analysis.

```
Opportunity {
  id: string                      // unique, e.g. "OPP-001"
  title: string                   // human-readable, e.g. "Stabilize LoginPage interaction layer"
  description: string             // paragraph explaining the problem and why it matters
  
  problem_statement: string       // one-sentence root cause, e.g. "LoginPage has duplicated interaction logic and fragile selectors"
  
  supporting_findings: Finding[]  // raw findings from detectors (immutable references)
  correlations: Correlation[]     // Correlation[] entries linking these findings
  
  root_causes: {
    primary: string               // e.g. "duplicated_logic"
    secondary: string[]           // e.g. ["fragile_selector", "timing_dependency"]
  }
  
  affected_components: {
    files: string[]               // e.g. ["LoginPage.java", "AuthHelper.java"]
    classes: string[]             // e.g. ["LoginPage", "AuthHelper"]
    methods: string[]             // e.g. ["LoginPage.login()", "LoginPage.authenticateUser()"]
    tests: string[]               // e.g. ["LoginTest.java::test_login_success", ...]
    test_plans: string[]          // if available
    entry_points: string[]        // main entry points affected
  }
  
  impact: {
    // Direct impact: immediate dependencies/callers
    direct: {
      count: number               // e.g. 10
      components: string[]        // which components depend on this
      description: string         // e.g. "directly called by AuthService, AuthUI, SessionManager"
    }
    
    // Transitive impact: reachable via 2-3 hops
    transitive: {
      hop_1_count: number
      hop_2_count: number
      hop_3_count: number
      
      // Weighted by: distance decay, relationship type, usage frequency, test involvement, centrality
      weighted_high_confidence: number  // filtered set, only high-impact paths
      description: string               // e.g. "80 reachable components, but only 24 with high-confidence impact"
    }
    
    // Evidence of real impact
    execution_evidence: {
      flaky_test_count: number    // # of tests known to fail due to this
      failure_frequency: number   // % of test runs affected
      cascade_failures: number    // # of tests failing as consequence of this issue
    }
  }
  
  risk: {
    blast_radius: string          // "Small" | "Medium" | "Large"  (based on affected count)
    complexity: string            // "Low" | "Medium" | "High" (based on coupling depth)
    level: string                 // "Low" | "Medium" | "High" (combination)
    justification: string         // why this risk level
  }
  
  confidence: {
    finding_consensus: number     // 0-100, how many findings agree on root cause
    evidence_strength: number     // 0-100, weighted by correlation strength
    execution_corroboration: number // 0-100, does execution data support this?
    overall: number               // 0-100, final confidence
  }
  
  effort: {
    estimated_lines_to_change: number
    refactor_scope: string        // "Isolated" | "Moderate" | "Large"
    risk_of_regression: string    // "Low" | "Medium" | "High"
    estimated_effort: string      // "Small" | "Medium" | "Large"
  }
  
  priority: {
    score: number                 // 0-100, calculated from impact × confidence / (effort × risk)
    level: string                 // "Critical" | "High" | "Medium" | "Low"
    justification: string         // why this priority
  }
  
  recommendation: {
    action: string                // e.g. "Extract LoginPage.waitForElement() helper; consolidate to single wait implementation"
    affected_areas: string[]      // which files/classes will change
    estimated_reduction: string   // e.g. "50 lines removed, 8 duplications consolidated"
    tests_affected: number        // how many tests might be affected by this refactor
  }
  
  metadata: {
    created_at: string            // ISO timestamp
    phase_3_run: string           // audit run ID
    relationship_to_other_opportunities: string[] // references to related OPP-IDs
  }
}
```

### Example Opportunity

```json
{
  "id": "OPP-001",
  "title": "Stabilize LoginPage interaction layer",
  "description": "LoginPage contains duplicated wait logic, fragile selectors, and timing dependencies that are causing test flakiness. Multiple tests fail due to timeouts and missing elements. The root cause is 3x duplicated interaction patterns that should be consolidated into robust helper methods.",
  "problem_statement": "LoginPage has duplicated interaction logic and fragile selectors, creating systemic test instability",
  "supporting_findings": [
    {
      "id": "DI-014",
      "detector_family": "instability",
      "title": "Duplicated wait-for-element in login interaction",
      "confidence": "high"
    },
    {
      "id": "DU-032",
      "detector_family": "duplication",
      "title": "Duplicated interaction implementation",
      "confidence": "high"
    },
    {
      "id": "DI-089",
      "detector_family": "instability",
      "title": "Fragile selector in authentication flow",
      "confidence": "medium"
    }
  ],
  "correlations": [
    {
      "finding_ids": ["DI-014", "DU-032"],
      "strength": "strong"
    },
    {
      "finding_ids": ["DU-032", "DI-089"],
      "strength": "medium"
    }
  ],
  "root_causes": {
    "primary": "duplicated_logic",
    "secondary": ["fragile_selector", "timing_dependency"]
  },
  "affected_components": {
    "files": ["tests/e2e/LoginPage.java", "tests/e2e/AuthHelper.java"],
    "classes": ["LoginPage", "AuthHelper"],
    "methods": ["LoginPage.login()", "LoginPage.authenticateUser()", "AuthHelper.validate()"],
    "tests": ["LoginTest::test_login_success", "LoginTest::test_login_invalid_creds", "AuthTest::test_auth_flow"],
    "test_plans": ["e2e-smoke", "e2e-regression"],
    "entry_points": ["LoginPage"]
  },
  "impact": {
    "direct": {
      "count": 3,
      "components": ["LoginTest", "AuthTest", "UIHelper"],
      "description": "directly call LoginPage.login() and LoginPage.authenticateUser()"
    },
    "transitive": {
      "hop_1_count": 8,
      "hop_2_count": 23,
      "hop_3_count": 42,
      "weighted_high_confidence": 12,
      "description": "42 reachable components, but only 12 have high-confidence impact (weighted by distance and usage frequency)"
    },
    "execution_evidence": {
      "flaky_test_count": 7,
      "failure_frequency": 23,
      "cascade_failures": 4
    }
  },
  "risk": {
    "blast_radius": "Medium",
    "complexity": "Medium",
    "level": "Medium",
    "justification": "Affects 3 direct callers, 12 high-confidence transitive; moderate complexity (consolidating 3 duplications, 2 files)"
  },
  "confidence": {
    "finding_consensus": 94,
    "evidence_strength": 85,
    "execution_corroboration": 78,
    "overall": 86
  },
  "effort": {
    "estimated_lines_to_change": 47,
    "refactor_scope": "Moderate",
    "risk_of_regression": "Medium",
    "estimated_effort": "Medium"
  },
  "priority": {
    "score": 72,
    "level": "High",
    "justification": "High impact (test flakiness affecting 7+ tests) + high confidence (3 findings, 86%) / medium effort → High priority. This refactoring would stabilize 23% of flaky test suite."
  },
  "recommendation": {
    "action": "Extract LoginPage.waitForElement(selector, timeout) helper; consolidate duplicated implementations. Consolidate fragile selectors into stable page-object patterns.",
    "affected_areas": ["LoginPage.java", "AuthHelper.java"],
    "estimated_reduction": "47 lines removed, 3 duplications consolidated, 2 fragile selectors replaced",
    "tests_affected": 7
  },
  "metadata": {
    "created_at": "2026-08-10T14:35:00Z",
    "phase_3_run": "audit-2026-08-10",
    "relationship_to_other_opportunities": ["OPP-003 (related: test data lifecycle)"]
  }
}
```

---

## Impact Object (Detailed Graph Traversal)

Used internally during Phase 4 (Impact Analysis) to calculate affected components.

```
Impact {
  target_node: string             // the primary code entity being analyzed
  
  direct_impact: {
    immediate_callers: string[]   // direct fan-in
    immediate_callees: string[]   // direct fan-out
    fan_in_count: number
    fan_out_count: number
  }
  
  transitive_impact: {
    hop_1: {
      nodes: string[]
      count: number
      weight_sum: number          // accumulated weight (distance decay)
    },
    hop_2: {
      nodes: string[]
      count: number
      weight_sum: number
    },
    hop_3: {
      nodes: string[]
      count: number
      weight_sum: number
    }
  }
  
  weighting_factors: {
    distance_decay: number        // 1.0 for hop 1, 0.7 for hop 2, 0.4 for hop 3
    relationship_types: {
      "call": 1.0,                // direct function/method call
      "import": 0.8,              // module/file import
      "data_flow": 0.6,           // data dependency
      "semantic": 0.3             // conceptually related
    },
    usage_frequency: number|null  // if available from execution logs
    test_involvement: number|null // # of tests that exercise this path
    centrality_score: number      // graph centrality (how hub-like this node is)
  }
  
  affected_totals: {
    files_affected: string[]
    classes_affected: string[]
    methods_affected: string[]
    tests_affected: string[]
    high_confidence_count: number // after filtering by weight
  }
}
```

---

## Priority Calculation

```
priority_score = (impact_score × confidence_score) / (effort_score × risk_score)

Where:
  impact_score = (direct_impact_count + weighted_transitive_count) × execution_evidence_weight
  confidence_score = 0-100 (finding consensus + evidence strength + execution corroboration)
  effort_score = 1-100 (lines to change, refactor scope, regression risk)
  risk_score = 1-100 (blast radius, complexity)

Result: 0-100 score
  90-100 → Critical
  70-89  → High
  40-69  → Medium
  0-39   → Low
```

---

## Constraints and Design Principles

1. **Every opportunity must have at least one high-confidence finding.** Weak correlations alone do not create opportunities.

2. **Affected components are verified, not inferred.** Graph traversal confirms reachability; we do not assume.

3. **Weighted transitive impact prevents inflated scores.** 80 reachable nodes, weighted down to 12 high-confidence, prevents false positives.

4. **Execution evidence is the strongest signal.** If tests are actually failing due to this issue, that corroborates the opportunity and increases priority.

5. **Opportunities can be related but remain separate.** OPP-001 and OPP-002 may be part of the same refactoring campaign, but each is independently scored.

6. **Priority is transparent.** The score and justification are always visible so a reviewer can understand why this opportunity ranks above others.
