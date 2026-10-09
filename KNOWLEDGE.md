# Slot Debugger — Knowledge & Remaining Work

> Domain/reference docs live in [knowledge/](knowledge/README.md). This file tracks status and remaining work.

**P0 update (2026-10-06):** bugs 1–6, 9, 10 and the test-layout items below are fixed; 27 tests pass via a single `pytest`. Items still open from P0: relative/configurable tolerance (bug 8), think.py `--history`/`--export` and atomic writes (bug 11 validation itself is now done: sequential numbers, valid revision/branch refs, strict booleans, clean errors, `--state` flag), tuple order still heuristic-based.

_Snapshot: 2026-10-06, commit `7fe8725`. Tests: 17 pass (8 + 9), but see "Known bugs" — some passing tests are vacuous._

## 1. What this project is

An **autonomous RTP debugging agent** for slot games. Goal: take an RTP report (CSV/XLSX from a simulation), find which feature/component's RTP is off, then drill down (reels, weights, win calc, RNG, bonus) to a root cause and fix suggestion — using an LLM constrained by a deterministic planning/thinking CLI.

Target pipeline (from [Read.md](Read.md)):

```
CSV/XLSX → parse → normalize → plan → agent loop (LLM ⇄ think.py ⇄ tools) → final report
```

Only the first three-and-a-half boxes exist today. The agent loop, LLM, tools and final report do **not**.

## 2. Repo map

| Path | Role | State |
|---|---|---|
| [report_parser/parser.py](report_parser/parser.py) | Reads tab-separated CSV / XLSX report → `RTPReport` (metadata + `RTPComponent` list). SOLID-style: `IReportReader`, `CSVReader`, `XLSXReader`, `BaseParser`, `RTPValueParser`, `ReaderFactory`, `RTPService`. | Works; CLI commented out |
| [RTPNormalizer/rtpnormalizer.py](RTPNormalizer/rtpnormalizer.py) | `RTPDependencyResolver` (parent/child graph from names), `RTPNormalizer` (RTP & hit-rate as fractions), `RTPAnalyzer` (parent == Σ children check, total RTP). | **Buggy** (see §4) |
| [sequential_thinking/think.py](sequential_thinking/think.py) | Stateful CLI: plan (`--setPlan`, `--nextStep`), thoughts, revisions, branches, `--status`, `--reset`. State in `.think_state.json`. | Works; see gaps |
| [sequential_thinking/skill.md](sequential_thinking/skill.md) | Claude skill describing the CLI. | Good |
| [sequential_thinking/READ.md](sequential_thinking/READ.md) | Flowchart of think.py + design notes ("Plan = which component's RTP mismatches → sub-component → reel/weights/win calc"). Ends with "NOTE: Rewrite in clean way". | Draft |
| [Read.md](Read.md) | System architecture flowcharts. | Aspirational |
| [skill.md](skill.md) | Root skill file. | **Empty** |
| [requirements.txt](requirements.txt) | `openpyxl>=3.1.0` only. | OK |

## 3. Key knowledge / domain facts

**Report format** (parser expectations)
- Delimiter is **tab**, even for `.csv`.
- Metadata keys captured: Engine Name/Version, Total number of plays, Plays with stake, Total amount staked/paid, Largest win, Game RTP, Jackpot RTP, Total RTP, Win standard deviation, Win Hit Rate.
- Component rows start after a row whose first cell is `Event` and stop at a row starting `RTP (`.
- Component value is a tuple string `"(a, b)"`; order is auto-detected: the element containing `.` is RTP-win, the other is frequency. **Ambiguous** if the win amount is an integer (e.g. `(100, 5)`) — it will be read as freq=100, rtp=5.0.
- Unparseable rows are silently skipped.

**Normalizer semantics**
- `rtp` of a component = `component_win / total_game_win` → this is the component's **share of total win**, not its RTP vs. stake. Sum of "total" components ≈ 1.0 (this is what `test_total_rtp` checks).
- `hit_rate` = `frequency / total_plays`.
- Hierarchy is inferred purely from **names**: `<prefix>_total_win` is the parent of `<prefix>_*` components.

