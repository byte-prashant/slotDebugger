"""Read a game's volume tester and engine to learn how its totals are built.

Two facts, in two files, chain together:

    volume_tester.py   dump_event('action_bonus_win', p.result['bonus_prize_winning'])
                       -> the event `action_bonus_win` reports result field `bonus_prize_winning`
    engine.py          current_winnings = bonus_prize_winning + current_line_winnings
                       'current_winnings': current_winnings        (the result dict)
                       -> field `current_winnings` is the sum of two other fields

So the event reporting `current_winnings` has the event reporting `bonus_prize_winning`
as a child. Nothing here runs the game; both files are read as syntax trees, so what
cannot be followed statically is reported as not known instead of guessed at:

- an event name built at run time becomes a pattern, `'action_' + action + '_win'` -> `action_*_win`;
- a field is a *sum* only if every assignment to its variable is an addition (or `sum(...)`);
  anything else (a product, a function call, mixed assignments) is a dependency without a formula;
- a dependency nobody emits an event for is listed, so the sum is not offered as complete.
"""

import ast
import fnmatch
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

EMIT_CALLS = ("dump_event", "emit_event")


@dataclass(frozen=True)
class Event:
    """An event a volume tester emits: its name pattern and the result field it reports."""
    pattern: str
    key: Optional[str] = None

    def matches(self, name: str) -> bool:
        return fnmatch.fnmatchcase(name.lower(), self.pattern.lower())


@dataclass
class Engine:
    #: result field -> the variable the engine stores in it
    fields: Dict[str, Set[str]] = field(default_factory=dict)
    #: variable -> variables its value is computed from
    reads: Dict[str, Set[str]] = field(default_factory=dict)
    #: variable -> "add" when every assignment is an addition / sum(), else "other"
    kind: Dict[str, str] = field(default_factory=dict)

    def dependencies(self, key: str) -> Tuple[List[str], str]:
        """Result fields `key` is computed from, and whether that is a plain sum.

        Follows intermediate variables until it reaches ones that are result fields
        in their own right.
        """
        by_var: Dict[str, List[str]] = {}
        for k, variables in self.fields.items():
            for v in variables:
                by_var.setdefault(v, []).append(k)
        found: List[str] = []
        kinds: Set[str] = set()

        def walk(var: str, seen: Set[str]) -> None:
            kinds.add(self.kind.get(var, "other"))
            for name in sorted(self.reads.get(var, ())):
                if name == var or name in seen:
                    continue
                if name in by_var:
                    found.extend(k for k in by_var[name] if k != key)
                elif name in self.reads:
                    walk(name, seen | {name})

        for var in sorted(self.fields.get(key, ())):
            walk(var, {var})
        return list(dict.fromkeys(found)), "add" if kinds == {"add"} else "other"


def scan_volume_tester(path: str) -> List[Event]:
    """Events a volume tester emits, each with the result field its value reads."""
    tree = _parse(path)
    found: List[Event] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        env = _origins(fn)
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and len(node.args) >= 2 and _callee(node) in EMIT_CALLS:
                pattern = _pattern(node.args[0])
                if any(ch != "*" for ch in pattern):  # a bare `*` explains nothing
                    found.append(Event(pattern, _field(node.args[1], env)))
    return list(dict.fromkeys(found))


def scan_engine(path: str) -> Engine:
    """How an engine's result fields are computed from one another."""
    tree = _parse(path)
    engine = Engine()
    assigns: Dict[str, List[str]] = {}  # variable -> "add" / "other" per assignment
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    _assigned(engine, assigns, target.id, node.value, augmented=False)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            _assigned(engine, assigns, node.target.id, node.value,
                      augmented=isinstance(node.op, ast.Add))
        for key, value in _result_entries(node):
            if isinstance(value, ast.Name):
                engine.fields.setdefault(key, set()).add(value.id)
    for var, kinds in assigns.items():
        engine.kind[var] = "add" if kinds and set(kinds) == {"add"} else "other"
    return engine


