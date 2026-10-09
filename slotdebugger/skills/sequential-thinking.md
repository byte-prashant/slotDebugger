---
name: sequential-thinking
description: Use this skill for tasks that benefit from explicit, traceable step-by-step reasoning with persistent state. Trigger it when the user says `slotdebug think`, asks to debug why an RTP report differs from the game's expected report or which engine code causes it, or asks to think step by step, break down a complex problem, reason carefully, debug iteratively, revise earlier conclusions, or explore alternative branches of analysis.
---

# Sequential Thinking

Use this skill when the work benefits from a structured reasoning loop instead of a one-shot answer.

## Use This Skill For

- Complex debugging or investigation
- Problems with unclear scope at the start
- Analysis that may need correction midstream
- Work that benefits from a persisted plan and thought history

## Files

- `think.py`: CLI entrypoint for the reasoning workflow (inside a slotdebug workspace, run it as `slotdebug think <args>`; the examples below work with either)
- `.think_state.json`: persisted state for plan progress, thought history, branches (with what each is about), branch conclusions, findings and targets

## Workflow

1. Reset state before starting a new reasoning session:

```bash
python3 think.py --reset
```

2. Set the plan. `--setPlan` accepts either comma-separated steps or a JSON array string:

```bash
python3 think.py --setPlan "Check RTP,Analyze reels,Validate RNG,Check bonus"
```

3. Record a thought for the current plan step, citing what it rests on with `--evidence` (repeatable: a `file:line` you read or a command you ran):

```bash
python3 think.py \
  --thought "RTP is lower than expected" \
  --thoughtNumber 1 \
  --totalThoughts 5 \
  --nextThoughtNeeded true \
  --evidence "slotdebug analyze --file report.xlsx"
```

A thought without `--evidence` is saved with a warning. Treat that warning as a claim you
still have to check.

4. Advance to the next plan step when the current one is complete:

```bash
python3 think.py --nextStep
```

5. Check status at any time:

```bash
python3 think.py --status
```

## Revisions

Use a revision when a later finding corrects an earlier thought.

```bash
python3 think.py \
  --thought "Correction: the mismatch comes from bonus logic" \
  --thoughtNumber 3 \
  --totalThoughts 5 \
  --nextThoughtNeeded true \
  --isRevision \
  --revisesThought 1
```

## Branching

Use branching when you want to explore an alternative explanation without losing the main path.

```bash
python3 think.py \
  --thought "Alternative hypothesis: RNG distribution is biased" \
  --thoughtNumber 4 \
  --totalThoughts 7 \
  --nextThoughtNeeded true \
  --branchFromThought 2 \
  --branchId rng-check
```

## Concluding branches

Close every branch once you have checked it. `--verdict`, `--reason` and `--evidence` are all required:

```bash
python3 think.py --conclude rng-check --verdict refuted \
  --reason "hit rates match the reel strip weights" \
  --evidence "engine.py:109" --evidence "slotdebug aggregate --file report.xlsx"
```

After a branch is concluded you can't add thoughts to it. To reopen the question, start a new `--branchId`.

## Targets: what the session must explain

`--setTargets` lists the keys the session has to account for. It takes either the file `slotdebug diff` writes (its `open` keys) or comma-separated keys:

```bash
python3 think.py --setTargets slotdebugger/analysis/report.diff.json
python3 think.py --setTargets "components.FG12,bet"
```

While targets are set:

- **New branches:** each one needs `--target KEY --cause CAUSE`, where the key is one of the targets.
- **Findings:** each one names the keys it explains with `--target` (repeatable).
- **Finishing:** you can't record a thought with `--nextThoughtNeeded false` while a branch is open or a target has no finding.

## What a branch is about, and never asking twice

A branch can say which key it explains and what it suspects:

```bash
python3 think.py --thought "the winnings cap may clip FG12" --thoughtNumber 4 --totalThoughts 9 \
  --nextThoughtNeeded true --branchFromThought 2 --branchId fg12-cap \
  --target components.FG12 --cause cap --evidence "engine.py:143"
```

