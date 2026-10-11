# Report-Driven Slot Engine Debugging

## Purpose

This document explains how to improve the `Sequential Thinking` skill used to investigate RTP mismatches between an expected slot-game report and a report produced by the volume tester.

The goal is not simply to make the agent read more code. The goal is to help it find the **smallest evidence-backed explanation for a specific mismatch**, avoid repeating old investigations, and clearly distinguish a confirmed cause from a plausible guess.

---

## 1. The Problem We Are Solving

When actual RTP results differ from expected results, the cause may live in several layers:

- The game's math specification or configuration
- The volume tester's event and result-field mapping
- The game-specific `engine.py`
- A shared or inherited engine implementation
- Feature conditions, multipliers, caps, or state transitions
- The aggregation formulas used to produce the report

If the agent begins by reading the entire engine, it can waste time exploring unrelated code. If it jumps to a plausible cause, it may reach a conclusion that the evidence does not support. If it opens poorly named branches, it becomes difficult to understand what has already been tested.

**We are improving the workflow so the report determines what to investigate, code dependencies determine where to look, and evidence determines what to conclude.**

## 2. Start From the Report

### What to do

1. Compare the expected report with the actual volume-test report.
2. Run the existing analysis and diff workflow.
3. Select a specific mismatching target, such as a component RTP field or a parent total.
4. Record the target and the evidence that identified it.

Existing workflow:

```bash
slotdebug analyze --file report.xlsx
slotdebug diff --file report.xlsx
slotdebug think --reset
slotdebug think --setTargets slotdebugger/analysis/report.diff.json
slotdebug think --setPlan '["Pick the failing total", "Map events to result fields", "Trace each field in engine.py", "Check inherited engine code", "Compare with aggregate", "Conclude"]'
```

Use the actual paths and options supported by the current workspace.

### Why we do it

A report mismatch gives the investigation a concrete boundary. It reduces unrelated exploration and makes every hypothesis answer a specific question: **what could explain this target's observed difference?**

### Important distinction

A missing or null value may be a mapping or formula problem rather than an engine defect. Follow the existing analysis notes and aggregate-review workflow before assuming the game code is wrong.

## 3. Trace the Report Field to Its Source

For each target, trace the full calculation path:

1. The report field and its expected/actual values
2. The aggregate formula and its dependencies
3. The volume-tester event, including `dump_event(...)`
4. The result-dictionary field read by the tester
5. The variable and assignments that produce that field
6. Conditions, multipliers, caps, and feature-state changes affecting the value
7. Any shared or inherited engine methods involved
8. The game's specification and math configuration that define expected behavior

### Why we do it

This prevents the agent from stopping at a variable name or a nearby function. It also helps separate different classes of problems:

- The engine never emits the expected field.
- The tester reads a different field name.
- A calculation applies an unexpected condition or multiplier.
- A cap changes the final payout.
- An aggregate includes or omits a dependency.
- Inherited engine behavior changes the result after game-specific logic runs.
- The implementation differs from the game's specification.

Do not assume that a statically inferred dependency is correct. Python variable reuse, dynamic dispatch, and inherited methods can make simple text-based scans misleading. Mark uncertain edges and verify them.

## 4. Build a Report-to-Code Dependency Graph

Represent the relationships needed to produce a report value as a graph.

Illustrative flow:

```text
RTP report target
    -> aggregate formula
    -> volume-tester event
    -> engine result field
    -> source variable
    -> calculation / condition / multiplier
    -> cap or post-processing
```

For each edge, record as much of the following as is available:

- Source and destination
- Relationship type: assignment, dependency, condition, multiplier, cap, or aggregation
- File and line range
- Evidence source
- Whether the relationship is statically inferred or runtime/test verified
- Uncertainty or unresolved ambiguity

Illustrative record (not a claim about the current implementation):

```json
{
  "target": "components.FG12",
  "source": "engine.py:calculate_feature_payout",
  "relationship": "multiplier",
  "mechanism": "feature_payout_times_multiplier",
  "evidence_type": "static_analysis",
  "verified": false
}
```

### Why we do it

