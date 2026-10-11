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


def _setup_tester(ws, project_dir) -> list:
    """Copy the game's volume tester into tester/ on first setup; on re-runs only report drift."""
    try:
        if ws.tester_record() is None:
            found = _find_volume_tester(ws, os.path.join(project_dir, "_"), None)
            if found:
                ws.copy_tester(found)
                return [f"copied {found} -> tester/"]
            return []
        status = ws.tester_status()
        if status == "changed":
            return ["volume tester changed since it was copied; "
                    "`slotdebug trace-tester --refresh-tester` updates the copy"]
        if status == "original-missing":
            return ["the copied volume tester's original is gone; the copy in tester/ is kept"]
    except OSError as e:
        return [f"could not copy the volume tester: {e}"]
    return []


def _tester_to_run(ws, report, args):
    """(original, copy to run or None, notes). Outside a workspace, or with --live, the original runs."""
    record = ws.tester_record() if ws else None
    original = args.tester or (record["original"] if record else None) \
        or _find_volume_tester(ws, report or os.path.join(os.getcwd(), "_"), None)
    if not original:
        raise WorkspaceError("no volume_tester.py found; pass --tester PATH")
    if not ws or args.live:
        return original, None, []
    notes = []
    if record is None or os.path.realpath(original) != record["original"] or args.refresh_tester:
        if record and args.refresh_tester and ws.tester_status() == "current" \
                and os.path.realpath(original) == record["original"]:
            notes.append("volume tester copy already up to date")
        else:
            record = ws.copy_tester(original)
            notes.append(f"copied {record['original']} -> {record['file']}")
    status = ws.tester_status()
    if status == "changed":
        notes.append(f"the original changed since it was copied ({record['copied']}); running the copy, "
                     f"labelled with its own path. `--refresh-tester` updates it")
    elif status == "original-missing":
        notes.append("the original volume tester is gone; running the copy")
    return record["original"], ws.path(record["file"]), notes


def _trace_tester(ws, args) -> int:
    """Run the volume tester traced; map report keys to tester lines, response keys and conditions."""
    from engine_indexer import tester_map
    report = (ws.resolve_report(args.file) if ws else args.file) if args.file else None
    tester, copy, notes = _tester_to_run(ws, report, args)
    for note in notes:
        print(f"note: {note}", file=sys.stderr)
    try:
        doc = tester_map.build(tester, n=args.n, python=args.python, paths=args.path, kwargs=args.kw,
                               cls=args.cls, seed=args.seed, report=report, timeout=args.timeout,
                               source=copy)
    except tester_map.TraceError as e:
        raise WorkspaceError(str(e)) from None
    text = json.dumps(doc, indent=2)
    if ws:
        out = ws.inside(args.out, base=ws.analysis) if args.out else ws.tester_map_path(report)
        ws._write_json(out, doc)
    elif args.out:
        out = args.out
        with open(out, "w") as f:
            f.write(text + "\n")
    else:
        out = None
    print(text)
    s = doc["spins"]
    fired = sum(1 for site in doc["sites"] if site["fired"])
    print(f"{s['completed']}/{s['requested']} spins; {fired} sites fired, {len(doc['never_fired'])} never fired; "
          f"{len(doc['keys'])} distinct keys", file=sys.stderr)
    for site_id in doc["missing_response_keys"]:
        site = next(x for x in doc["sites"] if x["id"] == site_id)
        what = "never returns" if site["response_key_status"] == "never_returned" else "always returns empty"
        print(f"note: {site_id} reads {site['response_key']!r}, which the engine {what} "
              f"({doc['results_seen']} results seen)", file=sys.stderr)
    if doc["skipped_kwargs"]:
        print(f"note: tester arguments set from the command line in its __main__ were left at their defaults: "
              f"{', '.join(doc['skipped_kwargs'])} (pass --kw NAME=VALUE)", file=sys.stderr)
    if doc["report"] and doc["report"].get("warning"):
        print(f"warning: {doc['report']['warning']}", file=sys.stderr)
    elif doc["report"]:
        r = doc["report"]
        print(f"report: {len(r['unexplained_rows'])} rows no site explains, "
              f"{len(r['not_reproduced_rows'])} matched only by pattern (not reached in this run)", file=sys.stderr)
    if out:
        print(f"saved -> {out}", file=sys.stderr)
    if not args.no_index:  # the engine map and the join come with every trace
        engine = _find_engine(doc["tester"], args.engine)
        if engine:
            engine_out = None
            if not ws and out:
                stem = out[: -len(".tester_map.json")] if out.endswith(".tester_map.json") else os.path.splitext(out)[0]
                engine_out = stem + ".engine_map.json"
            try:
                _engine_maps(ws, report, engine, args.path, depth=args.depth, tester_map=doc,
                             map_path=out, out=engine_out)
            except ValueError as e:
                print(f"warning: engine map not built: {e}", file=sys.stderr)
        else:
            print("note: no engine.py beside the volume tester; engine map not built (pass --engine PATH)",
                  file=sys.stderr)
    if doc["error"]:
        print(f"error: the tester stopped after {s['completed']} spins: {doc['error']}", file=sys.stderr)
        return 1
    return 0