Later thoughts on the same branch keep its target and cause. A branch can't switch to a different question.

A target + cause pair is one question, whatever the branch is called. Case and spacing don't matter. If you open a new branch on a pair that was:

- **already concluded:** it is refused, and the error shows the earlier verdict and reason.
- **still open in another branch:** it is refused, and you are told to continue that branch.

Use `--force` only when the evidence has really changed; the thought is then recorded with `forced: true`. The same cause on a **different** target is a new question and is allowed.

## Checking whether something was already covered

Every new thought is compared with the earlier thoughts and conclusions:

- **Shared evidence:** the same `--evidence` reference as an earlier entry → a warning that names that entry.
- **Alike wording:** a word-level score of 0.6 or more → a warning showing the entry, its branch and its verdict.

These are warnings and the thought is still recorded. Alike wording can be a different check (the same idea about another key), and different wording can be the same check. Entries about another target, and earlier thoughts of the branch being continued, are not compared. `file:line` references count as whole words, so `engine.py:143` and `engine.py:136` don't look alike.

To check **before** writing a thought, use:

```bash
python3 think.py --similar "does the cap clip FG12" --target components.FG12 --evidence "engine.py:143"
```

It prints up to three matches as JSON (`thought`, `branch`, `verdict`, `text`, `score`, `sharedEvidence`). If a match already answers your question, use its verdict instead of investigating again.

## Findings

Record each final result as a finding. `--evidence` is required, and `--target` names the keys it explains. If any branch is still open, you get a warning:

```bash
python3 think.py --finding "ways-win rows never link to base_game_winning: the tester reads ways_wins, the engine stores line_wins" \
  --target components.BG --evidence "tests/volume_tester.py:96" --evidence "engine.py:174"
```

`--status` shows `openBranches`, `concludedBranches`, the number of `findings` and `targets`, and the `unexplainedTargets`. `--history` prints thoughts, then conclusions, then findings.

## Debugging a report against the expected report

This is the full loop when the user says "debug this report" or "slotdebug think" in a
game workspace that has an `expected_rtp_report.json`:

```bash
slotdebug analyze --file report.xlsx           # shapes the result like the expected report
slotdebug diff --file report.xlsx              # writes analysis/report.diff.json; `open` = keys to explain
slotdebug think --reset
slotdebug think --setTargets slotdebugger/analysis/report.diff.json
slotdebug think --setPlan '["Pick the failing total", "Map events to result fields", "Trace each field in engine.py", "Check inherited engine code", "Compare with aggregate", "Conclude"]'
```

Then, **for each target**, until `--status` shows no `unexplainedTargets` and no `openBranches`:

1. Before each candidate cause, run `--similar "<question>" --target <key>`. If a concluded match answers it, use that verdict and don't open the branch.
2. Open one branch per candidate cause: `--branchFromThought <n> --branchId <id> --target <key> --cause <cause>`.
3. Investigate it with the engine-debugging steps below, citing `--evidence` on every thought.
4. Close it with `--conclude <id> --verdict confirmed|refuted --reason ... --evidence ...`.
5. Record what holds with `--finding ... --target <key> --evidence ...`.

A `missing` key (the analysis left it null) is usually a mapping or formula problem
(read the `note:` lines from `analyze`, then follow the aggregate-review skill), not an
engine problem. Finish with `--nextThoughtNeeded false`; the CLI refuses that until
every branch is concluded and every target has a finding.

## Debugging a game's engine.py

Here an RTP total doesn't add up and the reason is in the game code. This plan does not
replace `slotdebug analyze`, `slotdebug aggregate` or the aggregate-review skill; it
records why their numbers come out as they do.

