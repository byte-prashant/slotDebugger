"""Read the three maps back as answers: trees, tables, problems, and Markdown summaries.

    maps = Maps.load(tester="r.tester_map.json", engine="r.engine_map.json", joined="r.report_map.json")
    print(tree(maps, row="action_bonus_win"))
    print(method(maps, "check_winnings_cap"))
    for p in problems(maps): ...
    write_summaries(maps, "analysis/r")   # r.summary.md, r.tester_summary.md, r.engine_summary.md

Everything here only reads the maps; nothing is re-run or re-parsed. Each view states
where its facts come from (a run, or reading the code), so they can be cited as evidence.
"""

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from engine_indexer.engine_map import render

SEVERITY = {"high": 0, "medium": 1, "low": 2}


@dataclass
class Maps:
    tester: Optional[Dict] = None
    engine: Optional[Dict] = None
    joined: Optional[Dict] = None

    @classmethod
    def load(cls, tester: Optional[str] = None, engine: Optional[str] = None,
             joined: Optional[str] = None) -> "Maps":
        def read(path):
            if path and os.path.isfile(path):
                with open(path) as f:
                    return json.load(f)
            return None
        return cls(read(tester), read(engine), read(joined))

    def need(self, *parts: str) -> None:
        missing = [p for p in parts if getattr(self, p) is None]
        if missing:
            raise ValueError(f"no {' / '.join(m + ' map' for m in missing)}: run `slotdebug trace-tester` first")


# ---- the chain ----

def tree(maps: Maps, row: Optional[str] = None, key: Optional[str] = None, site: Optional[str] = None,
         depth: int = 6) -> str:
    """The full chain for a report row, a response key or a tester line."""
    maps.need("tester", "engine")
    sites = {s["id"]: s for s in maps.tester["sites"]}
    lines: List[str] = []
    if row is not None:
        match = ((maps.tester.get("report") or {}).get("rows") or {}).get(row)
        if match is None:
            raise ValueError(f"no report row {row!r} in the tester map (rows come from --file)")
        lines.append(f"REPORT ROW  {row}  [{match['match'] or 'no tester line produces it'}]")
        chosen = match["sites"]
    elif site is not None:
        chosen = [s for s in sites if s == site or s.endswith(site)]
        if not chosen:
            raise ValueError(f"no tester line {site!r}; `slotdebug map sites` lists them")
    elif key is not None:
        chosen = [s for s, v in sites.items() if v.get("response_key") == key]
        if not chosen:
            return "\n".join(_engine_tree(maps, key, depth, "  "))
    else:
        raise ValueError("give --row, --key or --site")
    for site_id in chosen:
        s = sites[site_id]
        cond = " and ".join(c["expression"] for c in s["conditions"]) or "always"
        lines.append(f"  TESTER    {site_id}  {s['function']}  "
                     f"{'fired ' + str(s['hits']) + 'x' if s['fired'] else 'never fired'}")
        lines.append(f"            emits {s['key_expression']}  when {cond}")
        if s["keys_seen"]:
            lines.append(f"            keys seen: {_counts(s['keys_seen'])}")
        if s.get("response_key"):
            lines.append(f"  RESPONSE  {s['response_key']}  [{s.get('response_key_status', 'unknown')}]  "
                         f"value: {s['value_expression']}")
            lines += _engine_tree(maps, s["response_key"], depth, "  ")
        else:
            lines.append(f"  RESPONSE  none: the value is `{s['value_expression']}`")
    return "\n".join(lines)


def _engine_tree(maps: Maps, key: str, depth: int, indent: str) -> List[str]:
    return [f"{indent}ENGINE {l}" for l in render(maps.engine, key, depth).splitlines()]


# ---- tables ----