The graph provides a reusable map of how report numbers are produced. It allows the agent to explore only the relevant path, explain why a function matters, and avoid repeatedly rediscovering the same dependencies.

The graph is a guide, not proof. Every important relationship should be checked against the code, tests, runtime traces, or the applicable game specification.

## 5. Name Branches Consistently

Use this proposed branch-ID convention:

```text
<target>--<cause>--<mechanism>--<location>
```

- `target`: the exact report field or component being explained
- `cause`: a concise category of possible cause
- `mechanism`: the specific behavior being tested
- `location`: the relevant subsystem or code area

Examples:

| Branch ID | Question being investigated |
|---|---|
| `components.FG12--cap--post-evaluation-clipping--engine` | Does a cap reduce the relevant feature payout? |
| `components.FG12--mapping--missing-result-field--volume-tester` | Does the tester read a field the engine does not populate? |
| `base_game--condition--feature-eligibility--engine` | Does a feature-eligibility condition affect the expected result? |
| `total_rtp--aggregation--missing-or-duplicate-events--aggregator` | Are events missing or counted more than once? |
| `bet--state-transition--feature-bet-reset--bet-manager` | Is bet state reset or carried over incorrectly? |

These names are examples of hypotheses, not diagnoses.

### Why we do it

Consistent names make the history searchable and understandable. They show which report target, mechanism, and subsystem a branch concerns. They also make reviews easier when several possible explanations are being tested.

### Important: branch identity is not just its name

The current `think.py` workflow identifies duplicate investigation questions using the **target + cause** pair. A descriptive branch ID does not override that rule.

Before opening a branch:

```bash
slotdebug think --similar "does the cap clip FG12" \
  --target components.FG12 \
  --evidence "engine.py:143"
```

Use the actual target and evidence reference. Continue an open investigation or reuse a concluded verdict when appropriate. Use `--force` only when genuinely new evidence justifies reopening the same question.

## 6. Make Every Hypothesis Testable

Before investigating a branch, write down:

- **Target:** which report mismatch must be explained?
- **Cause:** what class of mechanism is suspected?
- **Hypothesis:** what specifically might be happening?
- **Expected observation:** what should be visible if the hypothesis is true?
- **Falsification condition:** what evidence would rule it out?
- **Required evidence:** which code paths, values, tests, or traces must be inspected?

Example:

```json
{
  "target": "components.FG12",
  "cause": "cap",
  "branch_id": "components.FG12--cap--post-evaluation-clipping--engine",
  "hypothesis": "The cap reduces the feature payout.",
  "expected_observation": "The pre-cap payout exceeds the final recorded payout.",
  "falsification_condition": "The cap is not reached in the cases producing the mismatch.",
  "required_evidence": [
    "pre-cap payout",
    "post-cap payout",
    "final result-field assignment"
  ]
}
```

This is an illustrative hypothesis record, not an existing `think.py` schema.

### Why we do it

Without a falsification condition, an agent can keep searching for evidence that supports its first guess. A testable hypothesis gives the investigation a clear path to confirmation or rejection.

## 7. Explore Code in a Targeted Order

Use the following order, adapting it to the target:

1. Read the report diff and analysis notes.
2. Inspect the aggregate formula and its declared dependencies.
3. Locate the corresponding `dump_event(...)` call in the volume tester.
4. Verify the exact result-dictionary field consumed by the tester.
5. Trace the field's source variable and assignments in the game engine.
6. Inspect relevant conditions, multipliers, caps, and feature transitions.
7. Inspect shared or inherited engine methods invoked by the path.
8. Compare behavior with the game specification and math configuration.
9. Run a focused test or reproduction.
10. Update the branch verdict and target finding.

### Why we do it

This order follows the value from the report toward its implementation. It starts with the strongest known evidence and expands only when necessary, reducing unrelated file reads and speculative branches.

### Prioritization heuristics

| Symptom | Investigate first |
|---|---|
| A component is missing or null | Result-field mapping, event emission, key names |
| Parent total differs from children | Aggregate dependencies, missing children, duplicated events |
| Component is consistently high or low | Formula, bet scaling, multiplier, conditions |
| Mismatch appears only in a feature | Feature entry/exit, retriggers, state transitions, caps |
| Individual modes match but combined mode differs | Mode selection, shared state, bet management, aggregation, interaction effects |
| Difference grows with spin count | Frequency-weighted payouts, rare events, accumulation, statistical uncertainty |

