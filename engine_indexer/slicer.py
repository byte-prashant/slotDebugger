"""Work backward from a value in the engine to everything it is computed from.

    graph = Graph(index, max_depth=6)
    root = graph.expression(value_node, Context(module, cls, owner, function), line)

Inside a function, a variable at line L depends on the assignments to it that can reach
L: those above it in the function, and those later in a loop that encloses L. Each is a
node carrying the `if`/`for` chain it sits in. From the assigned expression the walk
continues through:

- `self.method(...)`  resolved through the concrete class's MRO (game -> OGLE -> OGA);
                      the callee's `return` values are followed, and a parameter they
                      reach is linked to the argument passed at this call site, so an
                      argument that does not reach the result has no edge (a parameter
                      the computation ignores);
- `self.attr`         every `self.attr = ...` in the class hierarchy;
- `defs.X`, `CONST`   the module-level value;
- `f(...)`            a module function, followed like a method;
- builtins / other    a node whose children are the arguments: the value depends on them.

Nothing is executed. What cannot be followed statically (a method not found in the
MRO, `getattr`, a receiver of unknown type) becomes an `unresolved`/`external` node,
never a guess. Calls deeper than `max_depth` become `truncated`.
"""

import ast
import hashlib
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from engine_indexer.code_index import ClassInfo, CodeIndex, Module

BUILTINS = {"sum", "float", "int", "len", "max", "min", "abs", "round", "list", "dict", "set", "tuple",
            "sorted", "str", "bool", "Decimal", "enumerate", "zip", "range", "any", "all", "map",
            "filter", "reversed", "deepcopy", "copy", "isinstance", "getattr", "Money"}
MAX_ATTRIBUTE_SITES = 8
# `x.append(v)` and friends change x in place: a dependency of x, like `x += v`
MUTATORS = {"append", "extend", "insert", "update", "add", "setdefault", "appendleft"}


@dataclass(frozen=True)
class Context:
    module: Module
    cls: Optional[ClassInfo]       # the concrete class: `self.m()` dispatches through its MRO
    owner: Optional[ClassInfo]     # the class that defines `function`
    function: Optional[ast.AST]    # None at module level

    @property
    def label(self) -> Optional[str]:
        if self.function is None:
            return None
        return f"{self.owner.name}.{self.function.name}" if self.owner else self.function.name


@dataclass
class Def:
    name: str
    line: int
    kind: str                # assign | augassign | mutate | unpack | loop | with | param
    value: Optional[ast.AST]
    op: str
    conditions: List[Dict]
    loops: List[tuple]       # (first, last) lines of each enclosing loop
    via: Optional[str] = None  # a mutate through a loop variable: the item it changed


