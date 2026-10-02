# Refactor Radar: Workflow Category Picker

**Use this in step-01 to help humans choose the right workflow for their situation.**

---

## Three Workflows, Three Questions You're Asking

### **1. Full Refactor (Code-Based)**

**The Question You're Asking:**
> "What structural problems does our codebase have? Where should we refactor?"

**What You'll Learn:**
- Which files are the most complex/risky to maintain
- Where code is duplicated (copy-paste smell)
- Hardcoded config, secrets, or environment switches
- Tests that are fragile (bad selectors, fixed waits)
- Missing logging that would block future debugging
- Architecture gaps (wrong layers, mixed responsibilities)

**Decision You Can Make:**
- Prioritize refactoring work: "Fix this hotspot first, it affects the most code"
- Plan architecture: "These tests live in the wrong layer, let's move them"
- Estimate effort: "This duplication is worth a dedicated fix sprint"

**Who Cares:**
- Tech leads planning a refactor roadmap
- Architects auditing code organization
- Teams deciding "what should we touch first?"

**Time:** 5-15 minutes (runs once, outputs ranked report)

**Prerequisites:** Codebase indexed (automatic, requires one-time setup)

---

### **2. Log-Based (Execution-Based)**

**The Question You're Asking:**
> "Why are our tests flaky? Which test failures are real problems vs. false signals?"

**What You'll Learn:**
- Which tests fail consistently (real bugs)
- Which tests fail intermittently (flakiness, timing issues, environment sensitivity)
- Which tests pass but don't actually check anything (false negatives)
- Common failure patterns: "This always fails when tests run in parallel" or "This fails on Fridays"
- Why failures happen: app bug, environment issue, bad test data, or test code bug

**Decision You Can Make:**
- Focus on flaky tests: "Here are the 3 tests we should stabilize this sprint"
- Understand root cause: "This fails because of a race condition, not bad code"
- Separate signal from noise: "These 5 test failures are environmental, not code"
- Plan test infrastructure fixes: "We need better test data isolation"

**Who Cares:**
- QA leads tracking test reliability
- Teams frustrated with "sometimes it passes, sometimes it doesn't"
- Anyone shipping code and nervous about test coverage

**Time:** 1-2 hours (includes running test suite 3x, analyzing logs)

**Prerequisites:** Working test suite, ability to run tests, logs in standard format

---

### **3. Combined (Code + Execution)**

**The Question You're Asking:**
> "What are our top refactoring priorities, backed by proof that these issues cause real test failures?"

