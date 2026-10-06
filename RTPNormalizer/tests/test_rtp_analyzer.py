import unittest

from RTPNormalizer.rtpnormalizer import RTPDependencyResolver, RTPNormalizer, RTPAnalyzer


class TestRTPAnalyzer(unittest.TestCase):
    COMPONENTS = [
        {"name": "base_bonus_win", "rtp": 34892496.9, "frequency": 94967255},
        {"name": "base_line_win", "rtp": 39105990.6, "frequency": 94967255},
        {"name": "base_total_win", "rtp": 73998487.5, "frequency": 94967255},
        {"name": "ultraboost_cash_win", "rtp": 18078533.5, "frequency": 5032745},
        {"name": "ultraboost_total_win", "rtp": 19966483.5, "frequency": 5032745},
        {"name": "ultraboost_line_win", "rtp": 1887950.0, "frequency": 5032745}
    ]

    def setUp(self):
        self.components = self.COMPONENTS
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


class TestDependencyGraph(unittest.TestCase):
    def test_graph_links_children_to_totals(self):
        comps = TestRTPAnalyzer.COMPONENTS
        graph = RTPDependencyResolver(comps).build_dependency_graph()
        self.assertEqual(set(graph["base_total_win"]["children"]),
                         {"base_bonus_win", "base_line_win"})
        self.assertEqual(set(graph["ultraboost_total_win"]["children"]),
                         {"ultraboost_cash_win", "ultraboost_line_win"})

    def test_totals_are_not_children_of_each_other(self):
        graph = RTPDependencyResolver(TestRTPAnalyzer.COMPONENTS).build_dependency_graph()
        for node in graph.values():
            self.assertFalse(any("_total" in c for c in node["children"]))


class TestMismatchDetection(unittest.TestCase):
    def _analyze(self, components, metadata=None):
        metadata = metadata or {"total_plays": 100000000, "total_game_win": 93964971.0}
        return RTPAnalyzer(RTPNormalizer(components, metadata).run())

    def test_detects_parent_child_mismatch(self):
        comps = [dict(c) for c in TestRTPAnalyzer.COMPONENTS]
        # Inflate a child by ~5% of total win so the parent no longer matches
        for c in comps:
            if c["name"] == "base_bonus_win":
                c["rtp"] += 0.05 * 93964971.0
        result = self._analyze(comps).run()
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("Mismatch in base_total_win", result["issues"])
        self.assertNotIn("Mismatch in ultraboost_total_win", result["issues"])

    def test_missing_child_is_flagged(self):
        comps = [c for c in TestRTPAnalyzer.COMPONENTS if c["name"] != "ultraboost_line_win"]
        issues = self._analyze(comps).validate_totals()
        self.assertIn("Mismatch in ultraboost_total_win", issues)

    def test_normalizer_does_not_print(self):
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self._analyze(TestRTPAnalyzer.COMPONENTS)
        self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
