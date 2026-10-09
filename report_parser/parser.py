#!/usr/bin/env python3
"""
RTP REPORT READER (CSV + XLSX) + METADATA + COMPONENT EXTRACTION
Production-ready, SOLID-compliant
"""

import csv
import os
import zipfile
from abc import ABC, abstractmethod
from typing import List, Dict, Tuple
from difflib import SequenceMatcher

try:
    import openpyxl
except ImportError:
    openpyxl = None


# ==============================
# METADATA MAPPER (Normalization)
# ==============================

class MetadataMapper:
    """
    Normalizes metadata field names across different report formats.
    Handles aliases and fuzzy matching for field names with the same meaning.
    """

    # Canonical field names -> known aliases
    ALIASES = {
        "Total number of plays": {
            "Total plays",
            "Total Plays",
            "Total # plays",
            "Num plays",
            "Number of plays",
            "Play count",
            "Total number of plays",
            "TOTAL_SPINS",  # Custom format
            "Total_Spins",
            "total_spins",
        },
        "Total amount paid": {
            "Total amount paid",
            "Total paid",
            "Total Paid",
            "Total payout",
            "Total win",
            "Total winnings",
            "TOTAL_WIN",  # Custom format
            "Total_Win",
            "total_win",
        },
        "Total amount staked": {
            "Total amount staked",
            "Total staked",
            "Total Staked",
            "Total bet",
            "Total wager",
            "TOTAL_STAKE",  # Custom format
            "Total_Stake",
            "total_stake",
            "STAKE_SPINS",  # Variant
        },
        "Plays with stake": {
            "Plays with stake",
            "Plays With Stake",
            "Plays with bet",
        },
        "Engine Name": {
            "Engine Name",
            "Engine name",
            "engine",
        },
        "Engine Version": {
            "Engine Version",
            "Engine version",
            "version",
        },
        "Game RTP": {
            "Game RTP",
            "Game RTP %",
            "Game rtp",
            "RTP",
        },
        "Total RTP": {
            "Total RTP",
            "Total RTP %",
            "Total rtp",
        },
    }

    @staticmethod
    def normalize_field_name(field: str, fuzzy_threshold: float = 0.75) -> str:
        """
        Map a field name to its canonical form.
        
        Args:
            field: The field name from the report
            fuzzy_threshold: Similarity threshold for fuzzy matching (0-1)
        
        Returns:
            Canonical field name or original if no match found
        """
        field = field.strip()
        
        # Exact match in aliases
        for canonical, aliases in MetadataMapper.ALIASES.items():
            if field in aliases:
                return canonical
        
        # Fuzzy match: find closest canonical name
        best_match = None
        best_score = fuzzy_threshold
        
        for canonical in MetadataMapper.ALIASES.keys():
            score = SequenceMatcher(None, field.lower(), canonical.lower()).ratio()
            if score > best_score:
                best_score = score
                best_match = canonical
        
        if best_match:
            return best_match
        
        return field  # Return original if no match

    @staticmethod
    def normalize_metadata(metadata: Dict[str, str]) -> Dict[str, str]:
        """
        Normalize all metadata field names in a dictionary.
        
        Args:
            metadata: Dict of {field_name: value}
        
        Returns:
            Dict with normalized field names
        """
        normalized = {}
        for field, value in metadata.items():
            canonical = MetadataMapper.normalize_field_name(field)
            if canonical in normalized and canonical != field:
                # Keep original if already exists to avoid silent overwrites
                normalized[field] = value
            else:
                normalized[canonical] = value
        return normalized


# ==============================
# ENTITY
# ==============================

class RTPComponent:
    def __init__(self, name: str, rtp: float, frequency: int):
        self.name = name
        self.rtp = rtp
        self.frequency = frequency

    def to_dict(self):
        return {
            "name": self.name,
            "rtp": self.rtp,
            "frequency": self.frequency
        }


class RTPReport:
    def __init__(self):
        self.metadata: Dict[str, str] = {}
        self.components: List[RTPComponent] = []

    def to_dict(self):
        return {
            "metadata": self.metadata,
            "components": [c.to_dict() for c in self.components]
        }


# ==============================
# INTERFACE
# ==============================

class IReportReader(ABC):
    @abstractmethod
    def read(self, file_path: str) -> RTPReport:
        pass


# ==============================
# VALUE PARSER
# ==============================

class RTPValueParser:

    @staticmethod
    def parse(value: str) -> Tuple[float, int]:
        value = value.strip().replace("(", "").replace(")", "")
        parts = [p.strip() for p in value.split(",")]

        if len(parts) != 2:
            raise ValueError("Invalid tuple")

        first, second = parts

        # Reports emit (win, frequency). Only swap when the order is
        # unambiguously reversed: integer first, decimal second.
        if "." not in first and "." in second:
            freq = int(float(first))
            rtp = float(second)
        else:
            rtp = float(first)
            freq = int(float(second))

        return rtp, freq