def _engine_maps(ws, report, engine, paths, keys=(), depth=6, tester_map=None, map_path=None, out=None):
    """Build the engine map and, with a tester map, the join; write both; report on stderr.

    Returns `(engine map, engine map path, report map path)`. Paths are None when there is
    nowhere to write (no workspace and no `out`).
    """
    from engine_indexer import engine_map
    doc = engine_map.build(engine, keys=keys, paths=paths, tester_map=tester_map, max_depth=depth)
    # three files, one map each: tester map (trace-tester), engine map, and the join of the two
    joined = {"tester_map": map_path, "engine": doc["engine"], **doc.pop("chain")} if tester_map else None
    joined_out = None
    if ws:
        out = ws.inside(out, base=ws.analysis) if out else ws.engine_map_path(report)
        joined_out = ws.report_map_path(report) if joined else None
    elif out and joined:
        stem = out[: -len(".engine_map.json")] if out.endswith(".engine_map.json") else os.path.splitext(out)[0]
        joined_out = stem + ".report_map.json"
    if out:
        Workspace._write_json(out, doc)
    if joined_out:
        joined["engine_map"] = out
        Workspace._write_json(joined_out, joined)
    found = [k for k, v in doc["keys"].items() if v["roots"]]
    print(f"engine: {len(found)}/{len(doc['keys'])} response keys found; {len(doc['nodes'])} nodes",
          file=sys.stderr)
    for key in sorted(set(doc["keys"]) - set(found)):
        print(f"note: no dict in the engine or its base classes carries {key!r}", file=sys.stderr)
    for base in doc["unresolved_bases"]:
        print(f"note: base class {base['base']} of {base['class']} not found; pass the framework "
              f"checkout with --path", file=sys.stderr)
    if doc["truncated"]:
        print("note: node limit reached; the map is incomplete", file=sys.stderr)
    if out:
        print(f"saved -> {out}", file=sys.stderr)
    if joined_out:
        print(f"saved -> {joined_out} (report row -> tester line -> response key -> engine roots)",
              file=sys.stderr)
    if out:  # Markdown summaries beside the maps: overview + problems, tester side, engine side
        from engine_indexer import views
        maps = views.Maps(tester=tester_map, engine=doc, joined=joined)
        for path in views.write_summaries(maps, _maps_stem(out, ".engine_map.json")):
            print(f"saved -> {path}", file=sys.stderr)
    return doc, out, joined_out


def _maps_stem(path, suffix):
    """`analysis/r.engine_map.json` -> `analysis/r`; `analysis/engine_map.json` -> `analysis/report`."""
    if path.endswith(suffix):
        stem = path[: -len(suffix)]
        return stem if os.path.basename(stem) else os.path.join(os.path.dirname(path), "report")
    if os.path.basename(path) == suffix.lstrip("."):
        return os.path.join(os.path.dirname(path), "report")
    return os.path.splitext(path)[0]


def _map_paths(ws, args):
    """(tester map, engine map, report map) paths for `slotdebug map`."""
    if args.prefix:
        stem = args.prefix
    elif ws:
        report = ws.resolve_report(args.file) if args.file else None
        return ws.tester_map_path(report), ws.engine_map_path(report), ws.report_map_path(report)
    else:
        raise WorkspaceError("no workspace: pass --prefix PATH (the maps are PATH.tester_map.json, ...)")
    return f"{stem}.tester_map.json", f"{stem}.engine_map.json", f"{stem}.report_map.json"


