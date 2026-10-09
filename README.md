# slotdebugger

Tools for debugging slot-game RTP reports: parse a CSV/XLSX simulation report, normalize it, check that parent totals equal the sum of their children, and track the investigation with a persistent step-by-step thinking CLI. Includes Claude Code / Cursor integration.

| Where to look | For |
|---|---|
| [AGENTS.md](AGENTS.md) | How to change this repo safely — conventions, traps, checklist |
| [knowledge/](knowledge/README.md) | Domain reference: RTP concepts, report format, debugging playbook |
| [docs/](docs/README.md) | Design proposals and analysis for planned work |
| [CHANGELOG.md](CHANGELOG.md) | What changed, and known issues |
| [KNOWLEDGE.md](KNOWLEDGE.md), [Read.md](Read.md) | Project status and architecture notes |

## Installation

Requires Python 3.10+.

```bash
pip install slotdebugger          # once published to PyPI
pip install .                     # from a checkout of this repo
pip install -e .                  # editable, for development
```

This installs the single command `slotdebug`. Use a virtualenv (or `pipx install .`) if you want them isolated.

### Claude Code / Cursor registration

Registration happens in two places:

- **`slotdebug setup`** registers at **project scope** into the new game directory: skills in `./.claude/skills/`, rules in `./.cursor/rules/`, and a command manifest in `slotdebugger/data/commands.json`. Pass `--no-register` to skip. Registration failures warn and never block workspace creation.
- **First `slotdebug` command on a machine** registers at **user scope** (`~/.claude/skills/`) once, since pip cannot run code at install time. Set `SLOTDEBUG_NO_AUTOREGISTER=1` to disable.

To do it explicitly:

```bash
slotdebug install claude                    # skills in ~/.claude/skills
slotdebug install claude --scope project    # skills in ./.claude (this project only)
slotdebug install claude --hook             # also add a SessionStart hook: `slotdebug think --status`
slotdebug install cursor                    # rules in ./.cursor/rules
slotdebug install all --hook                # everything
slotdebug uninstall all                     # remove skills, rules and the hook
```

Options: `--scope user|project` (Claude only), `--dir <project>` (default: current directory), `--hook` (Claude only; never added automatically). Installing is idempotent.

## Usage

### Set up a game workspace

Each game gets its own workspace. In the game's directory:

```bash
slotdebug setup                  # or: slotdebug setup --name blazing-7s
slotdebug setup --no-register    # workspace only, no Claude/Cursor registration
```

This creates `./slotdebugger/` next to your game files, and registers the skills with Claude Code and Cursor for that project:

```
slotdebugger/
  workspace.json       game name, schema version, what was registered
  reports/             RTP reports (CSV/XLSX)
  analysis/            one analysis JSON per report
  data/runs.json       history of runs: report, time, status, actual total RTP, issues
  data/commands.json   manifest of available commands, generated from the registry
  state/               thinking-CLI state (.think_state.json) and exports
  expected_rtp_report.json   optional: the game's target RTP, and the shape analysis output takes
```

`setup` is idempotent — **re-run it after upgrading slotdebugger** to bring an existing workspace up to date. It adds directories introduced by newer versions, migrates `workspace.json` and `data/runs.json`, refreshes the skills and command manifest, and removes skills it registered previously but no longer ships. It reports what it changed, and leaves your reports, analysis output and thinking state alone.

Inside a game directory (or any subdirectory of it) `slotdebug` finds the workspace and **reads and writes only inside it**:

- `slotdebug add path/to/report.csv` copies a report into `reports/`. This is the only place a file is read from outside.
- `slotdebug analyze --file report.csv` takes a name from `reports/`; a path outside the workspace is refused.
- Analysis results, run history and thinking state are all saved in the workspace; `--out`, `--state` and `--export` paths that point outside it are refused.
- `slotdebug runs` lists the recorded runs.

Without a workspace, `analyze` still works on any path and only prints JSON.

### Analyze a report

```bash
slotdebug add report.csv
slotdebug analyze --file report.csv            # prints JSON, saves analysis/report.json, appends to data/runs.json
slotdebug analyze --file report.csv --out summary.json    # extra copy under analysis/
```

The report is tab-delimited (even for `.csv` and `.xlsx`) and needs a play-count and a total-paid metadata row, plus component rows like `base_line_win<TAB>(40.0, 10)`. See [knowledge/report-format.md](knowledge/report-format.md).

Metadata field names are normalized to canonical names, so engine-specific headers work without editing the report. For example `TOTAL_SPINS` → `Total number of plays`, `TOTAL_WIN` → `Total amount paid`, `RTP` → `Game RTP`. Unknown names fall back to fuzzy matching.

A file with an `.xlsx` extension that is not a real XLSX (not a zip archive) is retried as tab-delimited text, so exported reports that only *look* like spreadsheets still parse.

> **Known limitation.** Field normalization is string-based and can collide: a count field and an amount field with similar names may map to the same canonical key, and the last one wins silently. Verify that `Total amount staked` in the output matches the report. See [docs/PLAN_RAG_METADATA_MAPPING.md](docs/PLAN_RAG_METADATA_MAPPING.md) for the analysis and the planned fix.

