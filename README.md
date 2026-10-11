# slotdebugger

Tools for debugging slot-game RTP reports. `slotdebug` parses a simulation report, compares it with the game's expected RTP, traces each report row to the volume tester line that emits it and to the engine code that computes it, flags what looks wrong, and records the investigation step by step. It works from a terminal and from Claude Code or Cursor.

| Where to look | For |
|---|---|
| [AGENTS.md](AGENTS.md) | How to change this repo safely: conventions, traps, checklist |
| [knowledge/](knowledge/README.md) | Domain reference: RTP concepts, report format, debugging playbook |
| [docs/](docs/README.md) | Design proposals and analysis for planned work |
| [CHANGELOG.md](CHANGELOG.md) | What changed, and known issues |
| [KNOWLEDGE.md](KNOWLEDGE.md), [Read.md](Read.md) | Project status and architecture notes |

## Installation

Requires Python 3.10+.

```bash
pip install .                     # from a checkout of this repo
pip install -e .                  # editable, for development
```

This installs one command, `slotdebug`. Use a virtualenv (or `pipx install .`) to keep it isolated.

Tracing a volume tester runs the game, so you also need the game's own environment: its Python interpreter (e.g. a venv with OGA's dependencies) and the OGA/OGLE checkout. They're passed with `--python` and `--path` (see [Trace a report to the engine](#trace-a-report-to-the-engine)). The game's environment can be an older Python (3.8+) than slotdebugger's.

## Quick start

In the game's directory:

```bash
slotdebug setup                                   # workspace + Claude Code / Cursor registration
slotdebug add path/to/report.csv
slotdebug analyze --file report.csv               # parse, check totals, build the aggregate
slotdebug diff --file report.csv                  # compare with expected_rtp_report.json, if the game has one
slotdebug trace-tester --file report.csv \
    --python ../venv/bin/python --path ../OGA-5.0.2 -n 5000 --kw stake=1
cat slotdebugger/analysis/report.summary.md       # every problem found, ranked, with file:line
slotdebug map tree --file report.csv --row base_bonus_win
```

## Set up a game workspace

```bash
slotdebug setup                  # or: slotdebug setup --name blazing-7s
slotdebug setup --no-register    # workspace only, no Claude/Cursor registration
```

This creates `./slotdebugger/` next to the game files:

```
slotdebugger/
  workspace.json       game name, schema version, what was registered, the tester copy's origin and hash
  reports/             RTP reports (CSV/XLSX)
  analysis/            everything analyze, diff, trace-tester and index write (see below)
  data/runs.json       history of runs: report, time, status, actual total RTP, issues
  data/commands.json   manifest of available commands, generated from the registry
  state/               thinking-CLI state (.think_state.json) and exports
  tester/              byte-for-byte copy of the game's volume tester, which trace-tester runs
  expected_rtp_report.json   optional: the game's target RTP, and the shape analysis output takes
```

`setup` is idempotent. **Re-run it after upgrading slotdebugger** to bring an existing workspace up to date. It:
- adds directories introduced by newer versions
- migrates `workspace.json` and `data/runs.json`
- refreshes the skills, the command reference and the manifest
- removes skills it registered previously but no longer ships
- reports a volume tester that changed since it was copied, without replacing the copy

It leaves your reports, analysis output and thinking state alone.

Inside a game directory (or any subdirectory) `slotdebug` finds the workspace and **reads and writes only inside it**:

- `slotdebug add path/to/report.csv` copies a report into `reports/`. This is the only place a report is read from outside the workspace.
- `--file report.csv` takes a name from `reports/`; a path outside the workspace is refused.
- `--out`, `--state` and `--export` paths that point outside the workspace are refused.
- `slotdebug runs` lists the recorded runs.

The game's files are never modified. `trace-tester` reads the game's engine and runs the workspace copy of its tester.

Without a workspace, `analyze` still works on any path and only prints JSON.

## Analyze a report

```bash
slotdebug analyze --file report.csv            # prints JSON; saves analysis/report.json; appends to data/runs.json
```

