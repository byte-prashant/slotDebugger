---
name: slot-map
description: Use this skill to find which slot-engine code produces a report value. Trigger when the user asks which engine code computes a report row or response key (e.g. current_winnings), why a report row is wrong, missing or zero, where an engine method is defined (game or OGLE/OGA framework), what a method's result depends on, or what is suspicious in an engine; also when asked to trace a report key to the volume tester or the engine, or to show the slot map or tree.
---

# Slot map: report row → volume tester → engine code

Three maps, built once per report, answer these questions without reading the engine by hand.

## Build the maps (once, and again after the engine or tester changes)

```bash
slotdebug trace-tester --file <report> --python <game venv>/bin/python --path <OGA checkout> -n 5000 [--kw stake=1]
```

This runs the workspace's verbatim copy of the volume tester (the game's files are never changed) and writes six files to `slotdebugger/analysis/`:

| File | Contents |
|---|---|
| `<report>.tester_map.json` | report key → tester line → response key, **from the run** |
| `<report>.engine_map.json` | response key → engine code, **from reading the code** (game + OGLE/OGA) |
| `<report>.report_map.json` | the two joined |
| `<report>.summary.md` | **read this first**: the run in numbers and every problem ranked, each with a file:line |
| `<report>.tester_summary.md` / `<report>.engine_summary.md` | tables, class hierarchy, how each key is computed |

If `trace-tester` reports `error`, everything else covers only the spins that ran. If it reports `skipped_kwargs`, pass the tester's arguments with `--kw NAME=VALUE`, using the stake the report was made with.

## Ask one question

`slotdebug map <view> --file <report>` reads the maps (it doesn't run anything again):

| You want | Command |
|---|---|
| The whole chain for a report row | `map tree --row <row>` (or `--key <response key>`, `--site volume_tester.py:92`) |
| Everything suspicious, ranked | `map problems` (`--json` for structured output) |
| Where a method is defined and what its result depends on | `map method --name <method>` |
| The class hierarchy, and which class each method comes from | `map class` / `map class --name <class>` |
| Where a variable or expression is used | `map find --text <text>` |
| The tables | `map rows` / `map keys` / `map sites` |

## Read the answers correctly

- **`fired` / `hits`** (tester line): whether the run reached that line. "Never fired" means not reached in N spins, which isn't proof the line can't run: rare features need a larger `-n`.
- **`response_key_status`**: what the engine actually returned for that key in the run. `never_returned` means the tester reads a key the engine doesn't produce. `always_empty` means a loop over it can never run.
- **Engine tree nodes:** each node has a `file:line` and the `if`/`for` conditions it runs under. `variable` means several assignments can reach that use. `param` means the result depends on that argument, and a parameter missing from the tree doesn't affect it. `external` / `unresolved` mean the trace couldn't follow it.
- **Problems** each come with `from: run`, `code` or `run+code`. A problem from reading the code is a **lead**: open the file:line and confirm before reporting it as a bug.
- The engine map covers only code the traced response keys depend on. `map method` works for any method of the engine's classes.

## In a debugging session

Cite map output as evidence, using the `where` field:

```bash
slotdebug think --thought "stop_list is fixed after the spin" --thoughtNumber 3 --totalThoughts 8 \
  --nextThoughtNeeded true --evidence "engine.py:87" --evidence "slotdebug map problems --file r.csv"
```

When the engine is edited, rebuild the engine map with `slotdebug index --file <report> --path <OGA checkout>` (a hook can do this automatically; see `slotdebug install claude --hook`). Rebuild the tester map with `trace-tester` when the tester changes.
