# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Record every user-visible change under `[Unreleased]` as part of the change itself,
not afterwards. See [AGENTS.md](AGENTS.md).

---

## [Unreleased]

### Added

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