The report is tab-delimited (even for `.csv` and `.xlsx`) and needs a play-count and a total-paid metadata row, plus component rows like `base_line_win<TAB>(40.0, 10)`. See [knowledge/report-format.md](knowledge/report-format.md). A file named `.xlsx` that isn't a real XLSX (not a zip archive) is retried as tab-delimited text.

Metadata field names are normalized to canonical names, e.g. `TOTAL_SPINS` → `Total number of plays`, `TOTAL_WIN` → `Total amount paid`. Unknown names fall back to fuzzy matching.

| Key | Meaning |
|---|---|
| `metadata` | Header fields from the report |
| `components` | Per component: `rtp` (share of total win) and `hit_rate` |
| `dependency_graph` | Parent → children rows. These come only from the game's `engine.py` and `volume_tester.py` (found beside the report or in the workspace, or given with `--engine` / `--volume-tester`); nothing is inferred from row names |
| `analysis` | `status` (`OK`/`FAIL`), `issues` (`Mismatch in <parent>`), `total_rtp` |

A mismatch means a parent differs from the sum of its children by more than 0.001. Errors (missing file, unsupported extension, missing fields) print `error: ...` to stderr and exit with code 2.

Each `analyze` also writes `analysis/<report>.aggregate.json` (a formula for every value) and `<report>.inputs.json` (the report's numbers). `slotdebug aggregate --file <report>` lists what in it needs checking. After you correct it, `--mark-reviewed` makes later runs keep it; `analyze --use-aggregate` uses a specification as it is. The aggregate-review skill describes the process.

### Match the game's own report format

Put the game's target RTP, in whatever shape its maths team uses, in the workspace as `expected_rtp_report.json`. `analyze` then emits **exactly that shape** (same keys, nesting, scale and precision), filled with the measured values:

```json
{"bet": 75, "total": 96.32, "components": {"BG": 40.60, "FG1": 7.82, "FG12": 13.65}}
```

- Any shape works: nested objects, lists of `{"name": ..., "rtp": ...}`, extra labels.
- Scale and precision follow the file: `96.32` gets percentages, `0.9632` gets fractions.
- The game's feature names (`BG`, `FG12`) are matched to report rows heuristically. **Every match is printed as a `note:` on stderr.** A key with no match is left `null` rather than guessed. Check the notes.
- `--expected path/to/file.json` overrides where it's looked for.

### Compare with the expected report

```bash
slotdebug diff --file report.csv
```

This compares the saved analysis with `expected_rtp_report.json` key by key and writes `analysis/<report>.diff.json`. Each key is `ok`, `mismatch` or `missing`, with the expected value, the actual value and the delta. `open` lists every key that isn't `ok`: the keys an investigation has to explain.

The default tolerance is 0.001 of a stake: 0.1 points in percent, or 0.001 as a fraction. Shares and hit rates are compared to 0.001; bet and play counts exactly. `--tolerance` overrides it.

## Trace a report to the engine

```bash
slotdebug trace-tester --file report.csv \
    --python /path/to/game-venv/bin/python --path /path/to/OGA-5.0.2 -n 5000 --kw stake=1
```

One command builds three maps and three summaries:

1. **It runs the volume tester for real.** The workspace copy runs in the game's own interpreter, with `dump_event` wrapped in memory, so line numbers match the original tester. For every tester line that emits an event, it records:
   - the keys actually produced, dynamic ones included
   - the response key the value reads (`p.result['current_winnings']`)
   - the `if`/`for` conditions around the line, and the values that built dynamic keys
   - whether the engine returned that response key (`present`, `always_empty`, `never_returned`)
   - lines that never fired, and events emitted by the framework's base class
2. **It reads the engine, without running it**, backwards from each response key. It follows every place the key is put into a response dict, then the assignments, conditions, method calls (resolved through the class hierarchy into OGLE/OGA), parameters, `self.` attributes and `defs` constants it's computed from.
3. **It joins the two**: report row → tester line → response key → engine code.

| File in `analysis/` | Contents |
|---|---|
| `<report>.tester_map.json` | report key → tester line → response key, **from the run** |
| `<report>.engine_map.json` | response key → engine code, **from reading the code** |
| `<report>.report_map.json` | the two joined |
| `<report>.summary.md` | **read first**: the run in numbers and every problem ranked high/medium/low, each with a file:line |
| `<report>.tester_summary.md` | tables: report rows, tester lines, response keys |
| `<report>.engine_summary.md` | class hierarchy, how each response key is computed, methods on the traced path, suspicious assignments |

Options:
- **`-n`:** the number of spins. A feature that triggers rarely needs more.
- **`--kw NAME=VALUE`** (or `NAME:=JSON`): tester constructor arguments, e.g. the stake the report was made with.
- **`--refresh-tester`:** re-copy the tester after it changes. The old copy is kept beside it.
- **`--live`:** run the game's own tester file instead of the copy.
- **`--no-index`:** write the tester map only.
- **`slotdebug index`:** rebuild the engine map without re-running the tester. `--tree KEY` prints one key's dependency tree.

Problems the maps report include:
- **From the run:**
  - a response key the engine never returns (the tester reads a field the engine doesn't set)
  - a tester line that never fired
  - a report row no tester line produces (the report likely came from another tester version)
- **From reading the code:**
  - a computed value replaced by a fixed one on every path (`stop_list = self.spin(...)` then `stop_list = [123, 123, 123]`, or a list filled and then reset to `[]`)
  - a trailing-comma tuple (`x = 1,`)
  - a parameter that doesn't reach a method's result
  - a response key no engine code sets

A problem found by reading the code is a lead: confirm it at its file:line.

### Ask the maps

`slotdebug map <view> --file <report>` answers one question from the maps without re-running anything:

| View | Answers |
|---|---|
| `tree --row R` / `--key K` / `--site volume_tester.py:92` | The whole chain for a report row, response key or tester line |
| `problems [--json]` | Everything suspicious, ranked, with where to look |
| `method --name M` | Where M is defined (game or which OGLE/OGA class), which version the engine uses, parameters that don't reach its result, its callers, what its result depends on |
| `class [--name C]` | The class hierarchy, and which class each method comes from |
| `find --text T` | Where T appears on the traced paths |
| `rows` / `keys` / `sites` / `summary` | The tables and the summary |

For example, `slotdebug map tree --file report.csv --row action_bonus_win` prints (shortened):

```
REPORT ROW  action_bonus_win  [traced]
  TESTER    tests/volume_tester.py:92  dump_map_result  fired 300x
  RESPONSE  bonus_prize_winning  [present]
  ENGINE    'bonus_prize_winning': bonus_prize_winning  (engine.py:175 base_game)
              bonus_prize_winning = self.apply_bonus_multipliers(...)  (engine.py:132)  [when len(bonus_symbol_reel) >= 3]
                -> apply_bonus_multipliers returns total_bonus_winning ...
```

## Track the investigation

`slotdebug think` records an investigation as numbered thoughts, branches and findings, each backed by evidence:

```bash
slotdebug think --reset
slotdebug think --setTargets slotdebugger/analysis/report.diff.json     # the keys that must be explained
slotdebug think --setPlan '["Pick the failing total", "Trace in the engine", "Conclude"]'
slotdebug think --thought "bonus is computed only with 3+ bonus reels" --thoughtNumber 1 --totalThoughts 5 \
    --nextThoughtNeeded true --evidence "engine.py:128"
slotdebug think --thought "the win cap may clip FG12" --thoughtNumber 2 --totalThoughts 5 --nextThoughtNeeded true \
    --branchFromThought 1 --branchId fg12-cap --target components.FG12 --cause cap --evidence engine.py:143
slotdebug think --conclude fg12-cap --verdict refuted --reason "cap is applied after the event value" --evidence engine.py:143
slotdebug think --finding "..." --target components.FG12 --evidence engine.py:87
slotdebug think --status          # plan, open branches, unexplained targets
```

- **Thoughts:** numbers must be sequential. Use `--isRevision --revisesThought N` to correct one, and `--branchFromThought N --branchId ID` to explore an alternative.
- **Evidence:** `--evidence` (repeatable) cites a file:line or a command. A thought without it is saved with a warning; conclusions and findings require it.
- **Never asking the same question twice:** a branch's `--target` + `--cause` is one question, whatever the branch is called. A pair that was already concluded is refused, and the error shows the earlier verdict. `--force` overrides.
- **Repeats are flagged:** a new thought that shares evidence with an earlier one, or is worded alike, gets a warning. `--similar TEXT` checks before you write.
- **Finishing:** `--nextThoughtNeeded false` is refused while a branch is open or a target has no finding.
- **State:** saved atomically to `slotdebugger/state/.think_state.json` (or `./.think_state.json` outside a workspace). `--state PATH` keeps a separate session.

### Typical debugging flow

0. `slotdebug setup` once per game, then `slotdebug add report.csv`.
1. `slotdebug analyze --file report.csv`, then `slotdebug diff --file report.csv`.
2. `slotdebug trace-tester --file report.csv --python ... --path ...`, then read `analysis/report.summary.md`.
3. `slotdebug think --setTargets` with the diff file. For each open key, use `slotdebug map tree` / `map method` to find the engine code, open one branch per suspected cause, and conclude each with evidence.
4. Record a `--finding` per key; `slotdebug think --export session.json` keeps the record.

With the skills installed, Claude Code and Cursor follow the same flow.

## The slot-debugger agent (Claude Code)

`slotdebug setup` registers a **subagent**, `slot-debugger`, in `.claude/agents/`. When a conversation needs an RTP problem diagnosed, Claude delegates to it, or you can ask for it by name.
- **Its own context.** It runs the whole procedure above in a separate context: analyze, diff, trace-tester, the maps, and a `think` session with evidence.
- **What it returns.** It finishes with `slotdebug report`, which writes the result the parent reads, `analysis/<report>.agent_report.json` (and `.md`):
  - status
  - findings, each with the report keys it explains, file:line evidence checked to exist, and a **proposed fix**
  - every hypothesis it refuted
  - unexplained targets
  - problems the maps report that no finding cites
  - next steps

It is **diagnose-only by construction**. It has no Edit/Write tools, and a PreToolUse guard in its definition (`slotdebug hook agent-bash`) refuses any Bash command that isn't `slotdebug` or read-only (`cat`, `grep`, `sed -n`, `git log/show/diff`, …). The parent conversation reviews the diagnosis and applies the fixes.

With the hooks installed (`slotdebug install claude --scope project --hook`):
- **SubagentStop:** the agent can't finish until `slotdebug report` is written and all its evidence resolves. If not, it's sent back once with what's missing.
- **PostToolUse on the agent:** its diagnosis (findings, proposed fixes, next steps) is added to the parent's context.
- **UserPromptSubmit:** a prompt that names a report in the workspace gets a note offering both ways to diagnose it (see below).

### Without the agent

The agent is optional: a session can run the same diagnosis itself. The steps are plain `slotdebug` commands, set out under "Diagnose a report" in the `slot-debugger` skill: analyze, diff, trace-tester, a `think` session with evidence, then `slotdebug report`. This is the only way in Cursor, which has no subagents. In Claude Code, `/slotdebug:diagnose <report>` starts it in the current conversation, and `/slotdebug:debug <report>` delegates it to the agent. Both ways write the same `agent_report.json`. In the session itself, `slotdebug report` is written before any file is changed, so the diagnosis is recorded before the fixes go in. Only the agent is read-only by construction.

## Claude Code, Cursor, hooks and the plugin

| Install as | Command | What you get |
|---|---|---|
| Skills, agent + command reference, per game | `slotdebug setup` | Skills `slot-debugger`, `slot-map`, `sequential-thinking`, `aggregate-review` in `./.claude/skills/` and `./.cursor/rules/`; the `slot-debugger` agent in `./.claude/agents/`; the command list in a marked block of `./CLAUDE.md` and an always-applied Cursor rule |
| Skills, explicitly | `slotdebug install claude [--scope user\|project]`, `slotdebug install cursor` | The same skills; user scope (`~/.claude/skills`) or project scope |
| Claude Code hooks | `slotdebug install claude --scope project --hook` | **Session start:** the newest maps' problems, and whether game files changed since they were built. **After Edit/Write:** when the edited file is one the engine map was read from, the engine map is rebuilt and the problems that are gone or new are reported. **The agent's hooks:** the finish check, passing its result to the parent, and the prompt note (see above) |
| Claude Code plugin | `slotdebug install plugin`, then `claude plugin marketplace add ~/.local/share/slotdebug/claude-marketplace` and `claude plugin install slotdebug@slotdebug` | The skills, the agent (`slotdebug:slot-debugger`), the hooks, and `/slotdebug:debug` (via the agent), `/slotdebug:diagnose` (in the session), `/slotdebug:tree`, `/slotdebug:problems`, `/slotdebug:method`, `/slotdebug:summary`. Plugins can't carry an agent's own hooks, so the plugin's agent has no Bash guard: no Edit/Write tools and its instructions keep it diagnose-only |

- **Removing it:** `slotdebug uninstall <target>` removes what slotdebug installed and nothing else. `CLAUDE.md` keeps everything outside its marked block, and other hooks in `settings.json` are left alone.
- **Re-installing:** the hooks and plugin call `slotdebug` by its absolute path, so re-run the install after moving the virtualenv.
- **Cursor:** gets the skills as rules. Hooks and the agent are Claude Code only.
- **First run on a machine:** the first `slotdebug` command registers the skills at user scope once. Set `SLOTDEBUG_NO_AUTOREGISTER=1` to turn this off.

Every command describes itself:

```bash
slotdebug command-list             # names only
slotdebug command-list --detail    # description, arguments, return value, examples
slotdebug <command> -h             # exact flags
```

## Known limitations

- **Report formats:** the parser reads `name<TAB>(win, hits)` rows. Reports that put wins and hits in separate columns, or text exports in UTF-16, give no rows, and `trace-tester` warns that nothing could be matched.
- **Metadata normalization** is string-based and can collide: a component row named exactly `total_win` is taken as `Total amount paid` and overwrites it. Check `Total amount staked` and `Total amount paid` in the output. See [docs/PLAN_RAG_METADATA_MAPPING.md](docs/PLAN_RAG_METADATA_MAPPING.md).
- **Engine map:** it's built from reading the code.
  - A dict that carries a response key but never becomes part of a response is also listed as a starting point.
  - Calls through an object of unknown type, such as `self.engine.method()` in state-machine engines, aren't followed.
  - Unpacking (`a, b = f()`) depends on everything `f` returns.
- **Tester map:** "never fired" means not reached in N spins, not proof that a line can't run.

## Development

```bash
pip install -e . pytest
pytest
```

| Package | Responsibility |
|---|---|
| `report_parser` | CSV/XLSX → report; metadata field normalization |
| `RTPNormalizer` | normalize components, dependency graph, analysis |
| `rtp_aggregator` | the aggregate's formula language; reading the volume tester and engine for dependencies |
| `engine_indexer` | `trace-tester` (run the tester traced), `index` (engine map from reading the code), `map` views and summaries |
| `sequential_thinking` | `think.py`: the persistent investigation CLI |
| `slotdebugger` | CLI, pipeline, registry, workspace, installer, hooks, bundled skills |
| `slotdebugger.report_formatter` | re-shape the analysis into the game's expected-report format; `diff` |

Read [AGENTS.md](AGENTS.md) before making changes. It records the conventions and the non-obvious traps (silent parser failures, alias collisions, stale `build/` trees). Record user-visible changes in [CHANGELOG.md](CHANGELOG.md) as part of the change.

### Adding a command

Declare it in `_register_all_commands()` in [slotdebugger/cli.py](slotdebugger/cli.py), alongside its argparse parser, and list every flag under `args`. The registry is what puts the command into:
- `slotdebug command-list`
- each workspace's `commands.json`
- the command reference `setup` writes to `CLAUDE.md` and the Cursor rule

Tests fail if a CLI command or flag is missing from it. See [slotdebugger/registry.py](slotdebugger/registry.py).
