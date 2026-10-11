# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Record every user-visible change under `[Unreleased]` as part of the change itself,
not afterwards. See [AGENTS.md](AGENTS.md).

---

## [Unreleased]

### Added

- `slot-debugger` subagent (`slotdebugger/agents/slot-debugger.md`), registered by
  `slotdebug setup` (project `.claude/agents/`), `install claude` and the plugin. It diagnoses
  only: tools Bash, Read, Grep, Glob (no Edit/Write), and a PreToolUse guard in its front
  matter (`slotdebug hook agent-bash`) refuses Bash that is not `slotdebug` or read-only
  (redirection, `sed -i`, `rm`, `git checkout`, `find -delete`, `slotdebug install` ...). It
  runs analyze -> diff -> trace-tester -> maps -> a think session, and ends with
  `slotdebug report`. Plugins ignore agent hooks, so the plugin's agent has no guard.
- `slotdebug report [--file R] [--json]` (`slotdebugger/agent_report.py`): the diagnosis
  for the parent process, `<report>.agent_report.json` / `.md`: status, targets with
  expected/actual, findings with evidence (each cited `file.py:N` checked to exist) and a
  proposed fix, branch verdicts, open branches, unexplained targets, map problems no
  finding cites, next steps.
- `think --finding ... --fix "<change>"`: the fix the diagnosis proposes.
- Hooks for the agent (registered by `install claude --hook` and the plugin): SubagentStop
  `agent-stop` sends the agent back once until the report is written, newer than its
  session, with resolving evidence; PostToolUse on `Agent` `agent-result` gives the parent
  the diagnosis; UserPromptSubmit `prompt` suggests the agent when a prompt names a report
  in the workspace. Plugin command `/slotdebug:debug <report>`.
- The diagnosis runs without the agent too. The `slot-debugger` skill's "Diagnose a report"
  section has the full procedure (workspace, measure, map, investigate, report, then fix
  and confirm), so a session runs it inline, including in Cursor. The plugin command
  `/slotdebug:diagnose <report>` starts it in the current conversation, with no file changed
  before `slotdebug report` is written. The prompt hint offers both ways. The report's
  `mode` now reads "diagnosis: fixes are proposed, not applied" in both modes.
- `slot-map` skill (Claude Code skill and Cursor rule, registered by `setup`): which engine
  code produces a report value: building the maps, the `map` views, reading the answers.
- `slotdebug hook session-start|post-edit` (`slotdebugger/hooks.py`): editor hooks that
  answer in Claude Code `hookSpecificOutput` JSON (`--format text` for plain). Session
  start adds the newest maps' problems and flags game files changed since they were
  built; post-edit re-indexes the engine (and rewrites the engine/report maps and
  summaries) when the edited file is one the engine map was read from, and reports
  problems now gone or new. Silent without a workspace or maps; never fails the editor.
  `install claude --hook` now registers both (PostToolUse on Edit/Write/MultiEdit),
  replacing the earlier `think --status` SessionStart hook and leaving other hooks alone.
- `slotdebug install|uninstall plugin [--dir D]`: a local Claude Code marketplace
  (default `~/.local/share/slotdebug/claude-marketplace`) with the `slotdebug` plugin:
  the skills, both hooks, and `/slotdebug:tree`, `:problems`, `:method`, `:summary`.
  Validated with `claude plugin validate`. Uninstall removes only a marketplace it made.
- Engine map records `paths` (to rebuild it) and `files` (every file it read).
- `slotdebug map <view>` (`engine_indexer/views.py`): answers one question from the three
  maps without re-running anything: `tree` (report row / response key / tester line ->
  tester line, conditions, hits -> response key and whether it was returned -> engine
  code), `rows`, `keys`, `sites`, `method` (where a method is defined, which engine class
  uses that version, parameters that do not reach its result, callers, what its result is
  computed from), `class` (hierarchy and which class each method comes from), `find`,
  `problems` (ranked, with file:line and whether from the run or the code; `--json`),
  `summary`. `trace-tester` / `index` also write `<report>.summary.md` (overview +
  problems), `<report>.tester_summary.md` and `<report>.engine_summary.md` beside the maps.