Output is JSON:

| Key | Meaning |
|---|---|
| `metadata` | Header fields from the report |
| `components` | Per component: `rtp` (share of total win) and `hit_rate` |
| `dependency_graph` | `<prefix>_total_*` parent → `<prefix>_*` children |
| `analysis` | `status` (`OK`/`FAIL`), `issues` (`Mismatch in <parent>`), `total_rtp` |

A mismatch means a parent differs from the sum of its children by more than 0.001. Errors (missing file, unsupported extension, missing fields) print `error: ...` to stderr and exit with code 2.

### Match the game's own report format

Every game states its target RTP in its own shape, with its own names for features. Put that file in the workspace as `expected_rtp_report.json` and `analyze` emits **exactly that shape** — same keys, same nesting, same scale — filled with the values measured from the report, so expected and actual can be diffed line by line.

```json
{"bet": 75, "total": 96.32, "components": {"BG": 40.60, "FG1": 7.82, "FG12": 13.65}}
```

```bash
slotdebug analyze --file report.csv
note: formatted like slotdebugger/expected_rtp_report.json
note: components.BG -> base_total_win
note: components.FG1 -> freegame_1_total_win
{
  "bet": 75.0,
  "total": 96.32,
  "components": {"BG": 40.6, "FG1": 7.81, "FG12": 13.64}
}
```

- Any shape works: nested objects, lists of `{"name": ..., "rtp": ...}`, extra labels. Keys it does not measure (strings, units, game names) pass through unchanged.
- Scale and precision follow the file: `96.32` gets percentages, `0.9632` gets fractions, and each value is rounded to the number of decimals the file states.
- The game's own feature names (`BG`, `FG12`, ...) are matched against the report's component rows heuristically; **every match is reported as a `note:` on stderr**, and a key with no match is left `null` rather than guessed at. Check the notes.
- It is looked for beside the report, in the workspace root, `data/` and `reports/`, and in the game directory; `--expected path/to/file.json` overrides.
- Without such a file the native analysis format above is used. Either way `data/runs.json` records the native status, total RTP and issues, so `slotdebug runs` stays comparable across games.

### Discover available commands

Every command is declared in a central registry, so the CLI can describe itself:

```bash
slotdebug command-list             # names only
slotdebug command-list --detail    # description, arguments, return value, examples
```

The same registry is written to `slotdebugger/data/commands.json` at setup, which is what lets an agent discover the toolset without a hand-maintained list.

### Track the investigation

`slotdebug think` takes the thinking-CLI flags below.

```bash
slotdebug think --reset
slotdebug think --setPlan "Check RTP,Analyze reels,Validate RNG,Check bonus"
slotdebug think --thought "base_total_win is 5% low" --thoughtNumber 1 --totalThoughts 4 --nextThoughtNeeded true
slotdebug think --nextStep
slotdebug think --status
slotdebug think --history
slotdebug think --export session.json
```

- Thought numbers must be sequential; `--nextThoughtNeeded false` on the final thought.
- Correct an earlier thought with `--isRevision --revisesThought N`; explore an alternative with `--branchFromThought N --branchId ID`.
- State is saved atomically. In a workspace it goes to `slotdebugger/state/.think_state.json`; otherwise `./.think_state.json`. Use `--state PATH` or `$THINK_STATE_FILE` for a separate file per session (inside a workspace the path must stay within it).
- Only one of `--setPlan`, `--nextStep`, `--status`, `--history`, `--export`, `--thought` per call.

### Typical debugging flow

0. `slotdebug setup` once per game, then `slotdebug add report.csv`.
1. `slotdebug analyze --file report.csv` and read `analysis.issues`.
2. `slotdebug think --reset` and `--setPlan` from component down to reels / weights / win calculation / RNG.
3. Record each finding with `--thought`, advance with `--nextStep`, finish with `--nextThoughtNeeded false`.
4. `slotdebug think --export session.json` to keep the record.

With the skills installed, Claude Code follows the same flow when you share a report.

## Development

```bash
pip install -e . pytest
pytest
```

Packages: `report_parser` (CSV/XLSX → report), `RTPNormalizer` (normalize + analyze), `sequential_thinking` (`think.py`), `slotdebugger` (CLI, pipeline, registry, installer, bundled skills), `slotdebugger.report_formatter` (re-shape the analysis into the game's expected-report format).

Read [AGENTS.md](AGENTS.md) before making changes — it records the conventions and the
non-obvious traps (silent parser failures, alias collisions, stale `build/` trees).
Record user-visible changes in [CHANGELOG.md](CHANGELOG.md) as part of the change.

### Adding a command

Declare it in `_register_all_commands()` in [slotdebugger/cli.py](slotdebugger/cli.py) alongside its argparse parser. It is then listed by `slotdebug command-list`, included in each workspace's `commands.json`, and available to the planned MCP layer — no separate tool definition to maintain. See [slotdebugger/registry.py](slotdebugger/registry.py).