def derive_graph(events: List[Event], engine: Engine, names: Iterable[str]) -> Dict:
    """Parent -> children among report rows, from what the engine says each event is made of.

    `{"children": [...], "complete": bool, "op": "add"|"other"}`: `complete` is False
    when some field the total depends on has no event (or no row), so summing the
    children found would give a number that is plausible and wrong.
    """
    names = list(names)
    rows: Dict[str, List[str]] = {}
    for event in events:
        if event.key:
            rows.setdefault(event.key, []).extend(n for n in names if event.matches(n))
    graph: Dict[str, Dict] = {}
    for key, parents in rows.items():
        deps, op = engine.dependencies(key)
        if not deps:
            continue
        observed = [d for d in deps if rows.get(d)]
        children = list(dict.fromkeys(n for d in observed for n in rows[d]))
        for parent in dict.fromkeys(parents):
            kids = [c for c in children if c != parent]
            if kids:
                graph[parent] = {"children": kids, "op": op,
                                 "complete": op == "add" and len(observed) == len(deps),
                                 "depends_on": deps}
    return graph


# ---- volume tester internals ----

def _origins(fn: ast.AST) -> Dict[str, Optional[str]]:
    """Local variable -> the result field it was read from (`wins = p.result.get('ways_wins')`)."""
    env: Dict[str, Optional[str]] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            env[node.targets[0].id] = _field(node.value, env)
        elif isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            env[node.target.id] = _field(node.iter, env)
    return env


def _field(node: ast.AST, env: Dict[str, Optional[str]]) -> Optional[str]:
    """The result field an expression reads: `p.result['x']`, `p.result.get('x')`, or an
    item of a collection that was read from one (`win['winnings']` inside `for win in wins`)."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Subscript) and _const(sub.slice) is not None:
            base = sub.value
            if isinstance(base, ast.Name) and env.get(base.id):
                return env[base.id]
            return _const(sub.slice)
        if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "get" and sub.args and _const(sub.args[0]) is not None):
            return _const(sub.args[0])
        if isinstance(sub, ast.Name) and env.get(sub.id):
            return env[sub.id]
    return None


def _callee(node: ast.Call) -> Optional[str]:
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)


def _const(node: ast.AST) -> Optional[str]:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _pattern(node: ast.AST) -> str:
    """A string expression with each run-time part replaced by `*`."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        text = node.value
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        text = _pattern(node.left) + _pattern(node.right)
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        text = _pattern(node.left).replace("%s", "*").replace("%d", "*")
    elif isinstance(node, ast.JoinedStr):
        text = "".join(_pattern(v) if isinstance(v, ast.Constant) else "*" for v in node.values)
    elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
          and node.func.attr == "format"):
        text = _pattern(node.func.value)
        while "{" in text and "}" in text:
            start = text.index("{")
            text = text[:start] + "*" + text[text.index("}", start) + 1:]
    else:
        text = "*"
    while "**" in text:
        text = text.replace("**", "*")
    return text


# ---- engine internals ----

def _assigned(engine: Engine, assigns: Dict[str, List[str]], var: str, value: ast.AST,
              augmented: bool) -> None:
    names = _names(value)
    names.discard(var)
    engine.reads.setdefault(var, set()).update(names)
    assigns.setdefault(var, []).append("add" if augmented or _is_sum(value) else "other")


def _is_sum(value: ast.AST) -> bool:
    """`a + b + c`, `sum(...)` or a bare variable: a plain addition of other quantities."""
    if isinstance(value, ast.BinOp):
        return isinstance(value.op, ast.Add) and _is_sum(value.left) and _is_sum(value.right)
    return isinstance(value, ast.Name) or (isinstance(value, ast.Call) and _callee(value) == "sum")


def _names(node: ast.AST) -> Set[str]:
    """Variables an expression reads, not counting a comprehension's own loop variables."""
    bound = {t.id for c in ast.walk(node) if isinstance(c, ast.comprehension)
             for t in ast.walk(c.target) if isinstance(t, ast.Name)}
    return {n.id for n in ast.walk(node)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)} - bound


def _result_entries(node: ast.AST):
    """`{'key': value}` literals and `state['key'] = value` assignments."""
    if isinstance(node, ast.Dict):
        for k, v in zip(node.keys, node.values):
            if k is not None and _const(k) is not None:
                yield _const(k), v
    elif isinstance(node, ast.Assign) and len(node.targets) == 1:
        t = node.targets[0]
        if isinstance(t, ast.Subscript) and _const(t.slice) is not None:
            yield _const(t.slice), node.value


def _parse(path: str) -> ast.AST:
    with open(path, encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)
