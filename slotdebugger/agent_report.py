"""The slot-debugger agent's result, assembled from what it recorded, for whoever acts on it.

    doc = build(ws, report_path)          # dict; also written as <report>.agent_report.json / .md

The agent only diagnoses. Its conclusions live in the thinking session (findings,
branch verdicts, targets) and in the maps (problems); this module turns them into one
document with a fixed shape, so the parent process does not depend on how the agent
worded its last message:

    status            complete | incomplete | not_started
    targets           each key to explain: expected/actual from the diff, findings that explain it
    findings          what holds, with evidence, a proposed fix, and whether each cited
                      file:line exists
    conclusions       every branch verdict (target, cause, confirmed/refuted, reason, evidence)
    open_branches / unexplained_targets
    problems          the maps' high/medium problems no finding cites yet
    next_steps        what the parent should do with it
    artifacts         the files behind it

Nothing here decides a cause: it only collects and checks what was recorded.
"""

import json
import os
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from slotdebugger.workspace import Workspace

REF = re.compile(r"^(?P<file>[\w./\\-]+\.py):(?P<line>\d+)$")


def build(ws: Workspace, report: Optional[str] = None) -> Dict:
    state = _read(ws.think_state) or {}
    maps = _maps(ws, report)
    diff = _read(_diff_path(ws, report)) or {}
    diffs = {d["key"]: d for d in diff.get("diffs", [])}
    findings = [_finding(i, f, ws, maps) for i, f in enumerate(state.get("findings", []), 1)]
    targets = state.get("targets", [])
    meta = state.get("branchMeta", {})
    conclusions = [{"branch": b, "target": meta.get(b, {}).get("target"), "cause": meta.get(b, {}).get("cause"),
                    **c} for b, c in state.get("conclusions", {}).items()]
    open_branches = [b for b in state.get("branches", {}) if b not in state.get("conclusions", {})]
    explained = {t.casefold() for f in findings for t in f["targets"]}
    unexplained = [t for t in targets if t.casefold() not in explained]

    cited = {e for f in findings for e in f["evidence"]} | {e for c in conclusions for e in c.get("evidence", [])}
    problems = []
    if maps is not None:
        from engine_indexer import views
        problems = [p for p in views.problems(maps) if p["severity"] in ("high", "medium")
                    and not any(_same_place(p["where"], e) for e in cited)]

    if not state.get("thoughtHistory"):
        status = "not_started"
    elif open_branches or unexplained:
        status = "incomplete"
    else:
        status = "complete"
    doc = {
        "report": os.path.basename(report) if report else None,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "diagnosis: fixes are proposed, not applied; apply `fix` items after review",
        "status": status,
        "targets": [{"key": t, **{k: diffs[t][k] for k in ("expected", "actual", "delta", "status") if t in diffs},
                     "explained_by": [f["id"] for f in findings if t.casefold() in {x.casefold() for x in f["targets"]}]}
                    for t in targets],
        "findings": findings,
        "conclusions": conclusions,
        "open_branches": open_branches,
        "unexplained_targets": unexplained,
        "problems": problems,
        "next_steps": _next_steps(status, findings, unexplained, open_branches, problems),
        "artifacts": _artifacts(ws, report),
    }
    return doc


def write(ws: Workspace, report: Optional[str], doc: Dict) -> List[str]:
    stem = ws.analysis_path(report)[: -len(".json")] if report else os.path.join(ws.analysis, "report")
    json_path, md_path = f"{stem}.agent_report.json", f"{stem}.agent_report.md"
    Workspace._write_json(json_path, doc)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown(doc))
    return [json_path, md_path]


def markdown(doc: Dict) -> str:
    lines = [f"# Slot debugger diagnosis: {doc['report'] or 'workspace'}", "",
             f"- **Status**: {doc['status']}", f"- **Mode**: {doc['mode']}", ""]
    if doc["findings"]:
        lines += ["## Findings", ""]
        for f in doc["findings"]:
            lines.append(f"### {f['id']}. {f['finding']}")
            if f["targets"]:
                lines.append(f"- explains: {', '.join(f['targets'])}")
            for e in f["evidence_check"]:
                mark = "" if e["exists"] is None else (" ✓" if e["exists"] else " ✗ not found")
                lines.append(f"- evidence: `{e['ref']}`{mark}")
            if f.get("fix"):
                lines.append(f"- **proposed fix**: {f['fix']}")
            lines.append("")
    if doc["targets"]:
        lines += ["## Targets", "", "| key | expected | actual | delta | explained by |", "|---|---|---|---|---|"]
        for t in doc["targets"]:
            lines.append(f"| {t['key']} | {t.get('expected', '-')} | {t.get('actual', '-')} | {t.get('delta', '-')} "
                         f"| {', '.join(map(str, t['explained_by'])) or '**unexplained**'} |")
        lines.append("")
    if doc["conclusions"]:
        lines += ["## Hypotheses checked", ""]
        lines += [f"- {c['verdict']}: {c.get('target') or c['branch']} / {c.get('cause') or '-'}: {c['reason']} "
                  f"({', '.join(c.get('evidence', []))})" for c in doc["conclusions"]]
        lines.append("")
    if doc["problems"]:
        lines += ["## Problems the maps report that no finding cites", ""]
        lines += [f"- [{p['severity']}] `{p['kind']}` at `{p['where']}`: {p['message']}" for p in doc["problems"]]
        lines.append("")
    lines += ["## Next steps", ""] + [f"- {s}" for s in doc["next_steps"]] + [""]
    return "\n".join(lines)


