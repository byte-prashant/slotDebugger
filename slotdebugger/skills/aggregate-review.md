---
name: aggregate-review
description: Use this skill after `slotdebug analyze` has written analysis/<report>.aggregate.json (formulas) and <report>.inputs.json (numbers), to check and correct that file against the game's engine.py and volume_tester.py. Trigger when `slotdebug aggregate` lists findings, when a parent total does not equal the sum of its children, or when asked to verify or fix an aggregate.
---

# Review and correct aggregate.json

`slotdebug analyze` generates two files in `analysis/` from the report, the game's
`volume_tester.py` and `engine.py`: `<report>.aggregate.json` (the formulas, which you
review and correct) and `<report>.inputs.json` (the report's measured numbers, which you
never edit and which are regenerated from the report on every run). It is a **draft**: it reads code
statically and can be wrong where only a reader of the game's code can tell. The
normalized RTPs and the final report are computed from this file, so a wrong formula
becomes a plausible wrong number with no error. Your job is to correct the draft,
and to leave a real bug in the game visible, not "fix" it away.

## Procedure

1. **Get the findings.**
   ```bash
   slotdebug aggregate --file <report>
   ```
   It prints `findings`, each with `severity`, `code`, `component`, `message` and a `fix` direction. Work through them one at a time.

2. **Read the sources, not just the file.** For each finding open the game's `engine.py` and `volume_tester.py` (paths are in `source` and `derived_from` inside the aggregate) and find the lines that produce that field. Do not decide from names alone.

3. **Fix by finding code** (below), editing only `components` in `<report>.aggregate.json`.

4. **Re-check.** `slotdebug aggregate --file <report>` again. Every remaining finding must be either fixed or *explained* (see "Leaving a finding").

5. **Mark it reviewed**, so the next `analyze` keeps your corrections instead of regenerating:
   ```bash
   slotdebug aggregate --file <report> --mark-reviewed
   ```
   It refuses if the file does not parse or evaluate.

6. **Run `slotdebug analyze --file <report>`** and confirm stderr says `using reviewed aggregate` and the output is what you expect.

## Findings and what to do

| code | meaning | action |
|---|---|---|
| `invalid` | the document does not parse or evaluate | read the message; it names the component and problem. Fix the formula (unknown op, dangling `ref`, mixed units, missing input). |
| `total_mismatch` | `<parent>.children_sum` != the parent's own `.rtp` | Find out which: (a) a child is missing or wrongly included in `children_sum` → correct the formula; or (b) the children are complete and the report's total is genuinely different → **this is the game bug**. Leave the formula, keep the finding, and report it. |
| `incomplete_total` | engine says the total depends on fields that have no event/row, or is not a plain sum | Read the engine expression. If a missing part is provably zero or never reaches the report, add `children_sum` with the parts that exist and say why. If it is a product, a call or conditional logic, leave it without `children_sum`. |
| `unscoped` | the expected report's keys matched no report rows, so the aggregate covers every row | check the expected report's keys against the report's rows. |
| `unmatched_event` | tester emits an event no report row matches | usually the report names it differently or it is never reached; do not invent a row. |
| `unexplained_row` | no tester event produces this scoped row | find where it is emitted; if you cannot, say so. |

## The files

```
<report>.aggregate.json   components    formulas: <row>.rtp, <row>.hit_rate, <row>.rtp_vs_stake, <parent>.children_sum
                          derived_from  what the engine said each total depends on (complete: true/false)
                          volume_tester events found; unmatched_events / unmatched_components
<report>.inputs.json      inputs        measured: <row>.win, <row>.frequency, total_plays, total_game_win, overall_rtp
```

Formula operations: `input`, `ref`, `add`, `multiply`, `divide`, `sum` (all components of a `category`), `if`. `{"ref": "x"}` is shorthand for `{"op":"ref","name":"x"}`. A component's `unit` (`share_of_win`, `rate`, `rtp_fraction`) must match what an `add`/`sum` combines.

## Hard rules

- **Never edit `<report>.inputs.json`.** It is the report's numbers, and `analyze` overwrites it from the report every run. Changing a number to make a check pass is falsifying the result. Reviewed formulas stay valid when only the numbers change; if the report's rows change so they no longer evaluate, `analyze` sets your file aside as `*.aggregate.stale.json` and regenerates.
- **Never add a component you cannot trace to a line in the engine or tester.** No guessed children, no plugging a difference with a made-up term.
- **Do not make `children_sum` equal the parent by construction** (for example by adding a `divide`/remainder term). A mismatch you can explain by a code reading is a finding to report; one you paper over hides the bug.
- **Units:** never add a count to an amount, or a share to an RTP. This repo already shipped that mistake once (`STAKE_SPINS` vs `TOTAL_STAKE`).
- Change only what a finding requires. Do not rewrite untouched formulas.

## Leaving a finding

If you cannot resolve a finding from the code, leave it, and write it down in the aggregate under a top-level `"review_notes"` list: `{"component": ..., "finding": <code>, "note": "<what you read, what is still unknown>"}`. Then say it in your reply. An unexplained finding that is stated is acceptable; one that is silently deleted is not.

## Report back

State, for each finding: fixed (what you changed and the code line that justifies it), left (why), or a suspected game bug (the field, the engine line, the numbers). Include the final `slotdebug aggregate` output.