class FunctionFacts:
    """Assignments, returns and `self.x =` stores of one function, with their conditions."""

    def __init__(self, function: ast.AST):
        self.defs: Dict[str, List[Def]] = {}
        self.returns: List[Def] = []
        self.self_stores: Dict[str, List[Def]] = {}
        self.params = [a.arg for a in function.args.posonlyargs + function.args.args + function.args.kwonlyargs]
        for extra in (function.args.vararg, function.args.kwarg):
            if extra is not None:
                self.params.append(extra.arg)
        self._alias: Dict[str, str] = {}  # loop variable -> the collection it iterates
        self._walk(function.body, [], [])

    def _walk(self, body, conditions, loops):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue  # nested definitions have their own scope
            if isinstance(node, ast.If):
                test = ast.unparse(node.test)
                self._walk(node.body, conditions + [_cond("if", test, node)], loops)
                self._walk(node.orelse, conditions + [_cond("else", f"not ({test})", node)], loops)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                span = (node.lineno, getattr(node, "end_lineno", node.lineno))
                cond = _cond("for", f"{ast.unparse(node.target)} in {ast.unparse(node.iter)}", node)
                for name in _target_names(node.target):
                    self._add(name, Def(name, node.lineno, "loop", node.iter, "in", conditions, loops))
                saved = dict(self._alias)
                if isinstance(node.target, ast.Name) and isinstance(node.iter, ast.Name):
                    # `for line in line_wins: line['x'] *= m` changes an item of line_wins
                    self._alias[node.target.id] = node.iter.id
                self._walk(node.body, conditions + [cond], loops + [span])
                self._alias = saved
                self._walk(node.orelse, conditions, loops)
            elif isinstance(node, ast.While):
                span = (node.lineno, getattr(node, "end_lineno", node.lineno))
                self._walk(node.body, conditions + [_cond("while", ast.unparse(node.test), node)], loops + [span])
                self._walk(node.orelse, conditions, loops)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if item.optional_vars is not None:
                        for name in _target_names(item.optional_vars):
                            self._add(name, Def(name, node.lineno, "with", item.context_expr, "as", conditions, loops))
                self._walk(node.body, conditions, loops)
            elif isinstance(node, ast.Try):
                self._walk(node.body, conditions, loops)
                for handler in node.handlers:
                    caught = ast.unparse(handler.type) if handler.type else "any exception"
                    self._walk(handler.body, conditions + [_cond("except", caught, handler)], loops)
                self._walk(node.orelse, conditions, loops)
                self._walk(node.finalbody, conditions, loops)
            elif isinstance(node, ast.Return) and node.value is not None:
                self.returns.append(Def("<return>", node.lineno, "return", node.value, "return", conditions, loops))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    self._store(target, node, node.value, "=", conditions, loops)
            elif isinstance(node, ast.AugAssign):
                self._store(node.target, node, node.value, _op(node.op) + "=", conditions, loops)
            elif (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                  and isinstance(node.value.func, ast.Attribute) and node.value.func.attr in MUTATORS
                  and isinstance(node.value.func.value, ast.Name)):
                call = node.value
                parts = list(call.args) + [k.value for k in call.keywords]
                name = call.func.value.id
                self._mutated(name, Def(name, node.lineno, "mutate", ast.Tuple(elts=parts, ctx=ast.Load()),
                                    f".{call.func.attr}", conditions, loops))

    def _store(self, target, node, value, op, conditions, loops):
        if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
            # `x[k] = v` changes x in place
            key = ast.Tuple(elts=[target.slice, value], ctx=ast.Load())
            name = target.value.id
            self._mutated(name, Def(name, node.lineno, "mutate", key, f"[{_short(target.slice)}] {op}",
                                    conditions, loops))
            return
        if isinstance(target, ast.Name):
            kind = "augassign" if op != "=" else "assign"
            self._add(target.id, Def(target.id, node.lineno, kind, value, op, conditions, loops))
        elif isinstance(target, (ast.Tuple, ast.List)):
            for name in _target_names(target):
                self._add(name, Def(name, node.lineno, "unpack", value, op, conditions, loops))
        elif (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
              and target.value.id == "self"):
            self.self_stores.setdefault(target.attr, []).append(
                Def(f"self.{target.attr}", node.lineno, "attribute", value, op, conditions, loops))

    def _mutated(self, name, d):
        """An in-place change of `name`, and of the collection `name` is an item of."""
        self._add(name, d)
        if name in self._alias:
            collection = self._alias[name]
            self._add(collection, Def(collection, d.line, "mutate", d.value, d.op, d.conditions, d.loops,
                                      via=name))

    def _add(self, name, d):
        self.defs.setdefault(name, []).append(d)

    def reaching(self, name: str, line: int) -> List[Def]:
        """Assignments to `name` that can reach `line`: earlier ones, and loop-carried ones.

        An earlier assignment is dropped when a later one (still before `line`) always runs
        after it: its condition chain is a prefix of the earlier one's, so it is in the same
        block or one enclosing it. `x += ...` reads x, so it never hides what came before.
        Early `return`/`break` are not modelled, which can only keep an extra assignment.
        """
        before, carried = [], []
        for d in self.defs.get(name, []):
            if d.line < line:
                before.append(d)
            elif any(first <= line <= last and first <= d.line <= last for first, last in d.loops):
                carried.append(d)
        live = [d1 for d1 in before
                if not any(d2.line > d1.line and d2.kind not in ("augassign", "mutate") and _prefix(d2, d1)
                           for d2 in before)]
        return live + carried


