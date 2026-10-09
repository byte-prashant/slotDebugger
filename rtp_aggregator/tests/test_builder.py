"""aggregate.json: built from a report's analysis and a volume tester's events."""

import pytest

from rtp_aggregator import Event, build, compute, key, parse, scan_volume_tester

COMPONENTS = [
    {"name": "base_line_win", "rtp": 30.0, "frequency": 40},
    {"name": "base_bonus_win", "rtp": 10.0, "frequency": 10},
    {"name": "base_total_win", "rtp": 40.0, "frequency": 50},
]
META = {"total_plays": 100, "total_game_win": 100.0}
GRAPH = {"base_total_win": {"children": ["base_line_win", "base_bonus_win"]}}


def doc(**kw):
    return build(COMPONENTS, META, GRAPH, overall_rtp=0.9632, **kw)


TESTER = '''
class T(SlotVolumeTester):
    def dump_map_result(self, p, action="spin"):
        self.dump_kv("STAKE", p.stake)
        self.dump_event("action_total_win", 1.0)
        self.dump_event("action_" + action + "_ways_win_" + p.i, 1.0)
        self.dump_event(f"{action}_scatter", 1.0)
        self.dump_event("fs_%s_win" % action, 1.0)
        self.dump_event("{}_cash".format(action), 1.0)
        self.dump_event(p.name, 1.0)
'''


def test_scan_reads_event_names_as_patterns(tmp_path):
    f = tmp_path / "volume_tester.py"
    f.write_text(TESTER)
    assert [e.pattern for e in scan_volume_tester(str(f))] == [
        "action_total_win", "action_*_ways_win_*", "*_scatter", "fs_*_win", "*_cash"]


def test_components_are_formulas_over_the_reports_numbers():
    values = compute(doc())
    assert values["base_line_win.rtp"] == pytest.approx(0.3)
    assert values["base_line_win.hit_rate"] == pytest.approx(0.4)
    assert values["base_line_win.rtp_vs_stake"] == pytest.approx(0.3 * 0.9632)


def test_parent_total_is_computed_as_sum_of_children():
    values = compute(doc())
    assert values["base_total_win.children_sum"] == pytest.approx(0.4)
    assert values["base_total_win.children_sum"] == pytest.approx(values["base_total_win.rtp"])


def test_changing_a_formula_changes_the_result():
    d = doc()
    d["components"]["base_line_win.rtp"]["formula"]["args"][1] = {"op": "input", "name": "total_plays"}
    assert compute(d)["base_line_win.rtp"] == pytest.approx(0.3)  # 30 / 100 plays
    d["inputs"]["total_plays"] = 200
    assert compute(d)["base_line_win.rtp"] == pytest.approx(0.15)


def test_normalizer_takes_its_values_from_the_aggregate():
    from RTPNormalizer.rtpnormalizer import RTPNormalizer
    d = doc()
    d["inputs"]["total_game_win"] = 200.0  # an edit to the aggregate, not to the report
    values = compute(d)
    measured = {c["name"]: {"rtp": values[key(c["name"], "rtp")],
                            "hit_rate": values[key(c["name"], "hit_rate")]} for c in COMPONENTS}
    out = RTPNormalizer(COMPONENTS, META, GRAPH, measured).run()["normalized_components"]
    assert out["base_line_win"]["rtp"] == 0.15
    assert RTPNormalizer(COMPONENTS, META, GRAPH).run()["normalized_components"][
        "base_line_win"]["rtp"] == 0.3


def test_document_is_a_valid_spec_and_cross_checks_the_volume_tester():
    d = doc(events=[Event("base_*_win"), Event("free_*")])
    parse(d)
    assert d["components"]["base_line_win.rtp"]["event"] == "base_*_win"
    assert d["volume_tester"]["unmatched_events"] == ["free_*"]
    assert d["volume_tester"]["unmatched_components"] == []
