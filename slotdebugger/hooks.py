"""What the editor hooks say: the debugger's state at session start, and what an edit changed.

    slotdebug hook session-start      # problems from the newest maps, and whether they are stale
    slotdebug hook post-edit          # re-index the engine if an edited file is in its map

Both read the hook's JSON on stdin (`cwd`, and for post-edit `tool_input.file_path`),
stay silent when there is no workspace or nothing relevant changed, and never fail the
editor: any error becomes a one-line note. The text they return is wrapped for the
editor by `cli._hook`.
"""

import glob
import json
import os
import re
from typing import Dict, List, Optional, Tuple

from engine_indexer import engine_map, views
from engine_indexer.tester_map import _run_dir, sha256
from slotdebugger.workspace import Workspace

MAX_LISTED = 6


def latest_maps(ws: Workspace) -> Optional[Tuple[Dict[str, str], views.Maps]]:
    """Paths (`tester`, `engine`, `joined`, `stem` for summaries) and maps of the newest engine map.

    `analysis/r.engine_map.json` goes with `r.tester_map.json`, `r.report_map.json`, `r.*.md`;
    `analysis/engine_map.json` (no report) with `tester_map.json`, `report_map.json`, `report.*.md`.
    """
    found = sorted(glob.glob(os.path.join(ws.analysis, "*engine_map.json")), key=os.path.getmtime)
    if not found:
        return None
    engine = found[-1]
    if os.path.basename(engine) == "engine_map.json":
        base, stem = os.path.join(ws.analysis, ""), os.path.join(ws.analysis, "report")
        paths = {"tester": base + "tester_map.json", "joined": base + "report_map.json"}
    else:
        stem = engine[: -len(".engine_map.json")]
        paths = {"tester": stem + ".tester_map.json", "joined": stem + ".report_map.json"}
    paths.update(engine=engine, stem=stem)
    return paths, views.Maps.load(paths["tester"], engine, paths["joined"])


def stale_files(maps: views.Maps, engine_map_path: Optional[str] = None) -> List[str]:
    """Game files changed since the maps were built (hash for the tester map, mtime for the engine map)."""
    changed = []
    t = maps.tester or {}
    if t.get("game_files") and t.get("tester"):
        base = os.path.dirname(_run_dir(t["tester"]))
        for rel, digest in t["game_files"].items():
            path = rel if os.path.isabs(rel) else os.path.join(base, rel)
            if os.path.isfile(path) and sha256(path) != digest:
                changed.append(path)
    if maps.engine and engine_map_path and os.path.isfile(engine_map_path):
        built = os.path.getmtime(engine_map_path)
        changed += [f for f in maps.engine.get("files", [])
                    if os.path.isfile(f) and os.path.getmtime(f) > built and f not in changed]
    return changed


def session_start(cwd: str) -> Optional[str]:
    ws = Workspace.find(cwd)
    if ws is None:
        return None
    latest = latest_maps(ws)
    if latest is None:
        return ("slotdebug: this game has a slotdebugger workspace but no maps yet. "
                "`slotdebug trace-tester --file <report> --python <game venv python> --path <OGA checkout>` "
                "builds them (report key -> tester line -> engine code).")
    paths, maps = latest
    stem = paths["stem"]
    found = views.problems(maps)
    high = [p for p in found if p["severity"] == "high"]
    lines = [f"slotdebug: maps for `{os.path.basename(stem)}` in {ws.analysis} "
             f"({len(high)} high, {len(found) - len(high)} other problems). "
             f"Read {stem}.summary.md; ask with `slotdebug map <view>` (tree, problems, method, class, find)."]
    for p in high[:MAX_LISTED]:
        lines.append(f"- [{p['kind']}] {p['where']}: {p['message']}")
    if len(high) > MAX_LISTED:
        lines.append(f"- ... {len(high) - MAX_LISTED} more: `slotdebug map problems`")
    changed = stale_files(maps, paths["engine"])
    if changed:
        lines.append("Stale: changed since the maps were built: "
                     + ", ".join(os.path.basename(c) for c in changed[:5])
                     + ". Re-run `slotdebug trace-tester` (tester/run) or `slotdebug index` (engine only).")
    return "\n".join(lines)