class Graph:
    """Nodes keyed by a deterministic id; `children` are what a node is computed from."""

    def __init__(self, index: CodeIndex, max_depth: int = 6, max_nodes: int = 5000):
        self.index = index
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.nodes: Dict[str, Dict] = {}
        self.truncated = False
        self._facts: Dict[int, FunctionFacts] = {}
        self._expanded: Set[str] = set()

    # ---- entry ----
    def expression(self, expr: ast.AST, ctx: Context, line: int, depth: int = 0) -> List[str]:
        """Nodes for what `expr` (evaluated at `line` in `ctx`) is computed from."""
        children = []
        for atom in _atoms(expr):
            node_id = self._atom(atom, ctx, line, depth)
            if node_id and node_id not in children:
                children.append(node_id)
        return children

    def facts(self, function: ast.AST) -> FunctionFacts:
        key = id(function)
        if key not in self._facts:
            self._facts[key] = FunctionFacts(function)
        return self._facts[key]

    # ---- atoms ----
    def _atom(self, atom: ast.AST, ctx: Context, line: int, depth: int) -> Optional[str]:
        if isinstance(atom, ast.Name):
            return self._name(atom.id, ctx, line, depth)
        if isinstance(atom, ast.Attribute):
            return self._attribute(atom, ctx, line, depth)
        if isinstance(atom, ast.Call):
            return self._call(atom, ctx, line, depth)
        return None

    def _name(self, name: str, ctx: Context, line: int, depth: int) -> Optional[str]:
        if name in ("self", "cls", "True", "False", "None"):
            return None
        if ctx.function is not None:
            facts = self.facts(ctx.function)
            reaching = facts.reaching(name, line)
            # the value passed in still reaches `line` unless a plain assignment at the
            # function's top level replaced it first (in-place changes keep it)
            param = name in facts.params and not any(
                d.line < line and d.kind not in ("augassign", "mutate") and not d.conditions
                for d in facts.defs.get(name, []))
            if param and not reaching:
                return self._node("param", name, ctx, ctx.function.lineno, key=f"param:{name}")
            if reaching:
                if len(reaching) == 1 and not param:
                    return self._def(reaching[0], ctx, depth)
                sources = len(reaching) + param
                node_id = self._node("variable", name, ctx, line, note=f"{sources} assignments reach here"
                                     + (" (one is the value passed in)" if param else ""))
                for d in reaching:
                    self._link(node_id, self._def(d, ctx, depth))
                if param:
                    self._link(node_id, self._node("param", name, ctx, ctx.function.lineno, key=f"param:{name}"))
                return node_id
        target = self.index.resolve_name(ctx.module, name)
        if isinstance(target, tuple) and target[0] == "constant":
            return self._constant(target[1], target[2], name, depth)
        if target is not None:
            return None  # a class, function or module used as a value: not a data dependency
        return self._node("unresolved", name, ctx, line, note="not assigned in this function or module")

    def _def(self, d: Def, ctx: Context, depth: int) -> str:
        node_id = self._node(d.kind, _label(d), ctx, d.line, conditions=d.conditions, op=_shape(d.value))
        if self._expand(node_id):
            for child in self.expression(d.value, ctx, d.line, depth):
                self._link(node_id, child)
            if d.kind in ("augassign", "mutate"):  # `x += v`, `x.append(v)`: the previous x, plus v
                self._link(node_id, self._name(d.name, ctx, d.line, depth))
        return node_id

    def _attribute(self, atom: ast.Attribute, ctx: Context, line: int, depth: int) -> Optional[str]:
        base = atom.value
        if isinstance(base, ast.Name) and base.id == "self" and ctx.cls is not None:
            node_id = self._node("attribute", f"self.{atom.attr}", ctx, line, key=f"attr:{ctx.cls.qualname}.{atom.attr}")
            if self._expand(node_id):
                sites = 0
                for klass in self.index.mro(ctx.cls):
                    for method in klass.methods.values():
                        for d in self.facts(method).self_stores.get(atom.attr, []):
                            if sites >= MAX_ATTRIBUTE_SITES:
                                self.nodes[node_id]["note"] = f"first {MAX_ATTRIBUTE_SITES} assignments shown"
                                break
                            sites += 1
                            store_ctx = Context(klass.module, ctx.cls, klass, method)
                            self._link(node_id, self._def(d, store_ctx, depth))
                if not sites:
                    self.nodes[node_id]["note"] = "no `self.%s = ...` in the class hierarchy" % atom.attr
            return node_id
        target = self.index.resolve_expr(ctx.module, atom)
        if isinstance(target, tuple) and target[0] == "constant":
            return self._constant(target[1], target[2], ast.unparse(atom), depth)
        return self._name(base.id, ctx, line, depth) if isinstance(base, ast.Name) else None

    def _constant(self, module: Module, assign: ast.Assign, label: str, depth: int) -> str:
        ctx = Context(module, None, None, None)
        node_id = self._node("constant", f"{label} = {_short(assign.value)}", ctx, assign.lineno)
        if self._expand(node_id):
            for child in self.expression(assign.value, ctx, assign.lineno, depth):
                self._link(node_id, child)
        return node_id

    def _call(self, call: ast.Call, ctx: Context, line: int, depth: int) -> Optional[str]:
        func = call.func
        callee = None  # (ClassInfo or None, function node, module, concrete class)
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "self" \
                and ctx.cls is not None:
            found = self.index.find_method(ctx.cls, func.attr)
            if found:
                callee = (found[0], found[1], found[0].module, ctx.cls)
            else:
                return self._args_node("unresolved", call, ctx, line, depth,
                                       note=f"`{func.attr}` not found in {ctx.cls.name}'s class hierarchy")
        elif isinstance(func, ast.Attribute) and isinstance(
                self.index.resolve_expr(ctx.module, func.value), ClassInfo):
            # `PotAward.apply(...)`: a static/class method of a class the index can see
            klass = self.index.resolve_expr(ctx.module, func.value)
            found = self.index.find_method(klass, func.attr)
            if found:
                callee = (found[0], found[1], found[0].module, klass)
        elif _is_super_call(func) and ctx.owner is not None and ctx.cls is not None:
            mro = self.index.mro(ctx.cls)
            after = mro[mro.index(ctx.owner) + 1:] if ctx.owner in mro else []
            klass = next((k for k in after if func.attr in k.methods), None)
            if klass:
                callee = (klass, klass.methods[func.attr], klass.module, ctx.cls)
        elif isinstance(func, ast.Name):
            if func.id in BUILTINS:
                return self._args_node("builtin", call, ctx, line, depth)
            target = self.index.resolve_name(ctx.module, func.id)
            if isinstance(target, tuple) and target[0] == "function":
                callee = (None, target[2], target[1], None)
            elif isinstance(target, ClassInfo):
                return self._args_node("construct", call, ctx, line, depth, note=f"constructs {target.qualname}")
        if callee is None:
            return self._args_node("external", call, ctx, line, depth, receiver=True)

        owner, function, module, concrete = callee
        callee_ctx = Context(module, concrete, owner, function)
        node_id = self._node("call", _short(call), ctx, line,
                             target=f"{callee_ctx.label} ({_where(module, function.lineno)})")
        if depth >= self.max_depth:
            self.nodes[node_id]["kind"] = "truncated"
            self.nodes[node_id]["note"] = f"call depth {self.max_depth} reached"
            return node_id
        returns_id = self._returns(callee_ctx, depth + 1)
        self._link(node_id, returns_id)
        # link each parameter the result depends on to what this call site passes for it
        for param, arg in _bind(function, call, is_method=owner is not None):
            param_id = self._id("param", param, callee_ctx, function.lineno, f"param:{param}")
            if param_id in self.nodes:
                for child in self.expression(arg, ctx, line, depth):
                    self._link(param_id, child, via=f"{_where(ctx.module, line)}")
        return node_id

    def _returns(self, ctx: Context, depth: int) -> str:
        """One node per function: what its return values are computed from."""
        node_id = self._node("returns", f"{ctx.label} returns", ctx, ctx.function.lineno, key="returns")
        if self._expand(node_id):
            facts = self.facts(ctx.function)
            if not facts.returns:
                self.nodes[node_id]["note"] = "no return value"
            for r in facts.returns:
                ret_id = self._node("return", f"return {_short(r.value)}", ctx, r.line,
                                    conditions=r.conditions, op=_shape(r.value))
                self._link(node_id, ret_id)
                if self._expand(ret_id):
                    for child in self.expression(r.value, ctx, r.line, depth):
                        self._link(ret_id, child)
        return node_id

    def _args_node(self, kind: str, call: ast.Call, ctx: Context, line: int, depth: int,
                   note: Optional[str] = None, receiver: bool = False) -> str:
        node_id = self._node(kind, _short(call), ctx, line, note=note)
        if self._expand(node_id):
            parts = list(call.args) + [k.value for k in call.keywords]
            if receiver and isinstance(call.func, ast.Attribute):
                parts.insert(0, call.func.value)
            for part in parts:
                for child in self.expression(part, ctx, line, depth):
                    self._link(node_id, child)
        return node_id

    # ---- graph plumbing ----
    def _id(self, kind, label, ctx, line, key=None) -> str:
        where = f"{ctx.module.path}:{ctx.label}:{key or f'{kind}:{line}:{label}'}"
        return hashlib.sha1(where.encode()).hexdigest()[:12]

    def _node(self, kind, label, ctx, line, key=None, **extra) -> str:
        node_id = self._id(kind, label, ctx, line, key)
        if node_id not in self.nodes:
            if len(self.nodes) >= self.max_nodes:
                self.truncated = True
            self.nodes[node_id] = {"id": node_id, "kind": kind, "label": label, "file": ctx.module.path,
                                   "line": line, "function": ctx.label, "children": [],
                                   **{k: v for k, v in extra.items() if v not in (None, [], "")}}
        return node_id

    def _expand(self, node_id: str) -> bool:
        """True the first time a node is expanded (shared nodes are linked, not re-walked)."""
        if node_id in self._expanded or self.truncated:
            return False
        self._expanded.add(node_id)
        return True

    def _link(self, parent: str, child: Optional[str], via: Optional[str] = None) -> None:
        if not child or child == parent:
            return
        node = self.nodes[parent]
        if child not in node["children"]:
            node["children"].append(child)
        if via:
            node.setdefault("via", {}).setdefault(child, via)


