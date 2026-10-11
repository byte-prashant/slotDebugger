"""Report key -> volume tester line -> engine response key, from a traced run.

    doc = build("tests/volume_tester.py", n=2000, python="venv/bin/python",
                paths=["/path/to/OGA"], report="report.xlsx")

1. `tracer.py` runs the tester unchanged in the game's interpreter and records each
   event's concrete key, the line that emitted it, and the local values at that moment.
2. Each file the trace passed through is read (`sites.scan`) for what a line cannot
   tell at run time: the key expression, the response key the value reads
   (`p.result['current_winnings']`), and the `if`/`for` chain around the call.
3. The two are joined per call site. Sites in the tester that never fired are kept,
   with `fired: false`: a branch the run did not reach, or one that cannot fire.
4. With a report, each row is matched to the sites that produced it: `traced` (the run
   produced exactly that key), `pattern` (only the static key pattern matches: the run
   did not reach it), or unexplained.

`source` runs a verbatim copy of the tester (kept in the workspace) in place of the
original: it is loaded under the original's module name, so the game's package and
relative imports are the same. Sites are labelled with the original's path when the
copy still matches it byte for byte (same lines), and with the copy's path when the
original has changed since. Every file of the game that was loaded is hashed into
`game_files`, so a later map can tell whether the engine or defs changed in between.

The engine's own results are counted too (`response_keys`: per top-level key, how many
results had it and how many had it empty), and each site's `response_key_status` says
what happened to the key it reads: `present` (returned with a value at least once),
`always_empty` (returned, but always None or empty), `never_returned` (in none of the
results: the tester reads a key the engine does not produce), or `unknown` (no response
key, or no results were seen).

A site the run did not reach is not proof it never fires: rare features need more
spins. `fired: false` means "not in N spins", and the document says what N was.
"""

import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional

from engine_indexer import sites as static

TRACER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tracer.py")


class TraceError(RuntimeError):
    """The tracer could not run or produced no result."""


def build(tester: str, n: int = 1000, python: Optional[str] = None, paths: Optional[List[str]] = None,
          kwargs: Optional[List[str]] = None, cls: Optional[str] = None, seed: Optional[int] = None,
          report: Optional[str] = None, timeout: Optional[float] = None, source: Optional[str] = None) -> Dict:
    tester = os.path.abspath(tester)
    source = os.path.abspath(source) if source else None
    trace = run_tracer(tester, n, python or sys.executable, paths or [], kwargs or [], cls, seed, timeout, source)
    ran = source or tester
    label = tester if source is None or (os.path.isfile(tester) and sha256(tester) == sha256(source)) else source
    for site in trace.get("sites", []):
        if site["file"] == ran:
            site["file"] = label
    doc = merge(label, trace, scan_from=ran)
    doc["tester"] = tester
    doc["ran"] = ran
    doc["labelled_as"] = label
    base = os.path.dirname(_run_dir(tester))
    files = [ran] + [f for f in trace.get("game_modules", []) if f != ran]
    doc["game_files"] = {_rel(f, base): sha256(f) for f in files if os.path.isfile(f)}
    annotate_response_keys(doc, trace)
    doc["report"] = None
    if report:
        rows = report_rows(report)
        doc["report"] = dict(join_report(doc, rows), file=os.path.abspath(report), rows_parsed=len(rows))
        if not rows:  # nothing matched is not the same as everything matched
            doc["report"]["warning"] = "no component rows could be read from the report; nothing was matched"
    return doc