def post_edit(cwd: str, file_path: Optional[str]) -> Optional[str]:
    """Re-index the engine when an edited file is one the engine map was built from."""
    if not file_path or not file_path.endswith(".py"):
        return None
    ws = Workspace.find(cwd) or Workspace.find(os.path.dirname(os.path.abspath(file_path)))
    if ws is None:
        return None
    latest = latest_maps(ws)
    if latest is None:
        return None
    paths, maps = latest
    edited = os.path.realpath(file_path if os.path.isabs(file_path) else os.path.join(cwd, file_path))
    tester = (maps.tester or {}).get("tester")
    if tester and edited in (os.path.realpath(tester), os.path.realpath((maps.tester or {}).get("ran", ""))):
        return (f"slotdebug: {os.path.basename(edited)} is the volume tester the maps came from; "
                f"`slotdebug trace-tester --refresh-tester` re-copies it and rebuilds all maps.")
    eng = maps.engine
    if not eng or edited not in {os.path.realpath(f) for f in eng.get("files", [])}:
        return None
    before = views.problems(maps)
    doc = engine_map.build(eng["engine"], keys=list(eng["keys"]), paths=eng.get("paths", []),
                           tester_map=maps.tester, max_depth=eng.get("max_depth", 6))
    joined = {"tester_map": paths["tester"], "engine": doc["engine"], **doc.pop("chain")} if maps.tester else None
    engine_path = paths["engine"]
    Workspace._write_json(engine_path, doc)
    if joined:
        joined["engine_map"] = engine_path
        Workspace._write_json(paths["joined"], joined)
    after_maps = views.Maps(maps.tester, doc, joined)
    views.write_summaries(after_maps, paths["stem"])
    after = views.problems(after_maps)
    fixed = [p for p in before if _identity(p) not in {_identity(a) for a in after} and p["source"] != "run"]
    new = [p for p in after if _identity(p) not in {_identity(b) for b in before}]
    lines = [f"slotdebug: re-indexed the engine after the edit to {os.path.basename(edited)} "
             f"({engine_path}). Static check only; re-run `slotdebug trace-tester` to confirm with a run."]
    for label, items in (("no longer found", fixed), ("new", new)):
        for p in items[:MAX_LISTED]:
            lines.append(f"- {label}: [{p['kind']}] {p['where']}: {p['message']}")
    if not fixed and not new:
        lines.append("- no change in the problems the maps can see")
    return "\n".join(lines)


def _identity(problem: Dict) -> tuple:
    """A problem across an edit: line numbers shift, so they are left out."""
    strip = lambda text: re.sub(r"\d+", "#", str(text))  # noqa: E731
    return problem["kind"], strip(problem["where"]), strip(problem["message"])


def read_event(stream) -> Dict:
    try:
        data = json.load(stream)
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


# ---- the slot-debugger agent: read-only guard, finish gate, result hand-off, prompt hint ----

AGENT_NAMES = ("slot-debugger", "slotdebug:slot-debugger")
READ_ONLY = {"cat", "head", "tail", "grep", "egrep", "rg", "ls", "wc", "sort", "uniq", "diff", "find",
             "file", "stat", "pwd", "echo", "true", "tree", "basename", "dirname", "realpath", "which", "cut", "nl"}
GIT_READ = {"log", "show", "diff", "status", "blame", "grep", "ls-files", "rev-parse"}
SLOTDEBUG_DENIED = {"install", "uninstall", "hook"}


def agent_bash(command: str) -> Optional[str]:
    """None if the agent may run `command`, else why not. Diagnose-only: read, never write.

    Every part of a pipeline / `&&` / `;` chain must be allowed. Output redirection,
    `sed -i`, `find -delete/-exec` and anything not on the read-only list are refused;
    `slotdebug` is allowed except `install`, `uninstall` and `hook`.
    """
    # any `>` left once `2>&1`-style duplications are removed writes a file
    if ">" in re.sub(r"\d*>&\d+", "", command) or re.search(r"\btee\b|`|\$\(", command):
        return "output redirection, tee and command substitution are not allowed (diagnose only)"
    for part in re.split(r"\s*(?:&&|\|\||;|\|)\s*", command.strip()):
        words = part.split()
        while words and re.match(r"^\w+=", words[0]):  # leading VAR=value
            words = words[1:]
        if not words:
            continue
        exe = os.path.basename(words[0])
        if exe == "slotdebug":
            if len(words) > 1 and words[1] in SLOTDEBUG_DENIED:
                return f"`slotdebug {words[1]}` changes the editor setup; not part of a diagnosis"
            continue
        if exe == "sed":
            if "-n" not in words or any(w.startswith("-i") or w == "--in-place" for w in words):
                return "only `sed -n` (print) is allowed; the agent never edits files"
            continue
        if exe == "find" and any(w in ("-delete", "-exec", "-execdir", "-ok", "-fprint") for w in words):
            return "find may only list files"
        if exe == "git":
            sub = next((w for w in words[1:] if not w.startswith("-")), "")
            if sub not in GIT_READ:
                return f"`git {sub}` is not read-only; allowed: {', '.join(sorted(GIT_READ))}"
            continue
        if exe not in READ_ONLY:
            return (f"`{exe}` is not allowed for the slot-debugger agent (diagnose only): use `slotdebug` "
                    f"and read-only commands ({', '.join(sorted(READ_ONLY))}, sed -n, git log/show/diff)")
    return None