# ---- expression helpers ----

def _atoms(node: ast.AST) -> List[ast.AST]:
    """Names, self/module attributes and calls an expression reads, outermost first."""
    found: List[ast.AST] = []
    bound = {t.id for c in ast.walk(node) if isinstance(c, ast.comprehension)
             for t in ast.walk(c.target) if isinstance(t, ast.Name)}

    def visit(n):
        if isinstance(n, ast.Name):
            if isinstance(n.ctx, ast.Load) and n.id not in bound:
                found.append(n)
        elif isinstance(n, ast.Call):
            found.append(n)
        elif isinstance(n, ast.Attribute):
            if isinstance(n.value, ast.Name) and n.value.id not in bound:
                found.append(n)  # self.x, defs.X, obj.x: decided in _attribute
            else:
                visit(n.value)
        elif isinstance(n, ast.Lambda):
            return
        else:
            for child in ast.iter_child_nodes(n):
                visit(child)
    visit(node)
    return found


def _bind(function, call: ast.Call, is_method: bool):
    """(parameter, argument expression) pairs for a call site."""
    params = [a.arg for a in function.args.posonlyargs + function.args.args]
    if is_method and params and params[0] in ("self", "cls"):
        params = params[1:]
    for param, arg in zip(params, call.args):
        if not isinstance(arg, ast.Starred):
            yield param, arg
    names = set(params) | {a.arg for a in function.args.kwonlyargs}
    for kw in call.keywords:
        if kw.arg in names:
            yield kw.arg, kw.value