**What You'll Learn:**
- Everything from Full Refactor (#1)
- Everything from Log-Based (#2)
- Which code problems actually show up as test failures (vs. structural issues that don't matter yet)
- Confidence ranking: "This complexity hotspot failed real tests 7 times" vs. "This duplication exists but tests pass"

**Decision You Can Make:**
- Make a business case: "Yes, this refactor is urgent—it's causing 30% of test failures"
- Deprioritize: "This architectural issue is real but not causing problems yet; do it later"
- Allocate resources: "Spend time on the 2 hotspots that break tests, skip the others"
- Prove ROI: "After we fix these, test reliability improved from 85% to 98%"

**Who Cares:**
- Engineering managers justifying refactor budgets to leadership
- Teams wanting to fix the "right problems first"
- Anyone shipping with confidence (need to know code quality matters)

**Time:** 1-2 hours (includes Full Refactor + Log-Based)

**Prerequisites:** All of Full Refactor + all of Log-Based

---

## Decision Tree: Which One Do You Need?

```
Are you shipping code soon?
├─ YES → "Is the codebase stable right now?"
│        ├─ NO (flaky tests, crashes) → Use LOG-BASED
│        │                              "Why are tests failing?"
│        └─ YES → Use FULL REFACTOR
│                 "What should we improve next?"
│
└─ NO (planning phase) → "Do you have test execution data?"
                        ├─ YES → Use COMBINED
                        │        "What problems cause real failures?"
                        └─ NO → Use FULL REFACTOR
                                "What's the overall state of code?"
```

---

## Real-World Scenarios

| Scenario | Choose | Why |
|----------|--------|-----|
| "Tests pass locally but fail in CI randomly" | Log-Based | You need to understand the failure pattern, not code structure |
| "We have 6 months to refactor before a major rewrite" | Combined | Time to be thorough; want decisions backed by evidence |
| "New developer asking 'where should I start improving things?'" | Full Refactor | Quick overview of architecture + hotspots |
| "QA says 'half our tests are unreliable'" | Log-Based | Need to classify what's flaky vs. real |
| "Planning a 2-week sprint, what's worth fixing?" | Full Refactor | Fast audit + prioritized list of 5-10 fixes |
| "Executive asking 'is this codebase maintainable?'" | Combined | Highest confidence, backed by execution evidence |
| "We just refactored X; did it help?" | Log-Based | Compare test reliability before/after |

---

## Quick Comparison Table

| | Full Refactor | Log-Based | Combined |
|---|---|---|---|
| **Best Answer For** | Structure & architecture | Test reliability & flakiness | Everything, highest confidence |
| **Speed** | 5-15 min | 1-2 hours | 1-2 hours |
| **Decision Type** | "What to refactor" | "Why tests fail" | "What to refactor + proof" |
| **Audience** | Tech leads, architects | QA, test engineers | Engineering leadership |
| **Typical Output** | "Fix these 5 hotspots" | "These 3 tests are flaky" | "Rank-1: fix this (causes 40% failures)" |

---

## Status Check Template

**For step-01 to fill in and present:**

```
📋 Workflow Category Availability Check

Checking prerequisites...

✅ Full Refactor (Code-Based)
   ├─ Codebase indexed: YES
   ├─ Status: AVAILABLE
   ├─ Time: ~7 minutes
   └─ Runs all 10 detectors against source code

❌ Log-Based (Execution-Based)
   ├─ Test suite present: YES
   ├─ Can run tests: YES
   ├─ Can capture logs: NO
   ├─ Blocker: Logs not in standard format (have: console output, need: JUnit/Surefire XML or Playwright JSON)
   └─ Status: BLOCKED

❌ Combined (Code + Execution)
   └─ Status: BLOCKED (Log-Based prerequisite failed)

---

Available categories: [ 1 ]

Which do you want?
  1 = Full Refactor only
  (2 and 3 are blocked — see details above)
```

---

## Blocker Explanations

**"Codebase not indexed"**
- Fix: Run `code-graph index --wait 600` in the project's sandbox
- Time: 1-5 min depending on size

**"Test suite doesn't exist / can't run"**
- Why: Can't verify test pass/fail
- Fix: Set up test runner (mvn test, pytest, etc.) and verify at least one test runs
- Time: varies

**"Logs not capturable"**
- Why: Need logs in standard format (JUnit/Surefire XML, Playwright JSON, Jenkins console)
- Current: {actual format found, if any}
- Fix: Configure test runner to output logs in one of the standard formats
- Time: 15-30 min usually

**"Test suite present but very slow / would take hours"**
- Note: Log-Based is accurate but slow by design (needs real test runs)
- Suggestion: Run Full Refactor first while you wait; run Log-Based overnight
- Your call: proceed with Full Refactor alone? (1) or wait? (proceed when ready)

---

## Human's Next Step

Once they pick a category:
1. If Full Refactor: Proceed to step-02 (run all 10 detectors)
2. If Log-Based: Launch `rrd-establish-execution-baseline` instead (this skill pauses)
3. If Combined: Launch `rrd-establish-execution-baseline` first, then call back to this skill

---

## Agents: Reference This in Step-01

In `step-01-preflight-and-init.md`, after checking prerequisites, include:

```markdown
[See CATEGORY_PICKER.md for status check template, blocker explanations, and detailed comparison table]
```

This keeps the logic centralized and avoids duplicate prose in every skill.
