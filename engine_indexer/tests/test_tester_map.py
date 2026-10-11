"""Tracing a volume tester: report key -> tester line -> response key, with conditions.

The game here is a stand-in with the same shape as an OGA game (a package holding
engine.py and tests/volume_tester.py, and a framework base class that owns
`dump_event`), so the tests run without OGA. The tracer runs it in a subprocess with
this interpreter, exactly as it runs a real game in the game's own venv.
"""
import json
import sys
import textwrap

import pytest

from engine_indexer import sites, tester_map
from engine_indexer.tester_map import TraceError

FRAMEWORK = '''
import sys

class VolumeTester(object):
    def __init__(self, engine, defs=None, per_spin=False, per_play=False, **kwargs):
        self.outfile = sys.stdout
        self.e = engine()
        print("engine chatter that must not leak")

    def dump_event(self, key, amount=1):
        self.outfile.write("EVENT\\t('{}', {})\\n".format(key, amount))

    def dump_map_result(self, p):
        self.dump_event("WIN:" + str(p.result["current_winnings"]), p.result["current_winnings"])

    def gameplay(self):
        self.p = self.e.play({})
        self.dump_map_result(self.p)
'''

ENGINE = '''
class Result(object):
    def __init__(self, result):
        self.result = result

class Engine(object):
    def __init__(self):
        self.n = 0

    def play(self, request):
        self.n += 1
        action = "free" if self.n % 3 == 0 else "spin"
        return Result({"request": {"action": action}, "current_winnings": 5, "scatter_wins": [],
                       "line_wins": [{"index": "L1", "symbols": ["W", "A"], "winnings": 2},
                                     {"index": "L2", "symbols": ["A"], "winnings": 3}]})
'''

TESTER = '''
from fakeoga import VolumeTester
from .. import defs
from ..engine import Engine


class GameVolumeTester(VolumeTester):
    def __init__(self, **kwargs):
        self.stake = kwargs.pop("stake", "9")
        super(GameVolumeTester, self).__init__(**kwargs)

    def dump_map_result(self, p):
        action = p.result['request']['action']
        self.dump_event('game_total_win', float(p.result['current_winnings']))
        self.dump_event('action_' + action + '_tot_win',
                        float(p.result['current_winnings']))
        if action == 'free':
            self.dump_event('free_bonus', p.result.get('bonus', 0))
        else:
            self.dump_event('base_stake_' + self.stake, 1)
        for win in p.result.get('ways_wins', []):
            self.dump_event('ways_' + win['index'], win['winnings'])
        for hit in p.result['scatter_wins']:
            self.dump_event('scatter_hit', hit)
        for win in p.result['line_wins']:
            wild = '_wild' if defs.WILD in win['symbols'] else ''
            self.dump_event('line_' + win['index'] + wild, win['winnings'])
        super(GameVolumeTester, self).dump_map_result(p)


if __name__ == '__main__':
    import argparse
    args = argparse.ArgumentParser().parse_args()
    GameVolumeTester(engine=Engine, defs=defs, per_spin=args.perspin, stake=args.stake)
'''


def line_of(text, needle):
    return next(i for i, l in enumerate(text.splitlines(), 1) if needle in l)


@pytest.fixture
def game(tmp_path):
    return make_game(tmp_path)


def make_game(tmp_path):
    """tmp/fakeoga (the framework, on the path) and tmp/my-game (a package, hyphenated)."""
    (tmp_path / "fakeoga").mkdir()
    (tmp_path / "fakeoga/__init__.py").write_text(FRAMEWORK)
    pkg = tmp_path / "my-game"
    (pkg / "tests").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "defs.py").write_text("WILD = 'W'\n")
    (pkg / "engine.py").write_text(ENGINE)
    (pkg / "tests/__init__.py").write_text("")
    (pkg / "tests/volume_tester.py").write_text(TESTER)
    return tmp_path


def trace(game, *kwargs, n=6, **extra):
    return tester_map.build(str(game / "my-game/tests/volume_tester.py"), n=n, python=sys.executable,
                            paths=[str(game)], kwargs=list(kwargs), timeout=60, **extra)


def site(doc, needle):
    line = line_of(TESTER, needle)
    return next(s for s in doc["sites"] if s["source"] == "tester" and s["line"] == line)


def test_runs_the_tester_unchanged_and_records_keys_per_line(game):
    doc = trace(game)
    assert doc["error"] is None
    assert doc["spins"] == {"requested": 6, "completed": 6}
    assert doc["class"] == "GameVolumeTester"
    assert doc["wrapped"] == ["VolumeTester.dump_event"]
    total = site(doc, "'game_total_win'")
    assert total["keys_seen"] == {"game_total_win": 6}
    assert total["response_key"] == "current_winnings"
    assert total["function"] == "dump_map_result"
    assert doc["keys"]["game_total_win"] == [total["id"]]
    assert total["id"] == f"my-game/tests/volume_tester.py:{line_of(TESTER, 'game_total_win')}"


def test_dynamic_key_is_explained_by_the_values_that_built_it(game):
    doc = trace(game)
    s = site(doc, "'action_' + action")
    # a call spanning two lines is matched to its site, whichever line the run reports
    assert s["keys_seen"] == {"action_spin_tot_win": 4, "action_free_tot_win": 2}
    assert s["pattern"] == "action_*_tot_win"
    assert sorted(s["condition_values"]["action"]) == ["free", "spin"]


