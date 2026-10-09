"""`slotdebug` command: analyze reports, run the thinking CLI, register with Claude/Cursor."""

import argparse
import json
import os
import sys

import rtp_aggregator
from slotdebugger import install, pipeline, report_formatter
from slotdebugger.workspace import Workspace, WorkspaceError
from slotdebugger.registry import (
    get_registry,
    list_registered_commands,
    describe_all_registered_commands,
)


def auto_register() -> None:
    """First run after `pip install slotdebugger`: register the skills with Claude Code (user scope).

    pip can't run code at install time, so the first `slotdebug` call does it. It happens once
    (marker file), skips if ~/.claude doesn't exist, and `SLOTDEBUG_NO_AUTOREGISTER=1` disables it.
    The SessionStart hook is never added automatically; use `slotdebug install claude --hook`.
    """
    if os.environ.get("SLOTDEBUG_NO_AUTOREGISTER"):
        return
    marker = os.path.join(os.path.expanduser("~/.config"), "slotdebug", "registered")
    if os.path.exists(marker) or not os.path.isdir(os.path.expanduser("~/.claude")):
        return
    try:
        install.install_claude("user", os.getcwd(), hook=False)
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        open(marker, "w").close()
        print("slotdebug: registered skills with Claude Code (~/.claude/skills). "
              "Undo: slotdebug uninstall claude", file=sys.stderr)
    except OSError:
        pass  # never block the real command


def _confine_think_args(ws: Workspace, args: list) -> list:
    """Default --state to the workspace and require --state/--export to stay inside it."""
    args = list(args)
    has_state = False
    for i, a in enumerate(args):
        flag, eq, val = a.partition("=")
        if flag in ("--state", "--export"):
            if not eq:
                if i + 1 >= len(args):
                    continue  # argparse reports the missing value
                val = args[i + 1]
            resolved = ws.inside(val, base=ws.path("state"))
            if eq:
                args[i] = f"{flag}={resolved}"
            else:
                args[i + 1] = resolved
            has_state = has_state or flag == "--state"
    if not has_state and not os.environ.get("THINK_STATE_FILE"):
        args = ["--state", ws.think_state] + args
    elif not has_state and not ws.contains(os.environ["THINK_STATE_FILE"]):
        raise WorkspaceError("$THINK_STATE_FILE points outside the workspace")
    return args


def _expected(ws, report, explicit):
    """Path of the game's `expected_rtp_report.json`, or None.

    Looked for beside the report, in the workspace (root, `data/`, `reports/`) and in
    the project directory holding it.
    """
    dirs = [os.path.dirname(os.path.abspath(report))]
    if ws:
        dirs += [ws.root, ws.path("data"), ws.reports, os.path.dirname(ws.root)]
    else:
        dirs.append(os.getcwd())
    return explicit or report_formatter.find(*dirs)


def _shape(result, template, path):
    """Re-shape the analysis to the game's expected report; native format without one."""
    if template is None:
        return result, []
    output, notes = report_formatter.apply(template, result)
    return output, [f"formatted like {path}"] + notes


def _find_volume_tester(ws, report, explicit):
    """The game's volume_tester.py: given, or beside the report / in the workspace / in its tests/."""
    if explicit:
        if not os.path.isfile(explicit):
            raise WorkspaceError(f"no such volume tester: {explicit}")
        return explicit
    dirs = [os.path.dirname(os.path.abspath(report))]
    dirs += [ws.root, ws.path("data"), os.path.dirname(ws.root)] if ws else [os.getcwd()]
    for d in dirs:
        for candidate in (os.path.join(d, "volume_tester.py"), os.path.join(d, "tests", "volume_tester.py")):
            if os.path.isfile(candidate):
                return candidate
    return None


def _find_engine(vt_path, explicit):
    """The game's engine.py: given, or beside the volume tester / one directory above it."""
    if explicit:
        if not os.path.isfile(explicit):
            raise WorkspaceError(f"no such engine: {explicit}")
        return explicit
    if not vt_path:
        return None
    here = os.path.dirname(os.path.abspath(vt_path))
    for d in (here, os.path.dirname(here)):
        if os.path.isfile(os.path.join(d, "engine.py")):
            return os.path.join(d, "engine.py")
    return None


