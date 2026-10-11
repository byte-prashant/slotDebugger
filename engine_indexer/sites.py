"""Where a volume tester emits report events, read from its source.

Every `dump_event(key, value)` call is a *site*: a line that can put rows in the
report. For each one this records what can be known without running anything:

    {"line": 91, "end_line": 91, "function": "dump_map_result",
     "key_expression": "'action_' + action + '_tot_win'", "pattern": "action_*_tot_win",
     "value_expression": "float(p.result['current_winnings'])",
     "response_key": "current_winnings",
     "conditions": [{"kind": "for", "expression": "win in wins", "line": 95}],
     "names": ["action", "win", "wins"]}

`response_key` follows the function in source order: `wins = p.result.get('ways_wins')`
then `for win in wins` makes `win['winnings']` a read of `ways_wins`, and a later
`for win in p.result['line_wins']` rebinds `win` for its own body only.

`conditions` is the chain of `if` / `else` / `for` / `while` / `with` / `try`
blocks the call sits inside, outermost first; an `else` branch is recorded as
`not (<test>)`. `names` are the variables those expressions read: the tracer
records their run-time values so a dynamic key can be explained by the values
that built it.
"""

import ast
from typing import Dict, List, Optional

from rtp_aggregator.sources import EMIT_CALLS, _callee, _field, _pattern


def scan(path: str) -> List[Dict]:
    """Every event-emitting call in `path`, in source order."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    found: List[Dict] = []
    _walk(tree.body, [], None, None, found)
    return sorted(found, key=lambda s: s["line"])


def _walk(body: List[ast.stmt], conditions: List[Dict], function: Optional[ast.AST],
          env: Optional[Dict], out: List[Dict]) -> None:
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _walk(node.body, [], node, {}, out)  # a function starts a new scope
        elif isinstance(node, ast.ClassDef):
            _walk(node.body, [], None, None, out)
        elif isinstance(node, ast.If):
            test = ast.unparse(node.test)
            _calls(node.test, conditions, function, env, out)
            _walk(node.body, conditions + [_cond("if", test, node)], function, env, out)
            if node.orelse:
                _walk(node.orelse, conditions + [_cond("else", f"not ({test})", node)], function, env, out)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            _calls(node.iter, conditions, function, env, out)
            loop = _cond("for", f"{ast.unparse(node.target)} in {ast.unparse(node.iter)}", node)
            body_env = dict(env or {})
            if isinstance(node.target, ast.Name):
                body_env[node.target.id] = _field(node.iter, env or {})
            _walk(node.body, conditions + [loop], function, body_env, out)
            _walk(node.orelse, conditions, function, env, out)
        elif isinstance(node, ast.While):
            test = ast.unparse(node.test)
            _walk(node.body, conditions + [_cond("while", test, node)], function, env, out)
            _walk(node.orelse, conditions, function, env, out)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            items = ", ".join(ast.unparse(i) for i in node.items)
            _walk(node.body, conditions + [_cond("with", items, node)], function, env, out)
        elif isinstance(node, ast.Try):
            _walk(node.body, conditions + [_cond("try", "", node)], function, env, out)
            for handler in node.handlers:
                caught = ast.unparse(handler.type) if handler.type else "any exception"
                _walk(handler.body, conditions + [_cond("except", caught, handler)], function, env, out)
            _walk(node.orelse, conditions, function, env, out)
            _walk(node.finalbody, conditions, function, env, out)
        else:
            _calls(node, conditions, function, env, out)
            if (env is not None and isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)):
                env[node.targets[0].id] = _field(node.value, env)  # later lines see this binding


def _calls(node: ast.AST, conditions: List[Dict], function: Optional[ast.AST],
           env: Optional[Dict], out: List[Dict]) -> None:
    """Event calls inside one simple statement (or expression)."""
    for call in ast.walk(node):
        if not (isinstance(call, ast.Call) and _callee(call) in EMIT_CALLS and call.args):
            continue
        key, value = call.args[0], call.args[1] if len(call.args) > 1 else None
        names = _names(key) | {n for c in conditions for n in c["names"]}
        out.append({
            "line": call.lineno,
            "end_line": getattr(call, "end_lineno", call.lineno),
            "function": function.name if function is not None else None,
            "key_expression": ast.unparse(key),
            "pattern": _pattern(key),
            "value_expression": ast.unparse(value) if value is not None else None,
            "response_key": _field(value, env or {}) if value is not None else None,
            "conditions": [{k: c[k] for k in ("kind", "expression", "line")} for c in conditions],
            "names": sorted(names),
        })


def _cond(kind: str, expression: str, node: ast.AST) -> Dict:
    names = set()
    for part in ("test", "target", "iter", "items", "type"):
        sub = getattr(node, part, None)
        for item in sub if isinstance(sub, list) else [sub]:
            if isinstance(item, ast.AST):
                names |= _names(item)
    return {"kind": kind, "expression": expression, "line": node.lineno, "names": names}


def _names(node: ast.AST) -> set:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
