import unittest

from RTPNormalizer.rtpnormalizer import RTPNormalizer, RTPAnalyzer


class TestRTPAnalyzer(unittest.TestCase):
    def setUp(self):
        self.components = [
            {"name": "base_bonus_win", "rtp": 34892496.9, "frequency": 94967255},
            {"name": "base_line_win", "rtp": 39105990.6, "frequency": 94967255},
            {"name": "base_total_win", "rtp": 73998487.5, "frequency": 94967255},
            {"name": "ultraboost_cash_win", "rtp": 18078533.5, "frequency": 5032745},
            {"name": "ultraboost_total_win", "rtp": 19966483.5, "frequency": 5032745},
            {"name": "utraboosst_line_win", "rtp": 1887950.0, "frequency": 5032745}
        ]
        self.metadata = {
            "total_plays": 100000000,
            "total_game_win": 93964971.0
        }
        normalizer = RTPNormalizer(self.components, self.metadata)
        normalized_data = normalizer.run()
        self.analyzer = RTPAnalyzer(normalized_data)

    def test_total_rtp(self):
        total = self.analyzer.total_rtp()
        self.assertAlmostEqual(total, 1.0, places=3)

    def test_base_consistency(self):
        issues = self.analyzer.validate_totals()
        self.assertNotIn("Mismatch in base_total_win", issues)

    def test_ultraboost_consistency(self):
        issues = self.analyzer.validate_totals()
        self.assertNotIn("Mismatch in ultraboost_total_win", issues)

    def test_status_ok(self):
        result = self.analyzer.run()
        self.assertEqual(result["status"], "OK")

suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestRTPAnalyzer)
runner = unittest.TextTestRunner()
runner.run(suite)