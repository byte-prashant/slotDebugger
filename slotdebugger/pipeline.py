"""parse -> normalize -> analyze for a single RTP report."""

from typing import Any, Dict, Optional

import rtp_aggregator
from slotdebugger import report_formatter
from RTPNormalizer.rtpnormalizer import RTPNormalizer
from report_parser.parser import ReaderFactory, RTPService, MetadataMapper


def analyze(file_path: str, volume_tester: Optional[str] = None, game: Optional[str] = None,
            expected: Any = None, engine: Optional[str] = None,
            reviewed: Optional[Dict] = None, fixed: Optional[Dict] = None) -> Dict:
    """parse -> build aggregate.json -> evaluate it -> normalize -> analyze.

    Every number in the result is an aggregate formula evaluated; nothing is worked out
    by assuming what a row name or a report column means. Component RTPs and hit rates
    are the aggregate's `<row>.rtp` / `<row>.hit_rate`, `analysis.total_rtp` is its
    `total_rtp` (the RTP the report states), and `analysis.issues` are its
    `<parent>.children_sum` formulas that disagree with the parent's own `.rtp`.
    Rows the aggregate does not cover are not in `components`, and without an engine
    and volume tester there are no dependencies, so no totals are checked. The document itself is returned as `result["aggregate"]["document"]`
    for the caller to save; `components` holds every value the formulas produced.

    With an `expected` template (the game's `expected_rtp_report.json`, loaded), the
    aggregate holds only the rows that template mentions and what they depend on.
    A `fixed` aggregate (formulas the caller supplied, already holding content) is used
    exactly as given and never replaced: its formulas are evaluated against this report's inputs, and if
    they do not evaluate that is an error, not a reason to regenerate. Status `provided`.
    A `reviewed` aggregate (the formulas file an LLM or person corrected and marked
    `"reviewed": true`) replaces the generated formulas when it still evaluates against
    this report's inputs; the
    result's `aggregate.status` says which was used (`generated`, `reviewed`, or
    `reviewed-stale` when the report changed and the reviewed file was set aside).
    With a `volume_tester` and `engine`, "depends on" is read from them (what the
    engine adds up to make each result field); otherwise it is inferred from row names.
    """
    report = RTPService(ReaderFactory.get_reader(file_path)).process(file_path)
    meta = report["metadata"]
    
    # Normalize metadata field names (handle aliases)
    normalized_meta = MetadataMapper.normalize_metadata(meta)
    
    try:
        metadata = {
            "total_plays": int(float(normalized_meta["Total number of plays"])),
            "total_game_win": float(normalized_meta["Total amount paid"]),
        }
    except KeyError as e:
        # Show helpful error with available fields
        available = ", ".join(sorted(normalized_meta.keys()))
        raise ValueError(
            f"report is missing metadata field {e}. "
            f"Available fields: {available}"
        ) from None
    if not report["components"]:
        raise ValueError("report contains no components")

    graph = {}  # parent -> children; known only from the engine and volume tester
    names = [c["name"] for c in report["components"]]
    events = rtp_aggregator.scan_volume_tester(volume_tester) if volume_tester else []
    derived = None
    if events and engine:
        graph = rtp_aggregator.derive_graph(events, rtp_aggregator.scan_engine(engine), names)
        derived = {"engine": engine, "dependencies": {
            parent: {"depends_on": g["depends_on"], "complete": g["complete"]}
            for parent, g in graph.items()}}
    only = (report_formatter.matched_components(expected, names, normalized_meta)
            if expected is not None else None)
    if expected is not None and not only:  # nothing to scope to; the shaper leaves those keys null
        only = None
        derived = {**(derived or {}),
                   "scope": "expected report matched no report rows; aggregate covers every row"}
    doc = rtp_aggregator.build(
        report["components"], metadata, graph, events=events, game=game,
        overall_rtp=rtp_aggregator.overall_rtp(normalized_meta),
        report=file_path.rsplit("/", 1)[-1], volume_tester=volume_tester, only=only,
        derived_from=derived)
    status = "generated"
    if fixed is not None:
        doc, status = {**fixed, "inputs": doc["inputs"]}, "provided"
    elif reviewed is not None:
        # The reviewed file holds formulas only; it is kept while it still evaluates
        # against this report's own inputs (same rows), whatever the numbers are now.
        candidate = {**reviewed, "inputs": doc["inputs"]}
        try:
            rtp_aggregator.compute(candidate)
            doc, status = candidate, "reviewed"
        except rtp_aggregator.AggregatorError:
            status = "reviewed-stale"
    values = rtp_aggregator.compute(doc)
    measured = {
        c["name"]: {"rtp": values[rtp_aggregator.key(c["name"], "rtp")],
                    "hit_rate": values[rtp_aggregator.key(c["name"], "hit_rate")]}
        for c in report["components"]
        if rtp_aggregator.key(c["name"], "rtp") in values
        and rtp_aggregator.key(c["name"], "hit_rate") in values
    }

    normalized = RTPNormalizer([c for c in report["components"] if c["name"] in measured],
                               metadata, graph, measured).run()
    issues = [f"Mismatch in {f['component']}" for f in rtp_aggregator.check(doc)
              if f["code"] == "total_mismatch"]
    total = values.get("total_rtp")
    return {
        "aggregate": {"document": doc, "status": status, "components": values,
                      "volume_tester": doc.get("volume_tester")},
        "metadata": normalized_meta,
        "rows": names,
        "components": normalized["normalized_components"],
        "dependency_graph": normalized["dependency_graph"],
        "analysis": {"status": "OK" if not issues else "FAIL", "issues": issues,
                     "total_rtp": None if total is None else round(total, 6)},
    }