def _ask_aggregate_path() -> str:
    """Ask the user where the aggregate specification is; only possible on a terminal."""
    if not sys.stdin.isatty():
        raise WorkspaceError("--use-aggregate: no workspace to find an aggregate in; "
                             "pass its path (--use-aggregate PATH)")
    return input("Path to the aggregate.json to use (written if empty): ").strip()


def _is_empty(text: str) -> bool:
    """A file with no content, or a JSON object with nothing in it."""
    if not text.strip():
        return True
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False  # corrupt is not empty: never overwrite what we cannot read
    return not data


def _resolve_aggregate(ws, report, given):
    """Where `--use-aggregate` points, and its content if it has any.

    PATH, else this report's saved `aggregate.json`, else (no workspace) ask. A file that
    is missing or empty comes back as `(path, None)` and is the one case that gets written.
    """
    path = given or (ws.aggregate_path(report) if ws else None) or _ask_aggregate_path()
    if not path:
        raise WorkspaceError("--use-aggregate: no aggregate specification path given")
    if not os.path.isfile(path):
        return path, None
    with open(path) as f:
        text = f.read()
    if _is_empty(text):
        return path, None
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise WorkspaceError(f"{path}: not valid JSON ({e}); fix or empty it") from None
    if not isinstance(doc, dict):
        raise WorkspaceError(f"{path}: an aggregate specification must be a JSON object")
    if not doc.get("components"):
        return path, None  # has other keys but no formulas: nothing to preserve
    return path, doc


def _write_if_empty(ws, report, path, document) -> list:
    """Write a generated aggregate to `path`, and the inputs beside it only if that file is empty."""
    formulas, inputs = rtp_aggregator.split(document)
    Workspace._write_json(path, formulas)
    wrote = [path]
    inputs_file = ws.inputs_path(report) if ws and path == ws.aggregate_path(report) else None
    if inputs_file and (not os.path.isfile(inputs_file)
                        or _is_empty(open(inputs_file).read())):
        ws._write_json(inputs_file, inputs)
        wrote.append(inputs_file)
    return wrote


def _analyze(ws, args) -> int:
    """Analyze RTP report - command handler."""
    report = ws.resolve_report(args.file) if ws else args.file
    vt_path = _find_volume_tester(ws, report, args.volume_tester)
    engine = _find_engine(vt_path, args.engine)
    expected_path = _expected(ws, report, args.expected)
    template = report_formatter.load(expected_path) if expected_path else None
    use = args.use_aggregate is not None
    fixed_path, fixed = _resolve_aggregate(ws, report, args.use_aggregate) if use else (None, None)
    result = pipeline.analyze(report, vt_path, ws.info().get("game") if ws else None, template, engine,
                              reviewed=ws.load_reviewed(report) if ws and fixed is None else None,
                              fixed=fixed)
    document = result["aggregate"].pop("document")
    status = result["aggregate"]["status"]
    if status == "provided":  # nothing aggregate-related is created or rewritten
        result["aggregate"]["file"] = fixed_path
        print(f"using aggregate specification {fixed_path} (not modified)", file=sys.stderr)
    elif use:  # the specification was missing or empty: the one case where it is written
        result["aggregate"]["file"] = fixed_path
        for written in _write_if_empty(ws, report, fixed_path, document):
            print(f"aggregate specification was empty; wrote {written}", file=sys.stderr)
    elif ws:
        if status == "reviewed":
            result["aggregate"]["file"] = ws.aggregate_path(report)
            # formulas are the reviewed ones; the inputs are always this report's own
            ws._write_json(ws.inputs_path(report), rtp_aggregator.split(document)[1])
            print(f"using reviewed aggregate {result['aggregate']['file']}", file=sys.stderr)
        else:
            if status == "reviewed-stale":
                print(f"note: the report changed since the aggregate was reviewed; old one kept as "
                      f"{ws.set_aside_aggregate(report)}", file=sys.stderr)
            result["aggregate"]["file"] = ws.save_aggregate(report, document)
            print(f"saved -> {result['aggregate']['file']} (+ {os.path.basename(ws.inputs_path(report))})",
                  file=sys.stderr)
    else:
        result["aggregate"]["file"] = None
    output, notes = _shape(result, template, expected_path)
    for note in notes:
        print(f"note: {note}", file=sys.stderr)
    text = json.dumps(output, indent=2)
    if ws:
        saved = ws.save_analysis(report, result, output)
        if args.out:
            ws._write_json(ws.inside(args.out, base=ws.analysis), output)
        print(text)
        print(f"saved -> {saved}", file=sys.stderr)
        return 0
    if args.out:
        with open(args.out, "w") as f:
            f.write(text + "\n")
    else:
        print(text)
    return 0


