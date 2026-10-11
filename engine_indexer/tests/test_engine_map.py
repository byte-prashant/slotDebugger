"""Engine index: response key -> the engine code that computes it.

A stand-in framework (`fw`, re-exporting its classes from `fw/__init__.py` as OGLE does)
and a game engine subclassing it, laid out on disk like an OGA game. Nothing is
imported or run: the index reads the files.
"""
import json
import textwrap

import pytest

from engine_indexer import engine_map

FW_INIT = "from fw.slot import SlotBase\nfrom fw.core import CoreEngine\n"

FW_CORE = '''
class CoreEngine(object):
    def check_cap(self, total, finish):
        capped = min(total, self.max_win)
        return capped, finish

    def draw(self, weights):
        return weights[0]
'''

FW_SLOT = '''
from fw.core import CoreEngine


class SlotBase(CoreEngine):
    def __init__(self):
        self.max_win = 5000
'''

DEFS = "MULTIPLIER = 3\nPAYTABLE = {'A': 10}\n"

ENGINE = '''
from fw import SlotBase
from . import defs


class GameEngine(SlotBase):
    def base_game(self, request):
        stake = request['stake']
        bonus = 0
        prizes = self.prizes(stake)
        if len(prizes) >= 3:
            for p in prizes:
                bonus += p * stake
            bonus = self.boost(prizes, bonus, stake)
        line_win = self.line_wins(stake)
        current = bonus + line_win
        total, finish = self.check_cap(current, True)
        state = {'current_winnings': current, 'total_winnings': total}
        state['bonus_win'] = bonus
        return state

    def free_game(self, request):
        win = self.line_wins(request['stake']) * defs.MULTIPLIER
        return {'current_winnings': win}

    def prizes(self, stake):
        found = []
        for symbol in self.grid:
            found.append(defs.PAYTABLE[symbol])
        return found

    def boost(self, prizes, ignored, stake):
        return sum(p * stake for p in prizes)

    def line_wins(self, stake):
        return stake * self.draw([2, 3])
'''


def make_game(tmp_path):
    fw = tmp_path / "framework/fw"
    fw.mkdir(parents=True)
    (fw / "__init__.py").write_text(FW_INIT)
    (fw / "core.py").write_text(FW_CORE)
    (fw / "slot.py").write_text(FW_SLOT)
    game = tmp_path / "game"
    game.mkdir()
    (game / "__init__.py").write_text("")
    (game / "defs.py").write_text(DEFS)
    (game / "engine.py").write_text(ENGINE)
    return game / "engine.py", tmp_path / "framework"


@pytest.fixture
def built(tmp_path):
    engine, framework = make_game(tmp_path)
    return engine_map.build(str(engine), keys=["current_winnings", "total_winnings", "bonus_win", "ways_wins"],
                            paths=[str(framework)])


def nodes_where(doc, **match):
    return [n for n in doc["nodes"].values() if all(n.get(k) == v for k, v in match.items())]


def children(doc, node):
    return [doc["nodes"][c] for c in node["children"]]


def line(text, needle):
    return next(i for i, l in enumerate(text.splitlines(), 1) if needle in l)


def test_framework_classes_resolve_through_reexports(built):
    assert built["classes"]["engine.GameEngine"] == ["engine.GameEngine", "fw.slot.SlotBase", "fw.core.CoreEngine"]
    assert built["unresolved_bases"] == []


def test_a_base_class_that_cannot_be_found_is_recorded(tmp_path):
    engine, _ = make_game(tmp_path)
    doc = engine_map.build(str(engine), keys=["current_winnings"])  # framework root not given
    assert doc["unresolved_bases"][0]["base"] == "SlotBase"


def test_each_response_carrying_the_key_is_a_root(built):
    roots = built["keys"]["current_winnings"]["roots"]
    assert [built["nodes"][r]["function"] for r in roots] == ["GameEngine.base_game", "GameEngine.free_game"]
    assert built["keys"]["ways_wins"] == {"roots": [], "status": "not_found"}
    # `state['bonus_win'] = bonus` is a root too
    assert built["nodes"][built["keys"]["bonus_win"]["roots"][0]]["line"] == line(ENGINE, "state['bonus_win']")


def test_overwritten_assignments_do_not_reach(built):
    variable = nodes_where(built, kind="variable", label="bonus", line=line(ENGINE, "current = bonus"))[0]
    reaching = sorted(n["line"] for n in children(built, variable))
    # `bonus = 0` (no bonus) and `bonus = self.boost(...)` (bonus): the `+=` inside the
    # loop is always overwritten by boost() on the same branch, so it cannot reach
    assert reaching == [line(ENGINE, "bonus = 0"), line(ENGINE, "bonus = self.boost")]
    boost = nodes_where(built, line=line(ENGINE, "bonus = self.boost"), kind="assign")[0]
    assert boost["conditions"][0]["expression"] == "len(prizes) >= 3"