# ==============================
# CORE PARSER
# ==============================

class BaseParser:

    METADATA_KEYS = {
        "Engine Name",
        "Engine Version",
        "Total number of plays",
        "Plays with stake",
        "Total amount staked",
        "Total amount paid",
        "Largest win",
        "Game RTP",
        "Jackpot RTP",
        "Total RTP",
        "Win standard deviation",
        "Win Hit Rate"
    }

    def __init__(self, value_parser: RTPValueParser):
        self.value_parser = value_parser

    def parse(self, rows: List[List[str]]) -> RTPReport:
        report = RTPReport()
        capture_components = False
        metadata_parsed = False

        for row in rows:
            if not row or len(row) < 2:
                continue

            key = str(row[0]).strip()
            value = str(row[1]).strip()

            # Normalize field name to canonical form (handles aliases like TOTAL_SPINS → Total number of plays)
            normalized_key = MetadataMapper.normalize_field_name(key)

            # Capture metadata
            if normalized_key in self.METADATA_KEYS:
                report.metadata[normalized_key] = value
                continue

            # Mark that we've finished normal metadata
            if normalized_key not in self.METADATA_KEYS and not metadata_parsed:
                metadata_parsed = True

            # Start capturing components
            if key.lower() == "event" or (metadata_parsed and not capture_components):
                capture_components = True
                # If this row is "event", skip it and continue
                if key.lower() == "event":
                    continue

            # Stop at RTP sections
            if key.startswith("RTP ("):
                break

            if capture_components and metadata_parsed:
                try:
                    rtp, freq = self.value_parser.parse(value)
                    report.components.append(RTPComponent(key, rtp, freq))
                except Exception:
                    continue

        return report


# ==============================
# CSV READER
# ==============================

class CSVReader(IReportReader):

    def __init__(self, parser: BaseParser):
        self.parser = parser

    def read(self, file_path: str) -> RTPReport:
        rows = []
        with open(file_path, "r", newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f, delimiter='\t')
            rows = list(reader)
        return self.parser.parse(rows)


# ==============================
# XLSX READER
# ==============================

class XLSXReader(IReportReader):

    def __init__(self, parser: BaseParser):
        if openpyxl is None:
            raise ImportError("Install openpyxl for XLSX support")
        self.parser = parser

    def read(self, file_path: str) -> RTPReport:
        try:
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
            sheet = wb.active

            rows = []
            for row in sheet.iter_rows(values_only=True):
                rows.append(["" if cell is None else str(cell) for cell in row])

            return self.parser.parse(rows)
        except (zipfile.BadZipFile, OSError) as e:
            # File is not a valid XLSX (not a zip file), try reading as CSV
            try:
                csv_reader = CSVReader(self.parser)
                return csv_reader.read(file_path)
            except Exception as csv_error:
                raise ValueError(
                    f"Failed to read '{file_path}' as XLSX: {e.__class__.__name__}. "
                    f"Attempted fallback to CSV also failed: {csv_error}"
                ) from e


# ==============================
# FACTORY
# ==============================

class ReaderFactory:

    @staticmethod
    def get_reader(file_path: str) -> IReportReader:
        parser = BaseParser(RTPValueParser())

        if file_path.endswith(".csv"):
            return CSVReader(parser)
        elif file_path.endswith(".xlsx"):
            return XLSXReader(parser)
        else:
            raise ValueError("Unsupported file")


# ==============================
# SERVICE
# ==============================

class RTPService:

    def __init__(self, reader: IReportReader):
        self.reader = reader

    def process(self, file_path: str) -> Dict:
        try:
            report = self.reader.read(file_path)
            return report.to_dict()
        except ValueError as e:
            # If XLSX reader failed and fallback to CSV didn't work,
            # try the opposite: if file ends in .xlsx, try CSV directly
            if file_path.endswith(".xlsx"):
                try:
                    csv_reader = CSVReader(BaseParser(RTPValueParser()))
                    report = csv_reader.read(file_path)
                    return report.to_dict()
                except Exception:
                    pass  # Re-raise original error below
            raise


# ==============================
# CLI EXECUTION
# ==============================

# if __name__ == "__main__":
#     import argparse
#
#     parser = argparse.ArgumentParser()
#     parser.add_argument("--file", required=True, help="Path to RTP report")
#
#     args = parser.parse_args()
#
#     reader = ReaderFactory.get_reader(args.file)
#     service = RTPService(reader)
#
#     result = service.process(args.file)
#
#     import json
#     print(json.dumps(result, indent=2))