def _map(ws, args) -> int:
    """Answer one question from the three maps."""
    from engine_indexer import views
    maps = views.Maps.load(*_map_paths(ws, args))
    view = args.view
    if view == "tree":
        text = views.tree(maps, row=args.row, key=args.key, site=args.site, depth=args.depth)
    elif view == "rows":
        text = views.rows(maps)
    elif view == "keys":
        text = views.keys(maps)
    elif view == "sites":
        text = views.sites(maps)
    elif view == "method":
        if not args.name:
            raise WorkspaceError("map method needs --name METHOD")
        text = views.method(maps, args.name)
    elif view == "class":
        text = views.classes(maps, args.name)
    elif view == "find":
        if not args.text:
            raise WorkspaceError("map find needs --text TEXT")
        text = views.find(maps, args.text)
    elif view == "problems":
        text = json.dumps(views.problems(maps), indent=2) if args.json else views.problems_text(maps)
    else:  # summary
        text = views.summary_md(maps)
    print(text)
    return 0


def _index(ws, args) -> int:
    """Index the engine from its response keys; join with the tester map when there is one."""
    from engine_indexer import engine_map
    report = (ws.resolve_report(args.file) if ws else args.file) if args.file else None
    map_path = args.tester_map or (ws.tester_map_path(report) if ws else None)
    tester_map = None
    if map_path and os.path.isfile(map_path):
        with open(map_path) as f:
            tester_map = json.load(f)
    elif args.tester_map:
        raise WorkspaceError(f"no such tester map: {args.tester_map}")
    tester = (tester_map or {}).get("tester") or ((ws.tester_record() or {}).get("original") if ws else None)
    engine = _find_engine(tester, args.engine)
    if not engine:
        raise WorkspaceError("no engine.py found beside the volume tester; pass --engine PATH")
    if not args.key and not tester_map:
        raise WorkspaceError("no response keys: run `slotdebug trace-tester` first, or pass --key KEY")
    doc, _, _ = _engine_maps(ws, report, engine, args.path, args.key, args.depth,
                             tester_map if tester_map else None, map_path if tester_map else None, args.out)
    if args.tree:
        for key in args.tree:
            print(engine_map.render(doc, key))
    else:
        print(json.dumps(doc, indent=2))
    return 0


def _report(ws, args) -> int:
    """Assemble the diagnosis (think session + diff + maps) into <report>.agent_report.json / .md."""
    from slotdebugger import agent_report
    if not ws:
        raise WorkspaceError("no workspace found; run `slotdebug setup` first")
    report = ws.resolve_report(args.file) if args.file else None
    doc = agent_report.build(ws, report)
    for path in agent_report.write(ws, report, doc):
        print(f"saved -> {path}", file=sys.stderr)
    print(json.dumps(doc, indent=2) if args.json else agent_report.markdown(doc))
    bad = [e["ref"] for f in doc["findings"] for e in f["evidence_check"] if e["exists"] is False]
    if bad:
        print(f"warning: evidence that does not resolve: {', '.join(bad)}", file=sys.stderr)
    return 0


HOOK_EVENTS = {"session-start": "SessionStart", "post-edit": "PostToolUse", "prompt": "UserPromptSubmit",
               "agent-result": "PostToolUse", "agent-stop": "SubagentStop", "agent-bash": "PreToolUse"}