- Engine map: `methods` (every method the engine classes can run, with file, lines,
  parameters, game/framework origin) and `detections` on the traced path: a computed value
  replaced by a fixed one on every path (`overwritten_by_literal`, e.g. `stop_list =
  [123, 123, 123]` after `self.spin()`, or a filled list reset to `[]`) and a trailing-comma
  one-element tuple (`x = 1,`).

### Fixed

- Engine map: a change made through a loop variable (`for line in line_wins: line['x'] *= m`)
  is a change to the collection, and a parameter's value still reaches a use alongside
  conditional or in-place assignments; both previously made a used parameter look unused.
- `slotdebug index` (`engine_indexer.code_index`, `slicer`, `engine_map`): indexes the
  engine backwards from its response keys, reading code only. Modules are found on disk
  (`--path` for the framework checkout), `from X import Y` is followed through
  re-exports, and classes get a C3 MRO across game, OGLE and OGA (unfound bases are
  listed). Each place the engine or an inherited class puts a key in a dict is a root;
  from it, a backward slice follows reaching assignments (with their `if`/`for`
  conditions; an assignment always overwritten on the same path is dropped; `+=`,
  `x.append(v)` and `x[k] = v` keep the earlier value), `self.method()` through the MRO
  into its returns, parameters back to the arguments passed (an argument that doesn't
  reach the result has no edge), `self.attr` to every store in the hierarchy, and
  `defs.X` / module constants. Keys come from the workspace's tester map, which is joined
  (`chain`: report row -> tester line -> response key -> engine roots;
  `keys_without_engine_root`). Writes `analysis/<report>.engine_map.json`; `--tree KEY`
  prints the dependency tree as text; `--depth` limits call depth.
  Imports inside module-level `if`/`try`/`with` blocks are read (OGA 4.8.1 re-exports
  `OGLE_Slot` under `if PACKAGE_MODE:`), and `ClassName.method(...)` (static/class methods
  of a class the index can resolve) is followed like `self.method(...)`.
  The join is written to its own file, `analysis/<report>.report_map.json` (with the
  paths of the tester map and engine map it joins), so the engine map holds the engine
  only.
- `trace-tester` writes all three maps in one run: after the tester map it indexes the
  engine from the traced response keys (engine beside the tester, or `--engine`; same
  `--path`; `--depth`) and writes `<report>.engine_map.json` and `<report>.report_map.json`.
  `--no-index` writes the tester map only. Outside a workspace the engine and report maps
  go beside `--out`. A missing engine or unreadable engine is a note, not a failure of
  the trace.
- Command registry: every CLI flag is now listed (trace-tester `--class`, `--depth`,
  `--seed`, `--timeout`, `--out`; index `--tester-map`, `--out`; think `--thoughtNumber`,
  `--totalThoughts`, `--nextThoughtNeeded`, `--branchId`, `--revisesThought`,
  `--verdict`, `--reason`, `--export`, `--state`; install `--hook`), so the generated
  CLAUDE.md / Cursor command reference shows them. A test fails if a flag accepted by the
  CLI is missing from the registry.
- `trace-tester` checks response keys against the engine: the tracer wraps `play` on the
  tester's engine instance and counts, for every result returned, each top-level key and
  whether it was empty (`response_keys`, `results_seen`). Each site gets
  `response_key_status` (`present` / `always_empty` / `never_returned` / `unknown`), and
  `missing_response_keys` lists the lines reading a key the engine never returns or only
  returns empty; the CLI prints a note for each.
- `slotdebug setup` (and `install --scope project`) writes an always-on command reference,
  generated from the command registry: a marked block in the project's `CLAUDE.md`
  (created if missing; only the text between `<!-- slotdebug:commands:start -->` and
  `<!-- slotdebug:commands:end -->` is ever rewritten) and a Cursor rule
  `.cursor/rules/slotdebug-commands.mdc` with `alwaysApply: true`. Re-running setup
  refreshes both; `uninstall` removes the block (and the file only if nothing else is in
  it) and the rule. A user-scope install never edits `CLAUDE.md`. The `think` registry
  entry now lists its real flags, `add`'s report argument is marked positional, and
  `install` / `uninstall` are registered; a test fails if a CLI sub-command is missing
  from the registry.
