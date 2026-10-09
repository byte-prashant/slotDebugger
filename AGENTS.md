# Working in this repository

Conventions for anyone — human or agent — changing this codebase.
Claude Code and Cursor read this file automatically.

Domain reference lives in [knowledge/](knowledge/README.md). Status and open work
live in [KNOWLEDGE.md](KNOWLEDGE.md). This file is about *how to make a change safely*.

---

## The one rule

**Verify, don't assert.** Every claim about behaviour must be backed by a command you
actually ran. "This should work" is not a result.

```bash
pytest -q                                   # full suite, must stay green
slotdebug analyze --file <report>           # end-to-end check
```

This matters more than usual here: the parser fails *silently*. A wrong metadata
mapping produces a plausible number and no error. Tests and real reports are the
only signal.

---

## Layout

| Package | Responsibility |
|---|---|
| `report_parser/` | CSV/XLSX/tab-delimited → `RTPReport`; metadata field normalization |
| `RTPNormalizer/` | normalize components, build dependency graph, analyze |
| `sequential_thinking/` | `think.py` — persistent step-by-step reasoning CLI |
| `slotdebugger/` | CLI, pipeline, command registry, workspace, installer, bundled skills |
| `slotdebugger/report_formatter/` | re-shape the analysis into the game's own `expected_rtp_report.json` format |

Tests sit in `<package>/tests/`, except `slotdebugger/test_slotdebug.py`, which
sits inside the package. Discovery is driven by `testpaths` in `pytest.ini` — add new
test directories there or they will not run. A `tests/` directory *nested* under a
package already listed there (as `slotdebugger/report_formatter/tests/` is) is found
without any change.

---

## Making a change

1. **Read before editing.** Do not propose changes to a file you have not opened.
2. **Reinstall after touching a package.** The editable install resolves to the source
   tree, but entry points and packaged data do not refresh on their own:
   ```bash
   pip install -e . --quiet
   ```
3. **Add a regression test for every bug fixed.** The test must fail before the fix.
4. **Record it in [CHANGELOG.md](CHANGELOG.md)** under `## [Unreleased]`.
5. **Run `pytest -q`.** Green before handing back.

### Adding a CLI command

Two places, both in [slotdebugger/cli.py](slotdebugger/cli.py):

- an `argparse` sub-parser in `main()`
- an entry in `_register_all_commands()`

The registry entry is what makes the command appear in `slotdebug command-list`, in
each workspace's `data/commands.json`, and — once it exists — in the MCP layer. Skip
it and the command works but is invisible to agents.

### Changing the workspace layout

`slotdebug setup` is idempotent and doubles as the migration path. If you add a
directory to `SUBDIRS`, change `workspace.json`, or stop shipping a skill, bump
`SCHEMA_VERSION` in [slotdebugger/workspace.py](slotdebugger/workspace.py) and make
`setup()` perform the migration, so existing workspaces upgrade by re-running it.

Migrations may rewrite files this tool owns (`workspace.json`, `data/runs.json`,
registered skills). They must never delete a user's reports, analysis output or
thinking state — when a format changes, move the old file aside as
`runs.legacy.json` does rather than dropping it.

---

## Traps specific to this repo

### Never add a metadata alias without checking its unit

This is how the live `STAKE_SPINS` bug was introduced. `MetadataMapper.ALIASES` in
[report_parser/parser.py](report_parser/parser.py) maps raw report headers onto
canonical names by string similarity alone. Two fields that *read* alike can mean
different things:

| Raw | Means | Unit |
|---|---|---|
| `TOTAL_STAKE` | currency wagered | amount |
| `STAKE_SPINS` | number of paid spins | count |

Both currently map to `Total amount staked`; the last one wins and silently
overwrites the correct value. Before adding an alias, confirm the unit matches and
that the arithmetic still holds:

```
total_paid / total_staked ≈ reported RTP
```

See [docs/PLAN_RAG_METADATA_MAPPING.md](docs/PLAN_RAG_METADATA_MAPPING.md) for the
analysis and planned fix. The known-bad mapping is pinned by an `xfail` test in
[report_parser/tests/test_parser.py](report_parser/tests/test_parser.py) — when you
fix it, that test will XPASS and fail the suite, which is your cue to remove the
marker.

### Reports are tab-delimited regardless of extension

`.csv` and `.xlsx` alike. A file named `.xlsx` that is not a zip archive is retried as
text — real reports in this project are often exactly that.

### Do not create a second copy of the source tree

`build/` is generated and gitignored. A stale `build/lib/` holding older copies of
these packages can shadow imports; setuptools warns about this explicitly. If it
reappears and behaviour goes strange, delete it:

```bash
rm -rf build *.egg-info && pip install -e . --quiet
```

### Workspace paths are confined on purpose

`Workspace.inside()` rejects paths escaping `slotdebugger/`. That is a security
boundary for `--out`, `--state` and `--export`, not an inconvenience to work around.

---

## Scope discipline

- Fix what was asked. A bug fix does not need the surrounding code refactored.
- Do not add error handling for conditions that cannot occur.
- Do not document code you did not change.
- Prefer editing an existing file to creating a new one.
- Keep generated artifacts out of git: `build/`, `*.egg-info/`, `__pycache__/`,
  `.think_state.json`.

---

## Before handing back

- [ ] `pytest -q` green
- [ ] reinstalled if a package changed
- [ ] regression test added for any bug fixed
- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] claims verified by a command that was actually run
