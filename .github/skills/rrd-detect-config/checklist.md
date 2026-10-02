# Detect Config Validation Checklist

## Prerequisites

- [ ] `code-graph status` run; index confirmed present and not stale (re-indexed first if needed)
- [ ] Target project confirmed indexed
- [ ] Knowledge fragment loaded: `evidence-and-diff-discipline.md`
- [ ] Knowledge fragment loaded: `detect-config.md`
- [ ] Knowledge fragment loaded: `detect-report-template.md`

**Halt if missing:** target project not indexed.

## Investigation

- [ ] Graph/code searched for hardcoded URLs, credentials, secrets, and API keys
- [ ] Timeout/wait literals checked for inline hardcoding vs. externalized config
- [ ] Environment-switch logic (DEV/QA/REC/PROD) checked for inline branching vs. centralized config
- [ ] Parallel-execution config checked for forced-serial settings used as a coupling workaround
- [ ] Cross-referenced any forced-serial finding against `rrd-detect-dependencies` output where available, rather than treating it as a standalone config issue
- [ ] Confidence distinguishes "config smell, never caused an incident" from "confirmed cross-environment leak/collision"

## Findings and Proposals

- [ ] Every finding names the exact hardcoded value or inline logic and its location
- [ ] Every finding cites its graph/code-search evidence
- [ ] Every fix externalizes the value into config, or centralizes the environment-switch logic
- [ ] Every fix written as a diff to the target project's `proposals/`

## Completion Criteria

- [ ] Findings summary written to `{rrd_artifacts}/detect-config-{target_project}.md`
- [ ] All diff proposals written to the target project's `proposals/`, listed in the summary