def _aggregate(ws, args) -> int:
    """List what in a report's generated aggregate.json needs checking; optionally mark it reviewed."""
    if not ws:
        raise WorkspaceError("no workspace found; run `slotdebug setup` first")
    report = ws.resolve_report(args.file)
    doc = ws.load_aggregate(report)
    if doc is None:
        raise WorkspaceError(f"no aggregate for {args.file}; run `slotdebug analyze --file {args.file}` first")
    findings = rtp_aggregator.check(doc)
    if args.mark_reviewed:
        if any(f["code"] == "invalid" for f in findings):
            print(json.dumps({"reviewed": False, "findings": findings}, indent=2))
            raise WorkspaceError("not marked reviewed: the aggregate does not parse/evaluate")
        doc["reviewed"] = True
        ws.save_aggregate(report, doc)
    print(json.dumps({"file": ws.aggregate_path(report), "reviewed": doc.get("reviewed") is True,
                      "findings": findings}, indent=2))
    return 0


def _diff(ws, args) -> int:
    """Compare the saved analysis with the expected report; write and print the keys that differ."""
    if not ws:
        raise WorkspaceError("no workspace found; run `slotdebug setup` first")
    report = ws.resolve_report(args.file)
    expected_path = _expected(ws, report, args.expected)
    if not expected_path:
        raise WorkspaceError(f"no {report_formatter.NAME} found; pass --expected PATH")
    analysis_path = ws.analysis_path(report)
    if not os.path.isfile(analysis_path):
        raise WorkspaceError(f"no analysis for {args.file}; run `slotdebug analyze --file {args.file}` first")
    expected = report_formatter.load(expected_path)
    actual = ws._read_json(analysis_path)
    if isinstance(expected, dict) and isinstance(actual, dict) and not set(expected) <= set(actual):
        raise WorkspaceError(f"{analysis_path} is not in the shape of {expected_path}; re-run "
                             f"`slotdebug analyze --file {args.file} --expected {expected_path}`")
    result = report_formatter.diff(expected, actual, args.tolerance)
    document = {"report": os.path.basename(report), "expected": expected_path, "analysis": analysis_path,
                "open": report_formatter.open_keys(result), **result}
    out = ws.diff_path(report)
    ws._write_json(out, document)
    print(json.dumps(document, indent=2))
    s = result["summary"]
    print(f"{s['mismatch']} mismatch, {s['missing']} missing, {s['ok']} ok; saved -> {out}", file=sys.stderr)
    return 0


