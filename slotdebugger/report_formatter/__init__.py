"""Shape the analysis like the game's own `expected_rtp_report.json`.

Every game states its target RTP in whatever shape its maths team uses, e.g.

    {"bet": 75, "total": 96.32, "components": {"BG": 40.60, "FG1": 7.82}}

When such a file sits in the workspace, `analyze` emits **exactly that shape** —
same keys, same nesting, same scale, same precision — filled with the values
measured from the report, so expected and actual can be diffed line by line. With
no such file, the native analysis format is used.

    path = find(report_dir, workspace_root)
    output, notes = apply(load(path), analysis)

The keys are the game's own (`BG`, `FG12`, ...), so they are matched against the
report's component names heuristically. Every match is reported in `notes`, and a
key that matches nothing is left `null` rather than guessed at — a missing number
is recoverable, a plausible wrong one is not.

| Module | Responsibility |
|---|---|
| [template.py](template.py) | find and load the file; read its scale and precision |
| [naming.py](naming.py) | interpret the game's key names; match them to report rows |
| [shaper.py](shaper.py) | walk the template, filling in measured values |
"""

from slotdebugger.report_formatter.shaper import Shaper, apply
from slotdebugger.report_formatter.template import NAME, find, load

__all__ = ["NAME", "Shaper", "apply", "find", "load"]
