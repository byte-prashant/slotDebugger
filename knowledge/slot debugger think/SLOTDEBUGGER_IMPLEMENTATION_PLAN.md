# Slot Debugger Implementation Plan

## Goal

Add a report-driven, specification-aware investigation planner without breaking the current `slotdebugger` commands or claiming root causes without evidence.

## Architecture

```text
Expected + actual report
        -> normalize/compare
        -> mismatch targets
        -> link expected-behavior requirements
        -> generate hypotheses + evidence checklist
        -> JSON/Markdown investigation plan
        -> human review
        -> targeted verification
        -> evidence-backed finding
```

## Phase 0 — Inspect the existing repository

**Why:** The project already has `analyze`, `diff`, `aggregate`, and `think` workflows. Reimplementing these blindly risks incompatible schemas and duplicate behavior.

Tasks:
- Inspect the repository and current `think.py`.
- Confirm actual `report.diff.json` and expected-report schemas.
- Confirm whether the executable is `slotdebug` or `slotdebugger`.
- Locate GDD/math configuration and feature specifications.
- Decide whether this starter becomes an internal module or an adapter.

Acceptance:
- Real input/output contracts documented.
- Existing command behavior unchanged.

## Phase 1 — Normalized report comparison (implemented)

**Why:** Investigation must start from a reproducible list of failing fields.

Tasks:
- Compare expected and actual dotted-path JSON maps.
- Separate missing keys, nulls, type mismatches, and numeric mismatches.
- Support absolute and relative tolerances.

Acceptance:
- Tests cover equality, tolerance, missing values, nulls, and types.
- No mismatch is labelled an engine bug.

## Phase 2 — Expected-behavior requirements (schema implemented; extraction not implemented)

**Why:** A difference is only a behavior bug if it violates the intended behavior or reporting contract.

Tasks:
- Define requirement ID, target, statement, acceptance criteria, source, and review status.
- Link requirements to report targets.
- Flag targets without a requirement as `specification_gap`.
- Require user review for ambiguous or inferred rules.

Acceptance:
- Each target exposes its specification status.
- Unreviewed requirements are not treated as authoritative.

## Phase 3 — Investigation-plan generation (implemented)

**Why:** Convert each discrepancy into a bounded investigation instead of unstructured code exploration.

Tasks:
- Suggest candidate areas from symptom categories.
- Generate readable branch IDs.
- Require expected observations and falsification conditions.
- Clearly label hypotheses as candidates.

Acceptance:
- Every target has a next action and evidence checklist.
- Branch IDs are deterministic.
- Existing `(target, cause)` deduplication remains authoritative.

## Phase 4 — Dependency graph and code provenance (data model planned, automatic discovery not implemented)

**Why:** Tracing report values to source code limits irrelevant file exploration.

Tasks:
- Connect report field -> aggregate -> tester event -> engine result field -> calculation.
- Store relation type, file/line, evidence type, and verification status.
- Add Python AST analysis for assignments, calls, and class inheritance.
- Add runtime tracing only where static analysis is insufficient.
- Mark inferred edges as unverified.

Acceptance:
- Every edge records provenance.
- Static inference is never presented as runtime-verified evidence.
- Relevant inherited methods are included where resolvable.

## Phase 5 — Feature-specific rule packs (not implemented)

**Why:** Feature knowledge should be reusable without duplicating the orchestrator.

Tasks:
- Define a common feature-rule interface.
- Start with one real feature and an approved specification.
- Encode applicable activation, state transitions, payout, multiplier, cap, retrigger, and bet restrictions.
- Generate test cases from reviewed acceptance criteria.
- Keep feature-specific rules separate from generic report logic.

Acceptance:
- Every rule links to its source.
- Rules have reviewed/unreviewed/ambiguous states.
- A rule pack loads independently.

## Phase 6 — User review checkpoints (workflow designed, UI not implemented)

**Why:** Users should resolve ambiguous requirements and approve consequential actions, not approve every file read.

Checkpoints:
1. Confirm expected behavior if ambiguous.
2. Review target, trace, hypotheses, and evidence.
3. Approve targeted tests or risky operations.
4. Review findings and proposed changes.

Acceptance:
- Decisions are recorded with actor and timestamp.
- Read-only exploration proceeds without unnecessary prompts.
- Code edits and production operations require configured approval.

## Phase 7 — Verification harness (not implemented)

**Why:** A plausible explanation is not a confirmed cause.

Tasks:
- Add deterministic tests for feature invariants.
- Add targeted engine scenarios and regression tests.
- For stochastic outcomes, include sample size, variance/confidence interval, and seed where supported.
- Separate statistical variation from deterministic logic defects.
- Store commands, environment, and outputs as evidence.

Acceptance:
- Findings link to reproducible observations or strong direct evidence.
- Statistical deviations are not automatically labelled bugs.
- Targeted and existing tests pass before a fix is considered verified.

## Phase 8 — Benchmark and rollout (not implemented)

**Why:** Quality must be measured by diagnostic correctness, not answer length.

Metrics:
- Target coverage
- Dependency accuracy
- Root-cause precision
- Duplicate investigation rate
- Evidence completeness
- Relevant files inspected per resolved target
- Regression safety

Acceptance:
- Test against known bugs and negative controls.
- Track false positives and unresolved cases.
- Run in report-only mode before enabling code-changing actions.

## Recommended first sprint

1. Complete Phase 0 repository inspection.
2. Run this starter and inspect the output.
3. Adapt the report reader to the actual diff artifact.
4. Add reviewed requirements for one real feature.
5. Review generated hypotheses with an engineer.
6. Implement AST tracing only after the report and requirement contracts are stable.

## Non-negotiable design principles

- A report discrepancy is not proof of an engine defect.
- The approved specification defines expected behavior; ambiguity requires review.
- Every dependency edge records provenance and uncertainty.
- Branch IDs are descriptive; `(target, cause)` remains the deduplication identity in the existing CLI.
- Do not document unsupported CLI flags or verdicts as implemented.
- Keep the first integrated release read-only.