def _register_all_commands() -> None:
    """Register all CLI commands in the global registry."""
    from slotdebugger.registry import get_registry
    
    registry = get_registry()
    
    # Only register once
    if len(registry.list_commands()) > 0:
        return
    
    # Register setup command
    registry.register(
        name="setup",
        description=(
            "Create a slotdebugger workspace for a game, or bring an existing one "
            "up to date; registers the skills/commands with Claude Code and Cursor "
            "at project scope. Safe to re-run after upgrading slotdebugger."
        ),
        func=None,  # Command handled in main()
        args={
            "name": {
                "type": "string",
                "description": "Game name (default: current directory name)",
                "required": False
            },
            "dir": {
                "type": "string",
                "description": "Directory to create workspace in (default: cwd)",
                "required": False
            },
            "no-register": {
                "type": "boolean",
                "description": "Skip registering skills/commands with Claude Code and Cursor",
                "required": False
            }
        },
        returns={
            "type": "object",
            "description": "Workspace created with subdirs: reports/, analysis/, data/, state/"
        },
        examples=[
            "slotdebug setup --name my_game",
            "slotdebug setup --dir /path/to/game",
            "slotdebug setup            # re-run to refresh an existing workspace",
            "slotdebug setup --no-register"
        ]
    )
    
    # Register add command
    registry.register(
        name="add",
        description="Import a report file into the workspace's reports/ directory",
        func=None,
        args={
            "file": {
                "type": "string",
                "description": "Path to report file (CSV/XLSX)",
                "required": True
            }
        },
        returns={
            "type": "string",
            "description": "Path where report was imported"
        },
        examples=[
            "slotdebug add ../reports/game_rtp.xlsx",
            "slotdebug add /path/to/report.csv"
        ]
    )
    
    # Register analyze command
    registry.register(
        name="analyze",
        description=(
            "Parse, normalize and analyze an RTP report (CSV/XLSX); output JSON analysis. "
            "When the game ships an expected_rtp_report.json, the output uses exactly that "
            "file's shape and keys, filled with the measured values"
        ),
        func=_analyze,
        args={
            "file": {
                "type": "string",
                "description": "Report file name in workspace reports/ (or any path if no workspace)",
                "required": True
            },
            "out": {
                "type": "string",
                "description": "Output file to write JSON analysis",
                "required": False
            },
            "expected": {
                "type": "string",
                "description": (
                    "Path to the expected RTP report whose format the output should copy "
                    "(default: expected_rtp_report.json beside the report or in the workspace)"
                ),
                "required": False
            },
            "use-aggregate": {
                "type": "string",
                "description": (
                    "Use the aggregate specification as it is. PATH, or the report's saved "
                    "aggregate.json, or (no workspace) the user is asked for a path. A file with "
                    "content is never overwritten; a missing or empty one is generated and written"
                ),
                "required": False
            },
            "engine": {
                "type": "string",
                "description": (
                    "Game's engine.py; read with the volume tester to learn which events each "
                    "total is made of (default: engine.py beside the volume tester or one "
                    "directory above)"
                ),
                "required": False
            },
            "volume-tester": {
                "type": "string",
                "description": (
                    "Game's volume_tester.py; its event names are cross-checked against the "
                    "report when aggregate.json is built (default: volume_tester.py beside the "
                    "report or in the workspace)"
                ),
                "required": False
            }
        },
        returns={
            "type": "object",
            "description": (
                "Analysis result with metadata, components, dependency graph and analysis "
                "summary — or the expected report's own shape when one is present"
            )
        },
        examples=[
            "slotdebug analyze --file reports/game_report.xlsx",
            "slotdebug analyze --file report.xlsx --out analysis/result.json",
            "slotdebug analyze --file report.xlsx --expected expected_rtp_report.json",
            "slotdebug analyze --file report.xlsx --volume-tester tests/volume_tester.py --engine engine.py",
            "slotdebug analyze --file report.xlsx --use-aggregate            # use the saved aggregate.json; write it only if empty",
            "slotdebug analyze --file report.xlsx --use-aggregate my_aggregate.json"
        ]
    )
    
    registry.register(
        name="aggregate",
        description=(
            "List what in a report's generated aggregate.json is wrong or unproven (findings, "
            "each with a fix direction); --mark-reviewed after correcting it so `analyze` keeps it"
        ),
        func=_aggregate,
        args={
            "file": {"type": "string", "description": "Report name in workspace reports/",
                     "required": True},
            "mark-reviewed": {"type": "boolean",
                              "description": "Mark the corrected aggregate reviewed (refused if it does not evaluate)",
                              "required": False},
        },
        returns={"type": "object", "description": "file, reviewed flag and findings"},
        examples=["slotdebug aggregate --file report.xlsx",
                  "slotdebug aggregate --file report.xlsx --mark-reviewed"],
    )

    registry.register(
        name="diff",
        description=(
            "Compare a report's saved analysis with the game's expected_rtp_report.json, key by "
            "key; writes analysis/<report>.diff.json whose `open` list is every key that is not "
            "within tolerance (mismatch or missing) -- the keys a debugging session must explain"
        ),
        func=_diff,
        args={
            "file": {"type": "string", "description": "Report name in workspace reports/ (run analyze first)",
                     "required": True},
            "expected": {"type": "string",
                         "description": "Expected report (default: found as analyze finds it)",
                         "required": False},
            "tolerance": {"type": "number",
                          "description": "RTP tolerance in the expected report's scale "
                                         "(default 0.001 of a stake: 0.1 in percent, 0.001 as a fraction)",
                          "required": False},
        },
        returns={"type": "object", "description": "summary, open keys, and expected/actual/delta/status per key"},
        examples=["slotdebug diff --file report.xlsx",
                  "slotdebug diff --file report.xlsx --tolerance 0.05",
                  "slotdebug think --setTargets analysis/report.diff.json"],
    )

    # Register runs command
    registry.register(
        name="runs",
        description="List all analysis runs recorded in the workspace",
        func=None,
        returns={
            "type": "array",
            "description": "List of past analysis runs with status, RTP, and issues"
        },
        examples=[
            "slotdebug runs"
        ]
    )
    
    # Register think command
    registry.register(
        name="think",
        description="Sequential-thinking CLI for game analysis reasoning",
        func=None,
        args={
            "args": {
                "type": "string",
                "description": "Arguments to pass to sequential-thinking CLI",
                "required": False
            }
        },
        examples=[
            "slotdebug think --help"
        ]
    )
    
    # Register command-list command
    registry.register(
        name="command-list",
        description="List all registered slotdebug commands",
        func=None,
        returns={
            "type": "array",
            "description": "List of command names"
        },
        examples=[
            "slotdebug command-list",
            "slotdebug command-list --detail"
        ]
    )