def run_tracer(tester: str, n: int, python: str, paths: List[str], kwargs: List[str],
               cls: Optional[str], seed: Optional[int], timeout: Optional[float],
               source: Optional[str] = None) -> Dict:
    """Run tracer.py from a scratch directory, so nothing beside it shadows the game's imports."""
    tester = os.path.abspath(tester)
    work = tempfile.mkdtemp(prefix="slotdebug-trace-")
    try:
        script = shutil.copy(TRACER, os.path.join(work, "tracer.py"))
        out = os.path.join(work, "trace.json")
        cmd = [python, script, "--tester", tester, "-n", str(n), "--out", out]
        cmd += [a for kw in kwargs for a in ("--kw", kw)]
        if source:
            cmd += ["--source", source]
        if cls:
            cmd += ["--class", cls]
        if seed is not None:
            cmd += ["--seed", str(seed)]
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([os.path.abspath(p) for p in paths]
                                            + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
        try:
            proc = subprocess.run(cmd, cwd=_run_dir(tester), env=env, capture_output=True,
                                  text=True, timeout=timeout)
        except FileNotFoundError:
            raise TraceError(f"no such interpreter: {python}") from None
        except subprocess.TimeoutExpired:
            raise TraceError(f"tracer did not finish within {timeout}s; use fewer spins (-n)") from None
        if not os.path.isfile(out):
            tail = "\n".join(proc.stderr.strip().splitlines()[-8:])
            raise TraceError(f"tracer produced no result (exit {proc.returncode}):\n{tail}")
        with open(out) as f:
            return json.load(f)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def merge(tester: str, trace: Dict, scan_from: Optional[str] = None) -> Dict:
    """One entry per call site: what the run saw, plus what the source says about the line.

    `scan_from` is the file whose source is read for the tester's sites (the copy that
    ran), when that is not `tester` itself.
    """
    tester = os.path.abspath(tester)
    base = os.path.dirname(_run_dir(trace.get("tester") or tester))
    scanned: Dict[str, List[Dict]] = {tester: _scan(scan_from or tester)}
    for site in trace.get("sites", []):
        scanned.setdefault(site["file"], _scan(site["file"]))

    # every site in the tester itself; sites in other files only when the run reached them
    entries = {_id(tester, s["line"], base): _entry(tester, s, base, "tester") for s in scanned[tester]}
    for t in trace.get("sites", []):
        s = _locate(scanned.get(t["file"], []), t["line"])
        site_id = _id(t["file"], s["line"] if s else t["line"], base)
        entry = entries.get(site_id) or _entry(t["file"], s, base, "tester" if t["file"] == tester else "inherited",
                                               line=t["line"], function=t["function"])
        entry["fired"] = True
        entry["hits"] += sum(t["keys"].values())
        for key, count in t["keys"].items():
            entry["keys_seen"][key] = entry["keys_seen"].get(key, 0) + count
        wanted = set(s["names"]) if s else None
        for name, values in t["values"].items():
            if wanted is None or name in wanted:
                entry["condition_values"].setdefault(name, [])
                entry["condition_values"][name] += [v for v in values if v not in entry["condition_values"][name]]
        entries[site_id] = entry

    ordered = sorted(entries.values(), key=lambda e: (e["source"] != "tester", e["file"], e["line"]))
    keys: Dict[str, List[str]] = {}
    for e in ordered:
        for key in e["keys_seen"]:
            keys.setdefault(key, []).append(e["id"])
    return {
        "tester": tester,
        "class": trace.get("class"),
        "python": trace.get("python"),
        "spins": {"requested": trace.get("requested"), "completed": trace.get("completed")},
        "error": trace.get("error"),
        "traceback": trace.get("traceback"),
        "kwargs": trace.get("kwargs"),
        "skipped_kwargs": trace.get("skipped_kwargs"),
        "wrapped": trace.get("wrapped"),
        "sites": ordered,
        "never_fired": [e["id"] for e in ordered if not e["fired"]],
        "keys": keys,
    }


def annotate_response_keys(doc: Dict, trace: Dict) -> None:
    """Mark each site's response key against what the engine actually returned."""
    seen, keys = trace.get("results_seen", 0), trace.get("response_keys", {})
    doc["results_seen"] = seen
    doc["response_keys"] = keys
    for site in doc["sites"]:
        key = site["response_key"]
        if not key or not seen:
            status = "unknown"
        elif key not in keys:
            status = "never_returned"
        elif keys[key]["empty"] == keys[key]["present"]:
            status = "always_empty"
        else:
            status = "present"
        site["response_key_status"] = status
    doc["missing_response_keys"] = [s["id"] for s in doc["sites"]
                                    if s["response_key_status"] in ("never_returned", "always_empty")]


def report_rows(report: str) -> List[str]:
    from report_parser.parser import ReaderFactory, RTPService
    parsed = RTPService(ReaderFactory.get_reader(report)).process(report)
    return [c["name"] for c in parsed["components"]]


def join_report(doc: Dict, rows: List[str]) -> Dict:
    """Each report row -> the sites that produce it."""
    matched: Dict[str, Dict] = {}
    for row in rows:
        traced = doc["keys"].get(row, [])
        if traced:
            matched[row] = {"match": "traced", "sites": traced}
            continue
        candidates = [e["id"] for e in doc["sites"] if e["pattern"] and fnmatch.fnmatchcase(row, e["pattern"])]
        matched[row] = {"match": "pattern" if candidates else None, "sites": candidates}
    return {
        "rows": matched,
        "unexplained_rows": [r for r, m in matched.items() if m["match"] is None],
        "not_reproduced_rows": [r for r, m in matched.items() if m["match"] == "pattern"],
        "keys_not_in_report": sorted(k for k in doc["keys"] if k not in set(rows)),
    }


# ---- helpers ----

def _entry(path: str, s: Optional[Dict], base: str, source: str,
           line: Optional[int] = None, function: Optional[str] = None) -> Dict:
    s = s or {}
    line = s.get("line", line)
    return {
        "id": _id(path, line, base),
        "file": path,
        "line": line,
        "function": s.get("function", function),
        "source": source,
        "key_expression": s.get("key_expression"),
        "pattern": s.get("pattern"),
        "value_expression": s.get("value_expression"),
        "response_key": s.get("response_key"),
        "conditions": s.get("conditions", []),
        "fired": False,
        "hits": 0,
        "keys_seen": {},
        "condition_values": {},
    }


def _scan(path: str) -> List[Dict]:
    try:
        return static.scan(path)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []  # not readable as source (a .pyc-only module, say): the trace still counts


def _locate(found: List[Dict], line: int) -> Optional[Dict]:
    """The static site a traced line belongs to; a call spanning lines reports any of them."""
    for s in found:
        if s["line"] <= line <= s.get("end_line", s["line"]):
            return s
    return None


def _id(path: str, line: int, base: str) -> str:
    return f"{_rel(path, base)}:{line}"


def _rel(path: str, base: str) -> str:
    rel = os.path.relpath(path, base)
    return path if rel.startswith("..") else rel


def sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _run_dir(tester: str) -> str:
    """The topmost package directory holding the tester (where `python -m` would be run from)."""
    top = os.path.dirname(os.path.abspath(tester))
    while os.path.isfile(os.path.join(os.path.dirname(top), "__init__.py")):
        top = os.path.dirname(top)
    return top if os.path.isfile(os.path.join(top, "__init__.py")) else os.path.dirname(os.path.abspath(tester))