1. **Pick the failing total.** Take one `open` key from `slotdebug diff`, or one `Mismatch in <parent>` / `incomplete_total` from `slotdebug analyze` / `slotdebug aggregate`. Note it as the thought, with the command as evidence.
2. **Map events to result fields.** In `volume_tester.py`, find each `dump_event(name, p.result['field'])` that feeds the total. Check that each `field` is a key the engine actually puts in its result dict; a name the tester reads but the engine never sets explains a row that can't be linked.
3. **Trace each field in engine.py.** Follow the field back to the variable it is assigned from, and through every assignment of that variable. Write down anything that is not a plain sum: multipliers, function calls, conditions (e.g. a bonus that only pays with 3+ reels), and anything applied *after* the total is formed, such as `check_winnings_cap`.
4. **Check inherited engine code.** The game engine subclasses the shared engine (e.g. `OGLE_Slot`). Methods it calls but doesn't define (win evaluation, cap, cascades) live in the library's `engine.py`. Read them before concluding anything about them.
5. **Compare with the aggregate.** Check `derived_from.dependencies` and `children_sum` in `analysis/<report>.aggregate.json` against what you traced. A static scan reads the whole file as one scope, so a variable name reused across functions can produce a wrong dependency.
6. **Conclude.** Open one branch per candidate cause (`cap`, `multiplier`, `missing-event`, `mapping`, `condition`), each with `--target` and `--cause`, and `--conclude` each with evidence. Record what holds as a `--finding --target <key>`. If the aggregate is wrong, fix it by following the aggregate-review skill.

## Operating Rules

- Reset once at the start of a new session.
- Set a plan before recording thoughts.
- Keep `--thoughtNumber` sequential and do not skip numbers.
- Use `--nextThoughtNeeded true` until the final thought, then set it to `false`.
- Use `--isRevision --revisesThought <n>` when correcting earlier reasoning.
- Use `--branchFromThought <n> --branchId <id>` when exploring alternatives.
- Call `--nextStep` only after finishing the current plan step.
- Keep each thought concise, evidence-based, and tied to the current step.
- Cite `--evidence` on every thought. Conclude every branch. End with at least one `--finding`.
- Give every branch a `--target` and `--cause`. Check `--similar` before opening one. Don't use `--force` to get past a concluded question.

## Validation

`think.py` exits with code 2 and an `error:` message (no traceback) when:

- `--thoughtNumber` is not the next number in sequence
- `--revisesThought` / `--branchFromThought` don't point to an earlier thought, or `--isRevision`/`--revisesThought` and `--branchFromThought`/`--branchId` aren't paired correctly
- `--nextThoughtNeeded` isn't `true`/`false`
- `--nextStep` is used with no plan (at the last step it reports `advanced: False`)
- more than one of `--setPlan`, `--nextStep`, `--status`, `--history`, `--export`, `--thought`, `--conclude`, `--finding`, `--setTargets`, `--similar` is passed in one call
- `--evidence` is empty or passed without `--thought`/`--conclude`/`--finding`/`--similar`; `--verdict`/`--reason` are passed without `--conclude`; `--target` without a branch thought, `--finding` or `--similar`; `--cause` or `--force` without `--thought`
- `--target`/`--cause` are not given together, are given on a thought that is not a branch, or change what an existing branch is about
- a new branch has no `--target`/`--cause` while targets are set, or names a key that is not a target
- a new branch repeats a target + cause that is concluded or open in another branch (without `--force`)
- `--setTargets` names a file that has no `open` list
- `--nextThoughtNeeded false` while a branch is open or a target has no finding (without `--force`)
- `--conclude` names an unknown or already-concluded branch, or lacks `--verdict`, `--reason` or `--evidence`
- a thought continues a concluded branch
- `--finding` lacks `--evidence`, comes before any thought, or names a `--target` that is not a target

Use `--state <path>` (or `$THINK_STATE_FILE`) to keep separate sessions in separate state files. The default state file is `./.think_state.json` in the current directory.

## Notes

- The current script automatically attaches the active plan step to each thought.
- `--status` reports the current thought number, whether another thought is needed, the current plan step, plan progress, open and concluded branches, findings, targets and unexplained targets.
- This skill should describe only the CLI behavior implemented in `think.py`. Do not document unsupported flags here.