These are prioritization heuristics, not proof. Verify each proposed cause.

## 8. Rank Candidate Investigations Carefully

A candidate-ranking heuristic can help the agent choose what to inspect first:

```text
priority = 4 * relevance
         + 3 * dependency_strength
         + 2 * evidence_quality
         + 2 * testability
         - 3 * uncertainty
```

Score each factor from 0 to 1.

- `relevance`: how directly the candidate relates to the failing target
- `dependency_strength`: how strongly the report-to-code path supports the connection
- `evidence_quality`: how reliable the available evidence is
- `testability`: how easily the hypothesis can be checked
- `uncertainty`: how much key mapping or behavior remains unknown

### Why we do it

A ranking policy makes exploration deliberate rather than arbitrary. It encourages the agent to test likely, well-supported, easy-to-check explanations first.

This score is only a proposed heuristic. It has not been calibrated and must not be treated as a probability that a hypothesis is true. Keep alternative explanations when the evidence does not distinguish them.

## 9. Record Evidence and Conclusions

Every thought should cite evidence using the existing CLI. Evidence may include:

- A file and line range that was actually inspected
- A command that was actually run
- A test result or deterministic reproduction
- A report or aggregate artifact that was actually examined

For each branch:

1. State the question and target.
2. Record the evidence gathered.
3. Explain how that evidence supports or contradicts the hypothesis.
4. Conclude using a verdict supported by the current CLI.
5. Record a finding against the affected target.

The current CLI documents `confirmed` and `refuted` as branch verdicts. If the evidence is insufficient, do not invent a supported verdict or claim the root cause is known. Record the limitation in the investigation notes and leave the target unresolved until it can be addressed.

### Why we do it

An evidence trail lets another engineer reproduce the investigation, challenge assumptions, and continue from the last verified point. It prevents unsupported explanations from becoming permanent debugging knowledge.

## 10. Completion Criteria

Do not declare the investigation complete until:

- Every required target has a finding backed by evidence.
- Every opened branch has been concluded.
- The evidence connects the proposed mechanism to the affected target.
- The aggregate and engine paths have been checked at the appropriate level.
- Remaining uncertainty is explicitly documented.
- Any proposed fix is validated by an appropriate test or reproduction.

A plausible explanation is not the same as a confirmed root cause. A confirmed cause is not the same as a verified fix.

## 11. Benchmark the Skill

Evaluate the workflow on previously diagnosed cases with known outcomes, including:

- Missing or incorrectly named result fields
- Missing or duplicated aggregate events
- Incorrect multipliers or conditions
- Winnings-cap behavior
- Feature-state transitions and retriggers
- Differences that appear only when game modes are combined

Track these measures:

| Metric | Meaning |
|---|---|
| Target coverage | Fraction of mismatching targets explained |
| Dependency accuracy | Fraction of traced relationships verified against code |
| Root-cause precision | Fraction of confirmed causes that actually explain the defect |
| Duplicate investigation rate | Fraction of branches repeating earlier questions |
| Evidence completeness | Fraction of conclusions with adequate evidence |
| Exploration efficiency | Relevant files/functions inspected per correctly resolved target |
| Regression safety | Fraction of proposed fixes passing targeted and existing tests |

Use known ground truth where possible. A skill should not receive a high score merely for producing many plausible explanations.

## 12. Recommended Implementation Order

Implement improvements in this order:

1. Standardize branch IDs and retain the existing target + cause duplicate protection.
2. Make target-to-code tracing explicit and record evidence for every relationship.
3. Add hypothesis fields for expected observations and falsification conditions to the agent's working protocol.
4. Add symptom-based exploration priorities.
5. Add candidate ranking only after the basic trace and evidence workflow is reliable.
6. Build a benchmark set and measure results before tuning heuristics.

This order prioritizes traceability and correctness before adding more complex scoring.

## Final Principle