def rows(maps: Maps) -> str:
    maps.need("tester")
    report = maps.tester.get("report")
    if not report:
        return "no report rows: run `slotdebug trace-tester --file <report>`"
    if report.get("warning"):
        return report["warning"]
    keys = (maps.engine or {}).get("keys", {})
    sites = {s["id"]: s for s in maps.tester["sites"]}
    out = ["| row | match | tester line(s) | response key | returned | engine places |", "|---|---|---|---|---|---|"]
    for row, m in report["rows"].items():
        response = sorted({sites[s]["response_key"] for s in m["sites"] if sites[s].get("response_key")})
        status = sorted({sites[s].get("response_key_status", "") for s in m["sites"] if sites[s].get("response_key")})
        places = [str(len(keys.get(k, {}).get("roots", []))) for k in response]
        out.append(f"| {row} | {m['match'] or 'unexplained'} | {', '.join(_short_site(s) for s in m['sites']) or '-'} "
                   f"| {', '.join(response) or '-'} | {', '.join(status) or '-'} | {', '.join(places) or '-'} |")
    return "\n".join(out)


def keys(maps: Maps) -> str:
    maps.need("tester", "engine")
    returned = maps.tester.get("response_keys", {})
    seen = maps.tester.get("results_seen", 0)
    users: Dict[str, List[str]] = {}
    for s in maps.tester["sites"]:
        if s.get("response_key"):
            users.setdefault(s["response_key"], []).append(_short_site(s["id"]))
    out = [f"engine results seen in the run: {seen}", "",
           "| response key | returned (with value / empty) | engine places | read by tester line(s) |",
           "|---|---|---|---|"]
    for key, entry in maps.engine["keys"].items():
        r = returned.get(key)
        got = f"{r['present'] - r['empty']} / {r['empty']}" if r else "never"
        places = ", ".join(_where(maps.engine["nodes"][n]) for n in entry["roots"]) or "none"
        out.append(f"| {key} | {got} | {places} | {', '.join(users.get(key, [])) or '-'} |")
    return "\n".join(out)


def sites(maps: Maps) -> str:
    maps.need("tester")
    out = ["| tester line | function | fired | keys seen | response key | returned | conditions |",
           "|---|---|---|---|---|---|---|"]
    for s in maps.tester["sites"]:
        cond = " and ".join(c["expression"] for c in s["conditions"]) or "-"
        out.append(f"| {_short_site(s['id'])} | {s['function']} | {s['hits'] if s['fired'] else 'never'} "
                   f"| {_counts(s['keys_seen']) or s['pattern']} | {s.get('response_key') or '-'} "
                   f"| {s.get('response_key_status', '-')} | {cond} |")
    return "\n".join(out)


# ---- engine implementation ----

def method(maps: Maps, name: str) -> str:
    """Where a method is defined, which engine class uses which definition, its parameters,
    who calls it on the traced path and what its result is computed from."""
    maps.need("engine")
    eng = maps.engine
    matches = {q: m for q, m in eng["methods"].items() if m["name"] == name or q.endswith("." + name)}
    if not matches:
        return f"no method {name!r} in the engine's classes or the framework classes they inherit"
    out = []
    for qual, m in matches.items():
        cls = m["class"].rsplit(".", 1)[-1]
        label = f"{cls}.{m['name']}"
        out.append(f"{qual}  ({m['origin']})  {m['file']}:{m['line']}-{m['end_line']}")
        for engine_cls, mro in eng["classes"].items():
            owner = next((c for c in mro if f"{c}.{m['name']}" in eng["methods"]), None)
            if owner == m["class"]:
                out.append(f"  used by {engine_cls}")
            elif owner and m["class"] in mro:
                out.append(f"  overridden for {engine_cls} by {owner}")
        reached = {n["label"] for n in eng["nodes"].values() if n["kind"] == "param" and n["function"] == label}
        calls = [n for n in eng["nodes"].values() if n["kind"] in ("call", "truncated")
                 and (n.get("target") or "").startswith(label + " ")]
        out.append(f"  parameters: {', '.join(m['params']) or 'none'}")
        if calls:
            unused = [p for p in m["params"] if p not in reached]
            out.append(f"  parameters the traced result depends on: {', '.join(sorted(reached)) or 'none'}")
            if unused:
                out.append(f"  parameters that do not reach the result: {', '.join(unused)}")
            out.append("  called on the traced path from: " + ", ".join(sorted({_where(c) for c in calls})))
            returns = next((i for i, n in eng["nodes"].items() if n["kind"] == "returns" and n["function"] == label), None)
            if returns:
                out.append("  result computed from:")
                out += ["    " + l for l in _subtree(eng, returns, 3)]
        else:
            out.append("  not on the traced path of any indexed response key")
    return "\n".join(out)


