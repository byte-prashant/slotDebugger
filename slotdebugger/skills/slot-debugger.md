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

## Map report keys to the volume tester

`slotdebug trace-tester --file <report> --python <game venv>/bin/python --path <OGA checkout> [-n 5000] [--kw stake=1]` runs the game's volume tester in the game's own interpreter, with `dump_event` traced. The game's files are never modified. What runs is the **verbatim copy** in `slotdebugger/tester/`, made at `slotdebug setup`, loaded in the game's package so its imports still work. It writes `analysis/<report>.tester_map.json`, which has an entry for each tester line that emits events (`sites`):

| Field | Meaning |
|---|---|
| `keys_seen` | the keys this line actually produced, with how many times each |
| `response_key` | the engine result field the value reads (`p.result['current_winnings']` → `current_winnings`) |
| `conditions` | the `if`/`else`/`for` chain around the call |
| `condition_values` | the run-time values that built dynamic keys (e.g. `action: ["spin", "free"]`) |
| `source` | `tester`, or `inherited` for events emitted by the framework's base class |
| `fired` | whether the run reached this line |
| `response_key_status` | what the engine actually returned for `response_key`: `present`, `always_empty` (returned but always None/empty, so a loop over it never runs), `never_returned` (the tester reads a key the engine doesn't produce), or `unknown` |

- **`missing_response_keys`:** lines whose response key is `never_returned` or `always_empty`. This is evidence from the engine's own results, not a guess. `response_keys` holds the count for every top-level key the engine returned (`present`, `empty`, out of `results_seen`).
- **`never_fired`:** tester lines the run did not reach in N spins. This means not reached, not proved impossible: rare features need a larger `-n`. Check `response_key` against the engine first: on Blazing 7s the tester reads `ways_wins`, which the engine never sets.
- **`report.unexplained_rows`:** rows that no tester line produces. The report probably came from a different tester version.
- **`report.not_reproduced_rows`:** rows matched only by a line's key pattern. The line exists, but this run didn't reach it.
- **`skipped_kwargs`:** constructor arguments the tester takes from its command line. They stay at the tester's defaults unless passed with `--kw NAME=VALUE`. Use the stake the report was made with.

- **`ran` / `labelled_as`:** which file ran and which path the line numbers refer to. If the original changed after it was copied, the copy keeps running and `labelled_as` names the copy, because its line numbers no longer match the original. `--refresh-tester` re-copies (the old copy is kept beside it), and `--live` runs the game's own file instead.
- **`game_files`:** hashes of the tester, engine, defs and the other game modules that were loaded. Compare two maps' `game_files` to tell whether a change in the map came from a code change.

Read `error` and `spins.completed` first: if the tester stopped early, everything else covers only the spins it ran. A `report.warning` means no rows could be read from the report, so no rows were matched. The parser reads `name<TAB>(win, hits)` rows only.

## Index the engine from its response keys

`trace-tester` does this automatically after the run, with the same `--path` and the engine found beside the tester (`--engine` to override, `--no-index` to skip). So a single `trace-tester` writes all three files. Run `slotdebug index` on its own to re-index without re-running the tester, add `--key`s, or print a tree.

`slotdebug index [--file <report>] --path <OGA checkout>` reads the engine (nothing is run). For each response key in the tester map, or each `--key`, it finds every place the engine, or a class it inherits from in OGLE/OGA, puts that key in a dict. Each place is a root, so the base-game and free-game responses are separate roots. From each root it works backward through the code and writes `analysis/<report>.engine_map.json`. `--tree KEY` prints a readable tree instead of the JSON. Read the tree, not the JSON, when investigating.

Node kinds:

| Kind | Meaning |
|---|---|
| `assign` / `augassign` / `mutate` / `unpack` / `loop` | a statement that sets or changes the variable (`mutate`: `x.append(v)`, `x[k] = v`) |
| `variable` | several assignments can reach this use; its children are those assignments |
| `call` | a method call, resolved through the class hierarchy into OGLE/OGA; `target` gives the class and file where it's defined |
| `returns` / `return` | what a called method returns |
| `param` | a parameter that the result depends on. `via` gives the call site that passed it. A parameter missing from the tree doesn't affect the result |
| `attribute` | `self.x`, with every `self.x = ...` in the class hierarchy |
| `constant` | a module-level value, e.g. `defs.X` |
| `builtin` / `construct` / `external` | `sum()` etc., a class instance, or a call it can't follow (its arguments are still traced) |
| `unresolved` / `truncated` | couldn't be followed / the `--depth` limit was reached |

`conditions` on a node is the `if`/`for` chain it runs under. An assignment that a later one always overwrites on the same path isn't shown as reaching.

With a tester map, the join is written to a separate file, `analysis/<report>.report_map.json`: report row → tester line → response key → engine roots. `keys_without_engine_root` lists keys the tester reads but no engine code puts in a dict. So there are three files, one map each:

| File | Written by | Holds |
|---|---|---|
| `<report>.tester_map.json` | `trace-tester` | report key → tester line → response key (from a run) |
| `<report>.engine_map.json` | `trace-tester` (or `index`) | response key → engine code (from reading the code) |
| `<report>.report_map.json` | `trace-tester` (or `index`) | the two joined |

Limits: everything comes from reading the code. A root can be a dict that never becomes part of a response, so check its `function`. Unpacking (`a, b = f()`) depends on everything `f` returns. Early `return`/`break` aren't modelled, which can only add assignments, never hide them. `unresolved_bases` means a framework class wasn't found: pass its checkout with `--path`.

## Ask the maps (`slotdebug map`)

Each `trace-tester` run also writes three Markdown summaries next to the maps. **Read `analysis/<report>.summary.md` first.**

| File | Contents |
|---|---|
| `<report>.summary.md` | the run in numbers, plus **all problems ranked**, each with a file:line |
| `<report>.tester_summary.md` | tables: report rows, tester event lines, response keys |
| `<report>.engine_summary.md` | class hierarchy, how each response key is computed (tree), methods on the traced path, suspicious assignments |

To answer one question without reading JSON, use `slotdebug map <view> --file <report>`:

| View | Answers |
|---|---|
| `tree --row R` / `--key K` / `--site volume_tester.py:92` | the whole chain: report row → tester line (conditions, hits) → response key (returned?) → engine code |
| `rows` / `keys` / `sites` | the tables |
| `method --name M` | where M is defined (game or framework, file:lines), which engine class uses that version, parameters that don't reach its result, its callers, what its result is computed from |
| `class [--name C]` | the class hierarchy, and which class each method comes from (overrides) |
| `find --text T` | where T appears on the traced paths |
| `problems [--json]` | everything suspicious, ranked high/medium/low, with where and whether the evidence comes from the run or from reading the code |

Problems found:
- **From the run:** a response key never returned or always empty, a tester line that never fired, a report row nothing explains, the tester stopping early.
- **From reading the code:**
  - a computed value replaced by a fixed one on every path (`stop_list = self.spin()` then `stop_list = [123, 123, 123]`, or a list filled then reset to `[]`)
  - a trailing-comma tuple (`x = 1,`)
  - a parameter that doesn't reach the method's result
  - a response key no engine code sets
  - a framework base class that can't be found

The code checks cover only methods on the traced paths. A problem is a lead to confirm, not a finding: cite its `where` with `think --evidence`.

## Install as skills, hooks or a plugin

| Target | Command | What you get |
|---|---|---|
| Claude Code + Cursor, one game | `slotdebug setup` | skills (`slot-debugger`, `slot-map`, `sequential-thinking`, `aggregate-review`) and the command reference in `CLAUDE.md` / `.cursor/rules` |
| Claude Code hooks | `slotdebug install claude --scope project --hook` | **SessionStart:** the newest maps' problems, and whether game files changed since they were built. **PostToolUse** (Edit/Write/MultiEdit): when an edited file is one the engine map was built from, the engine is re-indexed and problems that are gone or new are reported |
| Claude Code plugin | `slotdebug install plugin`, then `claude plugin marketplace add <dir>` and `claude plugin install slotdebug@slotdebug` | the skills, both hooks, and `/slotdebug:tree`, `/slotdebug:problems`, `/slotdebug:method`, `/slotdebug:summary` |

The hooks and plugin call `slotdebug` by its absolute path, so re-run the install after moving the venv. Cursor gets the skills as rules (`setup`, or `install cursor`); hooks are Claude Code only.

## Diagnose a report

There are two ways to run a diagnosis. Both use the same commands and produce the same result file:

- **In this session.** Follow the steps below yourself. This works in any editor, including Cursor, and without a subagent. It fits when the investigation is short, or when you want to see each step.
- **Through the `slot-debugger` agent** (Claude Code; `slotdebug setup` registers it). Delegate with the report name and, if you know them, the game's interpreter and OGA checkout. The agent runs these same steps in its own context and returns the diagnosis. It is diagnose-only: it cannot edit files. Use it for a long investigation you want kept out of this conversation's context.

Steps:

1. **Workspace.** If `slotdebugger/` is missing, run `slotdebug setup`. `slotdebug runs` lists the imported reports, and `slotdebug add <path>` imports one.
2. **Measure.** Run `slotdebug analyze --file <report>`, then `slotdebug diff --file <report>`. Read the `note:` lines. Without an expected report, use the `Mismatch in <parent>` entries in `analysis.issues` (a parent's RTP is off from the sum of its children by more than 0.001).
3. **Map.** Run `slotdebug trace-tester --file <report> --python <game venv python> --path <OGA checkout> -n 5000`. Add `--kw stake=<stake>` when the report was made at a specific stake. Then read `analysis/<report>.summary.md`.
4. **Investigate**, keeping the record in `slotdebug think`:
   - Start with `slotdebug think --reset`, then `slotdebug think --setTargets slotdebugger/analysis/<report>.diff.json`.
   - For each target, use `slotdebug map tree --row <row>` (or `--key`), `map method --name <method>` and `map problems`, and open each cited line to confirm it.
   - Open one branch per target and suspected cause (`--target`, `--cause`; check `--similar` first). Conclude each with `--conclude <id> --verdict confirmed|refuted --reason ... --evidence <file:line>`. The sequential-thinking skill, under "Debugging a report against the expected report", has the details.
   - For each confirmed cause, run `slotdebug think --finding "<cause>" --target <key> --evidence <file:line> --fix "<exact change>"`.
   - A problem the maps report is a lead, not a finding, until you confirm it at its file:line.
   - Finish with `--nextThoughtNeeded false`. It is refused while any branch is open or any target has no finding.
5. **Report.** Run `slotdebug report --file <report>`. It writes `analysis/<report>.agent_report.json` and `.md` and checks that every file:line you cited exists. Correct any evidence it flags.
6. **Fix and confirm.** Run this step only after `report` is written, so the diagnosis is on record before the code changes.
   - In this session: show the user the findings and proposed fixes, then apply the fixes they agree with.
   - After the agent returns: review its evidence first, then apply the fixes you agree with.
   - Then re-run `slotdebug trace-tester` and `slotdebug diff` to confirm the keys are now `ok`.
   - With the hooks installed, an edit to an engine file re-indexes the engine and reports which problems are gone or new.

## Notes

- Report format: tab-delimited even for `.csv`; component rows are `name<TAB>(win, frequency)`.
- Every number comes from an aggregate formula, never from an assumption about a row name or report column: component `rtp` is the share of total win, `rtp_vs_stake` the share scaled by the RTP the report states, and a value with no formula (`bet`, `total` when the report states no RTP) is `null` with a `note:`. Add the formula to the aggregate (aggregate-review skill) rather than expecting the formatter to guess.
- `slotdebug think` is the sequential-thinking CLI (see the sequential-thinking skill).