def latest_agent_report(ws: Workspace) -> Optional[str]:
    found = sorted(glob.glob(os.path.join(ws.analysis, "*agent_report.json")), key=os.path.getmtime)
    return found[-1] if found else None


def agent_stop(cwd: str) -> Optional[str]:
    """Why the slot-debugger agent may not finish yet, or None to let it finish."""
    ws = Workspace.find(cwd)
    if ws is None:
        return None  # nothing to check outside a workspace
    path = latest_agent_report(ws)
    if path is None:
        return ("Before finishing, run `slotdebug report --file <report>`: it writes the structured "
                "diagnosis the parent reads and checks your evidence.")
    if os.path.isfile(ws.think_state) and os.path.getmtime(ws.think_state) > os.path.getmtime(path):
        return ("Your investigation changed after the last `slotdebug report`; run it again so the "
                "diagnosis includes your latest findings.")
    with open(path) as f:
        doc = json.load(f)
    if doc.get("status") == "not_started":
        return ("No investigation is recorded. Record thoughts, conclusions and findings with "
                "`slotdebug think`, then run `slotdebug report`.")
    bad = [e["ref"] for fnd in doc.get("findings", []) for e in fnd.get("evidence_check", []) if e.get("exists") is False]
    if bad:
        return (f"Evidence in your findings does not resolve to a file:line: {', '.join(bad)}. Correct it "
                f"(`slotdebug think --finding ...` again with the right reference), then `slotdebug report`.")
    return None


def agent_result(cwd: str) -> Optional[str]:
    """The newest diagnosis, condensed, for the parent after the agent returns."""
    ws = Workspace.find(cwd)
    path = latest_agent_report(ws) if ws else None
    if path is None:
        return None
    with open(path) as f:
        doc = json.load(f)
    lines = [f"slot-debugger diagnosis ({doc.get('status')}) for {doc.get('report') or 'the workspace'}: "
             f"{path} (Markdown beside it). Diagnose-only: nothing in the game was changed."]
    for fnd in doc.get("findings", []):
        lines.append(f"- finding {fnd['id']}: {fnd['finding']} [evidence: {', '.join(fnd['evidence'])}]"
                     + (f" -> proposed fix: {fnd['fix']}" if fnd.get("fix") else ""))
    if doc.get("unexplained_targets"):
        lines.append(f"- unexplained: {', '.join(doc['unexplained_targets'])}")
    lines += [f"- next: {s}" for s in doc.get("next_steps", [])[:3]]
    return "\n".join(lines)


def prompt_hint(cwd: str, prompt: str) -> Optional[str]:
    """Suggest the agent when the prompt names a report in this workspace (explicit mentions only)."""
    ws = Workspace.find(cwd)
    if ws is None or not prompt or not os.path.isdir(ws.reports):
        return None
    named = [r for r in sorted(os.listdir(ws.reports)) if r in prompt or os.path.splitext(r)[0] in prompt.split()]
    if not named:
        return None
    return (f"slotdebug: the prompt names report(s) {', '.join(named)} in this workspace. To diagnose an RTP "
            f"problem, follow 'Diagnose a report' in the slot-debugger skill in this session, or delegate to "
            f"the slot-debugger agent (diagnose only, its own context). Either way `slotdebug report` writes "
            f"the causes, file:line evidence and proposed fixes to "
            f"slotdebugger/analysis/<report>.agent_report.json.")
