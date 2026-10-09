"""Read a formula spec, and refuse to accept one that cannot be trusted.

A spec is a JSON document naming components and how each is computed:

    {
      "schema_version": "1.0",
      "components": {
        "total_rtp":     {"unit": "rtp_fraction",
                          "formula": {"op": "add", "args": [{"ref": "base_game_rtp"},
                                                            {"ref": "free_spins_rtp"}]}},
        "base_game_rtp": {"unit": "rtp_fraction",
                          "formula": {"op": "input", "name": "base_game_rtp"}}
      }
    }

Everything that can be known without the numbers is checked here and reported in
one go: unknown operations, misspelled keys, dangling references, reference cycles,
categories nobody belongs to, and components whose parts are measured in different
units. That last one is the dangerous case in this codebase — adding a count to an
amount produces a plausible number and no error — so a formula may only add things
that declare the same `unit`.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from rtp_aggregator import ops
from rtp_aggregator.errors import SpecError

SCHEMA_VERSION = "1.0"
SUPPORTED_MAJOR = "1"


@dataclass(frozen=True)
class Component:
    """One line of an RTP report, and the formula that produces it."""

    name: str
    formula: Any
    unit: Optional[str] = None
    category: Optional[str] = None
    #: Anything else the author wrote (description, source, owner); carried, unused.
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Spec:
    version: str
    components: Dict[str, Component]

    def __contains__(self, name: str) -> bool:
        return name in self.components

    def __getitem__(self, name: str) -> Component:
        try:
            return self.components[name]
        except KeyError:
            raise SpecError(f"no component {name!r}; spec has {', '.join(self.names()) or 'none'}"
                            ) from None

    def names(self) -> Tuple[str, ...]:
        """Component names, in the order the document declares them."""
        return tuple(self.components)

    def by_category(self, category: str) -> Tuple[str, ...]:
        """Members of a category, in document order so a sum is reproducible."""
        return tuple(n for n, c in self.components.items() if c.category == category)

    def categories(self) -> Tuple[str, ...]:
        seen = {c.category for c in self.components.values() if c.category}
        return tuple(sorted(seen))

    def required_inputs(self) -> Tuple[str, ...]:
        """Every input the spec can ask for.

        A superset of what a given evaluation needs: `if` only evaluates the branch
        it takes, so a disabled feature's inputs are listed but never read.
        """
        found: Set[str] = set()
        for comp in self.components.values():
            _walk(comp.formula, lambda n: found.add(n["name"]) if n.get("op") == "input" else None)
        return tuple(sorted(found))

    def dependencies(self, name: str) -> Tuple[str, ...]:
        """Components `name` reads, directly."""
        return _dependencies(self, self[name].formula)


def load(path: str) -> Spec:
    try:
        with open(path) as f:
            return parse(json.load(f))
    except json.JSONDecodeError as e:
        raise SpecError(f"{path}: not valid JSON ({e})") from None
    except SpecError as e:
        raise SpecError(f"{path}: {e}") from None


def parse(document: Any) -> Spec:
    """Build a `Spec`, or raise `SpecError` listing everything wrong with it."""
    if not isinstance(document, dict):
        raise SpecError(f"spec must be an object, got {type(document).__name__}")

    version = document.get("schema_version")
    if version is None:
        raise SpecError(f"spec has no schema_version (this build writes {SCHEMA_VERSION!r})")
    if not isinstance(version, str) or str(version).split(".")[0] != SUPPORTED_MAJOR:
        raise SpecError(f"unsupported schema_version {version!r}; "
                        f"this build understands {SUPPORTED_MAJOR}.x")

    raw = document.get("components")
    if not isinstance(raw, dict) or not raw:
        raise SpecError("spec has no components")

    spec = Spec(version=version, components={
        name: _component(name, body) for name, body in raw.items()
    })
    problems = _problems(spec)
    if problems:
        raise SpecError(f"spec has {len(problems)} problem{'s' if len(problems) > 1 else ''}:\n  - "
                        + "\n  - ".join(problems))
    return spec


def _component(name: str, body: Any) -> Component:
    if not isinstance(body, dict):
        raise SpecError(f"{name}: must be an object, got {type(body).__name__}")
    if "formula" not in body:
        raise SpecError(f"{name}: has no formula")
    for key in ("unit", "category"):
        if key in body and not isinstance(body[key], str):
            raise SpecError(f"{name}: {key} must be a string, got {type(body[key]).__name__}")
    return Component(
        name=name,
        formula=body["formula"],
        unit=body.get("unit"),
        category=body.get("category"),
        extra={k: v for k, v in body.items() if k not in ("formula", "unit", "category")},
    )


# ---- validation ----

def _problems(spec: Spec) -> List[str]:
    found: List[str] = []
    for comp in spec.components.values():
        found += _expression_problems(spec, comp.formula, comp.name)
    if found:
        # Cycles and units are read off well-formed formulas; checking a malformed
        # one would only pile guesses on top of the real error.
        return found
    return _cycle_problems(spec) + _unit_problems(spec)


def _expression_problems(spec: Spec, node: Any, where: str) -> List[str]:
    """Shape and reference errors in one expression, depth first."""
    if isinstance(node, bool):
        return [f"{where}: true/false is not a value; use 1 or 0"]
    if isinstance(node, (int, float)):
        return []
    if not isinstance(node, dict):
        return [f"{where}: expected a number or an operation, got {type(node).__name__}"]

    node = ops.canonical(node)
    name = node.get("op")
    if not isinstance(name, str):
        return [f"{where}: no op; expected one of {', '.join(sorted(ops.OPS))}"]
    op = ops.OPS.get(name)
    if op is None:
        return [f"{where}: unknown op {name!r}; expected one of {', '.join(sorted(ops.OPS))}"]

    found = []
    for key in op.required:
        if key not in node:
            found.append(f"{where}: {name} needs {key!r}, e.g. {op.example}")
    for key in node:
        if key != "op" and key not in op.keys:
            found.append(f"{where}: {name} has no key {key!r}; it takes "
                         f"{', '.join(repr(k) for k in op.keys) or 'none'}")
    if found:
        return found  # the shape is wrong; descending would only add noise

    found += _arity_problems(node, op, name, where)
    found += _target_problems(spec, node, name, where)
    for key in op.exprs:
        if key not in node:
            continue
        value = node[key]
        if isinstance(value, list):
            for i, item in enumerate(value):
                found += _expression_problems(spec, item, f"{where}.{key}[{i}]")
        else:
            found += _expression_problems(spec, value, f"{where}.{key}")
    return found


def _arity_problems(node: Dict, op: ops.Op, name: str, where: str) -> List[str]:
    if op.arity is None:
        return []
    args = node.get("args")
    if not isinstance(args, list):
        return [f"{where}: {name} needs 'args' to be a list, got {type(args).__name__}"]
    low, high = op.arity
    if len(args) < low or (high is not None and len(args) > high):
        wanted = f"{low}" if high == low else f"at least {low}" if high is None else f"{low} to {high}"
        return [f"{where}: {name} takes {wanted} args, got {len(args)}"]
    return []


def _target_problems(spec: Spec, node: Dict, name: str, where: str) -> List[str]:
    """A reference or category that points at nothing would silently contribute zero."""
    if name == "ref" and node["name"] not in spec:
        return [f"{where}: refers to {node['name']!r}, which the spec does not define"]
    if name == "sum" and not spec.by_category(node["category"]):
        known = ", ".join(spec.categories()) or "none"
        return [f"{where}: sums category {node['category']!r}, which no component declares "
                f"(known categories: {known})"]
    return []


def _cycle_problems(spec: Spec) -> List[str]:
    """A component that depends on itself, however indirectly, can never be computed."""
    found, done, stack = [], set(), []

    def visit(name: str) -> None:
        if name in done:
            return
        if name in stack:
            cycle = stack[stack.index(name):] + [name]
            found.append("cycle: " + " -> ".join(cycle))
            return
        stack.append(name)
        for dep in _dependencies(spec, spec[name].formula):
            visit(dep)
        stack.pop()
        done.add(name)

    for component in spec.names():
        visit(component)
    return found


def _unit_problems(spec: Spec) -> List[str]:
    """Only things measured the same way may be added together."""
    found = []
    for comp in spec.components.values():
        _walk(comp.formula, lambda n: found.extend(_mixed_units(spec, n, comp.name)))
        stated, implied = comp.unit, _unit_of(spec, comp.formula)
        if stated and implied and stated != implied:
            found.append(f"{comp.name}: declared {stated!r} but its formula produces {implied!r}")
    return found


def _mixed_units(spec: Spec, node: Dict, where: str) -> List[str]:
    if node.get("op") not in ("add", "sum", "if"):
        return []
    units = _unit_set(spec, node)
    if len(units) > 1:
        return [f"{where}: {node['op']} mixes units {', '.join(sorted(repr(u) for u in units))}"]
    return []


def _unit_set(spec: Spec, node: Dict) -> Set[str]:
    """The declared units among an operation's parts, ignoring those that state none."""
    name = node.get("op")
    if name == "sum":
        parts: List[Any] = [{"op": "ref", "name": n} for n in spec.by_category(node["category"])]
    elif name == "add":
        parts = list(node.get("args") or [])
    elif name == "if":
        parts = [node[k] for k in ("then", "else") if k in node]
    else:
        return set()
    return {u for u in (_unit_of(spec, p) for p in parts) if u}


