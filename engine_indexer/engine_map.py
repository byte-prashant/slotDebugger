"""Response key -> the engine code that computes it, joined to report key -> tester line.

    doc = build("engine.py", keys=["current_winnings"], paths=["/path/to/OGA-5.0.2"])
    doc = build("engine.py", tester_map=json.load(open("r.tester_map.json")), paths=[...])
    print(render(doc, "current_winnings"))

For each response key, every place the engine (its own classes and everything they
inherit from OGLE/OGA) builds a dict with that key is a root:
`{'current_winnings': current_winnings}`, `gamestate['current_winnings'] = x`,
`d.update(current_winnings=x)`. An engine that returns several responses (base game,
free spins) has one root per response that carries the key, each with its own tree.
Roots are found by reading the code: a dict that carries the key but is not part of a
response is a root too, so read each root's `function`.

From each root, `slicer.Graph` walks back through assignments, calls, parameters,
attributes and constants (see slicer.py). With a tester map, `chain` joins the two
maps: report row -> tester line -> response key -> engine roots.
"""

import ast
import os
from typing import Dict, Iterable, List, Optional

from engine_indexer.code_index import CodeIndex, default_roots
from engine_indexer.slicer import Context, Graph, _short, literal_overwrites


def build(engine: str, keys: Optional[Iterable[str]] = None, paths: Optional[List[str]] = None,
          tester_map: Optional[Dict] = None, max_depth: int = 6, max_nodes: int = 5000) -> Dict:
    engine = os.path.abspath(engine)
    keys = list(dict.fromkeys(list(keys or []) + _keys_from(tester_map)))
    index = CodeIndex(default_roots(engine, paths or []))
    module = index.load(engine)
    if module is None:
        raise ValueError(f"cannot read engine {engine}: {index.parse_errors[-1]['error']}")
    graph = Graph(index, max_depth=max_depth, max_nodes=max_nodes)

    contexts = _contexts(index, module)
    found: Dict[str, List[str]] = {}
    for key in keys:
        roots = []
        for ctx in contexts:
            for line, value, how in _stores(ctx.function, key):
                root = graph._node("response_key", f"{how}: {_short(value)}", ctx, line, key=f"root:{key}:{line}")
                if graph._expand(root):
                    for child in graph.expression(value, ctx, value.lineno):
                        graph._link(root, child)
                if root not in roots:
                    roots.append(root)
        found[key] = roots

    doc = {
        "engine": engine,
        "roots": index.roots,
        "classes": {c.qualname: [k.qualname for k in index.mro(c)] for c in module.classes.values()},
        "unresolved_bases": index.unresolved,
        "parse_errors": index.parse_errors,
        "max_depth": max_depth,
        "truncated": graph.truncated,
        "keys": {k: {"roots": r, "status": "found" if r else "not_found"} for k, r in found.items()},
        "nodes": {i: dict(n, file=_rel(n["file"], index.roots)) for i, n in graph.nodes.items()},
        "paths": [os.path.abspath(p) for p in (paths or [])],  # to rebuild the same map later
        "files": sorted(index.modules),  # every file read: an edit to one of them makes this map stale
        "methods": _methods(index, module, engine),
        "detections": _detections(graph, contexts, index.roots),
    }
    if tester_map:
        doc["chain"] = chain(tester_map, doc)
    return doc


def chain(tester_map: Dict, doc: Dict) -> Dict:
    """Report row -> tester lines -> response key -> engine roots (and the reverse gaps)."""
    sites = {}
    for s in tester_map.get("sites", []):
        key = s.get("response_key")
        sites[s["id"]] = {
            "keys_seen": s.get("keys_seen", {}),
            "fired": s.get("fired"),
            "response_key": key,
            "response_key_status": s.get("response_key_status"),
            "engine_roots": doc["keys"].get(key, {}).get("roots", []) if key else [],
        }
    rows = {}
    for row, match in ((tester_map.get("report") or {}).get("rows") or {}).items():
        rows[row] = {"match": match["match"], "sites": match["sites"],
                     "response_keys": sorted({sites[s]["response_key"] for s in match["sites"]
                                              if s in sites and sites[s]["response_key"]})}
    return {
        "sites": sites,
        "rows": rows,
        "keys_without_engine_root": sorted(k for k, v in doc["keys"].items() if not v["roots"]),
    }


