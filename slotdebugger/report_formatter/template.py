"""Finding the game's expected report, and reading the conventions it is written in.

The file itself is the specification: whether RTP is stated as `96.32` or `0.9632`,
and to how many decimals, is read off its numbers rather than configured.
"""

import json
import os
from typing import Any, List, Optional

from slotdebugger.report_formatter import naming

NAME = "expected_rtp_report.json"

PERCENT = 100.0
FRACTION = 1.0

# A value above this can only be a percentage; RTP as a fraction is ~0.96.
PERCENT_THRESHOLD = 1.5


def find(*dirs: str) -> Optional[str]:
    """First `expected_rtp_report.json` found in `dirs`, in order."""
    for d in dirs:
        if not d:
            continue
        candidate = os.path.join(d, NAME)
        if os.path.isfile(candidate):
            return candidate
    return None


def load(path: str) -> Any:
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"{path}: not valid JSON ({e})") from None


def scale(template: Any) -> float:
    """`PERCENT` if the template states RTP as 96.32, `FRACTION` if as 0.9632."""
    found: List[float] = []
    _numbers(template, found)
    return PERCENT if any(n > PERCENT_THRESHOLD for n in found) else FRACTION


def default_places(scale_: float) -> int:
    return 2 if scale_ == PERCENT else 6


def places(value: Any, default: int) -> int:
    """Decimals to report a value to: as many as the template states it with."""
    text = repr(value) if isinstance(value, float) else ""
    if "." in text and "e" not in text:
        return min(len(text.split(".")[1]), 6)
    return default


def _numbers(node: Any, out: List[float], key: Optional[str] = None) -> None:
    """Every number in the template that is an RTP, so the scale can be read off it."""
    if isinstance(node, dict):
        for k, v in node.items():
            _numbers(v, out, k)
    elif isinstance(node, list):
        for v in node:
            _numbers(v, out, key)
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        if naming.role(key or "") not in ("bet", "plays"):  # counts and money are not RTP
            out.append(float(node))
