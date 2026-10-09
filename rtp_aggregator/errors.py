"""Failures in a formula spec, kept apart from failures in evaluating it.

Both are `ValueError`, so a CLI that already catches `(OSError, ValueError)` reports
them as `error: ...` without extra handling.
"""


class AggregatorError(ValueError):
    """Anything wrong with a spec or its evaluation."""


class SpecError(AggregatorError):
    """The spec itself is wrong: unknown op, dangling ref, cycle, clashing units.

    Raised when the document is parsed, before any value is computed, because none
    of these depend on the numbers being fed in.
    """


class EvaluationError(AggregatorError):
    """The spec is sound but these inputs cannot be evaluated against it.

    A missing input or a division by zero. The message names the component trail
    that led there, since a formula is only ever reached through its parents.
    """
