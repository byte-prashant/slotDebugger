"""Find what is wrong, or unproven, in a generated `aggregate.json`.

The generator reads code and report names, so it can be wrong in ways only a reader of
the game's code can settle. This turns those doubts into a list an LLM (or a person)
can work through, each with what to do about it:

    for f in check(doc): print(f["severity"], f["code"], f["component"], f["message"])

`error` findings mean the numbers cannot be trusted; `warning` means something is
unexplained. Nothing here changes the document.
"""

from typing import Any, Dict, List

from rtp_aggregator.builder import compute
from rtp_aggregator.errors import AggregatorError

TOLERANCE = 0.001  # the analyzer's own tolerance for parent == sum of children


def check(doc: Dict) -> List[Dict[str, Any]]:
    """Findings for one aggregate document; empty when nothing is in doubt."""
    try:
        values = compute(doc)
    except AggregatorError as e:
        return [_f("error", "invalid", None, str(e),
                   "Fix the formula the message names; never edit the inputs file to make it pass.")]

    found: List[Dict[str, Any]] = []
    components = doc.get("components") or {}
    for name in components:
        if not name.endswith(".children_sum"):
            continue
        parent = name[: -len(".children_sum")]
        stated = values.get(f"{parent}.rtp")
        if stated is not None and abs(values[name] - stated) > TOLERANCE:
            found.append(_f(
                "error", "total_mismatch", parent,
                f"children sum to {values[name]:.6f} but the report states {stated:.6f}",
                "Either a child is missing or wrongly included in children_sum, or the report's "
                "total really is wrong (that is the bug being hunted: do not 'fix' it away)."))

    derived = (doc.get("derived_from") or {})
    for parent, dep in (derived.get("dependencies") or {}).items():
        if not dep.get("complete"):
            found.append(_f(
                "warning", "incomplete_total", parent,
                f"engine says it depends on {dep.get('depends_on')}; not all have an event and "
                "row, or it is not a plain sum, so no children_sum was written",
                "Read engine.py and volume_tester.py for what feeds this field; add children_sum "
                "only if the missing parts are provably zero or provably absent from the report."))
    if derived.get("scope"):
        found.append(_f("warning", "unscoped", None, derived["scope"],
                        "Check the expected report's keys against the report's rows."))

    vt = doc.get("volume_tester") or {}
    scoped = {n.rsplit(".", 1)[0] for n in components}
    for pattern in vt.get("unmatched_events") or []:
        found.append(_f("warning", "unmatched_event", pattern,
                        "the volume tester emits this event but no report row matches it",
                        "Check whether the report names it differently (then the row's event is "
                        "mislabelled) or the event is never reached for this report."))
    for row in vt.get("unmatched_components") or []:
        if row in scoped:
            found.append(_f("warning", "unexplained_row", row,
                            "no volume-tester event produces this report row",
                            "Find where the game or tester emits it before trusting its formula."))
    return found


def _f(severity: str, code: str, component: Any, message: str, fix: str) -> Dict[str, Any]:
    return {"severity": severity, "code": code, "component": component,
            "message": message, "fix": fix}
