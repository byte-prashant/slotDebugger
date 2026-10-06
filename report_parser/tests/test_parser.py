"""
# Core dependency for Excel support
openpyxl>=3.1.0

# (Optional - future use)
# pandas>=2.0.0
# numpy>=1.24.0
"""
from report_parser.parser import ReaderFactory, RTPReport, RTPService, RTPValueParser

# ==============================
# 🧪 TEST CASES (UNIT TESTS)
# ==============================
import os
import unittest
import tempfile


SAMPLE_INPUT = """Engine Name	blazing-7s-cashway
Engine Version	version-unknown
Total number of plays	100000000
Plays with stake	100000000
Total amount staked	100000000.0000
Total amount paid	93964971.0000
Largest win	1800.0000
Game RTP	93.9650
Jackpot RTP	0.0000
Total RTP	93.9650
Win standard deviation	4.9451
Win Hit Rate	3.2474
Events	
Event	Miscellaneous
base_bonus_win	(34892496.9000, 94967255)
base_line_win	(39105990.6000, 94967255)
base_total_win	(73998487.5000, 94967255)
ultraboost_cash_win	(18078533.5000, 5032745)
ultraboost_total_win	(19966483.5000, 5032745)
ultraboost_line_win	(1887950.0000, 5032745)
Round statistics	10000000 plays
Standard deviation	0.1484
RTP (0 - 10000000)	93.9190
"""


class TestRTPReader(unittest.TestCase):

    def setUp(self):
        self.temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
        self.temp_file.write(SAMPLE_INPUT.encode())
        self.temp_file.close()

    def tearDown(self):
        os.remove(self.temp_file.name)

    def test_metadata_extraction(self):
        reader = ReaderFactory.get_reader(self.temp_file.name)
        service = RTPService(reader)

        result = service.process(self.temp_file.name)

        self.assertEqual(result["metadata"]["Engine Name"], "blazing-7s-cashway")
        self.assertEqual(result["metadata"]["Total RTP"], "93.9650")

    def test_component_extraction(self):
        reader = ReaderFactory.get_reader(self.temp_file.name)
        service = RTPService(reader)

        result = service.process(self.temp_file.name)
        components = result["components"]

        self.assertTrue(len(components) > 0)

        names = [c["name"] for c in components]
        self.assertIn("base_bonus_win", names)
        self.assertIn("ultraboost_total_win", names)

    def test_tuple_parsing(self):
        from report_parser.parser import RTPValueParser
        parser = RTPValueParser()

        rtp, freq = parser.parse("(34892496.9000, 94967255)")

        self.assertEqual(freq, 94967255)
        self.assertAlmostEqual(rtp, 34892496.9)

    def test_stop_at_rtp_section(self):
        reader = ReaderFactory.get_reader(self.temp_file.name)
        service = RTPService(reader)

        result = service.process(self.temp_file.name)
        components = result["components"]

        # Ensure RTP rows are not included
        names = [c["name"] for c in components]
        self.assertNotIn("RTP (0 - 10000000)", names)

    def test_tuple_parsing_integer_win_keeps_win_freq_order(self):
        rtp, freq = RTPValueParser().parse("(100, 5)")
        self.assertEqual((rtp, freq), (100.0, 5))

    def test_tuple_parsing_reversed_order(self):
        rtp, freq = RTPValueParser().parse("(5, 100.5)")
        self.assertEqual((rtp, freq), (100.5, 5))

    def test_no_stdout_noise(self):
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            RTPService(ReaderFactory.get_reader(self.temp_file.name)).process(self.temp_file.name)
        self.assertEqual(buf.getvalue(), "")


class TestXLSXReader(unittest.TestCase):

    def test_xlsx_matches_csv_and_keeps_zero(self):
        openpyxl = __import__("pytest").importorskip("openpyxl")
        wb = openpyxl.Workbook()
        ws = wb.active
        for line in SAMPLE_INPUT.splitlines():
            ws.append(line.split("\t"))
        ws.append([])
        path = tempfile.mktemp(suffix=".xlsx")
        try:
            wb.save(path)
            result = RTPService(ReaderFactory.get_reader(path)).process(path)
        finally:
            os.remove(path)
        self.assertEqual(result["metadata"]["Engine Name"], "blazing-7s-cashway")
        self.assertEqual(len(result["components"]), 6)

    def test_zero_numeric_cell_not_dropped(self):
        openpyxl = __import__("pytest").importorskip("openpyxl")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Jackpot RTP", 0])
        path = tempfile.mktemp(suffix=".xlsx")
        try:
            wb.save(path)
            result = RTPService(ReaderFactory.get_reader(path)).process(path)
        finally:
            os.remove(path)
        self.assertEqual(result["metadata"]["Jackpot RTP"], "0")


if __name__ == "__main__":
    unittest.main()
