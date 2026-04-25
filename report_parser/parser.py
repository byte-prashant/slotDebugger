#!/usr/bin/env python3
"""
RTP REPORT READER (CSV + XLSX) + METADATA + COMPONENT EXTRACTION
Production-ready, SOLID-compliant
"""

import csv
import os
from abc import ABC, abstractmethod
from typing import List, Dict, Tuple

try:
    import openpyxl
except ImportError:
    openpyxl = None


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

        if "." in first:
            rtp = float(first)
            freq = int(float(second))
        else:
            freq = int(float(first))
            rtp = float(second)

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

        for row in rows:
            if not row or len(row) < 2:
                continue

            key = str(row[0]).strip()
            value = str(row[1]).strip()

            # Capture metadata
            if key in self.METADATA_KEYS:
                report.metadata[key] = value
                continue

            # Start capturing components
            if key.lower() == "event":
                capture_components = True
                continue

            # Stop at RTP sections
            if key.startswith("RTP ("):
                break

            if capture_components:
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
        with open(file_path, "r") as f:
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
        wb = openpyxl.load_workbook(file_path)
        sheet = wb.active

        rows = []
        for row in sheet.iter_rows(values_only=True):
            rows.append([str(cell) if cell else "" for cell in row])

        return self.parser.parse(rows)


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
        report = self.reader.read(file_path)
        print(report)
        return report.to_dict()


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