**The report tells us what is wrong. The dependency graph tells us where to look. The hypothesis tells us what to test. The evidence tells us what to conclude.**

The goal is not to explore the most code. It is to explain each mismatch with the smallest sufficient, reproducible, evidence-backed investigation.

---

# Implementation Blueprint: Make `slotdebugger` Specification-Driven

This section turns the investigation principles above into an implementable product workflow. It distinguishes what the system should do from proposed implementation details. Do not assume a feature is implemented until the code and tests confirm it.

## 13. Product Goal and Non-Goals

### Goal

Given an RTP report, expected results, expected behavior, feature rules, and access to the relevant game code, `slotdebugger` should help an engineer identify and verify why observed behavior differs from expected behavior.

It must support two entry modes:

- **Report debugging:** a report or metric is failing.
- **Behavior verification:** the user wants to verify a feature or rule even when no report mismatch is known.

### Non-goals

- Do not claim that a statistical RTP deviation proves an engine bug.
- Do not change game math, engine code, expected reports, or production files automatically without the applicable approval.
- Do not treat LLM reasoning or static dependency inference as proof.
- Do not create a separate copy of the whole workflow for every feature.
- Do not invent expected behavior when the specification is missing or ambiguous.

## 14. Define the Inputs Explicitly

Use an input manifest to identify what the investigation has and what is missing. The manifest below is a proposed design, not an existing `slotdebugger` format.

```yaml
mode: report_debugging # or behavior_verification
workspace: /path/to/game
artifacts:
  actual_report: path/to/report.xlsx
  expected_report: path/to/expected_rtp_report.json
  game_spec: path/to/game_design_or_math_spec.md
  math_config: path/to/paytable_and_rule.xlsx
  engine_entrypoint: path/to/engine.py
  volume_tester: path/to/volume_tester.py
feature: expanding_wild # optional
user_goal: "Explain why the feature RTP differs"
permissions:
  read_code: true
  run_tests: ask
  edit_code: ask
  change_expected_results: never_without_explicit_approval
```

### Why we need a manifest

An explicit manifest prevents the agent from silently assuming that a report, specification, or code path exists. It also lets the system distinguish a missing input from a confirmed defect.

The exact schema should be adapted to the current repository and CLI. Validate file existence and supported formats before launching analysis.

## 15. Create an Expected-Behavior Model Before Diagnosing

For every relevant rule, record:

- A stable rule ID
- Feature and scenario
- Expected behavior in plain language
- Preconditions and trigger conditions
- Expected state transition or payout effect
- Source specification and exact section/page/cell when available
- Whether the rule is explicit, inferred, ambiguous, or missing
- Acceptance criteria or a testable invariant

Example (illustrative only):

```yaml
rule_id: expanding_wild.max_bet_during_feature
feature: expanding_wild
statement: "The bet must not exceed the configured maximum while the feature is active."
preconditions:
  - feature_active
expected_invariant: "current_bet <= configured_max_bet"
source:
  file: game_spec.md
  section: "Expanding Wild / Bet Restrictions"
status: explicit
```

### Why we need this model

The report tells us what differs, but not always what should happen. The expected-behavior model gives the investigation an independent reference point and allows feature verification without a failing RTP report.

**Do not generate a rule and present it as specification fact if the source does not support it.** Label inferred rules and ask the user to confirm ambiguous or conflicting requirements.

## 16. Use a Shared Orchestrator and Reusable Feature Packs

The orchestrator owns the end-to-end workflow. Reusable capabilities should be invoked only when relevant:

1. **Report analyzer:** normalize report values and identify mismatches.
2. **Specification validator:** extract or retrieve expected rules and acceptance criteria.
3. **Feature pack:** supply feature-specific rules, states, transitions, and edge cases.
4. **Code tracer:** map rules and report fields to actual implementation paths.
5. **Test planner/runner:** propose and, when permitted, run targeted checks.
6. **Evidence and conclusion writer:** summarize what was verified and what remains unknown.

### Why this architecture

The same report, tracing, evidence, and approval workflow applies to many features. Feature packs should provide domain-specific rules, not duplicate the orchestrator. This reduces drift and makes it easier to add features incrementally.