def _is_super_call(func) -> bool:
    return (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Call)
            and isinstance(func.value.func, ast.Name) and func.value.func.id == "super")


def _shape(value: Optional[ast.AST]) -> Optional[str]:
    """How a value is combined: a plain sum is what a parent total = sum of children needs."""
    if value is None:
        return None
    if isinstance(value, ast.BinOp):
        return {ast.Add: "add", ast.Sub: "sub", ast.Mult: "mul", ast.Div: "div"}.get(type(value.op), "op")
    if isinstance(value, ast.Call):
        name = getattr(value.func, "id", getattr(value.func, "attr", ""))
        return "sum" if name == "sum" else "call"
    if isinstance(value, ast.Constant):
        return "constant"
    if isinstance(value, (ast.Name, ast.Attribute, ast.Subscript)):
        return "copy"
    return "expr"


def _op(op) -> str:
    return {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}.get(type(op), "?")


def literal_overwrites(function: ast.AST, facts: "FunctionFacts", source: str) -> List[Dict]:
    """Suspicious assignments in one function, as {kind, variable, line, message, related}.

    - `overwritten_by_literal`: a fixed value replaces one computed (or built up) earlier on
      every path, e.g. `stop_list = self.spin(reels)` then `stop_list = [123, 123, 123]`, or
      a list filled by `.append()` and then reset to `[]`. A first assignment
      (`x = 0` before a loop) is never flagged: nothing was computed before it.
    - `trailing_comma_tuple`: `x = 1,` makes the one-element tuple `(1,)`.
    """
    found = []
    for name, defs in facts.defs.items():
        ordered = sorted(defs, key=lambda d: d.line)
        for i, d2 in enumerate(ordered):
            if d2.kind != "assign" or not _is_literal(d2.value):
                continue
            earlier = [d1 for d1 in ordered[:i] if _prefix(d2, d1)]
            computed = [d1 for d1 in earlier
                        if d1.kind in ("mutate", "augassign", "loop") or not _is_literal(d1.value)]
            if computed:
                last = computed[-1]
                found.append({"kind": "overwritten_by_literal", "variable": name, "line": d2.line,
                              "related": last.line,
                              "message": f"`{name} = {_short(d2.value)}` replaces the value from line "
                                         f"{last.line} (`{_label(last)}`) on every path"})
    for node in ast.walk(function):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Tuple) and len(node.value.elts) == 1
                and not (ast.get_source_segment(source, node.value) or "(").lstrip().startswith("(")):
            target = ast.unparse(node.targets[0])
            found.append({"kind": "trailing_comma_tuple", "variable": target, "line": node.lineno,
                          "message": f"`{ast.get_source_segment(source, node) or target}`: the trailing comma "
                                     f"makes {target} a one-element tuple"})
    return found