def render(doc: Dict, key: str, depth: int = 8) -> str:
    """The tree under a response key as indented text; a node met twice is shown once."""
    nodes, seen = doc["nodes"], set()
    entry = doc["keys"].get(key)
    if entry is None:
        return f"{key}: not indexed (pass --key {key})"
    if not entry["roots"]:
        return f"{key}: no dict in the engine or its base classes carries this key"
    lines = [f"{key}  ({len(entry['roots'])} place(s) the engine puts it in a dict)"]

    def visit(node_id, level):
        node = nodes[node_id]
        where = f"{node['file']}:{node['line']}" + (f" {node['function']}" if node.get("function") else "")
        extra = []
        if node.get("conditions"):
            extra.append("when " + " and ".join(c["expression"] for c in node["conditions"]))
        if node.get("target"):
            extra.append("-> " + node["target"])
        if node.get("note"):
            extra.append(node["note"])
        tail = f"  [{'; '.join(extra)}]" if extra else ""
        repeat = node_id in seen
        lines.append(f"{'  ' * level}{node['kind']}: {node['label']}  ({where}){tail}{'  (see above)' if repeat else ''}")
        if repeat:
            return
        seen.add(node_id)
        if level >= depth:
            if node["children"]:
                lines.append(f"{'  ' * (level + 1)}... {len(node['children'])} more")
            return
        for child in node["children"]:
            visit(child, level + 1)

    for root in entry["roots"]:
        visit(root, 1)
    return "\n".join(lines)


# ---- helpers ----

def _methods(index: CodeIndex, module, engine: str) -> Dict[str, Dict]:
    """Every method the engine module's classes can run: where it is defined and its parameters."""
    game_dir = os.path.dirname(engine) + os.sep
    methods: Dict[str, Dict] = {}
    for cls in module.classes.values():
        for klass in index.mro(cls):
            for name, fn in klass.methods.items():
                qual = f"{klass.qualname}.{name}"
                if qual in methods:
                    continue
                params = [a.arg for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs]
                methods[qual] = {
                    "name": name, "class": klass.qualname, "file": _rel(klass.module.path, index.roots),
                    "line": fn.lineno, "end_line": getattr(fn, "end_lineno", fn.lineno),
                    "params": [p for p in params if p not in ("self", "cls")],
                    "origin": "game" if klass.module.path.startswith(game_dir) else "framework",
                }
    return methods


def _detections(graph: Graph, contexts: List[Context], roots: List[str]) -> List[Dict]:
    """Suspicious assignments in the functions the dependency walk passed through."""
    visited = {(n["file"], n["function"]) for n in graph.nodes.values() if n.get("function")}
    found = []
    for ctx in contexts:
        if (ctx.module.path, ctx.label) not in visited:
            continue
        for d in literal_overwrites(ctx.function, graph.facts(ctx.function), ctx.module.source):
            found.append(dict(d, file=_rel(ctx.module.path, roots), function=ctx.label))
    return sorted(found, key=lambda d: (d["file"], d["line"]))


def _keys_from(tester_map: Optional[Dict]) -> List[str]:
    if not tester_map:
        return []
    return [s["response_key"] for s in tester_map.get("sites", []) if s.get("response_key")]


def _contexts(index: CodeIndex, module) -> List[Context]:
    """Every function the engine module's classes can run (their whole MRO) and its own functions."""
    seen, contexts = set(), []
    for cls in module.classes.values():
        for klass in index.mro(cls):
            for method in klass.methods.values():
                if id(method) not in seen:
                    seen.add(id(method))
                    contexts.append(Context(klass.module, cls, klass, method))
    for function in module.functions.values():
        contexts.append(Context(module, None, None, function))
    return contexts


def _stores(function: ast.AST, key: str):
    """(line, value, how) for each place `function` puts `key` into a dict."""
    for node in _own_nodes(function):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == key:
                    yield k.lineno, v, f"'{key}'"
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant)
                        and target.slice.value == key):
                    yield node.lineno, node.value, f"{ast.unparse(target)} ="
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", None))
            if name in ("update", "dict"):
                for kw in node.keywords:
                    if kw.arg == key:
                        yield kw.value.lineno, kw.value, f"{name}({key}=...)"


def _own_nodes(function: ast.AST):
    """Nodes of `function`, not of functions or classes nested in it."""
    stack = list(function.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _rel(path: str, roots: List[str]) -> str:
    for root in roots:
        if path.startswith(root.rstrip(os.sep) + os.sep):
            return os.path.relpath(path, root)
    return path