def classes(maps: Maps, name: Optional[str] = None) -> str:
    maps.need("engine")
    eng = maps.engine
    out = []
    if name is None:
        for cls, mro in eng["classes"].items():
            out.append(f"{cls}")
            for i, c in enumerate(mro):
                count = sum(1 for m in eng["methods"].values() if m["class"] == c)
                origin = next((m["origin"] for m in eng["methods"].values() if m["class"] == c), "?")
                out.append(f"  {i}. {c}  ({origin}, {count} methods)")
        for base in eng.get("unresolved_bases", []):
            out.append(f"  not found: base {base['base']} of {base['class']} (pass the framework with --path)")
        return "\n".join(out)
    mro = next((m for c, m in eng["classes"].items() if c == name or c.endswith("." + name)), None)
    if mro is None:
        return f"no engine class {name!r}; `slotdebug map class` lists them"
    names = sorted({m["name"] for m in eng["methods"].values() if m["class"] in mro})
    out.append(f"methods {name} can call, and the class each comes from (first in its MRO):")
    for n in names:
        defining = [c for c in mro if f"{c}.{n}" in eng["methods"]]
        m = eng["methods"][f"{defining[0]}.{n}"]
        hidden = f"  (overrides {', '.join(defining[1:])})" if len(defining) > 1 else ""
        out.append(f"  {n:34} {defining[0]}  {m['file']}:{m['line']}{hidden}")
    return "\n".join(out)


def find(maps: Maps, text: str, limit: int = 40) -> str:
    maps.need("engine")
    needle = text.lower()
    hits = [n for n in maps.engine["nodes"].values() if needle in n["label"].lower()]
    out = [f"{len(hits)} node(s) on the traced paths mention {text!r}" + (f"; first {limit}" if len(hits) > limit else "")]
    if not hits:
        out.append("  (only code the response keys depend on is in the map; `map method` covers any method)")
    for n in sorted(hits, key=lambda n: (n["file"], n["line"]))[:limit]:
        cond = f"  when {' and '.join(c['expression'] for c in n['conditions'])}" if n.get("conditions") else ""
        out.append(f"  {n['kind']:10} {n['label']}  ({_where(n)}){cond}")
    return "\n".join(out)


# ---- problems ----