def _hook(args) -> int:
    """Editor hook: read the event on stdin, answer for Claude Code (JSON) or as plain text.

    Never fails the editor: no workspace, no maps or an unrelated file means no output, and
    an error becomes a one-line note.
    """
    from slotdebugger import hooks
    event = hooks.read_event(sys.stdin) if not sys.stdin.isatty() else {}
    cwd = event.get("cwd") or os.getcwd()
    tool_input = event.get("tool_input") or {}
    if args.event == "agent-bash":  # PreToolUse guard: exit 2 + stderr refuses the command
        reason = hooks.agent_bash(tool_input.get("command") or "")
        if reason:
            print(f"slotdebug: refused: {reason}", file=sys.stderr)
            return 2
        return 0
    if args.event == "agent-stop":  # SubagentStop gate: block (once) until the diagnosis is written
        if event.get("stop_hook_active"):
            return 0  # already sent back once: let it finish rather than loop
        try:
            reason = hooks.agent_stop(cwd)
        except Exception as e:
            reason = None
            print(f"slotdebug: agent-stop check failed: {e}", file=sys.stderr)
        if reason:
            print(json.dumps({"decision": "block", "reason": reason}))
        return 0
    try:
        if args.event == "session-start":
            text = hooks.session_start(cwd)
        elif args.event == "prompt":
            text = hooks.prompt_hint(cwd, event.get("prompt") or "")
        elif args.event == "agent-result":
            agent = tool_input.get("subagent_type") or ""
            text = hooks.agent_result(cwd) if agent in hooks.AGENT_NAMES else None
        else:
            text = hooks.post_edit(cwd, args.file or tool_input.get("file_path") or tool_input.get("path"))
    except Exception as e:  # a hook must not break the session
        text = f"slotdebug: hook {args.event} failed: {type(e).__name__}: {e}"
    if not text:
        return 0
    if args.format == "text":
        print(text)
    else:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": HOOK_EVENTS[args.event],
                                                 "additionalContext": text}}))
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
                "required": True,
                "positional": True
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

    registry.register(
        name="trace-tester",
        description=(
            "Run the workspace's verbatim copy of the game's volume tester (made at setup; the game's "
            "files are never modified) in the game's own interpreter, with its event "
            "method traced. Maps every report key to the tester line that emits it, the engine "
            "response key the value reads, the if/for conditions around the call and the run-time "
            "values that built dynamic keys; lists tester lines that never fired and, with --file, "
            "report rows no line explains. Then indexes the engine from those response keys (as "
            "`slotdebug index`, same --path). Writes all three maps: <report>.tester_map.json, "
            "<report>.engine_map.json and the join, <report>.report_map.json"
        ),
        func=_trace_tester,
        args={
            "file": {"type": "string", "description": "Report to match rows against (workspace reports/)",
                     "required": False},
            "tester": {"type": "string", "description": "volume_tester.py (default: found as analyze finds it)",
                       "required": False},
            "n": {"type": "integer", "description": "Spins to run (default 1000); rare features need more",
                  "required": False},
            "python": {"type": "string", "description": "The game's interpreter, e.g. its venv's python",
                       "required": False},
            "path": {"type": "string", "description": "Directory added to PYTHONPATH, e.g. the OGA checkout (repeatable)",
                     "required": False},
            "kw": {"type": "string", "description": "Tester constructor argument NAME=TEXT or NAME:=JSON (repeatable)",
                   "required": False},
            "refresh-tester": {"type": "boolean",
                               "description": "Re-copy the tester into the workspace's tester/ (old copy kept)",
                               "required": False},
            "live": {"type": "boolean", "description": "Run the game's own tester instead of the workspace copy",
                     "required": False},
            "engine": {"type": "string", "description": "engine.py for the engine map (default: beside the tester)",
                       "required": False},
            "depth": {"type": "integer", "description": "Engine map: call depth to follow (default 6)",
                      "required": False},
            "no-index": {"type": "boolean", "description": "Write the tester map only", "required": False},
            "class": {"type": "string", "description": "Tester class, when the module defines several",
                      "required": False},
            "seed": {"type": "integer", "description": "Seed Python's `random` (the game's RNG may ignore it)",
                     "required": False},
            "timeout": {"type": "number", "description": "Give up after this many seconds", "required": False},
            "out": {"type": "string", "description": "Where to write the tester map (default: "
                                                     "analysis/<report>.tester_map.json; the other two maps "
                                                     "go beside it)", "required": False},
        },
        returns={"type": "object", "description": "sites (file:line, keys_seen, response_key, conditions, "
                                                   "condition_values, fired), never_fired, keys, report"},
        examples=["slotdebug trace-tester --file report.xlsx --python ../venv/bin/python --path ../OGA-5.0.2 -n 5000",
                  "slotdebug trace-tester --tester tests/volume_tester.py --kw stake=1"],
    )

    registry.register(
        name="index",
        description=(
            "Index the game engine backwards from its response keys: for each key, every place "
            "the engine (and the OGLE/OGA classes it inherits) puts it in a dict, and from there "
            "the assignments, conditions, method calls, parameters, self attributes and defs "
            "constants it is computed from. Reads code only. Keys come from the tester map "
            "(`trace-tester`). Writes analysis/<report>.engine_map.json and, with a tester map, "
            "the join in analysis/<report>.report_map.json: report row -> tester line -> "
            "response key -> engine roots"
        ),
        func=_index,
        args={
            "file": {"type": "string", "description": "Report whose tester map to use and join",
                     "required": False},
            "engine": {"type": "string", "description": "engine.py (default: beside the volume tester)",
                       "required": False},
            "path": {"type": "string", "description": "Framework checkout to resolve base classes, e.g. "
                                                      "OGA-5.0.2 (repeatable)", "required": False},
            "key": {"type": "string", "description": "Response key to index (repeatable; added to the "
                                                     "tester map's keys)", "required": False},
            "tree": {"type": "string", "description": "Print the dependency tree of this key as text "
                                                      "instead of JSON (repeatable)", "required": False},
            "depth": {"type": "integer", "description": "Call depth to follow (default 6)", "required": False},
            "tester-map": {"type": "string", "description": "Tester map JSON to take keys from and join "
                                                            "(default: the workspace's for --file)",
                           "required": False},
            "out": {"type": "string", "description": "Where to write the engine map (default: "
                                                     "analysis/<report>.engine_map.json)", "required": False},
        },
        returns={"type": "object", "description": "keys (roots per response key), nodes (kind, label, "
                                                   "file, line, function, conditions, children), chain"},
        examples=["slotdebug index --file report.xlsx --path ../OGA-5.0.2",
                  "slotdebug index --key current_winnings --tree current_winnings --path ../OGA-5.0.2"],
    )

    registry.register(
        name="map",
        description=(
            "Ask the three maps (written by trace-tester) one question. Views: `tree` (report row / "
            "response key / tester line -> tester -> engine code), `rows`, `keys`, `sites` (tables), "
            "`method` (where an engine method is defined, which class's version is used, parameters "
            "that do not reach its result, callers, what its result is computed from), `class` "
            "(hierarchy, which class each method comes from), `find` (search the engine map), "
            "`problems` (everything suspicious, ranked, with file:line to cite as evidence), "
            "`summary`. Reads the maps only; nothing is re-run"
        ),
        func=_map,
        args={
            "view": {"type": "string", "description": "tree | rows | keys | sites | method | class | find | "
                                                      "problems | summary", "required": True, "positional": True},
            "file": {"type": "string", "description": "Report whose maps to read (workspace reports/)",
                     "required": False},
            "row": {"type": "string", "description": "tree: a report row", "required": False},
            "key": {"type": "string", "description": "tree: a response key", "required": False},
            "site": {"type": "string", "description": "tree: a tester line, e.g. volume_tester.py:92",
                     "required": False},
            "name": {"type": "string", "description": "method: a method name; class: a class name",
                     "required": False},
            "text": {"type": "string", "description": "find: text to search node labels for", "required": False},
            "depth": {"type": "integer", "description": "tree: levels to show (default 6)", "required": False},
            "json": {"type": "boolean", "description": "problems: as JSON", "required": False},
            "prefix": {"type": "string", "description": "Outside a workspace: maps are PREFIX.tester_map.json, ...",
                       "required": False},
        },
        returns={"type": "string", "description": "Plain text / Markdown tables (problems: JSON with --json)"},
        examples=["slotdebug map tree --file report.xlsx --row action_bonus_win",
                  "slotdebug map method --name apply_bonus_multipliers",
                  "slotdebug map problems --file report.xlsx"],
    )

    registry.register(
        name="hook",
        description=(
            "Editor hook (registered by `slotdebug install claude --hook` or the plugin; not usually "
            "run by hand). `session-start`: the newest maps' problems and whether game files changed "
            "since they were built. `post-edit`: when an edited file is one the engine map was built "
            "from, re-index the engine (code only) and report problems now gone or new. `prompt`: "
            "suggest the slot-debugger agent when a prompt names a report in the workspace. "
            "`agent-result`: after the slot-debugger agent returns, give the parent its diagnosis. "
            "`agent-stop`: keep the agent going until `slotdebug report` is written with resolving "
            "evidence. `agent-bash`: the agent's read-only guard. Reads the hook event JSON on stdin"
        ),
        func=None,
        args={
            "event": {"type": "string", "description": "session-start | post-edit | prompt | agent-result | "
                                                       "agent-stop | agent-bash", "required": True,
                      "positional": True},
            "format": {"type": "string", "description": "claude (default: hookSpecificOutput JSON) or text",
                       "required": False},
            "file": {"type": "string", "description": "post-edit: the edited file (instead of stdin)",
                     "required": False},
        },
        examples=["slotdebug hook session-start --format text",
                  "slotdebug hook post-edit --file engine.py --format text"],
    )

    registry.register(
        name="report",
        description=(
            "Assemble the diagnosis for the parent process: findings (with evidence, each cited "
            "file:line checked, and the proposed fix), branch verdicts, targets with expected/actual "
            "from the diff, unexplained targets, open branches, map problems no finding cites, next "
            "steps. Built from the think session, the diff and the maps; writes "
            "analysis/<report>.agent_report.json and .md. Run it last in a diagnosis, in the session "
            "itself or in the slot-debugger agent"
        ),
        func=_report,
        args={
            "file": {"type": "string", "description": "Report the diagnosis is about (workspace reports/)",
                     "required": False},
            "json": {"type": "boolean", "description": "Print JSON instead of Markdown", "required": False},
        },
        returns={"type": "object", "description": "status, targets, findings, conclusions, problems, next_steps"},
        examples=["slotdebug report --file report.csv", "slotdebug report --file report.csv --json"],
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
        description=(
            "Sequential-thinking CLI: a persisted, evidence-backed investigation. One mode per "
            "call (--setPlan, --nextStep, --thought, --conclude, --finding, --setTargets, "
            "--similar, --status, --history, --export, --reset). State lives in the "
            "workspace's state/. Follow the sequential-thinking skill"
        ),
        func=None,
        args={
            "setPlan": {"type": "string", "description": "Plan steps: comma-separated or a JSON list",
                        "required": False},
            "nextStep": {"type": "boolean", "description": "Advance to the next plan step", "required": False},
            "thought": {"type": "string", "description": "Record a thought (with --thoughtNumber, "
                                                         "--totalThoughts, --nextThoughtNeeded true|false)",
                        "required": False},
            "thoughtNumber": {"type": "integer", "description": "Number of this thought: the next in sequence",
                              "required": False},
            "totalThoughts": {"type": "integer", "description": "Current estimate of thoughts needed",
                              "required": False},
            "nextThoughtNeeded": {"type": "boolean", "description": "true until the final thought; false is "
                                                                    "refused while branches or targets are open",
                                  "required": False},
            "evidence": {"type": "string", "description": "file:line or command a thought, conclusion or "
                                                          "finding rests on (repeatable)", "required": False},
            "branchFromThought": {"type": "integer", "description": "Open/continue a branch (with --branchId)",
                                  "required": False},
            "branchId": {"type": "string", "description": "Name of the branch", "required": False},
            "target": {"type": "string", "description": "Key a branch explains (with --cause), or keys a "
                                                        "finding explains (repeatable)", "required": False},
            "cause": {"type": "string", "description": "Suspected cause of a branch; a concluded "
                                                       "target + cause is refused", "required": False},
            "isRevision": {"type": "boolean", "description": "Correct an earlier thought (with --revisesThought)",
                           "required": False},
            "revisesThought": {"type": "integer", "description": "The thought a revision corrects",
                               "required": False},
            "conclude": {"type": "string", "description": "Close a branch: --verdict confirmed|refuted "
                                                          "--reason ... --evidence ...", "required": False},
            "verdict": {"type": "string", "description": "With --conclude: confirmed or refuted", "required": False},
            "reason": {"type": "string", "description": "With --conclude: why", "required": False},
            "finding": {"type": "string", "description": "Record a final result (--evidence required)",
                        "required": False},
            "fix": {"type": "string", "description": "With --finding: the change proposed to fix it",
                    "required": False},
            "setTargets": {"type": "string", "description": "Keys to explain: a `slotdebug diff` file or "
                                                            "comma-separated keys", "required": False},
            "similar": {"type": "string", "description": "List earlier entries that may already cover this",
                        "required": False},
            "force": {"type": "boolean", "description": "Record despite a duplicate question, or finish "
                                                       "with work open", "required": False},
            "status": {"type": "boolean", "description": "Plan progress, open branches, unexplained targets",
                       "required": False},
            "history": {"type": "boolean", "description": "All thoughts, conclusions and findings",
                        "required": False},
            "reset": {"type": "boolean", "description": "Start a new session", "required": False},
            "export": {"type": "string", "description": "Write the whole state (plan, thoughts, branches, "
                                                        "conclusions, findings) to a JSON file", "required": False},
            "state": {"type": "string", "description": "State file (default: the workspace's "
                                                       "state/.think_state.json)", "required": False},
        },
        examples=[
            "slotdebug think --setTargets slotdebugger/analysis/report.diff.json",
            "slotdebug think --thought \"cap may clip FG12\" --thoughtNumber 2 --totalThoughts 6 "
            "--nextThoughtNeeded true --branchFromThought 1 --branchId fg12-cap --target components.FG12 "
            "--cause cap --evidence engine.py:143",
        ]
    )

    for name, what in (("install", "Register"), ("uninstall", "Remove")):
        registry.register(
            name=name,
            description=(f"{what} the slotdebug skills, the always-on command reference (project "
                         f"CLAUDE.md block, Cursor rule) and, with --hook, Claude hooks (SessionStart: "
                         f"the maps' problems; PostToolUse on edits: re-index the engine and report what "
                         f"changed). Target `plugin`: a local Claude Code marketplace with the slotdebug "
                         f"plugin (skills, hooks, /slotdebug:tree, :problems, :method, :summary) at --dir "
                         f"(default ~/.local/share/slotdebug/claude-marketplace). `slotdebug setup` "
                         f"registers the skills for a game workspace"),
            func=None,
            args={
                "target": {"type": "string", "description": "claude, cursor, all (default) or plugin",
                           "required": False, "positional": True},
                "scope": {"type": "string", "description": "Claude: user (~/.claude, default) or project",
                          "required": False},
                "dir": {"type": "string", "description": "Project directory (default: cwd); for `plugin`, "
                                                         "where the marketplace goes", "required": False},
                **({"hook": {"type": "boolean", "description": "Claude: also register the SessionStart and "
                                                               "PostToolUse hooks (`slotdebug hook ...`)",
                             "required": False}}
                   if name == "install" else {}),
            },
            examples=[f"slotdebug {name} claude --scope project" + (" --hook" if name == "install" else ""),
                      f"slotdebug {name} plugin"],
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
        for name in sorted(set(previous.get("rules", [])) - set(install.RULES)):
            if install.remove_cursor_rule(project_dir, name):
                done.append(f"retired rule {name}")
    except OSError as e:
        print(f"warning: could not register Cursor rules: {e}", file=sys.stderr)

    try:
        manifest = ws.path("data", "commands.json")
        ws._write_json(manifest, get_registry().get_all_commands())
        done.append(f"commands -> {manifest}")
        ws.record_registered(skills=list(install.SKILLS), rules=list(install.RULES))
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

    tt = sub.add_parser("trace-tester",
                        help="run the volume tester traced: report key -> tester line -> response key")
    tt.add_argument("--file", help="report to match rows against (workspace reports/, or a path)")
    tt.add_argument("--tester", help="volume_tester.py (default: beside the report / in the workspace)")
    tt.add_argument("-n", type=int, default=1000, help="spins to run (default 1000)")
    tt.add_argument("--python", help="the game's interpreter (default: this one)")
    tt.add_argument("--path", action="append", default=[], help="add to PYTHONPATH, e.g. the OGA checkout")
    tt.add_argument("--kw", action="append", default=[], metavar="NAME=TEXT|NAME:=JSON",
                    help="tester constructor argument (repeatable)")
    tt.add_argument("--class", dest="cls", help="tester class, when the module defines several")
    tt.add_argument("--seed", type=int, help="seed Python's `random` (the game's own RNG may ignore it)")
    tt.add_argument("--timeout", type=float, help="give up after this many seconds")
    tt.add_argument("--out", help="where to write the map (default: analysis/<report>.tester_map.json)")
    tt.add_argument("--refresh-tester", action="store_true",
                    help="re-copy the volume tester into tester/ (the old copy is kept beside it)")
    tt.add_argument("--live", action="store_true",
                    help="run the game's own tester file instead of the workspace copy")
    tt.add_argument("--engine", help="engine.py for the engine map (default: beside the volume tester)")
    tt.add_argument("--depth", type=int, default=6, help="engine map: call depth to follow (default 6)")
    tt.add_argument("--no-index", action="store_true",
                    help="write the tester map only (skip the engine map and report map)")

    ix = sub.add_parser("index", help="index the engine backwards from its response keys")
    ix.add_argument("--file", help="report whose tester map to use (workspace reports/, or a path)")
    ix.add_argument("--tester-map", help="tester map JSON (default: the workspace's for --file)")
    ix.add_argument("--engine", help="engine.py (default: beside the volume tester)")
    ix.add_argument("--path", action="append", default=[], help="framework checkout, e.g. OGA-5.0.2")
    ix.add_argument("--key", action="append", default=[], help="response key to index (repeatable)")
    ix.add_argument("--tree", action="append", default=[], metavar="KEY",
                    help="print the dependency tree of KEY as text instead of JSON")
    ix.add_argument("--depth", type=int, default=6, help="call depth to follow (default 6)")
    ix.add_argument("--out", help="where to write the index (default: analysis/<report>.engine_map.json)")

    mp = sub.add_parser("map", help="ask the three maps: tree, rows, keys, sites, method, class, find, problems, summary")
    mp.add_argument("view", choices=("tree", "rows", "keys", "sites", "method", "class", "find", "problems", "summary"))
    mp.add_argument("--file", help="report whose maps to read (workspace reports/)")
    mp.add_argument("--row", help="tree: a report row")
    mp.add_argument("--key", help="tree: a response key")
    mp.add_argument("--site", help="tree: a tester line, e.g. volume_tester.py:92")
    mp.add_argument("--name", help="method / class: the name to look up")
    mp.add_argument("--text", help="find: text to search for")
    mp.add_argument("--depth", type=int, default=6, help="tree: levels to show (default 6)")
    mp.add_argument("--json", action="store_true", help="problems: print JSON")
    mp.add_argument("--prefix", help="outside a workspace: the maps are PREFIX.tester_map.json, ...")

    hk = sub.add_parser("hook", help="editor hook (reads the event JSON on stdin)")
    hk.add_argument("event", choices=tuple(HOOK_EVENTS))
    hk.add_argument("--format", choices=("claude", "text"), default="claude",
                    help="claude: hookSpecificOutput JSON (default); text: plain")
    hk.add_argument("--file", help="post-edit: the edited file (instead of the event on stdin)")

    rp = sub.add_parser("report", help="assemble the diagnosis: <report>.agent_report.json / .md")
    rp.add_argument("--file", help="report the diagnosis is about (workspace reports/)")
    rp.add_argument("--json", action="store_true", help="print JSON instead of Markdown")

    sub.add_parser("think", help="sequential-thinking CLI (sequential-thinking CLI)")

    for name, help_text in (("install", "register skills (and optional hook) with Claude Code / Cursor"),
                            ("uninstall", "remove what `install` registered")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("target", choices=("claude", "cursor", "all", "plugin"), nargs="?", default="all",
                       help="claude, cursor, all (both), or plugin (a local Claude Code plugin marketplace)")
        p.add_argument("--scope", choices=("user", "project"), default="user", help="Claude scope")
        p.add_argument("--hook", action="store_true", help="Claude: add SessionStart hook (install only)")
        p.add_argument("--dir", help="project dir (default: cwd)")

    args = parser.parse_args(argv)

    try:
        if args.cmd == "setup":
            made, changes = Workspace.setup(args.dir, args.name)
            fresh = "updated" not in made.info()  # only re-runs record `updated`
            changes += _setup_tester(made, args.dir)
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
                      "\n  state/     thinking-CLI state"
                      "\n  tester/    copy of the volume tester that trace-tester runs")
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
        if args.cmd == "trace-tester":
            return _trace_tester(ws, args)
        if args.cmd == "index":
            return _index(ws, args)
        if args.cmd == "map":
            return _map(ws, args)
        if args.cmd == "hook":
            return _hook(args)
        if args.cmd == "report":
            return _report(ws, args)
    except (OSError, ValueError) as e:  # WorkspaceError is a ValueError
        print(f"error: {e}", file=sys.stderr)
        return 2

    return install.run(args)


if __name__ == "__main__":
    sys.exit(main())