def _is_literal(value) -> bool:
    """A fixed value: a constant, or a list/tuple/dict/set made only of constants."""
    if isinstance(value, ast.Constant):
        return True
    if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_literal(e) for e in value.elts)
    if isinstance(value, ast.Dict):
        return all(k is not None and _is_literal(k) and _is_literal(v) for k, v in zip(value.keys, value.values))
    if isinstance(value, ast.UnaryOp):
        return _is_literal(value.operand)
    return False


def _label(d: Def) -> str:
    if d.kind == "mutate":
        parts = d.value.elts
        who = d.via or d.name
        tail = f"  (an item of {d.name})" if d.via else ""
        if d.op.startswith("."):  # x.append(v)
            return f"{who}{d.op}({', '.join(_short(p) for p in parts)}){tail}"
        return f"{who}{d.op} {_short(parts[1])}{tail}"  # x[k] = v
    return f"{d.name} {d.op} {_short(d.value)}"


def _prefix(later: Def, earlier: Def) -> bool:
    """`later` runs whenever `earlier` did: its enclosing blocks are among `earlier`'s."""
    a = [(c["kind"], c["line"]) for c in later.conditions]
    b = [(c["kind"], c["line"]) for c in earlier.conditions]
    return b[: len(a)] == a


def _cond(kind, expression, node) -> Dict:
    return {"kind": kind, "expression": expression, "line": node.lineno}


def _target_names(target) -> List[str]:
    return [n.id for n in ast.walk(target) if isinstance(n, ast.Name)]


def _short(node, limit: int = 140) -> str:
    text = " ".join(ast.unparse(node).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _where(module: Module, line: int) -> str:
    return f"{os.path.basename(module.path)}:{line}"
