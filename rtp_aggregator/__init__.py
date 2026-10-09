"""Declarative formulas for the components of an RTP report.

How a game's RTP breaks down is maths, not code: free spins are the base feature
plus retriggers plus expanding wilds, the total is base game plus free spins. This
package lets a game state that arithmetic as data and compute it, so the breakdown
lives next to the game instead of inside the debugger.

    {
      "schema_version": "1.0",
      "components": {
        "free_spins_rtp": {
          "unit": "rtp_fraction",
          "formula": {"op": "add", "args": [{"ref": "fs_base_rtp"},
                                            {"ref": "fs_retrigger_rtp"},
                                            {"ref": "fs_expanding_wild_rtp"}]}
        },
        "fs_base_rtp": {"unit": "rtp_fraction",
                        "formula": {"op": "input", "name": "fs_base_rtp"}}
      }
    }

    from rtp_aggregator import evaluate, load
    spec = load("rtp_formulas.json")
    values = evaluate(spec, {"fs_base_rtp": 0.0782, ...})

## The formula language

An expression is a number, or one of seven operations:

| Operation | Meaning | Example |
|---|---|---|
| `input` | read a measured value | base-game RTP |
| `ref` | reference another component | free-spins RTP |
| `add` | sum contributions | base + retrigger |
| `multiply` | multiply values | probability x payout |
| `divide` | calculate a ratio | total win / total bet |
| `sum` | aggregate a collection | sum RTP across a feature category |
| `if` | apply a conditional rule | include a feature only when enabled |

`{"ref": "x"}` is shorthand for `{"op": "ref", "name": "x"}`. `sum` takes a
`category` and adds up every component declaring it, so adding a feature to a
category is enough for it to be counted. `if` evaluates only the branch it takes,
which is what lets a disabled feature reference inputs the report does not carry.

## What it refuses to do

Silence is the enemy here: a wrong RTP breakdown returns a plausible number and no
error. So the spec is checked before any value is computed, and everything wrong
with it is reported at once \u2014 unknown operations, misspelled keys, references to
components that do not exist, reference cycles, and categories nobody belongs to.
Each of those would otherwise contribute a quiet zero.

Units are checked the same way. `add`, `sum` and the branches of an `if` may only
combine components that declare the same `unit`, and a component whose formula
produces a different unit from the one it declares is rejected. Adding a spin count
to a cash amount is the mistake this codebase has already made once.

At evaluation time a missing input is an error rather than zero, and so is a
division by zero; the message names the chain of components that led there.

| Module | Responsibility |
|---|---|
| [ops.py](ops.py) | the seven operations: shape and meaning in one registry |
| [spec.py](spec.py) | read a spec document; reject one that cannot be trusted |
| [evaluator.py](evaluator.py) | compute components from inputs, each one once |
| [builder.py](builder.py) | write `aggregate.json` from a report analysis and a volume tester |
| [sources.py](sources.py) | read a volume tester and engine for how totals are built |
| [review.py](review.py) | list what in a generated aggregate is wrong or unproven |
| [errors.py](errors.py) | `SpecError` and `EvaluationError`, both `ValueError` |
"""

from rtp_aggregator.builder import build, compute, join, key, overall_rtp, split
from rtp_aggregator.sources import Engine, Event, derive_graph, scan_engine, scan_volume_tester
from rtp_aggregator.review import check
from rtp_aggregator.errors import AggregatorError, EvaluationError, SpecError
from rtp_aggregator.evaluator import Evaluator, evaluate
from rtp_aggregator.ops import OPS, Op
from rtp_aggregator.spec import SCHEMA_VERSION, Component, Spec, load, parse

__all__ = [
    "AggregatorError",
    "Component",
    "EvaluationError",
    "Engine",
    "Event",
    "Evaluator",
    "OPS",
    "Op",
    "SCHEMA_VERSION",
    "Spec",
    "SpecError",
    "build",
    "check",
    "compute",
    "derive_graph",
    "evaluate",
    "join",
    "key",
    "load",
    "overall_rtp",
    "parse",
    "scan_engine",
    "split",
    "scan_volume_tester",
]
