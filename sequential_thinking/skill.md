---
name: sequential-thinking
description: Use this skill for tasks that benefit from explicit, traceable step-by-step reasoning with persistent state. Trigger it when the user asks to think step by step, break down a complex problem, reason carefully, debug iteratively, revise earlier conclusions, or explore alternative branches of analysis.
---

# Sequential Thinking

Use this skill when the work benefits from a structured reasoning loop instead of a one-shot answer.

## Use This Skill For

- Complex debugging or investigation
- Problems with unclear scope at the start
- Analysis that may need correction midstream
- Work that benefits from a persisted plan and thought history

## Files

- `think.py`: CLI entrypoint for the reasoning workflow
- `.think_state.json`: persisted state for plan progress, thought history, and branches

## Workflow

1. Reset state before starting a new reasoning session:

```bash
python3 think.py --reset
```

2. Set the plan. `--setPlan` accepts either comma-separated steps or a JSON array string:

```bash
python3 think.py --setPlan "Check RTP,Analyze reels,Validate RNG,Check bonus"
```

3. Record a thought for the current plan step:

```bash
python3 think.py \
  --thought "RTP is lower than expected" \
  --thoughtNumber 1 \
  --totalThoughts 5 \
  --nextThoughtNeeded true
```

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

## Operating Rules

- Reset once at the start of a new session.
- Set a plan before recording thoughts.
- Keep `--thoughtNumber` sequential and do not skip numbers.
- Use `--nextThoughtNeeded true` until the final thought, then set it to `false`.
- Use `--isRevision --revisesThought <n>` when correcting earlier reasoning.
- Use `--branchFromThought <n> --branchId <id>` when exploring alternatives.
- Call `--nextStep` only after finishing the current plan step.
- Keep each thought concise, evidence-based, and tied to the current step.

## Notes

- The current script automatically attaches the active plan step to each thought.
- `--status` reports the current thought number, whether another thought is needed, the current plan step, and plan progress.
- This skill should describe only the CLI behavior implemented in `think.py`. Do not document unsupported flags here.