def problems(maps: Maps) -> List[Dict]:
    """Everything suspicious in the maps, most severe first, each with where to look."""
    found: List[Dict] = []

    def add(severity, kind, where, message, source):
        found.append({"severity": severity, "kind": kind, "where": where, "message": message, "source": source})

    t, e = maps.tester, maps.engine
    if t:
        if t.get("error"):
            add("high", "tester_stopped", t.get("ran"), f"the tester stopped after {t['spins']['completed']} "
                f"spins: {t['error']}; everything else covers only those spins", "run")
        for s in t["sites"]:
            status = s.get("response_key_status")
            if status == "never_returned":
                add("high", "response_key_never_returned", s["id"],
                    f"reads `{s['response_key']}`, which the engine never returned in "
                    f"{t.get('results_seen')} results", "run")
            elif status == "always_empty":
                add("high", "response_key_always_empty", s["id"],
                    f"reads `{s['response_key']}`, which the engine always returned empty", "run")
            if not s["fired"] and status not in ("never_returned", "always_empty"):
                add("medium", "never_fired", s["id"], f"emits `{s['key_expression']}` but was not reached in "
                    f"{t['spins']['completed']} spins (rare branch, or one that cannot run)", "run")
        report = t.get("report") or {}
        if report.get("warning"):
            add("high", "report_unreadable", report.get("file"), report["warning"], "report")
        for row in report.get("unexplained_rows", []):
            add("high", "row_unexplained", row, "no tester line produces this report row: the report "
                "may come from another tester version", "run+code")
        for row in report.get("not_reproduced_rows", []):
            add("low", "row_not_reproduced", row, "only a tester line's key pattern matches; this run "
                "did not produce it (more spins?)", "run+code")
        if t.get("skipped_kwargs"):
            add("low", "tester_defaults", t.get("ran"), "tester arguments left at their defaults: "
                + ", ".join(t["skipped_kwargs"]) + " (pass --kw)", "run")
    if e:
        for d in e.get("detections", []):
            add("high", d["kind"], f"{d['file']}:{d['line']}", f"{d['function']}: {d['message']}", "code")
        for key, entry in e["keys"].items():
            if not entry["roots"]:
                add("high", "key_not_in_engine", key, f"no dict in the engine or its base classes carries "
                    f"`{key}`", "code")
        for base in e.get("unresolved_bases", []):
            add("medium", "base_not_found", f"{base['file']}:{base['line']}",
                f"base class {base['base']} of {base['class']} not found: inherited code is missing from "
                f"the map (pass --path)", "code")
        for qual, m in e.get("methods", {}).items():
            label = f"{m['class'].rsplit('.', 1)[-1]}.{m['name']}"
            called = any(n["kind"] == "call" and (n.get("target") or "").startswith(label + " ")
                         for n in e["nodes"].values())
            if not called:
                continue
            reached = {n["label"] for n in e["nodes"].values() if n["kind"] == "param" and n["function"] == label}
            unused = [p for p in m["params"] if p not in reached and not p.startswith("*")]
            if unused and reached:
                add("medium", "parameter_unused", f"{m['file']}:{m['line']}",
                    f"{label}: {', '.join(unused)} {'does' if len(unused) == 1 else 'do'} not reach its result "
                    f"(passed in, then ignored?)", "code")
        if e.get("truncated"):
            add("low", "map_truncated", e["engine"], "node limit reached; the engine map is incomplete", "code")
        unresolved = [n for n in e["nodes"].values() if n["kind"] == "unresolved"]
        if unresolved:
            add("low", "unresolved", ", ".join(sorted({_where(n) for n in unresolved})[:5]),
                f"{len(unresolved)} name(s)/call(s) could not be followed", "code")
    return sorted(found, key=lambda p: SEVERITY[p["severity"]])


def problems_text(maps: Maps) -> str:
    found = problems(maps)
    if not found:
        return "no problems found in the maps"
    return "\n".join(f"[{p['severity']}] {p['kind']}  {p['where']}\n    {p['message']}  (from: {p['source']})"
                     for p in found)


# ---- Markdown summaries ----

def write_summaries(maps: Maps, stem: str) -> List[str]:
    """`<stem>.summary.md`, `<stem>.tester_summary.md`, `<stem>.engine_summary.md`."""
    written = []
    for suffix, text in (("summary", summary_md(maps)), ("tester_summary", tester_md(maps)),
                         ("engine_summary", engine_md(maps))):
        if text is None:
            continue
        path = f"{stem}.{suffix}.md"
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        written.append(path)
    return written


def summary_md(maps: Maps) -> str:
    t, e = maps.tester or {}, maps.engine or {}
    found = problems(maps)
    lines = ["# Debug summary", ""]
    if t:
        report = t.get("report") or {}
        fired = sum(1 for s in t["sites"] if s["fired"])
        lines += [f"- **Tester**: `{t.get('labelled_as')}` ran {t['spins']['completed']}/{t['spins']['requested']} "
                  f"spins (Python {t.get('python')}); {fired} of {len(t['sites'])} event lines fired"]
        if report.get("rows"):
            counts = {m: sum(1 for r in report["rows"].values() if r["match"] == m) for m in ("traced", "pattern")}
            lines.append(f"- **Report**: {len(report['rows'])} rows; {counts['traced']} traced, {counts['pattern']} "
                         f"by pattern only, {len(report.get('unexplained_rows', []))} unexplained")
    if e:
        found_keys = sum(1 for v in e["keys"].values() if v["roots"])
        lines.append(f"- **Engine**: `{os.path.basename(e['engine'])}`; {found_keys}/{len(e['keys'])} response keys "
                     f"traced to engine code; {len(e['nodes'])} nodes; {len(e.get('methods', {}))} methods in reach")
    by = {s: [p for p in found if p["severity"] == s] for s in SEVERITY}
    lines += ["", f"## Problems ({len(by['high'])} high, {len(by['medium'])} medium, {len(by['low'])} low)", ""]
    if not found:
        lines.append("None found in the maps.")
    for p in found:
        lines.append(f"- **{p['severity']}** `{p['kind']}` at `{p['where']}`: {p['message']} _(from {p['source']})_")
    lines += ["", "## Look closer", "",
              "- `slotdebug map tree --row <row>` (or `--key`, `--site`): report row → tester line → response key → engine code",
              "- `slotdebug map method --name <name>`: where a method is defined, its unused parameters, what its result is computed from",
              "- `slotdebug map keys` / `rows` / `sites`: the tables behind this summary",
              "- `slotdebug map problems`: this list as plain text, to cite with `think --evidence`", ""]
    return "\n".join(lines)