def test_conditions_and_branches(game):
    doc = trace(game)
    free = site(doc, "'free_bonus'")
    base = site(doc, "'base_stake_'")
    assert [c["expression"] for c in free["conditions"]] == ["action == 'free'"]
    assert [c["expression"] for c in base["conditions"]] == ["not (action == 'free')"]
    assert free["keys_seen"] == {"free_bonus": 2} and base["keys_seen"] == {"base_stake_9": 4}
    line = site(doc, "'line_' + win")
    assert line["keys_seen"] == {"line_L1_wild": 6, "line_L2": 6}
    assert line["conditions"][0]["kind"] == "for"
    assert sorted(line["condition_values"]["wild"]) == ["", "_wild"]
    assert line["response_key"] == "line_wins"


def test_a_site_the_run_never_reached_is_kept(game):
    doc = trace(game)
    ways = site(doc, "'ways_'")
    assert ways["fired"] is False and ways["hits"] == 0
    assert ways["response_key"] == "ways_wins"  # the field the loop reads, never set by the engine
    assert ways["id"] in doc["never_fired"]


def test_events_from_the_framework_base_class_are_recorded(game):
    doc = trace(game)
    inherited = [s for s in doc["sites"] if s["source"] == "inherited"]
    assert len(inherited) == 1
    assert inherited[0]["keys_seen"] == {"WIN:5": 6}
    assert inherited[0]["file"].endswith("fakeoga/__init__.py")
    assert inherited[0]["response_key"] == "current_winnings"


def test_constructor_arguments(game):
    doc = trace(game)
    assert doc["skipped_kwargs"] == ["stake"]  # `args.stake`: command-line driven
    assert {"engine", "defs"} <= set(doc["kwargs"])
    doc = trace(game, "stake=1")
    assert doc["skipped_kwargs"] == []
    assert site(doc, "'base_stake_'")["keys_seen"] == {"base_stake_1": 4}


def test_report_rows_are_matched_to_sites(game, tmp_path):
    report = tmp_path / "r.csv"
    report.write_text("Total number of plays\t6\nTotal amount paid\t30.0\nEvent\tMisc\n"
                      "game_total_win\t(30.0, 6)\naction_spin_tot_win\t(20.0, 4)\n"
                      "ways_L9\t(1.0, 1)\nmystery_row\t(1.0, 1)\nRTP (x)\t1\n")
    doc = trace(game, report=str(report))
    rows = doc["report"]["rows"]
    assert rows["game_total_win"]["match"] == "traced"
    assert rows["ways_L9"] == {"match": "pattern", "sites": [site(doc, "'ways_'")["id"]]}
    assert doc["report"]["unexplained_rows"] == ["mystery_row"]
    assert doc["report"]["not_reproduced_rows"] == ["ways_L9"]
    assert "free_bonus" in doc["report"]["keys_not_in_report"]


def test_a_failing_tester_keeps_what_was_recorded(game):
    engine = game / "my-game/engine.py"
    engine.write_text(ENGINE.replace("self.n += 1", "self.n += 1\n        if self.n == 3:\n"
                                                     "            raise ValueError('boom')"))
    doc = trace(game)
    assert doc["error"] == "ValueError: boom"
    assert doc["spins"]["completed"] == 2
    assert site(doc, "'game_total_win'")["keys_seen"] == {"game_total_win": 2}


def test_missing_interpreter_is_a_clear_error(game):
    with pytest.raises(TraceError, match="no such interpreter"):
        tester_map.build(str(game / "my-game/tests/volume_tester.py"), python=str(game / "nope/python"))


def test_static_scan_of_elif_chain(tmp_path):
    src = tmp_path / "t.py"
    src.write_text(textwrap.dedent('''
        def f(self, x):
            if x == 1:
                self.dump_event('one', 1)
            elif x == 2:
                self.dump_event('two', 1)
    '''))
    found = sites.scan(str(src))
    assert [c["expression"] for c in found[1]["conditions"]] == ["not (x == 1)", "x == 2"]
    assert found[0]["names"] == ["x"]


def test_a_report_with_no_readable_rows_is_flagged(game, tmp_path):
    report = tmp_path / "empty.csv"
    report.write_text("Total number of plays\t6\n")
    doc = trace(game, report=str(report))
    assert doc["report"]["rows_parsed"] == 0
    assert "nothing was matched" in doc["report"]["warning"]


def test_response_keys_are_checked_against_what_the_engine_returned(game):
    doc = trace(game)
    assert doc["results_seen"] == 6  # every play() result, counted by wrapping the engine
    assert doc["response_keys"]["line_wins"] == {"present": 6, "empty": 0}
    assert site(doc, "'game_total_win'")["response_key_status"] == "present"
    # the tester reads keys the engine never puts in its response
    assert site(doc, "'ways_'")["response_key_status"] == "never_returned"
    assert site(doc, "'free_bonus'")["response_key_status"] == "never_returned"
    # returned, but always empty: the loop body can never run
    assert site(doc, "'scatter_hit'")["response_key_status"] == "always_empty"
    assert set(doc["missing_response_keys"]) == {site(doc, n)["id"] for n in ("'ways_'", "'free_bonus'", "'scatter_hit'")}