**think.py semantics**
- Plan step is auto-attached to each thought; `--nextStep` advances (clamped at last step; can't go "past the end").
- A thought with `--branchFromThought` + `--branchId` is appended to both main history and `branches[id]`.
- State file is next to the script (not cwd), shared by every run → concurrent/parallel sessions collide.

## 4. Known bugs (verified)

1. **Dependency graph is always empty for real names.** `name.replace("_total", "")` turns `base_total_win` into `base_win`, then looks for components starting with `base_win` — nothing does (`base_bonus_win` starts with `base_b…`). Reproduced: graph for `[base_bonus_win, base_total_win]` → `{}`.
   - Consequence: `RTPAnalyzer.validate_totals()` never checks anything, and `test_base_consistency` / `test_ultraboost_consistency` / `test_status_ok` **pass vacuously**.
   - Fix idea: parent prefix = text before `_total`; children = `startswith(prefix + "_")`, excluding other `*_total_*` names.
2. **Test data typo masks a real mismatch.** `utraboosst_line_win` (typo) should be `ultraboost_line_win`. Once the graph is fixed, `ultraboost_total_win` = cash (18.08M) + line (1.89M) = 19.97M ✔ — but only if the typo is fixed so the child is found.
3. **`RTPNormalizer.run()` has a stray `print`**, and `RTPService.process()` prints the report object (`print(report)` → useless `<object>` repr). Library code shouldn't print.
4. **Module-level side effects in tests/source**: `test_rtp_analyzer.py` runs the suite at import; `rtpnormalizer.py` imports `unittest` unused and starts with a stray comment.
5. **`sequential_thinking/test_think.py` fails under repo-root `pytest`** (`No module named 'think'`); passes only when run from inside `sequential_thinking/`. Also `report_parser/tests/tests.py` isn't matched by pytest's default `test_*.py` pattern unless passed explicitly (so a bare `pytest` skips it).
6. **XLSX reader drops zeros**: `str(cell) if cell else ""` turns numeric `0`/`0.0` into `""`.
7. **CSVReader** opens without `newline=""`/encoding; XLSX opens without `read_only`/`data_only` (formulas would return formula text if no cached value).
8. **Analyzer tolerance is absolute 0.001** of share-of-win — too coarse for small components, no relative tolerance, no per-game config.
9. **`total_rtp()` selects by substring `"total" in name`** — would also match e.g. `total_bet_stats` or nested totals and double count.
10. **`.think_state.json` is committed** (stale test data: "doing step") and there's **no `.gitignore`** — `__pycache__/` and `.idea/` are showing up/tracked.
11. **think.py gaps**: no validation that `thoughtNumber` is sequential, that `revisesThought`/`branchFromThought` refer to existing thoughts, or that `--nextStep` was legal; `branchFromThought=0` is falsy; `nextThoughtNeeded` accepts any string as false; `ValueError` tracebacks instead of CLI errors; `--setPlan` / `--nextStep` / `--status` return early, silently ignoring other flags; `needsMoreThoughts` is stored but never settable.

## 5. Remaining to implement

### P0 — make the existing parts correct
- [x] Fix dependency resolver (bug 1) and typo in test data (bug 2); add tests that **fail** on a real mismatch (e.g. child sum off by 5%) so the analyzer is proven to detect problems.
- [x] Remove prints from library code; return data only.
- [x] Add `.gitignore` (`__pycache__/`, `.idea/`, `.think_state.json`); untrack `.think_state.json`.
- [x] Make tests run from repo root with one command (`pytest`): add `conftest.py`/`pytest.ini` with `pythonpath`, rename `tests.py` → `test_parser.py`, drop import-time `runner.run`.
- [x] Fix XLSX zero handling; add XLSX reader test (currently untested).
- [~] (partly: ambiguous integer wins now default to (win, freq)) Disambiguate the `(a, b)` tuple parsing (use header/column info or explicit order from the report).

### P1 — the missing pipeline stages (per Read.md)
- [ ] **CLI entrypoint** (`slotdebug --file report.csv`) — uncomment/finish the parser CLI and chain parse → normalize → analyze, output JSON.
- [~] **Expected-vs-actual comparison**: the *input* now exists — `expected_rtp_report.json` (any shape, the game's own keys) makes `analyze` emit the same shape with measured values, via [slotdebugger/report_formatter/](slotdebugger/report_formatter/), so the two files can be diffed. Still missing: computing the diff in-tool, **sorting by impact**, and flagging critical issues (Read.md's "Calculate Diff / Sort by Impact / Critical Issues").
- [ ] **Plan generator**: issues → plan steps (detect type: base game / free spins / bonus / jackpot; then reels → weights → win calc → RNG) and feed `think.py --setPlan`.
- [ ] **Agent loop**: get state → current step → build prompt → LLM thought → validate → `think.py` → tool → loop until `nextThoughtNeeded=false`. Prefer importing `think` as a library (refactor `main()` into functions) rather than shelling out.
- [ ] **LLM adapter** (Claude API; structured thought JSON output; retries/validation). Keep behind an interface for tests.
- [ ] **Tool layer**: functions the LLM can call — e.g. read reel strips, read symbol weights/paytable, recompute theoretical RTP, run N-spin simulation, inspect bonus trigger odds, check RNG distribution (chi-square). Needs a defined input format for game configs.
- [ ] **Revision & branching policy**: when does the agent revise (tool result contradicts a thought) or branch (≥2 plausible hypotheses)? Currently only a manual CLI feature.
- [ ] **Final report**: aggregate thoughts → root cause + fix suggestion (markdown/JSON).

### P2 — hardening & polish
- [ ] think.py: validation items from bug 11; per-session state path (`--state` flag or env var) to allow concurrent runs; atomic writes; `--history` / `--export` commands.
- [ ] Config for tolerances (absolute + relative) and naming conventions of components (don't hard-code `_total_`).
- [ ] Support more report variants (headers differ across engines; localized numbers; multiple sheets).
- [ ] Type hints/dataclasses instead of dicts (`ThoughtData`, `RTPComponent`); package metadata (`pyproject.toml`) so imports work without path hacks.
- [ ] Logging instead of print; error messages for CLI users.
- [ ] Docs: fill root [skill.md](skill.md) (empty) with a skill for running the whole debugger; rewrite [sequential_thinking/READ.md](sequential_thinking/READ.md) as noted in its last line; add a README with a quickstart and a sample report fixture.
- [ ] Add sample fixture reports (CSV + XLSX) under `tests/data/` and an end-to-end test.
- [ ] CI (GitHub Actions: pytest on 3.10+).

## 6. Suggested order of work

1. Bugs 1–2 + `.gitignore` + unified `pytest` (small, unblocks trustworthy tests).
2. Expected-RTP input + diff/impact ranking (the analytic core).
3. Refactor think.py to importable library; build plan generator.
4. Tools + LLM adapter + agent loop; then final report.
5. Hardening (P2).

## 7. Open questions for the owner

- What does the **expected/target RTP source** look like (PAR sheet, math doc, JSON)? *Answered in part: a JSON file, `expected_rtp_report.json`, whose shape and keys are the game's own; slotdebugger copies that shape rather than imposing one.*
- What **game config formats** (reel strips, weights, paytable) will the tools read — per-engine, or one canonical schema?
- Is the report's second tuple field always frequency, and is the first always win amount? Can the engine emit headers to remove the guesswork?
- Should the agent run fully autonomously, or stop for human confirmation before running simulations?
- Which LLM/runtime (Claude API via SDK, or Claude Code driving `think.py` through the skill)?
