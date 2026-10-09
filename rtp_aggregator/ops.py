"""The seven operations a formula is built from.

One registry, holding for each operation both *what shape it must have* (checked
when the spec is parsed) and *what it computes* (used when it is evaluated), so the
two can never drift apart. Adding an operation means adding one entry here.

| Operation | Meaning | Example |
|---|---|---|
| `input` | read a measured value | base-game RTP |
| `ref` | reference another component | free-spins RTP |
| `add` | sum contributions | base + retrigger |
| `multiply` | multiply values | probability x payout |
| `divide` | calculate a ratio | total win / total bet |
| `sum` | aggregate a collection | sum RTP across a feature category |
| `if` | apply a conditional rule | include a feature only when enabled |
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

REF_KEY = "ref"


@dataclass(frozen=True)
class Op:
    meaning: str
    example: str
    required: Tuple[str, ...] = ()
    optional: Tuple[str, ...] = ()
    #: Of the keys above, those whose value is a sub-expression (or a list of them).
    exprs: Tuple[str, ...] = ()
    #: Permitted length of `args`, as (minimum, maximum); None for no maximum.
    arity: Optional[Tuple[int, Optional[int]]] = None
    evaluate: Optional[Callable[[Dict, Any], float]] = None

    @property
    def keys(self) -> Tuple[str, ...]:
        return self.required + self.optional


def canonical(node: Dict) -> Dict:
    """`{"ref": "x"}` is shorthand for `{"op": "ref", "name": "x"}`.

    Applied by both the validator and the evaluator so the two forms can never be
    understood differently. Any other key is carried through, so a typo alongside
    the shorthand is still caught rather than quietly dropped.
    """
    if isinstance(node, dict) and REF_KEY in node and "op" not in node:
        rest = {k: v for k, v in node.items() if k != REF_KEY}
        return {"op": "ref", "name": node[REF_KEY], **rest}
    return node


def _input(node, ev):
    return ev.input(node["name"])


def _ref(node, ev):
    return ev.component(node["name"])


def _add(node, ev):
    return sum(ev.expr(a) for a in node["args"])


def _multiply(node, ev):
    product = 1.0
    for arg in node["args"]:
        product *= ev.expr(arg)
    return product


def _divide(node, ev):
    numerator, denominator = (ev.expr(a) for a in node["args"])
    if denominator == 0:
        ev.fail("division by zero")
    return numerator / denominator


def _sum(node, ev):
    """Aggregate every component declaring this category.

    The members are resolved from the spec, not listed in the formula, so adding a
    feature to a category is enough for it to be counted.
    """
    return sum(ev.component(name) for name in ev.spec.by_category(node["category"]))


def _if(node, ev):
    """Only the branch taken is evaluated.

    That is what makes "include this feature only when it is enabled" work: the
    disabled branch may reference inputs the report does not carry at all.
    """
    if _truthy(ev.expr(node["cond"])):
        return ev.expr(node["then"])
    return ev.expr(node["else"]) if "else" in node else 0.0


def _truthy(value: float) -> bool:
    return value != 0


OPS: Dict[str, Op] = {
    "input": Op(
        meaning="read a measured value",
        example='{"op": "input", "name": "base_game_rtp"}',
        required=("name",),
        evaluate=_input,
    ),
    "ref": Op(
        meaning="reference another component",
        example='{"ref": "free_spins_rtp"}',
        required=("name",),
        evaluate=_ref,
    ),
    "add": Op(
        meaning="sum contributions",
        example='{"op": "add", "args": [{"ref": "a"}, {"ref": "b"}]}',
        required=("args",),
        exprs=("args",),
        arity=(1, None),
        evaluate=_add,
    ),
    "multiply": Op(
        meaning="multiply values",
        example='{"op": "multiply", "args": [{"ref": "probability"}, 25]}',
        required=("args",),
        exprs=("args",),
        arity=(1, None),
        evaluate=_multiply,
    ),
    "divide": Op(
        meaning="calculate a ratio",
        example='{"op": "divide", "args": [{"ref": "total_win"}, {"ref": "total_bet"}]}',
        required=("args",),
        exprs=("args",),
        arity=(2, 2),
        evaluate=_divide,
    ),
    "sum": Op(
        meaning="aggregate every component in a category",
        example='{"op": "sum", "category": "free_spins"}',
        required=("category",),
        evaluate=_sum,
    ),
    "if": Op(
        meaning="apply a conditional rule",
        example='{"op": "if", "cond": {"op": "input", "name": "fs_enabled"}, '
                '"then": {"ref": "free_spins_rtp"}, "else": 0}',
        required=("cond", "then"),
        optional=("else",),
        exprs=("cond", "then", "else"),
        evaluate=_if,
    ),
}
