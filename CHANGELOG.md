# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Record every user-visible change under `[Unreleased]` as part of the change itself,
not afterwards. See [AGENTS.md](AGENTS.md).

---

## [Unreleased]

### Added

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
