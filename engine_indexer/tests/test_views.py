"""Leftover detections, loop-item changes, and the views/summaries read from the maps."""
import json

import pytest

from engine_indexer import engine_map, views
from engine_indexer.tests.test_engine_map import make_game

LEFTOVERS = '''
from fw import SlotBase


class GameEngine(SlotBase):
    def play(self, request):
        stake_per_line = 1,
        total = 0
        stops = self.spin()
        stops = [1, 2, 3]
        wins = []
        for s in stops:
            wins.append(s * 2)
        wins = []
        mode = self.pick() > 0
        mode = False
        boosted = self.boost(wins, 7)
        return {'current_winnings': sum(wins) + total + boosted, 'mode': mode, 'stake': stake_per_line}

    def spin(self):
        return [0, 0, 0]

    def pick(self):
        return 1

    def boost(self, lines, factor):
        for line in lines:
            line['win'] *= factor
        return lines
'''


@pytest.fixture
def leftovers(tmp_path):
    engine, framework = make_game(tmp_path)
    engine.write_text(LEFTOVERS)
    return engine_map.build(str(engine), keys=["current_winnings", "mode", "stake"], paths=[str(framework)])


def line(needle):
    return next(i for i, l in enumerate(LEFTOVERS.splitlines(), 1) if needle in l)


def test_leftovers_are_detected(leftovers):
    found = {(d["kind"], d["line"]) for d in leftovers["detections"]}
    assert ("trailing_comma_tuple", line("stake_per_line = 1,")) in found
    assert ("overwritten_by_literal", line("stops = [1, 2, 3]")) in found   # computed, then fixed
    reset = [d for d in leftovers["detections"] if d["variable"] == "wins"]
    assert [d["line"] for d in reset] == [max(i for i, l in enumerate(LEFTOVERS.splitlines(), 1)
                                                if l.strip() == "wins = []")]  # the reset, not the first
    assert ("overwritten_by_literal", line("mode = False")) in found
    assert not any(d["variable"] == "total" for d in leftovers["detections"])  # a first assignment


def test_changes_through_a_loop_variable_change_the_collection(leftovers):
    params = {n["label"] for n in leftovers["nodes"].values()
              if n["kind"] == "param" and n["function"] == "GameEngine.boost"}
    assert params == {"lines", "factor"}  # `line['win'] *= factor` changes an item of lines
    assert any("(an item of lines)" in n["label"] for n in leftovers["nodes"].values())


def test_problems_rank_and_cite(leftovers):
    found = views.problems(views.Maps(engine=leftovers))
    kinds = [p["kind"] for p in found]
    assert kinds.count("overwritten_by_literal") == 3 and "trailing_comma_tuple" in kinds
    assert all(p["severity"] == "high" for p in found[:4])
    assert all(":" in p["where"] for p in found if p["kind"] == "overwritten_by_literal")


def test_method_view(leftovers):
    text = views.method(views.Maps(engine=leftovers), "boost")
    assert "engine.GameEngine.boost  (game)" in text
    assert "parameters the traced result depends on: factor, lines" in text
    framework = views.method(views.Maps(engine=leftovers), "check_cap")
    assert "fw.core.CoreEngine.check_cap  (framework)" in framework and "not on the traced path" in framework


def test_class_view_shows_where_each_method_comes_from(leftovers):
    text = views.classes(views.Maps(engine=leftovers), "GameEngine")
    assert "check_cap" in text and "fw.core.CoreEngine" in text
    overview = views.classes(views.Maps(engine=leftovers))
    assert "0. engine.GameEngine  (game" in overview and "fw.core.CoreEngine  (framework" in overview


def test_find(leftovers):
    found = views.find(views.Maps(engine=leftovers), "factor")
    assert "line['win'] *= factor" in found
    # `stops` only feeds `wins`, which is reset to [] before use: not on any traced path
    assert views.find(views.Maps(engine=leftovers), "stops").startswith("0 node(s)")
    assert "only code the response keys depend on" in views.find(views.Maps(engine=leftovers), "nothing_here")


def test_summaries_are_written(leftovers, tmp_path):
    written = views.write_summaries(views.Maps(engine=leftovers), str(tmp_path / "r"))
    assert [p.rsplit("/", 1)[-1] for p in written] == ["r.summary.md", "r.engine_summary.md"]
    summary = (tmp_path / "r.summary.md").read_text()
    assert "## Problems (" in summary and "overwritten_by_literal" in summary
    engine_md = (tmp_path / "r.engine_summary.md").read_text()
    assert "## Class hierarchy" in engine_md and "### `current_winnings`" in engine_md
    assert "## Suspicious assignments" in engine_md


def test_views_need_their_maps():
    with pytest.raises(ValueError, match="run `slotdebug trace-tester` first"):
        views.tree(views.Maps(), row="x")
