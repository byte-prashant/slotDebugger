"""Write `aggregate.json` for one report: the formulas the normalizer computes components with.

    doc = build(report["components"], metadata, graph, events=scan_volume_tester(path))
    values = compute(doc)

The document is a normal spec (see the package docs) plus keys the parser carries
and ignores. For every report row `<n>` it declares

    <n>.rtp            win / total game win       (share of total win)
    <n>.hit_rate       frequency / total plays
    <n>.rtp_vs_stake   share x overall RTP        (the game's RTP scale)

and for every parent in the dependency graph `<parent>.children_sum`, the sum of its
children's shares, to be compared with the parent's own `.rtp`. The measured numbers
live under `inputs`; on disk they are kept apart from the formulas (`split`).

The **volume tester and engine** (see `sources.py`) say how the totals are built:
the tester names the events and the result field each reports, the engine says how that
field is computed from others. When they are given, that is the dependency graph; a
total whose parts are not all observed gets no `children_sum`. A pattern no report row
matches, or a row no pattern explains, is listed under `volume_tester`.
"""

from typing import Any, Dict, Iterable, List, Optional

from rtp_aggregator.evaluator import evaluate
from rtp_aggregator.sources import Event
from rtp_aggregator.spec import SCHEMA_VERSION, parse

SHARE, RATE, RTP = "share_of_win", "rate", "rtp_fraction"


def key(name: str, what: str) -> str:
    """Name of a component in the document: `key("base_total_win", "rtp")`."""
    return f"{name}.{what}"


def build(components: List[Dict], metadata: Dict, graph: Optional[Dict] = None,
          overall_rtp: Optional[float] = None, events: Iterable[Event] = (), game: Optional[str] = None,
          report: Optional[str] = None, volume_tester: Optional[str] = None,
          only: Optional[Iterable[str]] = None,
          derived_from: Optional[Dict] = None) -> Dict:
    """The aggregate document for parsed `components` (`name`, `rtp` = win, `frequency`).

    Every report row is listed under `inputs`. With `only`, the formulas
    (`components`) cover just those rows and whatever they depend on (a parent's
    children, all the way down).

    `metadata` carries `total_plays` and `total_game_win`; `graph` maps a parent to
    `{"children": [...]}` and optionally `"complete": False` for a parent whose
    children are known not to add up to it (no `children_sum` is written for those).
    """
    events = list(events)
    names = [c["name"] for c in components]
    scoped = components
    if only is not None:
        keep = dependencies(set(only), graph or {})
        scoped = [c for c in components if c["name"] in keep]
    inputs: Dict[str, Any] = {
        "total_plays": metadata["total_plays"],
        "total_game_win": metadata["total_game_win"],
    }
    if overall_rtp is not None:  # only what the report states; no RTP, no RTP-scaled formulas
        inputs["overall_rtp"] = overall_rtp
    for c in components:  # every report row is an input, whether or not a formula uses it
        inputs[key(c["name"], "win")] = c["rtp"]
        inputs[key(c["name"], "frequency")] = c["frequency"]
    spec: Dict[str, Any] = {}
    for c in scoped:
        n = c["name"]
        share = {"unit": SHARE, "formula": {"op": "divide", "args": [
            {"op": "input", "name": key(n, "win")}, {"op": "input", "name": "total_game_win"}]}}
        event = _event_for(n, events)
        if event:
            share["event"] = event.pattern
            if event.key:
                share["result_field"] = event.key
        spec[key(n, "rtp")] = share
        spec[key(n, "hit_rate")] = {"unit": RATE, "formula": {"op": "divide", "args": [
            {"op": "input", "name": key(n, "frequency")}, {"op": "input", "name": "total_plays"}]}}
        if overall_rtp is not None:
            spec[key(n, "rtp_vs_stake")] = {"unit": RTP, "formula": {"op": "multiply", "args": [
                {"ref": key(n, "rtp")}, {"op": "input", "name": "overall_rtp"}]}}
    if overall_rtp is not None:
        spec["total_rtp"] = {"unit": RTP, "formula": {"op": "input", "name": "overall_rtp"}}
    scoped_names = {c["name"] for c in scoped}
    for parent, data in (graph or {}).items():
        children = [c for c in data["children"] if c in names]
        if parent in scoped_names and children and data.get("complete", True):
            spec[key(parent, "children_sum")] = {
                "unit": SHARE,
                "category": "children_sum",
                "formula": {"op": "add", "args": [{"ref": key(c, "rtp")} for c in children]},
            }

    doc = {
        "schema_version": SCHEMA_VERSION,
        "game": game,
        "source": {"report": report, "volume_tester": volume_tester},
        "inputs": inputs,
        "components": spec,
    }
    if derived_from:
        doc["derived_from"] = derived_from
    if events:
        doc["volume_tester"] = {
            "events": [{"pattern": e.pattern, "result_field": e.key} for e in events],
            "unmatched_events": [e.pattern for e in events if not any(e.matches(n) for n in names)],
            "unmatched_components": [n for n in names if not _event_for(n, events)],
        }
    parse(doc)  # a document that cannot be read back must not be written
    return doc


def dependencies(names: set, graph: Dict) -> set:
    """`names` plus every component their formulas read: children, recursively."""
    found, todo = set(), list(names)
    while todo:
        n = todo.pop()
        if n not in found:
            found.add(n)
            todo.extend((graph.get(n) or {}).get("children", []))
    return found


def split(doc: Dict) -> tuple:
    """`(formulas, inputs)`: the document as the two files it is stored in.

    The formulas file is what gets reviewed and corrected; the inputs file is the
    report's numbers and is regenerated from the report every time.
    """
    formulas = {k: v for k, v in doc.items() if k != "inputs"}
    inputs = {"schema_version": doc["schema_version"], "source": doc.get("source"),
              "inputs": doc["inputs"]}
    return formulas, inputs


def join(formulas: Dict, inputs: Dict) -> Dict:
    """The in-memory document again, from the two stored files."""
    return {**formulas, "inputs": inputs.get("inputs") or {}}


def compute(doc: Dict) -> Dict[str, float]:
    """Every component of an aggregate document, from its own recorded inputs."""
    return evaluate(parse(doc), doc.get("inputs") or {})


def overall_rtp(meta: Dict) -> Optional[float]:
    """The overall RTP the report itself states, as a fraction of stake; None if it states none.

    Never derived: `paid / staked` is only as good as the stake column, which this
    codebase has already mis-mapped once, so a report without a stated RTP gets no
    RTP-scaled values rather than a plausible guess.
    """
    for field in ("Total RTP", "Game RTP"):
        value = _number(meta.get(field))
        if value and value > 0:
            return value / 100 if value > 1.5 else value
    return None


# ---- helpers ----

def _event_for(name: str, events: List[Event]) -> Optional[Event]:
    return next((e for e in events if e.matches(name)), None)


def _number(value: Any) -> Optional[float]:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