A feature pack may contain:

- `SKILL.md` or equivalent instructions
- A feature rule catalogue
- State-transition notes or diagrams
- Known edge cases
- Links to relevant source files/tests
- Test templates
- Known failure signatures, if backed by past verified incidents

Treat feature packs as versioned knowledge. Record which version was used in each investigation.

## 17. Define the Two End-to-End Workflows

### Mode A — Report Debugging

1. Validate the actual and expected report inputs.
2. Normalize fields without discarding original labels or values.
3. Produce a diff with expected value, actual value, absolute difference, relative difference where meaningful, and missing/null status.
4. Read analysis notes and classify each discrepancy as missing mapping, aggregate mismatch, component mismatch, or possible statistical variation.
5. Resolve each target to report dependencies, tester events, result fields, and engine calculations.
6. Load relevant specification rules and feature packs.
7. Generate a small set of distinct hypotheses with expected observations and falsification conditions.
8. Check investigation history before opening branches.
9. Ask for user input only when expected behavior is ambiguous, the next action is consequential, or multiple interpretations materially change the investigation.
10. Run approved focused checks, record results, and update branch verdicts and findings.
11. Produce a conclusion with confidence limitations and unresolved targets.

### Mode B — Feature / Behavior Verification

1. Identify the feature or rule the user wants checked.
2. Find its authoritative specification and label any gaps or conflicts.
3. Build a checklist of preconditions, transitions, outputs, limits, and edge cases.
4. Trace each rule to the implementing code and existing tests.
5. Mark each checklist item as `verified`, `failed`, `not_covered`, or `blocked_by_ambiguity`.
6. Propose targeted tests for unverified or failed items.
7. Run tests only under the configured permission policy.
8. Report evidence, coverage gaps, and recommended next actions.

### Why two modes

A defect report is not required to find a bug. Feature verification is proactive: it can discover missing edge-case tests or specification gaps before a mismatch appears in a volume report.

## 18. Add User Checkpoints Without Making Debugging Slow

Use approval gates for decisions that need human judgment, not for every read-only operation.

| Checkpoint | Agent presents | User action needed when |
|---|---|---|
| Expected behavior | Rule, source, ambiguity, proposed interpretation | Specification is ambiguous, conflicting, or inferred |
| Investigation direction | Target, code path, hypotheses, evidence so far | There are materially different plausible interpretations or the user requests review |
| Test execution | Test command, expected side effects, scope | Tests are expensive, mutate data, require special environments, or may affect shared resources |
| Code change | Proposed diff, rationale, regression tests | Before applying a consequential change, unless explicit policy already authorizes it |
| Final conclusion | Findings, verification results, remaining uncertainty | Before accepting or merging a fix according to the team's normal process |

### Why this interaction model

Asking the user to approve every file read slows down routine debugging. Never asking the user risks implementing an incorrect interpretation of a game rule. Checkpoints should be triggered by ambiguity, risk, cost, and irreversible effects.

The user must always be able to correct the expected behavior or request another hypothesis. Record that correction as an explicit decision with provenance; do not silently overwrite the original source rule.

## 19. Keep Evidence, Inference, and Decisions Separate

Each investigation record should distinguish:

- **Observed fact:** directly read from a report, file, command output, or test.
- **Specification rule:** directly supported by a named source.
- **Inference:** a hypothesis derived from facts, not yet verified.
- **Test result:** observed output from a reproducible check.
- **User decision:** an interpretation or approval explicitly supplied by the user.
- **Conclusion:** the current evidence-backed status of a target or hypothesis.

Proposed evidence record:

```json
{
  "kind": "test_result",
  "summary": "The pre-cap and post-cap payout differ for the reproduced scenario.",
  "source": "tests/test_feature_payout.py::test_cap_applies_after_multiplier",
  "observed_at": "<timestamp>",
  "target": "components.FG12",
  "supports_hypothesis": "components.FG12--cap--post-evaluation-clipping--engine"
}
```

This is an illustrative record; adapt the schema to the actual implementation. Never fabricate timestamps, file locations, command results, or test outcomes.

### Why separation matters

