"""`slotdebug` command: analyze reports, run the thinking CLI, register with Claude/Cursor."""

import argparse
import json
import os
import sys

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


def _shape(result, ws, report, explicit):
    """Re-shape the analysis to the game's `expected_rtp_report.json`, if it has one.

    Looked for beside the report, in the workspace (root, `data/`, `reports/`) and in
    the project directory holding it. Without one, the native analysis format is used.
    """
    dirs = [os.path.dirname(os.path.abspath(report))]
    if ws:
        dirs += [ws.root, ws.path("data"), ws.reports, os.path.dirname(ws.root)]
    else:
        dirs.append(os.getcwd())
    path = explicit or report_formatter.find(*dirs)
    if not path:
        return result, []
    output, notes = report_formatter.apply(report_formatter.load(path), result)
    return output, [f"formatted like {path}"] + notes


def _analyze(ws, args) -> int:
    """Analyze RTP report - command handler."""
    report = ws.resolve_report(args.file) if ws else args.file
    result = pipeline.analyze(report)
    output, notes = _shape(result, ws, report, args.expected)
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
            "slotdebug analyze --file report.xlsx --expected expected_rtp_report.json"
        ]
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
    except (OSError, ValueError) as e:  # WorkspaceError is a ValueError
        print(f"error: {e}", file=sys.stderr)
        return 2

    return install.run(args)


if __name__ == "__main__":
    sys.exit(main())