- `engine_indexer` package and `slotdebug trace-tester`: runs the game's volume tester
  unchanged in the game's own interpreter (`--python`, `--path` for the OGA checkout,
  `--kw` for constructor arguments) with its `dump_event` wrapped where it is defined, and
  writes `analysis/<report>.tester_map.json`: per emitting tester line, the keys the run
  produced (dynamic ones included), the engine response key the value reads, the
  `if`/`else`/`for` chain around the call and the run-time values that built the key.
  Lines the run never reached are kept (`never_fired`); events emitted by the
  framework's base class are included (`source: inherited`). With `--file`, report rows
  are matched (`traced` / `pattern` / unexplained), and a report with no readable rows is
  flagged rather than reported as fully matched. The tracer is standard-library only and
  runs on Python 3.8+.
- Workspace `tester/` (schema 3): `slotdebug setup` copies the game's volume tester into
  it byte for byte and records the original's path and hash in `workspace.json`;
  `trace-tester` runs that copy, loaded under the original's module name so its relative
  imports resolve in the game's package. When the copy still matches the original, sites
  are labelled with the original's path (same lines); when the original has changed, the
  copy keeps running, labelled with its own path, with a note (`setup` re-runs report the
  change without replacing the copy). `--refresh-tester` re-copies, keeping the old copy
  as `volume_tester.<sha8>.py`; `--live` runs the game's own file. Each map records
  `ran`, `labelled_as` and `game_files` (hashes of the tester, engine, defs and other game
  modules loaded). Existing workspaces get `tester/` by re-running `slotdebug setup`.
- `think --evidence REF` (repeatable): cite the `file:line` or command a thought rests on;
  a thought without it is saved with a warning. `think --conclude BRANCH --verdict
  confirmed|refuted --reason ... --evidence ...` closes a branch (no further thoughts on
  it), and `think --finding ... --evidence ...` records a final result (warns while
  branches are open). `--status` shows open/concluded branches and the finding count;
  `--history` prints conclusions and findings. Older state files load unchanged.
- `slotdebug diff --file <report> [--expected PATH] [--tolerance X]`: compares the saved
  analysis with `expected_rtp_report.json` key by key (`ok` / `mismatch` / `missing`, with
  expected, actual and delta), writes `analysis/<report>.diff.json` and lists the keys that
  are not `ok` under `open`. Tolerance is 0.001 of a stake in the template's scale; shares
  and hit rates to 0.001; bet and play counts exactly. Registered in `command-list`.
- `think --setTargets <diff file | keys>`: the keys a session must explain. Branches take
  `--target KEY --cause CAUSE`; a target + cause already concluded (or open in another
  branch) is refused whatever the branch is called, with the earlier verdict shown
  (`--force` to override, recorded on the thought). Findings name the targets they explain
  (`--target`, repeatable). `--nextThoughtNeeded false` is refused while a branch is open
  or a target has no finding (`--force` to override, with a warning).
- `think` warns when a new thought may already be covered: same `--evidence` as an
  earlier entry, or alike wording (word-level, `file:line` kept whole). Entries about
  another target or from the branch being continued are skipped. `think --similar TEXT
  [--target KEY] [--evidence REF]` lists the matches as JSON before writing anything.
- sequential-thinking skill: a "Debugging a game's engine.py" plan (events -> result
  fields -> engine assignments -> inherited OGLE code -> aggregate -> conclude), and a
  "Debugging a report against the expected report" loop (analyze -> diff -> setTargets ->
  one branch per target and cause -> conclude -> finding per target); the slot-debugger
  skill's debug loop now starts from `slotdebug diff`.
  The skill's description (what Claude/Cursor match a request against) now names
  `slotdebug think` and debugging a report against the expected report.
- `analyze --use-aggregate [PATH]`: use the aggregate specification as it is. PATH, else
  the report's saved `aggregate.json`, else (outside a workspace) the user is asked for a
  path (a clear error when not on a terminal). A file that has components is never
  overwritten: its formulas are evaluated against this report's inputs, no aggregate or
  inputs file is written, and if they do not fit, `analyze` fails naming the missing input
  instead of regenerating. Only a file that is missing or empty is generated and written
  (the inputs file too, only if it is empty); a file that is not valid JSON is an error,
  never overwritten. `aggregate.status` is `provided` when the file was used.