It lets a reviewer see which parts are known, which are inferred, and which depend on a human decision. It also makes it possible to revise an inference without rewriting the underlying evidence.

## 20. Improve the Investigation State Model

The existing `think.py` state already tracks plans, thoughts, branches, conclusions, findings, targets, and evidence references. Preserve that behavior while evaluating whether the following metadata is useful to add:

- Investigation mode
- Input manifest or artifact identifiers
- Expected-behavior rule IDs
- Branch hypothesis, expected observation, and falsification condition
- Evidence type and verification status
- Test plan and test outcome
- User decisions and approvals
- Final target status and unresolved reason
- Tool/code version information needed for reproducibility

### Why extend state carefully

The state file should be the durable investigation record, not a dump of every model token or speculative thought. Persist concise, auditable facts and decisions. Add fields only when they support reproducibility, deduplication, verification, or useful resumption.

Do not break existing CLI flags or state files accidentally. Define a schema version and provide backward-compatible loading or an explicit migration path before changing the state format.

## 21. Test the Debugger Itself

Build a fixture set from previously diagnosed issues. Each fixture should include the input artifacts, known expected behavior, known root cause if established, and the acceptable evidence path. Keep the ground-truth answer hidden from the diagnosis process when evaluating it.

Recommended fixture categories:

- Missing or mismatched result-field names
- Missing or duplicated events in aggregation
- Incorrect multiplier or payout condition
- Cap applied at the wrong stage
- Feature entry/exit or retrigger state bug
- Inherited-engine behavior overlooked
- Individual modes correct but combined mode incorrect
- Expected behavior ambiguous or contradictory
- No actual defect: difference explained by sampling uncertainty
- Insufficient artifacts: investigation must ask for missing evidence instead of guessing

Measure at least:

- Correct target identification
- Root-cause precision and recall where ground truth is available
- Evidence validity
- False-positive rate
- Duplicate hypothesis rate
- Unresolved-target honesty
- Test reproducibility
- Relevant code exploration cost
- Regression safety of proposed fixes

### Why these cases

A debugger must be tested on both positive and negative cases. It should detect real defects, but it must also resist falsely diagnosing normal statistical variation, missing inputs, or ambiguous specifications as engine bugs.

## 22. Implementation Roadmap

### Phase 1 — Reliable inputs and target selection

- Validate files and formats.
- Normalize report fields and preserve raw values.
- Make targets and missing values explicit.
- Keep current `analyze`, `diff`, `aggregate`, and `think` responsibilities distinct.

**Done when:** a fixture produces a reproducible target list and explains where each target came from.

### Phase 2 — Specification and feature rules

- Define a source-backed expected-behavior record.
- Add one feature pack for a feature with well-understood rules.
- Mark ambiguous and inferred requirements explicitly.

**Done when:** the agent can list feature rules and their sources without inventing requirements.

### Phase 3 — Report-to-code trace

- Trace report target to aggregate, event, result field, engine assignments, and inherited behavior.
- Store evidence locations and confidence/verification status.
- Treat static scans as candidate discovery, not proof.

**Done when:** a reviewer can follow the trace and verify every critical edge.

### Phase 4 — Hypothesis and approval workflow

- Add falsification criteria to the agent protocol.
- Use existing target/cause duplicate checks.
- Ask users at ambiguity and consequential-action checkpoints.
- Keep edits and expensive tests behind the permission policy.

**Done when:** the agent can reject a hypothesis and does not modify code without authorization.

### Phase 5 — Verification and benchmarks

- Add deterministic tests where possible.
- Use simulation for probabilistic behavior and report uncertainty honestly.
- Benchmark on known bugs and no-bug cases.
- Track false positives and unresolved cases.

**Done when:** quality is measured against ground truth rather than judged only by the apparent quality of explanations.

## 23. Final Design Principle

**Expected behavior defines correctness. The report identifies a discrepancy. The dependency trace narrows the code path. Hypotheses define what to test. Evidence and reproducible checks determine the conclusion. The user resolves ambiguity and approves consequential actions.**

The debugger is successful when it produces a minimal, reviewable, reproducible explanation—or honestly reports what it cannot yet establish.