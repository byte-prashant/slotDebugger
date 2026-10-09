"""parse -> normalize -> analyze for a single RTP report."""

from typing import Dict

from RTPNormalizer.rtpnormalizer import RTPAnalyzer, RTPNormalizer
from report_parser.parser import ReaderFactory, RTPService, MetadataMapper


def analyze(file_path: str) -> Dict:
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

    normalized = RTPNormalizer(report["components"], metadata).run()
    analysis = RTPAnalyzer(normalized).run()
    return {
        "metadata": normalized_meta,
        "components": normalized["normalized_components"],
        "dependency_graph": normalized["dependency_graph"],
        "analysis": analysis,
    }