def tester_md(maps: Maps) -> Optional[str]:
    if not maps.tester:
        return None
    t = maps.tester
    lines = ["# Report → volume tester", "",
             f"Run: `{t.get('ran')}` ({t['spins']['completed']} spins). Lines are labelled as "
             f"`{t.get('labelled_as')}`.", "", "## Report rows", "", rows(maps), "", "## Tester event lines", "",
             sites(maps), ""]
    if maps.engine:
        lines += ["## Response keys", "", keys(maps), ""]
    return "\n".join(lines)


def engine_md(maps: Maps) -> Optional[str]:
    if not maps.engine:
        return None
    e = maps.engine
    lines = ["# Engine implementation", "", f"Engine: `{e['engine']}` (read, not run).", "",
             "## Class hierarchy", "", "```", classes(maps), "```", "", "## How each response key is computed", ""]
    for key, entry in e["keys"].items():
        lines += [f"### `{key}`", "", "```", render(e, key, depth=4), "```", ""]
    called = sorted({(n.get("target") or "").split(" (")[0] for n in e["nodes"].values()
                     if n["kind"] in ("call", "truncated") and n.get("target")})
    if called:
        lines += ["## Methods on the traced path", "", "| method | defined in | origin | lines | parameters not reaching the result |",
                  "|---|---|---|---|---|"]
        for label in called:
            cls, name = label.rsplit(".", 1) if "." in label else ("", label)
            m = next((m for q, m in e["methods"].items() if m["name"] == name and m["class"].endswith(cls)), None)
            if m is None:
                continue
            reached = {n["label"] for n in e["nodes"].values() if n["kind"] == "param" and n["function"] == label}
            unused = [p for p in m["params"] if p not in reached] if reached else []
            lines.append(f"| {label} | {m['class']} | {m['origin']} | {m['file']}:{m['line']}-{m['end_line']} "
                         f"| {', '.join(unused) or '-'} |")
        lines.append("")
    if e.get("detections"):
        lines += ["## Suspicious assignments", ""]
        lines += [f"- `{d['file']}:{d['line']}` {d['function']}: {d['message']}" for d in e["detections"]]
        lines.append("")
    return "\n".join(lines)


# ---- helpers ----

def _subtree(eng: Dict, node_id: str, depth: int, level: int = 0, seen=None) -> List[str]:
    seen = set() if seen is None else seen
    node = eng["nodes"][node_id]
    out = [f"{'  ' * level}{node['kind']}: {node['label']}  ({_where(node)})" + ("  (see above)" if node_id in seen else "")]
    if node_id in seen or level >= depth:
        return out
    seen.add(node_id)
    for child in node["children"]:
        out += _subtree(eng, child, depth, level + 1, seen)
    return out


def _where(node: Dict) -> str:
    return f"{node['file']}:{node['line']}" + (f" {node['function']}" if node.get("function") else "")


def _short_site(site_id: str) -> str:
    parts = site_id.split("/")
    return "/".join(parts[-2:]) if len(parts) > 2 else site_id


def _counts(counts: Dict[str, int], limit: int = 4) -> str:
    items = sorted(counts.items(), key=lambda kv: -kv[1])
    text = ", ".join(f"{k} ×{v}" for k, v in items[:limit])
    return text + (f", +{len(items) - limit} more" if len(items) > limit else "")
