"""Compare the shaped analysis with the game's expected report, key by key.

    diff(expected, actual) -> {"tolerance", "summary", "diffs"}

Both documents have the template's shape (`analyze` guarantees that), so each number
in the expected report is looked up at the same path in the actual one. A list
element that names its component (`{"name": "FG1", ...}`) is labelled by that name,
so a diff reads `features.FG1.rtp`, not `features.0.rtp`.

Each number gets a status:

- `ok`        within tolerance
- `mismatch`  measured, but further off than the tolerance
- `missing`   the analysis left it null: nothing in the report answered the key

The tolerance is in the template's own scale: RTP to 0.001 of a stake (0.1 points
when stated in percent, 0.001 as a fraction), shares and hit rates to 0.001, bets and
play counts exactly.
"""

from typing import Any, Dict, List, Optional

from slotdebugger.report_formatter import naming, template as tpl

RTP_TOLERANCE = 0.001  # of a stake; multiplied by the template's scale
RATIO_TOLERANCE = 0.001  # shares and hit rates are fractions whatever the scale
STATUSES = ("ok", "mismatch", "missing")


def diff(expected: Any, actual: Any, tolerance: Optional[float] = None) -> Dict:
    """Every number in `expected`, compared with the value at the same path in `actual`.

    `tolerance` overrides the RTP tolerance (in the template's scale).
    """
    rtp_tol = RTP_TOLERANCE * tpl.scale(expected) if tolerance is None else tolerance
    diffs: List[Dict] = []
    _walk(expected, actual, [], diffs, rtp_tol)
    summary = {s: sum(d["status"] == s for d in diffs) for s in STATUSES}
    return {"tolerance": rtp_tol, "summary": summary, "diffs": diffs}


def open_keys(result: Dict) -> List[str]:
    """Keys still to explain: every one that is not `ok`."""
    return [d["key"] for d in result["diffs"] if d["status"] != "ok"]


def _walk(exp: Any, act: Any, path: List[str], out: List[Dict], rtp_tol: float) -> None:
    if isinstance(exp, dict):
        act = act if isinstance(act, dict) else {}
        for key, value in exp.items():
            _walk(value, act.get(key), path + [str(key)], out, rtp_tol)
    elif isinstance(exp, list):
        act = act if isinstance(act, list) else []
        for i, value in enumerate(exp):
            _walk(value, act[i] if i < len(act) else None, path + [_element(value, i)], out, rtp_tol)
    elif _is_number(exp):
        tol = _tolerance(path[-1] if path else "", rtp_tol)
        entry = {"key": ".".join(path) or "<root>", "expected": exp,
                 "actual": act if _is_number(act) else None, "tolerance": tol}
        if entry["actual"] is None:
            entry.update(delta=None, status="missing")
        else:
            delta = round(act - exp, 9)
            entry.update(delta=delta, status="ok" if abs(delta) <= tol + 1e-12 else "mismatch")
        out.append(entry)


def _element(value: Any, index: int) -> str:
    if isinstance(value, dict):
        for key, v in value.items():
            if naming.is_identifier(key) and isinstance(v, str):
                return v
    return str(index)


def _tolerance(key: str, rtp_tol: float) -> float:
    if naming.role(key) in ("bet", "plays"):
        return 0.0
    if naming.attribute(key) in ("share", "hit_rate"):
        return RATIO_TOLERANCE
    return rtp_tol


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
