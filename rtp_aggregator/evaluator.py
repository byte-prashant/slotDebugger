"""Compute a spec's components from measured inputs.

    >>> evaluate(spec, {"base_game_rtp": 0.406, "fs_base_rtp": 0.0782})

Each component is computed once and reused, so a value referenced by several
formulas is not recomputed and cannot come out differently in two places.

Nothing is rounded: a formula's result is as precise as the inputs allow, and
presentation is the caller's business.

The evaluator assumes a spec that came from `spec.parse()`. Unknown operations,
dangling references and cycles are rejected there, which is what lets the code
below read as plain arithmetic.
"""

from typing import Any, Dict, NoReturn

from rtp_aggregator import ops
from rtp_aggregator.errors import EvaluationError
from rtp_aggregator.spec import Spec


def evaluate(spec: Spec, inputs: Dict[str, Any]) -> Dict[str, float]:
    """Every component in the spec, in the order the document declares them."""
    return Evaluator(spec, inputs).all()


class Evaluator:
    def __init__(self, spec: Spec, inputs: Dict[str, Any]):
        self.spec = spec
        self.inputs = inputs or {}
        self._values: Dict[str, float] = {}
        self._trail: list = []

    def all(self) -> Dict[str, float]:
        return {name: self.component(name) for name in self.spec.names()}

    def component(self, name: str) -> float:
        """The value of a named component, computed once and remembered."""
        if name in self._values:
            return self._values[name]
        self._trail.append(name)
        try:
            value = self.expr(self.spec[name].formula)
        finally:
            self._trail.pop()
        self._values[name] = value
        return value

    def expr(self, node: Any) -> float:
        """The value of one expression: a literal number, or an operation."""
        if isinstance(node, (int, float)) and not isinstance(node, bool):
            return float(node)
        node = ops.canonical(node)
        return ops.OPS[node["op"]].evaluate(node, self)

    def input(self, name: str) -> float:
        """A measured value supplied by the caller.

        Missing is an error, never zero: a feature silently contributing nothing is
        the one failure that looks exactly like a correct report.
        """
        if name not in self.inputs:
            self.fail(f"no value supplied for input {name!r}")
        return self._number(name, self.inputs[name])

    def fail(self, message: str) -> NoReturn:
        where = " -> ".join(self._trail)
        raise EvaluationError(f"{where}: {message}" if where else message)

    def _number(self, name: str, value: Any) -> float:
        """Inputs arrive from reports and JSON, so numbers may be strings or flags."""
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            self.fail(f"input {name!r} is {value!r}, which is not a number")