- `aggregate-review` skill and `slotdebug aggregate --file <report>`: after `analyze`
  writes `aggregate.json`, an LLM follows the skill to check and correct it against the
  game's `engine.py` and `volume_tester.py`. The command lists findings with a fix
  direction each (`invalid`, `total_mismatch`, `incomplete_total`, `unscoped`,
  `unmatched_event`, `unexplained_row`; `rtp_aggregator/review.py`), and
  `--mark-reviewed` (refused if the file does not evaluate) makes `analyze` keep the
  corrected formulas instead of regenerating them; they stay valid when only the numbers
  change. If the report's rows change so they no longer evaluate, the reviewed file is
  moved to `*.aggregate.stale.json`, never deleted. The skill forbids editing the inputs file and patching a mismatch so a real game bug stays visible.
- `analyze` builds `analysis/<report>.aggregate.json` (formulas) with `analysis/<report>.inputs.json`
  (the report's measured numbers, kept apart and regenerated every run), and the normalizer's component
  RTPs and hit rates are that file's formulas evaluated (`rtp_aggregator`), not computed
  in the normalizer: `<row>.rtp` = win / total win, `<row>.hit_rate` = frequency /
  plays, `<row>.rtp_vs_stake` = share x overall RTP, and `<parent>.children_sum` for
  every parent in the dependency graph. The measured numbers sit under `inputs`, so
  the file recomputes on its own. The game's `volume_tester.py` (`--volume-tester`, or
  found beside the report / in the workspace) is read for the event names it emits
  (`dump_event(...)`, run-time parts as `*`) and cross-checked against the report:
  `volume_tester.unmatched_events` / `unmatched_components` list disagreements. All
  computed values are returned under `aggregate.components`.
  Implemented in `rtp_aggregator/builder.py`; `RTPNormalizer` takes them as `measured`.
  When the game has an `expected_rtp_report.json`, the aggregate's formulas cover only
  the rows that file's keys resolve to and what they depend on (`inputs` still lists
  every report row) (a parent's children, all the
  way down), and the shaped report reads those values from the aggregate
  (`<row>.rtp_vs_stake`). Rows outside it are still normalized and checked directly.
  Dependencies are read from the game's code, not guessed from row names: the volume
  tester says which result field each event reports, the engine says how that field is
  computed (`current_winnings = bonus_prize_winning + current_line_winnings`), and the
  two chain into a parent -> children graph (`rtp_aggregator/sources.py`, `--engine`,
  found beside the volume tester or one directory above). A total is only summed
  (`children_sum`) when the engine adds it up and every part has an event and a row;
  otherwise it is recorded as incomplete under `derived_from`. Without both files there
  are no dependencies; nothing is inferred from row names.
- Analysis output can follow the game's own format: when an `expected_rtp_report.json`
  is present, `analyze` emits exactly that file's shape, keys, scale and precision,
  filled with the measured values, so expected and actual can be diffed directly. Any
  shape works, the game's feature names (`BG`, `FG12`, ...) are matched against the
  report's component rows, every match is reported as a note on stderr, and an
  unmatched key is left `null` rather than guessed at. `--expected` points at a
  specific file; without one the native format is unchanged. `data/runs.json` keeps
  recording the native status and total RTP either way.
  Implemented in `slotdebugger/report_formatter/`: `template.py` (find and load the
  file, read its scale and precision), `naming.py` (interpret the game's key names
  and match them to report rows) and `shaper.py` (walk the template, filling in
  measured values).
- `slotdebug command-list` and `command-list --detail` to list registered commands
  with their arguments, return values and examples.
- Command registry (`slotdebugger/registry.py`) holding the metadata for every
  command in one place, so the CLI can describe itself.
- `slotdebug setup` now registers the Claude Code skills and Cursor rules at
  **project** scope into the new game directory, and writes a command manifest to
  `slotdebugger/data/commands.json`. Previously registration ran once per machine at
  user scope, so a new game workspace got nothing.
