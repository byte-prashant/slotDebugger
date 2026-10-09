---
name: slot-debugger
description: Use this skill to debug slot-game RTP reports (CSV/XLSX from a simulation). Trigger when the user shares an RTP report, asks which feature or component's RTP is off, or wants to check that parent totals equal the sum of their children.
---

# Slot Debugger

## Analyze a report

```bash
slotdebug analyze --file report.csv            # JSON to stdout
slotdebug analyze --file report.xlsx --out analysis.json
```

Output JSON has `metadata`, `components` (each with `rtp` as a share of total win and `hit_rate`), `dependency_graph` (parent -> children, known only from the game's engine and volume tester; empty without them) and `analysis` (`status` OK/FAIL, `issues`, `total_rtp` = the RTP the report states, null if it states none).

Exits with code 2 and `error:` on stderr for a missing file, unsupported extension, or a report without the metadata/components it needs.

## Match the game's expected report

If the game has an `expected_rtp_report.json` (workspace root, `data/`, `reports/`, beside the report, or `--expected <path>`), the output instead takes **exactly that file's shape, keys, scale and precision**, filled with measured values — so it can be diffed against the expected file directly. The game's feature names (`BG`, `FG12`, ...) are matched to the report's component rows heuristically: **read the `note:` lines on stderr** to check each match, and treat a `null` value as "no component matched this key", not as zero.

## Aggregate file

Every `analyze` builds `analysis/<report>.aggregate.json` (workspace only): a formula spec, with the report's raw numbers kept in `analysis/<report>.inputs.json`. The normalized `rtp` and `hit_rate` of each component are that file's formulas evaluated (`<row>.rtp`, `<row>.hit_rate`, `<row>.rtp_vs_stake`), plus `<parent>.children_sum` per parent total; all values come back under `aggregate.components`. If the game's `volume_tester.py` sits beside the report or in the workspace (or is given with `--volume-tester`), its `dump_event` names are read as patterns (`*` = built at run time) and cross-checked: read `volume_tester.unmatched_events` (emitted but absent from the report) and `unmatched_components` (in the report but no event explains it). Dependencies come from the game's code when `volume_tester.py` and `engine.py` are found (`--volume-tester`, `--engine`): the tester says which result field each event reports, the engine says how that field is computed, and `derived_from.dependencies` records the result (`complete: false` = some part has no event/row, so no `children_sum` is written). Without them there are no dependencies and no totals are checked: nothing is inferred from row names. With an `expected_rtp_report.json`, the inputs file lists every report row but the aggregate's formulas (`components`) cover only the rows its keys match (see the `note:` lines) and their dependencies, and the shaped report reads its values from it. Compare `<parent>.children_sum` with the parent's own `.rtp` to find a total that does not add up.

After `analyze`, run `slotdebug aggregate --file <report>`; if it lists findings, follow the aggregate-review skill before trusting the numbers.

To run `analyze` without touching the aggregate (for example to keep a spec you corrected by hand), use `--use-aggregate [PATH]`: a spec that has content is used as it is and never overwritten, and fails with the missing input's name if it no longer fits the report. Only a missing or empty one is generated and written. Outside a workspace with no PATH it asks for one.

## Compare with the expected report

`slotdebug diff --file <report>` (after `analyze`, inside a workspace) compares the saved analysis with `expected_rtp_report.json` key by key and writes `analysis/<report>.diff.json`. Each key has `expected`, `actual`, `delta` and a `status`:

- `ok`: within tolerance
- `mismatch`: off by more than the tolerance
- `missing`: the analysis left it null

The tolerance is 0.001 of a stake (0.1 points in percent, 0.001 as a fraction); shares and hit rates are compared to 0.001 and bets and play counts exactly. Use `--tolerance` to override the RTP tolerance. `open` lists every key that is not `ok`: these are the keys a debugging session must explain.

## Debug loop

1. Run `slotdebug analyze`, then `slotdebug diff`. Without an expected report, use the `Mismatch in <parent>` entries in `analysis.issues` instead (a parent's RTP differs from the sum of its children by more than 0.001).
2. Run `slotdebug think --setTargets <the diff file>` so the session knows which keys it must explain. Then follow "Debugging a report against the expected report" in the sequential-thinking skill: one branch per target and suspected cause, each concluded with evidence, and a finding per target.
3. Finish with `--nextThoughtNeeded false`. It is refused while a branch is open or a target has no finding.

## Notes

- Report format: tab-delimited even for `.csv`; component rows are `name<TAB>(win, frequency)`.
- Every number comes from an aggregate formula, never from an assumption about a row name or report column: component `rtp` is the share of total win, `rtp_vs_stake` the share scaled by the RTP the report states, and a value with no formula (`bet`, `total` when the report states no RTP) is `null` with a `note:`. Add the formula to the aggregate (aggregate-review skill) rather than expecting the formatter to guess.
- `slotdebug think` is the sequential-thinking CLI (see the sequential-thinking skill).