# ---- pieces ----

def _finding(i: int, f: Dict, ws: Workspace, maps) -> Dict:
    return {"id": i, "finding": f["finding"], "targets": f.get("targets", []), "evidence": f.get("evidence", []),
            "evidence_check": [_check(ref, ws, maps) for ref in f.get("evidence", [])],
            **({"fix": f["fix"]} if f.get("fix") else {})}


def _check(ref: str, ws: Workspace, maps) -> Dict:
    """`engine.py:87` -> does that file have a line 87? Commands and prose are not checked (None)."""
    m = REF.match(ref.strip())
    if not m:
        return {"ref": ref, "exists": None}
    path = _locate(m["file"], ws, maps)
    if path is None:
        return {"ref": ref, "exists": False, "note": "file not found"}
    with open(path, encoding="utf-8", errors="replace") as fh:
        count = sum(1 for _ in fh)
    return {"ref": ref, "exists": int(m["line"]) <= count, "file": path}


def _locate(name: str, ws: Workspace, maps) -> Optional[str]:
    candidates = [os.path.join(os.path.dirname(ws.root), name), os.path.join(ws.root, name)]
    files = (maps.engine or {}).get("files", []) if maps else []
    candidates += [f for f in files if f.endswith(os.sep + name) or f == name]
    tester = (maps.tester or {}).get("tester") if maps else None
    if tester:
        candidates.append(os.path.join(os.path.dirname(tester), os.path.basename(name)))
    return next((c for c in candidates if os.path.isfile(c)), None)


def _same_place(where: str, evidence: str) -> bool:
    """A problem at `engine.py:87` is cited by evidence `engine.py:87` (or a longer path to it)."""
    return bool(where) and (where == evidence or evidence.endswith("/" + where) or where.endswith("/" + evidence))


def _next_steps(status, findings, unexplained, open_branches, problems) -> List[str]:
    steps = []
    fixes = [f for f in findings if f.get("fix")]
    if fixes:
        steps.append(f"Review and apply the proposed fix of finding(s) {', '.join(str(f['id']) for f in fixes)}; "
                     f"then re-run `slotdebug trace-tester` (and `analyze` / `diff`) to confirm.")
    bad = [e["ref"] for f in findings for e in f["evidence_check"] if e["exists"] is False]
    if bad:
        steps.append(f"Evidence that does not resolve, check before acting: {', '.join(bad)}.")
    if unexplained:
        steps.append(f"Still unexplained: {', '.join(unexplained)}; the diagnosis does not cover them.")
    if open_branches:
        steps.append(f"Hypotheses left open: {', '.join(open_branches)}.")
    if problems:
        steps.append(f"{len(problems)} problem(s) from the maps are not cited by any finding; check whether they "
                     f"matter (see `problems`).")
    if status == "not_started":
        steps.append("No investigation recorded: run the slot-debugger agent (or `slotdebug think`) first.")
    return steps or ["Nothing to act on."]


def _maps(ws: Workspace, report: Optional[str]):
    paths = (ws.tester_map_path(report), ws.engine_map_path(report), ws.report_map_path(report))
    if not any(os.path.isfile(p) for p in paths):
        return None
    from engine_indexer import views
    return views.Maps.load(*paths)


def _diff_path(ws: Workspace, report: Optional[str]) -> Optional[str]:
    return ws.diff_path(report) if report else None


def _artifacts(ws: Workspace, report: Optional[str]) -> List[str]:
    stem = ws.analysis_path(report)[: -len(".json")] if report else os.path.join(ws.analysis, "")
    names = [".json", ".diff.json", ".tester_map.json", ".engine_map.json", ".report_map.json", ".summary.md"] \
        if report else ["tester_map.json", "engine_map.json", "report_map.json", "report.summary.md"]
    found = [stem + n for n in names if os.path.isfile(stem + n)]
    if os.path.isfile(ws.think_state):
        found.append(ws.think_state)
    return found


def _read(path: Optional[str]):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