def _register_workspace(ws: Workspace, project_dir: str) -> list:
    """Register (or refresh) the skills and command manifest for a workspace.

    `auto_register()` only ever runs once per machine (user scope), so a new game
    directory would otherwise have nothing registered. This registers at *project*
    scope so Claude/Cursor pick the skills up inside that game's repo.

    Re-running is how a workspace picks up a newer slotdebugger: skills and the
    command manifest are rewritten, and anything this tool registered previously but
    no longer ships is removed. Registration never blocks workspace creation:
    failures are reported and skipped.
    """
    done = []
    try:
        previous = ws.info().get("registered") or {}
    except (OSError, ValueError):
        previous = {}

    try:
        done += install.install_claude("project", project_dir, hook=False)
        for name in sorted(set(previous.get("skills", [])) - set(install.SKILLS)):
            if install.remove_claude_skill("project", project_dir, name):
                done.append(f"retired skill {name}")
    except OSError as e:
        print(f"warning: could not register Claude skills: {e}", file=sys.stderr)

    try:
        done += install.install_cursor(project_dir)
        for name in sorted(set(previous.get("rules", [])) - set(install.SKILLS)):
            if install.remove_cursor_rule(project_dir, name):
                done.append(f"retired rule {name}")
    except OSError as e:
        print(f"warning: could not register Cursor rules: {e}", file=sys.stderr)

    try:
        manifest = ws.path("data", "commands.json")
        ws._write_json(manifest, get_registry().get_all_commands())
        done.append(f"commands -> {manifest}")
        ws.record_registered(skills=list(install.SKILLS), rules=list(install.SKILLS))
    except OSError as e:
        print(f"warning: could not write command manifest: {e}", file=sys.stderr)

    return done


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    
    # Register commands early
    _register_all_commands()
    
    # Handle "command-list" before argparse (so -h doesn't interfere)
    if argv and argv[0] == "command-list":
        if len(argv) > 1 and argv[1] == "--detail":
            print(describe_all_registered_commands())
        else:
            commands = list_registered_commands()
            print("Registered commands:")
            for cmd in sorted(commands):
                print(f"  {cmd}")
        return 0
    
    auto_register()
    ws = Workspace.find(os.getcwd())

    # `think` forwards everything after it to the sequential-thinking CLI untouched,
    # except that inside a workspace its state/export files are confined to the workspace.
    if argv and argv[0] == "think":
        from sequential_thinking import think
        rest = list(argv[1:])
        if ws:
            try:
                rest = _confine_think_args(ws, rest)
            except WorkspaceError as e:
                print(f"error: {e}", file=sys.stderr)
                return 2
        return think.main(rest)

    parser = argparse.ArgumentParser(prog="slotdebug", description="Slot RTP debugging toolkit")
    sub = parser.add_subparsers(dest="cmd", required=True)

    st = sub.add_parser("setup", help="create a ./slotdebugger workspace for this game")
    st.add_argument("--name", help="game name (default: current directory name)")
    st.add_argument("--dir", default=os.getcwd(), help="where to create it (default: cwd)")
    st.add_argument("--no-register", action="store_true",
                    help="skip registering skills/commands with Claude Code and Cursor")

    ad = sub.add_parser("add", help="import a report file into the workspace's reports/")
    ad.add_argument("file")

    sub.add_parser("runs", help="list analysis runs recorded in the workspace")

    a = sub.add_parser("analyze", help="parse, normalize and check an RTP report (CSV/XLSX); prints JSON")
    a.add_argument("--file", required=True,
                   help="report name in the workspace's reports/ (or any path when there is no workspace)")
    a.add_argument("--out", help="write JSON here instead of stdout (inside the workspace when one exists)")
    a.add_argument("--expected",
                   help=f"expected RTP report to copy the output format from "
                        f"(default: {report_formatter.NAME} beside the report or in the workspace)")
    a.add_argument("--use-aggregate", nargs="?", const="", metavar="PATH",
                   help="use the aggregate specification as it is: PATH, else this report's saved "
                        "aggregate.json (else you are asked for a path). A file that has content is "
                        "never overwritten; one that is missing or empty is generated and written")
    a.add_argument("--engine",
                   help="game's engine.py; with the volume tester it says how each total is built "
                        "(default: engine.py beside the volume tester or one directory above)")
    a.add_argument("--volume-tester",
                   help="game's volume_tester.py, read for the event names it emits "
                        "(default: volume_tester.py beside the report or in the workspace)")

    ag = sub.add_parser("aggregate", help="check a report's aggregate.json; list findings to correct")
    ag.add_argument("--file", required=True, help="report name in the workspace's reports/")
    ag.add_argument("--mark-reviewed", action="store_true",
                    help="mark the (corrected) aggregate reviewed so analyze keeps it")

    df = sub.add_parser("diff", help="compare a report's analysis with the expected report, key by key")
    df.add_argument("--file", required=True, help="report name in the workspace's reports/")
    df.add_argument("--expected", help=f"expected report (default: {report_formatter.NAME} as analyze finds it)")
    df.add_argument("--tolerance", type=float,
                    help="RTP tolerance in the expected report's scale (default: 0.1 in percent, 0.001 as a fraction)")

    sub.add_parser("think", help="sequential-thinking CLI (sequential-thinking CLI)")

    for name, help_text in (("install", "register skills (and optional hook) with Claude Code / Cursor"),
                            ("uninstall", "remove what `install` registered")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("target", choices=("claude", "cursor", "all"), nargs="?", default="all")
        p.add_argument("--scope", choices=("user", "project"), default="user", help="Claude scope")
        p.add_argument("--hook", action="store_true", help="Claude: add SessionStart hook (install only)")
        p.add_argument("--dir", help="project dir (default: cwd)")

    args = parser.parse_args(argv)

    try:
        if args.cmd == "setup":
            made, changes = Workspace.setup(args.dir, args.name)
            fresh = "updated" not in made.info()  # only re-runs record `updated`
            if changes:
                print(f"workspace {made.root}")
                for line in changes:
                    print(f"  {line}")
            else:
                print(f"workspace {made.root} already up to date")
            if not args.no_register:
                for line in _register_workspace(made, args.dir):
                    print(f"  registered {line}")
            if fresh:
                print("\n  reports/   put RTP reports here (or `slotdebug add <file>`)"
                      "\n  analysis/  analyze output"
                      "\n  data/      runs.json (actual RTP per report)"
                      "\n  state/     thinking-CLI state")
            return 0
        if args.cmd in ("add", "runs") and not ws:
            raise WorkspaceError("no workspace found; run `slotdebug setup` first")
        if args.cmd == "add":
            print(f"imported -> {ws.add_report(args.file)}")
            return 0
        if args.cmd == "runs":
            print(json.dumps(ws.runs(), indent=2))
            return 0
        if args.cmd == "analyze":
            return _analyze(ws, args)
        if args.cmd == "aggregate":
            return _aggregate(ws, args)
        if args.cmd == "diff":
            return _diff(ws, args)
    except (OSError, ValueError) as e:  # WorkspaceError is a ValueError
        print(f"error: {e}", file=sys.stderr)
        return 2

    return install.run(args)


if __name__ == "__main__":
    sys.exit(main())