- `slotdebug setup --no-register` to create a workspace without registering.
- `AGENTS.md` with repository conventions for humans and coding agents.
- Metadata field-name normalization: engine-specific report headers such as
  `TOTAL_SPINS`, `TOTAL_WIN` and `RTP` now map onto canonical names, with fuzzy
  matching as a fallback for unknown names.

### Fixed

- Final report numbers are no longer worked out by assumption; every one is an aggregate
  formula evaluated. The `total` in a game's expected report was the analyzer's
  `total_rtp`, the summed share of every row whose name contains `_total`: overlapping
  sub-breakdowns (jackpot rows in credits, `*_total_winnings_triggering`) made it 2366.66
  where the report's own Game RTP is 96.2885. It is now the aggregate's `total_rtp`
  (the RTP the report states). Also removed: the row-name dependency graph
  (`<prefix>_total_*`), the shaper's `bet` = staked / plays and its paid / staked RTP
  fallback, and the normalizer's direct calculation for rows outside the aggregate. A
  value with no formula is `null` with a `note:` (`bet` needs an aggregate formula
  from the engine's stake). `analysis.total_rtp` is now an RTP fraction (0.9632), not a
  sum of shares, and is `null` when the report states no RTP; `analysis.issues` come
  from `children_sum` formulas, so totals are only checked when an engine and volume
  tester give the dependencies; `components` holds only the rows the aggregate covers.
- `slotdebug setup` failed to register a newly added skill on an installed copy ("No such file or directory: .../site-packages/slotdebugger/skills/aggregate-review.md"): `pyproject.toml` declared no package data, so setuptools packed only the skill files a stale `*.egg-info/SOURCES.txt` listed. `skills/*.md` is now declared explicitly. After upgrading, `rm -rf build *.egg-info && pip install .`.
- A file with an `.xlsx` extension that is not a real zip archive is now retried as
  tab-delimited text instead of raising `BadZipFile`. Several real reports are
  exported this way.
- `Workspace.resolve_report()` accepts both `report.xlsx` and `reports/report.xlsx`;
  the second form previously failed with "not found".
- `Workspace.runs()` no longer raises `AttributeError` when `data/runs.json` holds a
  legacy object instead of a list of runs.

### Known issues

- **Metadata alias collision.** `STAKE_SPINS` (a spin *count*) and `TOTAL_STAKE` (a
  currency *amount*) both normalize to `Total amount staked`; the last one parsed
  wins and silently overwrites the correct value. Field normalization compares
  strings without checking units. Pinned by an `xfail` test in
  `report_parser/tests/test_parser.py`; analysis and planned fix in
  [docs/PLAN_RAG_METADATA_MAPPING.md](docs/PLAN_RAG_METADATA_MAPPING.md).
- Reports whose header names are lexically distant from canonical names
  (`MAX_WIN`, `HIT_RATE`, `STD_DEV`) are dropped from metadata without a warning.

### Changed

- `slotdebug setup` is now idempotent. Re-running it on an existing workspace
  upgrades it in place instead of failing with "workspace already exists": missing
  directories are created, `workspace.json` is migrated to the current schema
  (version 2, preserving the original `created` timestamp), skills and the command
  manifest are refreshed, and skills this tool registered previously but no longer
  ships are removed. Reports, analysis output and thinking state are never touched.
- A legacy object in `data/runs.json` is moved to `data/runs.legacy.json` rather than
  being ignored, and `runs.json` is replaced with an empty run list.
- `workspace.json` records which skills and rules were registered, so a later `setup`
  knows what to retire.
- Repository layout: design and planning documents moved to `docs/`.
- Removed stale build artifacts: a duplicate source tree under `build/lib/` and an
  `egg-info` directory left over from the earlier `slot_debugger` package name. Both
  could shadow imports from the real source tree.

---

## [0.1.0] — unreleased

Initial toolkit: report parsing (`report_parser`), RTP normalization and analysis
(`RTPNormalizer`), the persistent sequential-thinking CLI (`sequential_thinking`),
and the `slotdebug` command with per-game workspaces and Claude Code / Cursor
integration (`slotdebugger`).
