#!/usr/bin/env python3
"""Build a feature-rooted dependency graph from a Volume Tester and engine index.

This is a static-analysis prototype. It does not execute the game or prove that
candidate calls are reached at runtime.
"""
from __future__ import annotations

import argparse
import ast
import json
from collections import deque
from pathlib import Path
from typing import Any


def dotted_name(node: ast.AST) -> str:
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def literal_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class TesterVisitor(ast.NodeVisitor):
    def __init__(self, source_path: str):
        self.source_path = source_path
        self.methods: dict[str, dict[str, Any]] = {}
        self.class_stack: list[str] = []
        self.function_stack: list[str] = []
        self.branch_stack: list[dict[str, Any]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_stack.append(node.name)
        for child in node.body:
            self.visit(child)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        name_parts = [*self.class_stack, *self.function_stack, node.name]
        qname = ".".join(name_parts)
        record = {
            "id": f"tester:{qname}",
            "name": node.name,
            "qualified_name": qname,
            "kind": "method" if self.class_stack else "function",
            "file": self.source_path,
            "line_start": node.lineno,
            "line_end": getattr(node, "end_lineno", node.lineno),
            "parent": ".".join([*self.class_stack, *self.function_stack]) or None,
            "calls": [],
            "branches": [],
            "result_fields": [],
            "events": [],
            "assignments": [],
        }
        self.methods[qname] = record
        self.function_stack.append(node.name)
        for child in node.body:
            self.visit(child)
        self.function_stack.pop()

    def _current(self) -> dict[str, Any] | None:
        if not self.function_stack:
            return None
        qname = ".".join([*self.class_stack, *self.function_stack])
        return self.methods.get(qname)

    def visit_Call(self, node: ast.Call) -> None:
        rec = self._current()
        if rec:
            call_name = dotted_name(node.func)
            rec["calls"].append({"name": call_name, "line": node.lineno})
            if call_name.endswith("dump_event") and node.args:
                rec["events"].append({
                    "key": literal_string(node.args[0]),
                    "key_expression": ast.unparse(node.args[0]),
                    "value_expression": ast.unparse(node.args[1]) if len(node.args) > 1 else None,
                    "line": node.lineno,
                    "dynamic_key": literal_string(node.args[0]) is None,
                })
            # Capture p.result.get("field") / result.get("field").
            if (call_name.endswith(".get") and node.args
                    and literal_string(node.args[0]) is not None):
                receiver = ast.unparse(node.func.value) if isinstance(node.func, ast.Attribute) else ""
                if ".result" in receiver or receiver == "result":
                    rec["result_fields"].append({
                        "field": literal_string(node.args[0]),
                        "receiver": receiver,
                        "line": node.lineno,
                    })
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        rec = self._current()
        if rec:
            rec["branches"].append({
                "condition": ast.unparse(node.test),
                "line_start": node.lineno,
                "line_end": getattr(node, "end_lineno", node.lineno),
                "depth": len(self.branch_stack),
            })
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        rec = self._current()
        if rec:
            rec["assignments"].append({
                "targets": [ast.unparse(t) for t in node.targets],
                "value": ast.unparse(node.value),
                "line": node.lineno,
            })
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        rec = self._current()
        if rec and node.value is not None:
            rec["assignments"].append({
                "targets": [ast.unparse(node.target)],
                "value": ast.unparse(node.value),
                "line": node.lineno,
            })
        self.generic_visit(node)


def analyze_tester(path: Path) -> dict[str, Any]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    visitor = TesterVisitor(str(path))
    visitor.visit(tree)
    return {"source": str(path), "methods": visitor.methods}


def load_engine_index(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    nodes = data.get("nodes", {})
    edges = data.get("edges", [])
    by_qname = {n.get("qualified_name"): n for n in nodes.values()}
    outgoing: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        if edge.get("kind") == "calls":
            outgoing.setdefault(edge.get("source_id", ""), []).append(edge)
    return {"raw": data, "nodes": nodes, "by_qname": by_qname, "outgoing": outgoing}


def resolve_engine_root(engine: dict[str, Any], qname: str) -> dict[str, Any] | None:
    if qname in engine["by_qname"]:
        return engine["by_qname"][qname]
    # Permit a short name only when it uniquely identifies one indexed symbol.
    matches = [n for n in engine["nodes"].values()
               if n.get("qualified_name", "").endswith(qname)]
    return matches[0] if len(matches) == 1 else None


def traverse_engine(engine: dict[str, Any], root: dict[str, Any], max_depth: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    queue = deque([(root["id"], 0)])
    while queue:
        node_id, depth = queue.popleft()
        if node_id in seen or depth > max_depth:
            continue
        seen.add(node_id)
        node = engine["nodes"].get(node_id)
        if not node:
            continue
        result.append({
            "id": node["id"],
            "qualified_name": node.get("qualified_name"),
            "kind": node.get("kind"),
            "file": node.get("file"),
            "line_start": node.get("line_start"),
            "line_end": node.get("line_end"),
            "depth": depth,
        })
        for edge in engine["outgoing"].get(node_id, []):
            for target_id in edge.get("target_candidate_ids", []):
                queue.append((target_id, depth + 1))
    return result


def build_feature_tree(feature_name: str, feature: dict[str, Any], tester: dict[str, Any], engine: dict[str, Any], max_depth: int) -> dict[str, Any]:
    methods = tester["methods"]
    tester_roots: list[dict[str, Any]] = []
    for requested in feature.get("tester_roots", []):
        matches = [m for q, m in methods.items() if q == requested or q.endswith(requested)]
        if len(matches) == 1:
            tester_roots.append(matches[0])
        elif not matches:
            tester_roots.append({"requested": requested, "status": "unresolved"})
        else:
            tester_roots.append({"requested": requested, "status": "ambiguous", "matches": [m["qualified_name"] for m in matches]})

    relevant_events = set(feature.get("report_events", []))
    tester_nodes = []
    for method in methods.values():
        matched_events = [
            e for e in method["events"]
            if e.get("key") in relevant_events
            or any(event_key in (e.get("key_expression") or "") for event_key in relevant_events)
        ]
        if method in tester_roots or matched_events:
            tester_nodes.append({
                **method,
                "feature_events": matched_events,
                "selected_reason": "configured_tester_root" if method in tester_roots else "emits_configured_event",
            })

    engine_root_names = feature.get("engine_roots", [])
    engine_roots = []
    engine_nodes = []
    for qname in engine_root_names:
        root = resolve_engine_root(engine, qname)
        if not root:
            engine_roots.append({"requested": qname, "status": "unresolved_or_ambiguous"})
            continue
        engine_roots.append({"id": root["id"], "qualified_name": root.get("qualified_name"), "status": "resolved"})
        engine_nodes.extend(traverse_engine(engine, root, max_depth))

    # Include methods reachable FROM configured roots via syntactic method-call candidates.
    by_short_name: dict[str, list[dict[str, Any]]] = {}
    for method in methods.values():
        by_short_name.setdefault(method["name"], []).append(method)
    selected_qnames = {m["qualified_name"] for m in tester_nodes}
    queue = deque(m for m in tester_roots if "qualified_name" in m)
    traversed: set[str] = set()
    while queue:
        current = queue.popleft()
        qname = current["qualified_name"]
        if qname in traversed:
            continue
        traversed.add(qname)
        for call in current.get("calls", []):
            short_name = call["name"].split(".")[-1]
            candidates = by_short_name.get(short_name, [])
            # Include all same-name candidates, but preserve ambiguity as a static limitation.
            for candidate in candidates:
                candidate_qname = candidate["qualified_name"]
                if candidate_qname not in selected_qnames:
                    tester_nodes.append({**candidate, "selected_reason": "reachable_call_candidate"})
                    selected_qnames.add(candidate_qname)
                if candidate_qname not in traversed:
                    queue.append(candidate)

    return {
        "schema_version": 1,
        "feature": feature_name,
        "description": feature.get("description", ""),
        "roots": {"tester": tester_roots, "engine": engine_roots},
        "tester_nodes": tester_nodes,
        "engine_nodes": engine_nodes,
        "report_events": sorted(relevant_events),
        "limitations": [
            "Static call relationships are candidates, not runtime proof.",
            "Dynamic dump_event keys are recorded as expressions and need manual or runtime resolution.",
            "Report aggregation formulas are not inferred by this prototype.",
            "Only methods selected by configured roots, event emission, or simple call-name matching are included.",
        ],
    }


def render_markdown(tree: dict[str, Any]) -> str:
    lines = [f"# Feature dependency tree: {tree['feature']}", ""]
    if tree.get("description"):
        lines += [tree["description"], ""]
    lines += ["## Roots", ""]
    for kind in ("tester", "engine"):
        lines.append(f"### {kind.title()}")
        for root in tree["roots"][kind]:
            lines.append(f"- `{root.get('qualified_name', root.get('requested', 'unknown'))}` — {root.get('status', 'configured')}")
        lines.append("")
    lines += ["## Volume Tester nodes", ""]
    for node in sorted(tree["tester_nodes"], key=lambda n: (n.get("line_start", 0), n.get("qualified_name", ""))):
        lines.append(f"- **`{node['qualified_name']}`** — `{node['file']}:{node['line_start']}-{node['line_end']}` ({node.get('selected_reason', 'selected')})")
        for branch in node.get("branches", []):
            lines.append(f"  - Branch L{branch['line_start']}: `{branch['condition']}`")
        for field in node.get("result_fields", []):
            lines.append(f"  - Result field L{field['line']}: `{field['field']}`")
        for event in node.get("events", []):
            lines.append(f"  - Event L{event['line']}: `{event.get('key') or event['key_expression']}`")
    lines += ["", "## Engine nodes", ""]
    for node in sorted(tree["engine_nodes"], key=lambda n: (n.get("depth", 0), n.get("qualified_name") or "")):
        lines.append(f"- {'  ' * node['depth']}`{node['qualified_name']}` — `{node['file']}:{node['line_start']}-{node['line_end']}`")
    lines += ["", "## Limitations", ""]
    lines += [f"- {item}" for item in tree["limitations"]]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tester", required=True, type=Path, help="Path to actual Volume Tester .py source")
    parser.add_argument("--engine-index", required=True, type=Path, help="Hierarchical engine index JSON")
    parser.add_argument("--features", required=True, type=Path, help="Feature manifest JSON")
    parser.add_argument("--feature", required=True, help="Feature key from manifest")
    parser.add_argument("--out", required=True, type=Path, help="Output JSON path")
    parser.add_argument("--markdown", type=Path, help="Optional Markdown output path")
    parser.add_argument("--max-depth", type=int, default=4)
    args = parser.parse_args()

    manifest = json.loads(args.features.read_text(encoding="utf-8"))
    if args.feature not in manifest.get("features", {}):
        parser.error(f"Feature {args.feature!r} not found in manifest")
    tester = analyze_tester(args.tester)
    engine = load_engine_index(args.engine_index)
    result = build_feature_tree(args.feature, manifest["features"][args.feature], tester, engine, args.max_depth)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_markdown(result), encoding="utf-8")
    print(f"Wrote {args.out}")
    if args.markdown:
        print(f"Wrote {args.markdown}")


if __name__ == "__main__":
    main()