def test_parameters_link_to_the_arguments_that_reach_the_result(built):
    params = {n["label"]: n for n in nodes_where(built, kind="param", function="GameEngine.boost")}
    assert set(params) == {"prizes", "stake"}  # `ignored` does not reach the return value
    assert params["prizes"]["via"]  # linked to the call site it came from


def test_in_place_appends_are_dependencies(built):
    returns = nodes_where(built, kind="returns", function="GameEngine.prizes")[0]
    ret = children(built, returns)[0]
    found = [n for n in children(built, ret)]
    labels = {n["label"] for n in found} | {c["label"] for n in found for c in children(built, n)}
    assert "found.append(defs.PAYTABLE[symbol])" in labels
    assert any(n["kind"] == "constant" and n["label"].startswith("defs.PAYTABLE") for n in built["nodes"].values())


def test_inherited_methods_and_attributes_resolve_into_the_framework(built):
    cap = nodes_where(built, kind="call", label="self.check_cap(current, True)")[0]
    assert cap["target"] == "CoreEngine.check_cap (core.py:3)"
    attr = nodes_where(built, kind="attribute", label="self.max_win")[0]
    assert [n["label"] for n in children(built, attr)] == ["self.max_win = 5000"]
    assert children(built, attr)[0]["file"].endswith("fw/slot.py")


def test_module_constants_are_followed(built):
    assert nodes_where(built, kind="constant", label="defs.MULTIPLIER = 3")


def test_depth_limit_truncates_calls(tmp_path):
    engine, framework = make_game(tmp_path)
    doc = engine_map.build(str(engine), keys=["current_winnings"], paths=[str(framework)], max_depth=0)
    assert nodes_where(doc, kind="truncated")


def test_render_shows_the_tree(built):
    text = engine_map.render(built, "current_winnings")
    assert "current_winnings  (2 place(s)" in text
    assert "assign: current = bonus + line_win" in text
    assert "when len(prizes) >= 3" in text
    assert engine_map.render(built, "ways_wins").endswith("no dict in the engine or its base classes carries this key")


def test_chain_joins_the_tester_map(tmp_path):
    engine, framework = make_game(tmp_path)
    tester_map = {
        "sites": [{"id": "t.py:5", "response_key": "current_winnings", "response_key_status": "present",
                   "keys_seen": {"total_win": 3}, "fired": True},
                  {"id": "t.py:9", "response_key": "ways_wins", "response_key_status": "never_returned",
                   "keys_seen": {}, "fired": False}],
        "report": {"rows": {"total_win": {"match": "traced", "sites": ["t.py:5"]}}},
    }
    doc = engine_map.build(str(engine), tester_map=tester_map, paths=[str(framework)])
    assert set(doc["keys"]) == {"current_winnings", "ways_wins"}  # keys taken from the tester map
    chain = doc["chain"]
    assert chain["sites"]["t.py:5"]["engine_roots"] == doc["keys"]["current_winnings"]["roots"]
    assert chain["rows"]["total_win"]["response_keys"] == ["current_winnings"]
    assert chain["keys_without_engine_root"] == ["ways_wins"]
    json.dumps(doc)  # serialisable as written to disk


def test_framework_imports_inside_if_and_try_blocks_resolve(tmp_path):
    """OGA 4.8.1 re-exports OGLE_Slot under `if PACKAGE_MODE:`."""
    engine, framework = make_game(tmp_path)
    (framework / "fw/__init__.py").write_text(
        "PACKAGE_MODE = True\nif PACKAGE_MODE:\n    from fw.slot import SlotBase\n"
        "try:\n    from fw.core import CoreEngine\nexcept ImportError:\n    CoreEngine = None\n")
    doc = engine_map.build(str(engine), keys=["current_winnings"], paths=[str(framework)])
    assert doc["unresolved_bases"] == []
    assert doc["classes"]["engine.GameEngine"][1] == "fw.slot.SlotBase"


def test_static_and_class_method_calls_on_a_named_class_are_followed(tmp_path):
    engine, framework = make_game(tmp_path)
    (engine.parent / "pots.py").write_text(
        "class PotAward(object):\n"
        "    @staticmethod\n"
        "    def apply(state, coins):\n"
        "        return coins * 2\n")
    engine.write_text(ENGINE.replace("from . import defs", "from . import defs\nfrom .pots import PotAward")
                      .replace("current = bonus + line_win", "current = bonus + line_win + PotAward.apply(None, stake)"))
    doc = engine_map.build(str(engine), keys=["current_winnings"], paths=[str(framework)])
    call = nodes_where(doc, kind="call", label="PotAward.apply(None, stake)")[0]
    assert call["target"] == "PotAward.apply (pots.py:3)"
    params = {n["label"] for n in nodes_where(doc, kind="param", function="PotAward.apply")}
    assert params == {"coins"}  # `state` does not reach the return value