def _unit_of(spec: Spec, node: Any) -> Optional[str]:
    """What a formula is measured in, when that follows from the spec alone.

    Only declarations are consulted, never other formulas, so this terminates even
    on a spec that turns out to contain a cycle. A `multiply`, `divide` or `input`
    states nothing, and nothing is inferred for it.
    """
    node = ops.canonical(node)
    if not isinstance(node, dict):
        return None
    name = node.get("op")
    if name == "ref":
        target = spec.components.get(node.get("name"))
        return target.unit if target else None
    if name in ("add", "sum", "if"):
        units = _unit_set(spec, node)
        return units.pop() if len(units) == 1 else None
    return None


# ---- traversal ----

def _walk(node: Any, visit) -> None:
    """Call `visit` on every operation node in an expression."""
    node = ops.canonical(node)
    if not isinstance(node, dict):
        return
    op = ops.OPS.get(node.get("op"))
    if op is None:
        return
    visit(node)
    for key in op.exprs:
        value = node.get(key)
        for item in (value if isinstance(value, list) else [value]):
            _walk(item, visit)


def _dependencies(spec: Spec, formula: Any) -> Tuple[str, ...]:
    """Components a formula reads, including everyone in a summed category."""
    found: List[str] = []

    def collect(node: Dict) -> None:
        if node.get("op") == "ref" and node["name"] in spec:
            found.append(node["name"])
        elif node.get("op") == "sum":
            found.extend(spec.by_category(node["category"]))

    _walk(formula, collect)
    return tuple(dict.fromkeys(found))
