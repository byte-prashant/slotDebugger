import json
import shutil

import pytest

from slotdebugger import cli, install

REPORT = """Total number of plays\t100
Total amount paid\t90.0
Event\tMisc
base_line_win\t(40.0, 10)
base_bonus_win\t(50.0, 5)
base_total_win\t(90.0, 15)
RTP (x)\t1
"""

# 100 plays at a bet of 75 -> 7500 staked, 7224 paid, 96.32% RTP.
FULL_REPORT = """Engine Name\ttest-game
Total number of plays\t100
Total amount staked\t7500.0
Total amount paid\t7224.0
Total RTP\t96.32
Event\tMisc
base_line_win\t(2745.0, 40)
base_bonus_win\t(300.0, 10)
base_total_win\t(3045.0, 50)
free_line_win\t(3000.0, 20)
free_bonus_win\t(1179.0, 5)
free_total_win\t(4179.0, 25)
RTP (0 - 100)\t96.32
"""


@pytest.fixture(autouse=True)
def no_autoregister(monkeypatch):
    monkeypatch.setenv("SLOTDEBUG_NO_AUTOREGISTER", "1")


@pytest.fixture
def report(tmp_path):
    p = tmp_path / "r.csv"
    p.write_text(REPORT)
    return p


def test_analyze_ok(report, capsys):
    assert cli.main(["analyze", "--file", str(report)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["analysis"]["status"] == "OK"
    # no engine / volume tester: dependencies are unknown, not guessed from `_total` names
    assert out["dependency_graph"] == {}
    assert out["analysis"]["total_rtp"] is None  # this report states no RTP


def test_analyze_clean_error(tmp_path, capsys):
    assert cli.main(["analyze", "--file", str(tmp_path / "missing.csv")]) == 2
    assert "error:" in capsys.readouterr().err


# ---- expected_rtp_report.json drives the output format ----
# How a template is converted is covered in slotdebugger/report_formatter/tests/;
# these cover how the CLI finds it, where it writes the result and what it reports.

@pytest.fixture
def full_report(tmp_path):
    p = tmp_path / "full.csv"
    p.write_text(FULL_REPORT)
    return p


def test_analyze_copies_expected_report_format(full_report, tmp_path, capsys):
    """An expected report beside the file is found, and the analysis takes its shape."""
    (tmp_path / "expected_rtp_report.json").write_text(json.dumps(
        {"bet": 75, "total": 96.32, "components": {"BG": 40.60, "FG": 55.72}}))

    assert cli.main(["analyze", "--file", str(full_report)]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "bet": None,  # no aggregate formula for the bet: left null, never staked / plays
        "total": 96.32,
        "components": {"BG": 40.6, "FG": 55.72},
    }
    assert "BG -> base_total_win" in captured.err
    assert "FG -> free_total_win" in captured.err


def test_expected_report_can_be_given_explicitly(full_report, tmp_path, capsys):
    """--expected names a file anywhere, under any name, and wins over discovery."""
    (tmp_path / "expected_rtp_report.json").write_text(json.dumps({"total": 0.0}))
    par = tmp_path / "par.json"
    par.write_text(json.dumps({"rtp": {"total": 96.32, "base": 40.60}}))

    assert cli.main(["analyze", "--file", str(full_report), "--expected", str(par)]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"rtp": {"total": 96.32, "base": 40.6}}
    assert f"formatted like {par}" in captured.err


def test_analyze_without_expected_report_keeps_native_format(full_report, capsys):
    assert cli.main(["analyze", "--file", str(full_report)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["analysis"]["status"] == "OK"
    assert "base_total_win" in out["components"]


def test_workspace_saves_expected_shape_but_indexes_native(full_report, tmp_path,
                                                           monkeypatch, capsys):
    game = tmp_path / "game"
    game.mkdir()
    monkeypatch.chdir(game)
    assert cli.main(["setup", "--name", "formatted"]) == 0
    ws = game / "slotdebugger"
    (ws / "expected_rtp_report.json").write_text(json.dumps(
        {"bet": 75, "total": 96.32, "components": {"BG": 40.60, "FG": 55.72}}))

    assert cli.main(["add", str(full_report)]) == 0
    assert cli.main(["analyze", "--file", "full.csv"]) == 0
    capsys.readouterr()

    saved = json.loads((ws / "analysis/full.json").read_text())
    assert saved == {"bet": None, "total": 96.32,
                     "components": {"BG": 40.6, "FG": 55.72}}

    # the run index still carries the native status/RTP, whatever shape was written
    cli.main(["runs"])
    runs = json.loads(capsys.readouterr().out)
    assert runs[0]["report"] == "full.csv" and runs[0]["status"] == "OK"


def test_analyze_writes_aggregate_json_and_computes_components(full_report, tmp_path,
                                                               monkeypatch, capsys):
    game = tmp_path / "game"
    game.mkdir()
    monkeypatch.chdir(game)
    assert cli.main(["setup", "--name", "agg"]) == 0
    ws = game / "slotdebugger"
    (ws / "volume_tester.py").write_text(
        'class T:\n    def run(self):\n        self.dump_event("base_" + self.kind + "_win", 1)\n')
    assert cli.main(["add", str(full_report)]) == 0
    capsys.readouterr()
    assert cli.main(["analyze", "--file", "full.csv"]) == 0

    out = json.loads(capsys.readouterr().out)
    doc = json.loads((ws / "analysis/full.aggregate.json").read_text())
    assert [e["pattern"] for e in doc["volume_tester"]["events"]] == ["base_*_win"]
    assert doc["components"]["base_line_win.rtp"]["event"] == "base_*_win"
    assert doc["components"]["free_line_win.rtp"].get("event") is None
    values = out["aggregate"]["components"]
    assert "base_total_win.children_sum" not in values  # no engine: dependencies unknown, not guessed
    assert values["base_line_win.rtp_vs_stake"] == pytest.approx(2745 / 7224 * 0.9632)
    # the normalized component is the aggregate's value
    assert out["components"]["base_line_win"]["rtp"] == round(values["base_line_win.rtp"], 6)


def test_aggregate_holds_only_what_the_expected_report_mentions(full_report, tmp_path,
                                                                monkeypatch, capsys):
    game = tmp_path / "game"
    game.mkdir()
    monkeypatch.chdir(game)
    assert cli.main(["setup", "--name", "scoped"]) == 0
    ws = game / "slotdebugger"
    (ws / "expected_rtp_report.json").write_text(json.dumps(
        {"bet": 75, "total": 96.32, "components": {"BG": 40.60}}))
    assert cli.main(["add", str(full_report)]) == 0
    assert cli.main(["analyze", "--file", "full.csv"]) == 0
    capsys.readouterr()

    doc = json.loads((ws / "analysis/full.aggregate.json").read_text())
    rows = {k.rsplit(".", 1)[0] for k in doc["components"]}
    # BG -> base_total_win and the reported total; nothing of free_*. Its children are only
    # known from an engine and volume tester, so they are not guessed from the name.
    assert rows == {"base_total_win", "total_rtp"}
    # every report row is still an input; only the formulas are scoped
    inputs = json.loads((ws / "analysis/full.inputs.json").read_text())["inputs"]
    assert "inputs" not in doc  # kept in their own file
    assert {"free_line_win.win", "free_total_win.frequency"} <= set(inputs)
    assert json.loads((ws / "analysis/full.json").read_text())["components"]["BG"] == 40.6


ENGINE_SRC = """
class Engine:
    def base_game(self):
        current_line_winnings = sum([w['winnings'] for w in line_wins])
        bonus_prize_winning = 5
        current_winnings = bonus_prize_winning + current_line_winnings
        state = {'current_winnings': current_winnings, 'bonus_prize_winning': bonus_prize_winning,
                 'base_game_winning': current_line_winnings}
"""
TESTER_SRC = """
class T:
    def dump_map_result(self, p):
        self.dump_event('all_wins', float(p.result['current_winnings']))
        self.dump_event('bonus_pay', float(p.result['bonus_prize_winning']))
        self.dump_event('line_pay', float(p.result['base_game_winning']))
        self.dump_event('side_pay', float(p.result['side']))
"""
GAME_REPORT = """Total number of plays\t100
Total amount staked\t7500.0
Total amount paid\t7224.0
Total RTP\t96.32
Event\tMisc
all_wins\t(7224.0, 60)
bonus_pay\t(1224.0, 10)
line_pay\t(6000.0, 50)
side_pay\t(100.0, 3)
RTP (0 - 100)\t96.32
"""


def test_dependencies_come_from_the_engine_and_volume_tester(tmp_path, monkeypatch, capsys):
    """`all_wins` has no `_total` in its name, so only the engine can say what it is made of."""
    game = tmp_path / "game"
    (game / "tests").mkdir(parents=True)
    (game / "engine.py").write_text(ENGINE_SRC)
    (game / "tests/volume_tester.py").write_text(TESTER_SRC)
    report = tmp_path / "g.csv"
    report.write_text(GAME_REPORT)
    expected = tmp_path / "expected.json"
    expected.write_text(json.dumps({"all_wins": 96.32}))

    assert cli.main(["analyze", "--file", str(report), "--expected", str(expected),
                     "--volume-tester", str(game / "tests/volume_tester.py")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"all_wins": 96.32}

    from slotdebugger import pipeline, report_formatter
    result = pipeline.analyze(str(report), str(game / "tests/volume_tester.py"), None,
                              report_formatter.load(str(expected)), str(game / "engine.py"))
    doc = result["aggregate"]["document"]
    rows = {k.rsplit(".", 1)[0] for k in doc["components"]}
    assert rows == {"all_wins", "bonus_pay", "line_pay", "total_rtp"}  # not side_pay
    assert doc["derived_from"]["dependencies"]["all_wins"] == {
        "depends_on": ["bonus_prize_winning", "base_game_winning"], "complete": True}
    values = result["aggregate"]["components"]
    assert values["all_wins.children_sum"] == pytest.approx(values["all_wins.rtp"])


def test_expected_report_matching_nothing_still_analyzes(tmp_path, capsys):
    report = tmp_path / "g.csv"
    report.write_text(GAME_REPORT)
    expected = tmp_path / "expected.json"
    expected.write_text(json.dumps({"zzz_unknown": 1.5}))
    assert cli.main(["analyze", "--file", str(report), "--expected", str(expected)]) == 0
    assert json.loads(capsys.readouterr().out) == {"zzz_unknown": None}


def test_analyze_detects_a_total_that_does_not_add_up(tmp_path, capsys):
    """A mismatch is found from the engine's own sum, not from what rows are called."""
    game = tmp_path / "game"
    (game / "tests").mkdir(parents=True)
    (game / "engine.py").write_text(ENGINE_SRC)
    (game / "tests/volume_tester.py").write_text(TESTER_SRC)
    report = tmp_path / "g.csv"
    report.write_text(GAME_REPORT.replace("(7224.0, 60)", "(7000.0, 60)"))
    assert cli.main(["analyze", "--file", str(report),
                     "--volume-tester", str(game / "tests/volume_tester.py")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["analysis"]["status"] == "FAIL"
    assert out["analysis"]["issues"] == ["Mismatch in all_wins"]


def test_engine_is_found_beside_the_volume_tester(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "engine.py").write_text("")
    vt = tmp_path / "tests/volume_tester.py"
    assert cli._find_engine(str(vt), None) == str(tmp_path / "engine.py")
    assert cli._find_engine(None, None) is None


def test_think_passthrough(tmp_path, capsys):
    assert cli.main(["think", "--state", str(tmp_path / "s.json"), "--status"]) == 0
    assert "thoughtNumber" in capsys.readouterr().out


def test_install_uninstall_roundtrip(tmp_path):
    for target in ("claude", "cursor"):
        cli.main(["install", target, "--scope", "project", "--hook", "--dir", str(tmp_path)])
    assert (tmp_path / ".claude/skills/slot-debugger/SKILL.md").exists()
    assert (tmp_path / ".cursor/rules/sequential-thinking.mdc").exists()
    settings = json.loads((tmp_path / ".claude/settings.json").read_text())
    assert len(settings["hooks"]["SessionStart"]) == 1

    cli.main(["install", "claude", "--scope", "project", "--hook", "--dir", str(tmp_path)])  # idempotent
    settings = json.loads((tmp_path / ".claude/settings.json").read_text())
    assert len(settings["hooks"]["SessionStart"]) == 1

    for target in ("claude", "cursor"):
        cli.main(["uninstall", target, "--scope", "project", "--dir", str(tmp_path)])
    assert not (tmp_path / ".claude/skills/slot-debugger").exists()
    assert not (tmp_path / ".cursor/rules/slot-debugger.mdc").exists()
    assert json.loads((tmp_path / ".claude/settings.json").read_text()) == {}


def test_auto_register_once(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SLOTDEBUG_NO_AUTOREGISTER")
    monkeypatch.setenv("HOME", str(tmp_path))
    cli.auto_register()  # no ~/.claude -> nothing happens
    assert not (tmp_path / ".claude/skills").exists()

    (tmp_path / ".claude").mkdir()
    cli.auto_register()
    assert (tmp_path / ".claude/skills/slot-debugger/SKILL.md").exists()
    assert not (tmp_path / ".claude/settings.json").exists()  # no hook automatically

    cli.main(["uninstall", "claude"])
    cli.auto_register()  # marker present -> not reinstalled after uninstall
    assert not (tmp_path / ".claude/skills/slot-debugger").exists()


# ---- per-game workspace ----

def test_workspace_flow(report, tmp_path, monkeypatch, capsys):
    game = tmp_path / "game"
    game.mkdir()
    monkeypatch.chdir(game)
    assert cli.main(["setup", "--name", "blazing"]) == 0
    ws = game / "slotdebugger"
    for sub in ("reports", "analysis", "data", "state"):
        assert (ws / sub).is_dir()
    assert cli.main(["setup"]) == 0  # idempotent: re-running updates in place
    assert json.loads((ws / "workspace.json").read_text())["game"] == "blazing"

    # outside file is refused until imported
    assert cli.main(["analyze", "--file", str(report)]) == 2
    assert cli.main(["add", str(report)]) == 0
    capsys.readouterr()
    assert cli.main(["analyze", "--file", "r.csv"]) == 0
    assert json.loads(capsys.readouterr().out)["analysis"]["status"] == "OK"
    assert (ws / "analysis/r.json").exists()

    cli.main(["runs"])
    runs = json.loads(capsys.readouterr().out)
    assert runs[0]["report"] == "r.csv" and runs[0]["status"] == "OK"

    # works from a subdirectory too, and thinking state lands in the workspace
    sub = game / "src"
    sub.mkdir()
    monkeypatch.chdir(sub)
    assert cli.main(["think", "--setPlan", "a,b"]) == 0
    assert (ws / "state/.think_state.json").exists()
    assert not (sub / ".think_state.json").exists()


def test_workspace_blocks_escape(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cli.main(["setup"])
    assert cli.main(["think", "--export", str(tmp_path / "out.json"), "--status"]) == 2
    assert cli.main(["think", "--state", "/tmp/x.json", "--status"]) == 2
    assert cli.main(["analyze", "--file", "../../etc/passwd"]) == 2
    assert not (tmp_path / "out.json").exists()


def test_setup_upgrades_existing_workspace(tmp_path, monkeypatch, capsys):
    """Re-running setup migrates an old workspace without losing user data."""
    monkeypatch.chdir(tmp_path)
    assert cli.main(["setup", "--name", "blazing"]) == 0
    ws = tmp_path / "slotdebugger"

    # Fake a workspace written by an older version: v1 marker, no state/ directory,
    # a legacy object in runs.json, and a skill that is no longer shipped.
    marker = json.loads((ws / "workspace.json").read_text())
    (ws / "workspace.json").write_text(json.dumps(
        {"game": "blazing", "version": 1, "created": marker["created"],
         "registered": {"skills": ["retired-skill"], "rules": ["retired-skill"]}}))
    shutil.rmtree(ws / "state")
    shutil.rmtree(ws / "tester")
    (ws / "data/runs.json").write_text('{"label": "Expected", "rtp": {"total": 96.3}}')
    (ws / "reports").mkdir(exist_ok=True)
    (ws / "reports/keep.csv").write_text("mine")
    stale = tmp_path / ".claude/skills/retired-skill"
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text("old")

    capsys.readouterr()
    assert cli.main(["setup"]) == 0
    out = capsys.readouterr().out

    info = json.loads((ws / "workspace.json").read_text())
    assert info["version"] == 3 and info["game"] == "blazing"
    assert info["created"] == marker["created"]  # creation time preserved
    assert (ws / "state").is_dir()               # directories added by newer versions
    assert (ws / "tester").is_dir()
    assert not stale.exists()                    # skill we no longer ship is retired
    assert "retired skill retired-skill" in out

    # Legacy runs.json is migrated to a list, with the old content kept, not dropped.
    assert json.loads((ws / "data/runs.json").read_text()) == []
    assert json.loads((ws / "data/runs.legacy.json").read_text())["label"] == "Expected"
    assert (ws / "reports/keep.csv").read_text() == "mine"  # user data untouched

    # A second run is a no-op beyond refreshing registration.
    capsys.readouterr()
    assert cli.main(["setup"]) == 0
    assert "already up to date" in capsys.readouterr().out


def _workspace_with_report(tmp_path, monkeypatch, report_text):
    game = tmp_path / "game"
    game.mkdir()
    monkeypatch.chdir(game)
    assert cli.main(["setup", "--name", "rev", "--no-register"]) == 0
    src = tmp_path / "full.csv"
    src.write_text(report_text)
    assert cli.main(["add", str(src)]) == 0
    return game / "slotdebugger"


def test_corrected_aggregate_is_kept_once_marked_reviewed(full_report, tmp_path, monkeypatch, capsys):
    ws = _workspace_with_report(tmp_path, monkeypatch, FULL_REPORT)
    assert cli.main(["analyze", "--file", "full.csv"]) == 0
    capsys.readouterr()

    assert cli.main(["aggregate", "--file", "full.csv"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["reviewed"] is False and first["findings"] == []

    # a reviewer corrects a formula and marks the file reviewed
    path = ws / "analysis/full.aggregate.json"
    doc = json.loads(path.read_text())
    doc["components"]["base_total_win.rtp_vs_stake"]["formula"]["args"][1] = {"op": "input", "name": "overall_rtp"}
    doc["review_notes"] = [{"component": "base_total_win", "finding": "x", "note": "checked"}]
    path.write_text(json.dumps(doc))
    assert cli.main(["aggregate", "--file", "full.csv", "--mark-reviewed"]) == 0
    capsys.readouterr()

    # analyze keeps it instead of regenerating
    assert cli.main(["analyze", "--file", "full.csv"]) == 0
    captured = capsys.readouterr()
    assert "using reviewed aggregate" in captured.err
    kept = json.loads(path.read_text())
    assert kept["reviewed"] is True and kept["review_notes"]


def test_reviewed_formulas_survive_new_numbers_and_inputs_are_refreshed(tmp_path, monkeypatch, capsys):
    ws = _workspace_with_report(tmp_path, monkeypatch, FULL_REPORT)
    assert cli.main(["analyze", "--file", "full.csv"]) == 0
    assert cli.main(["aggregate", "--file", "full.csv", "--mark-reviewed"]) == 0
    capsys.readouterr()

    (ws / "reports/full.csv").write_text(FULL_REPORT.replace("(2745.0, 40)", "(2700.0, 40)"))
    assert cli.main(["analyze", "--file", "full.csv"]) == 0
    assert "using reviewed aggregate" in capsys.readouterr().err
    assert json.loads((ws / "analysis/full.aggregate.json").read_text())["reviewed"] is True
    assert json.loads((ws / "analysis/full.inputs.json").read_text())["inputs"]["base_line_win.win"] == 2700.0


def test_reviewed_aggregate_is_set_aside_when_the_report_rows_change(tmp_path, monkeypatch, capsys):
    ws = _workspace_with_report(tmp_path, monkeypatch, FULL_REPORT)
    assert cli.main(["analyze", "--file", "full.csv"]) == 0
    assert cli.main(["aggregate", "--file", "full.csv", "--mark-reviewed"]) == 0
    capsys.readouterr()

    (ws / "reports/full.csv").write_text(FULL_REPORT.replace("free_bonus_win\t(1179.0, 5)\n", ""))
    assert cli.main(["analyze", "--file", "full.csv"]) == 0
    assert "old one kept as" in capsys.readouterr().err
    assert (ws / "analysis/full.aggregate.stale.json").exists()  # never deleted
    assert "reviewed" not in json.loads((ws / "analysis/full.aggregate.json").read_text())


def test_mark_reviewed_refuses_a_file_that_does_not_evaluate(tmp_path, monkeypatch, capsys):
    ws = _workspace_with_report(tmp_path, monkeypatch, FULL_REPORT)
    assert cli.main(["analyze", "--file", "full.csv"]) == 0
    path = ws / "analysis/full.aggregate.json"
    doc = json.loads(path.read_text())
    doc["components"]["base_line_win.rtp"]["formula"] = {"ref": "nowhere"}
    path.write_text(json.dumps(doc))
    capsys.readouterr()
    assert cli.main(["aggregate", "--file", "full.csv", "--mark-reviewed"]) == 2
    assert "reviewed" not in json.loads(path.read_text())


def test_aggregate_needs_an_analysis_first(tmp_path, monkeypatch, capsys):
    _workspace_with_report(tmp_path, monkeypatch, FULL_REPORT)
    assert cli.main(["aggregate", "--file", "full.csv"]) == 2
    assert "run `slotdebug analyze" in capsys.readouterr().err


def test_install_registers_the_aggregate_review_skill(tmp_path):
    install.install_claude("project", str(tmp_path), hook=False)
    text = (tmp_path / ".claude/skills/aggregate-review/SKILL.md").read_text()
    assert "slotdebug aggregate --file" in text and "Never edit `<report>.inputs.json`" in text


def test_use_aggregate_keeps_the_saved_specification_untouched(tmp_path, monkeypatch, capsys):
    ws = _workspace_with_report(tmp_path, monkeypatch, FULL_REPORT)
    assert cli.main(["analyze", "--file", "full.csv"]) == 0
    spec = ws / "analysis/full.aggregate.json"
    doc = json.loads(spec.read_text())
    doc["note"] = "hand edited, not reviewed"  # would be lost if analyze regenerated it
    spec.write_text(json.dumps(doc))
    before = {p.name: p.read_text() for p in (ws / "analysis").glob("full.*aggregate*")}
    capsys.readouterr()

    assert cli.main(["analyze", "--file", "full.csv", "--use-aggregate"]) == 0
    captured = capsys.readouterr()
    assert "not modified" in captured.err
    assert json.loads(captured.out)["aggregate"]["status"] == "provided"
    assert {p.name: p.read_text() for p in (ws / "analysis").glob("full.*aggregate*")} == before
    assert not (ws / "analysis/full.aggregate.stale.json").exists()


def test_use_aggregate_takes_a_given_path_and_does_not_write_one(full_report, tmp_path, monkeypatch, capsys):
    ws = _workspace_with_report(tmp_path, monkeypatch, FULL_REPORT)
    assert cli.main(["analyze", "--file", "full.csv"]) == 0
    mine = tmp_path / "mine.json"
    mine.write_text((ws / "analysis/full.aggregate.json").read_text())
    (ws / "analysis/full.aggregate.json").unlink()
    (ws / "analysis/full.inputs.json").unlink()
    capsys.readouterr()

    assert cli.main(["analyze", "--file", "full.csv", "--use-aggregate", str(mine)]) == 0
    assert json.loads(capsys.readouterr().out)["aggregate"]["file"] == str(mine)
    assert not (ws / "analysis/full.aggregate.json").exists()  # nothing created


def test_use_aggregate_writes_the_specification_only_when_it_is_empty(tmp_path, monkeypatch, capsys):
    ws = _workspace_with_report(tmp_path, monkeypatch, FULL_REPORT)
    capsys.readouterr()
    spec, inputs = ws / "analysis/full.aggregate.json", ws / "analysis/full.inputs.json"

    # nothing saved yet: generated and written
    assert cli.main(["analyze", "--file", "full.csv", "--use-aggregate"]) == 0
    assert "was empty; wrote" in capsys.readouterr().err
    assert json.loads(spec.read_text())["components"] and inputs.exists()

    # an empty file counts as empty too
    spec.write_text("")
    inputs.write_text(json.dumps({"inputs": {"kept": 1}}))  # has content: not overwritten
    assert cli.main(["analyze", "--file", "full.csv", "--use-aggregate"]) == 0
    assert json.loads(spec.read_text())["components"]
    assert json.loads(inputs.read_text()) == {"inputs": {"kept": 1}}
    capsys.readouterr()

    # now it has content: left alone
    before = spec.read_text()
    assert cli.main(["analyze", "--file", "full.csv", "--use-aggregate"]) == 0
    assert "not modified" in capsys.readouterr().err and spec.read_text() == before


def test_use_aggregate_writes_a_given_path_that_is_missing(tmp_path, monkeypatch, capsys):
    _workspace_with_report(tmp_path, monkeypatch, FULL_REPORT)
    mine = tmp_path / "mine.json"
    capsys.readouterr()
    assert cli.main(["analyze", "--file", "full.csv", "--use-aggregate", str(mine)]) == 0
    assert json.loads(mine.read_text())["components"]


def test_use_aggregate_never_overwrites_a_file_it_cannot_read(tmp_path, monkeypatch, capsys):
    ws = _workspace_with_report(tmp_path, monkeypatch, FULL_REPORT)
    spec = ws / "analysis/full.aggregate.json"
    spec.write_text("{not json")
    capsys.readouterr()
    assert cli.main(["analyze", "--file", "full.csv", "--use-aggregate"]) == 2
    assert spec.read_text() == "{not json"


def test_use_aggregate_outside_a_workspace_asks_for_a_path(full_report, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["analyze", "--file", str(full_report), "--use-aggregate"]) == 2
    assert "pass its path" in capsys.readouterr().err
    target = tmp_path / "asked.json"
    monkeypatch.setattr(cli, "_ask_aggregate_path", lambda: str(target))
    assert cli.main(["analyze", "--file", str(full_report), "--use-aggregate"]) == 0
    assert json.loads(target.read_text())["components"]


def test_use_aggregate_never_regenerates_one_that_does_not_fit(tmp_path, monkeypatch, capsys):
    ws = _workspace_with_report(tmp_path, monkeypatch, FULL_REPORT)
    assert cli.main(["analyze", "--file", "full.csv"]) == 0
    spec = ws / "analysis/full.aggregate.json"
    before = spec.read_text()
    (ws / "reports/full.csv").write_text(FULL_REPORT.replace("free_bonus_win\t(1179.0, 5)\n", ""))
    capsys.readouterr()

    assert cli.main(["analyze", "--file", "full.csv", "--use-aggregate"]) == 2
    assert "free_bonus_win" in capsys.readouterr().err
    assert spec.read_text() == before


def test_every_shipped_skill_is_declared_as_package_data():
    """A skill file missing from the package breaks `slotdebug setup` on installed copies.

    Without an explicit declaration setuptools only packs files an old
    `*.egg-info/SOURCES.txt` happens to list, so a new skill silently goes missing.
    """
    import os
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(cli.__file__)))
    text = open(os.path.join(root, "pyproject.toml")).read()
    assert re.search(r'^slotdebugger\s*=\s*\[[^\]]*"skills/\*\.md"', text, re.M)
    for name in install.SKILLS:
        assert os.path.isfile(os.path.join(root, "slotdebugger", "skills", f"{name}.md")), name


# ---- slotdebug diff ----

def _diff_workspace(full_report, tmp_path, monkeypatch, expected):
    game = tmp_path / "game"
    game.mkdir()
    monkeypatch.chdir(game)
    assert cli.main(["setup", "--name", "diffed", "--no-register"]) == 0
    ws = game / "slotdebugger"
    (ws / "expected_rtp_report.json").write_text(json.dumps(expected))
    assert cli.main(["add", str(full_report)]) == 0
    return ws


def test_diff_writes_open_keys(full_report, tmp_path, monkeypatch, capsys):
    ws = _diff_workspace(full_report, tmp_path, monkeypatch,
                         {"bet": 75, "total": 96.32, "components": {"BG": 41.00, "FG": 55.72}})
    assert cli.main(["analyze", "--file", "full.csv"]) == 0
    capsys.readouterr()
    assert cli.main(["diff", "--file", "full.csv"]) == 0
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["open"] == ["bet", "components.BG"]  # bet has no formula; BG is 0.4 points off
    assert out["summary"] == {"ok": 2, "mismatch": 1, "missing": 1}
    assert json.loads((ws / "analysis/full.diff.json").read_text()) == out
    assert "1 mismatch, 1 missing, 2 ok" in captured.err

    # the diff file is what the thinking session takes its targets from
    assert cli.main(["think", "--setTargets", str(ws / "analysis/full.diff.json")]) == 0
    assert "components.BG" in capsys.readouterr().out


def test_diff_needs_analysis_first(full_report, tmp_path, monkeypatch, capsys):
    _diff_workspace(full_report, tmp_path, monkeypatch, {"total": 96.32})
    assert cli.main(["diff", "--file", "full.csv"]) == 2
    assert "run `slotdebug analyze" in capsys.readouterr().err


def test_diff_refuses_analysis_in_another_shape(full_report, tmp_path, monkeypatch, capsys):
    ws = _diff_workspace(full_report, tmp_path, monkeypatch, {"total": 96.32})
    (ws / "expected_rtp_report.json").unlink()
    assert cli.main(["analyze", "--file", "full.csv"]) == 0  # native format: no expected report
    other = tmp_path / "expected.json"
    other.write_text(json.dumps({"total": 96.32, "components": {"BG": 40.6}}))
    capsys.readouterr()
    assert cli.main(["diff", "--file", "full.csv", "--expected", str(other)]) == 2
    assert "not in the shape of" in capsys.readouterr().err


def test_diff_is_registered():
    from slotdebugger.registry import get_registry
    cli._register_all_commands()
    names = [c["name"] if isinstance(c, dict) else c for c in get_registry().list_commands()]
    assert "diff" in names and "trace-tester" in names


# ---- slotdebug trace-tester ----

def test_trace_tester_writes_map_into_the_workspace(tmp_path, monkeypatch, capsys):
    import sys
    from engine_indexer.tests.test_tester_map import make_game
    game = make_game(tmp_path)
    monkeypatch.chdir(tmp_path / "my-game")
    assert cli.main(["setup", "--name", "traced", "--no-register"]) == 0
    report = tmp_path / "r.csv"
    report.write_text("Total number of plays\t6\nTotal amount paid\t30.0\nEvent\tMisc\n"
                      "game_total_win\t(30.0, 6)\nmystery_row\t(1.0, 1)\nRTP (x)\t1\n")
    assert cli.main(["add", str(report)]) == 0
    capsys.readouterr()
    assert cli.main(["trace-tester", "--file", "r.csv", "--tester", str(game / "my-game/tests/volume_tester.py"),
                     "-n", "3", "--python", sys.executable, "--path", str(game), "--kw", "stake=1"]) == 0
    captured = capsys.readouterr()
    saved = json.loads((tmp_path / "my-game/slotdebugger/analysis/r.tester_map.json").read_text())
    assert saved == json.loads(captured.out)
    assert saved["report"]["unexplained_rows"] == ["mystery_row"]
    assert "3/3 spins" in captured.err and "never fired" in captured.err


def test_trace_tester_without_a_tester_is_a_clear_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["trace-tester"]) == 2
    assert "no volume_tester.py found" in capsys.readouterr().err


# ---- the volume tester copy in tester/ ----

def _traced_workspace(tmp_path, monkeypatch):
    """The fake game from engine_indexer's tests, with a workspace set up inside it."""
    from engine_indexer.tests.test_tester_map import make_game
    game = make_game(tmp_path)
    root = game / "my-game"
    monkeypatch.chdir(root)
    assert cli.main(["setup", "--name", "traced", "--no-register"]) == 0
    return game, root, root / "slotdebugger"


def _trace(game, *extra):
    import sys
    return cli.main(["trace-tester", "-n", "3", "--python", sys.executable, "--path", str(game),
                     "--kw", "stake=1", *extra])


def test_setup_copies_the_volume_tester_verbatim(tmp_path, monkeypatch, capsys):
    game, root, ws = _traced_workspace(tmp_path, monkeypatch)
    original = root / "tests/volume_tester.py"
    assert (ws / "tester/volume_tester.py").read_bytes() == original.read_bytes()
    info = json.loads((ws / "workspace.json").read_text())
    assert info["tester"]["original"] == str(original.resolve())
    assert "copied" in capsys.readouterr().out


def test_trace_runs_the_copy_and_labels_lines_with_the_original(tmp_path, monkeypatch, capsys):
    game, root, ws = _traced_workspace(tmp_path, monkeypatch)
    capsys.readouterr()
    assert _trace(game) == 0
    doc = json.loads((ws / "analysis/tester_map.json").read_text())
    assert doc["ran"] == str((ws / "tester/volume_tester.py").resolve())
    assert doc["labelled_as"] == doc["tester"] == str((root / "tests/volume_tester.py").resolve())
    assert any(s["id"].startswith("my-game/tests/volume_tester.py:") and s["fired"] for s in doc["sites"])
    assert "my-game/engine.py" in doc["game_files"]  # engine hashed: a later map can spot a change


def test_trace_keeps_running_the_copy_when_the_original_changes(tmp_path, monkeypatch, capsys):
    game, root, ws = _traced_workspace(tmp_path, monkeypatch)
    original = root / "tests/volume_tester.py"
    original.write_text(original.read_text().replace("'game_total_win'", "'renamed_win'"))
    capsys.readouterr()
    assert _trace(game) == 0
    err = capsys.readouterr().err
    assert "changed since it was copied" in err
    doc = json.loads((ws / "analysis/tester_map.json").read_text())
    assert "game_total_win" in doc["keys"] and "renamed_win" not in doc["keys"]  # the copy ran
    assert doc["labelled_as"] == doc["ran"]  # lines are the copy's, so it names the copy

    # --refresh-tester re-copies; the old copy is kept beside it, not deleted
    assert _trace(game, "--refresh-tester") == 0
    doc = json.loads((ws / "analysis/tester_map.json").read_text())
    assert "renamed_win" in doc["keys"]
    assert len(list((ws / "tester").glob("volume_tester.*.py"))) == 1


def test_trace_live_runs_the_games_own_file(tmp_path, monkeypatch, capsys):
    game, root, ws = _traced_workspace(tmp_path, monkeypatch)
    original = root / "tests/volume_tester.py"
    original.write_text(original.read_text().replace("'game_total_win'", "'renamed_win'"))
    assert _trace(game, "--live") == 0
    doc = json.loads((ws / "analysis/tester_map.json").read_text())
    assert doc["ran"] == str(original.resolve()) and "renamed_win" in doc["keys"]


def test_setup_rerun_reports_a_changed_tester_without_replacing_it(tmp_path, monkeypatch, capsys):
    game, root, ws = _traced_workspace(tmp_path, monkeypatch)
    copy = (ws / "tester/volume_tester.py").read_bytes()
    original = root / "tests/volume_tester.py"
    original.write_text(original.read_text() + "\n# edited\n")
    capsys.readouterr()
    assert cli.main(["setup", "--no-register"]) == 0
    assert "changed since it was copied" in capsys.readouterr().out
    assert (ws / "tester/volume_tester.py").read_bytes() == copy


# ---- always-on command reference: CLAUDE.md block and Cursor rule ----

def _registered_names():
    from slotdebugger.registry import get_registry
    cli._register_all_commands()
    return get_registry().list_commands()


def test_every_cli_command_is_registered():
    """A sub-command missing from the registry is invisible to the LLM: fail on it."""
    import re
    source = open(cli.__file__).read()
    parsers = set(re.findall(r'sub\.add_parser\("([a-z-]+)"', source)) | {"install", "uninstall"}
    assert parsers <= set(_registered_names())


def test_setup_writes_command_reference_for_claude_and_cursor(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("# My game\n\nKeep this.\n")
    assert cli.main(["setup", "--name", "g"]) == 0
    claude = (tmp_path / "CLAUDE.md").read_text()
    assert claude.startswith("# My game\n\nKeep this.\n")
    block = claude[claude.index(install.BLOCK_START):]
    for name in _registered_names():
        assert f"### `slotdebug {name}`" in block
    assert "`--evidence`" in block and "`<file>`" in block  # think flags, positional args
    rule = (tmp_path / ".cursor/rules/slotdebug-commands.mdc").read_text()
    assert "alwaysApply: true" in rule and "### `slotdebug trace-tester`" in rule

    # re-running replaces the block in place: one block, user text untouched
    (tmp_path / "CLAUDE.md").write_text(claude + "\n## Notes added later\n")
    assert cli.main(["setup"]) == 0
    again = (tmp_path / "CLAUDE.md").read_text()
    assert again.count(install.BLOCK_START) == 1 and again.count(install.BLOCK_END) == 1
    assert "Keep this." in again and "## Notes added later" in again


def test_command_rule_survives_setup_reruns(tmp_path, monkeypatch, capsys):
    """The rule is recorded as ours, so re-running setup must not retire it."""
    monkeypatch.chdir(tmp_path)
    assert cli.main(["setup", "--name", "g"]) == 0
    assert cli.main(["setup"]) == 0
    assert (tmp_path / ".cursor/rules/slotdebug-commands.mdc").exists()


def test_uninstall_removes_only_the_block(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("# Mine\n")
    install.write_claude_block(str(tmp_path))
    assert cli.main(["uninstall", "all", "--scope", "project", "--dir", str(tmp_path)]) == 0
    assert (tmp_path / "CLAUDE.md").read_text() == "# Mine\n"
    assert not (tmp_path / ".cursor/rules/slotdebug-commands.mdc").exists()
    (tmp_path / "x").mkdir()
    install.write_claude_block(str(tmp_path / "x"))
    install.remove_claude_block(str(tmp_path / "x"))
    assert not (tmp_path / "x/CLAUDE.md").exists()  # a file that held only our block goes


def test_user_scope_install_does_not_touch_claude_md(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    assert cli.main(["install", "claude", "--dir", str(tmp_path)]) == 0
    assert not (tmp_path / "CLAUDE.md").exists()


# ---- slotdebug index ----

def test_index_joins_the_tester_map_with_the_engine(tmp_path, monkeypatch, capsys):
    game, root, ws = _traced_workspace(tmp_path, monkeypatch)
    assert _trace(game) == 0
    capsys.readouterr()
    assert cli.main(["index"]) == 0  # keys and engine come from the workspace's tester map
    captured = capsys.readouterr()
    doc = json.loads((ws / "analysis/engine_map.json").read_text())
    assert doc == json.loads(captured.out)
    assert "chain" not in doc  # the engine map holds the engine only
    assert doc["keys"]["current_winnings"]["status"] == "found"
    assert doc["nodes"][doc["keys"]["current_winnings"]["roots"][0]]["function"] == "Engine.play"
    joined = json.loads((ws / "analysis/report_map.json").read_text())
    assert joined["tester_map"].endswith("tester_map.json") and joined["engine_map"].endswith("engine_map.json")
    assert "ways_wins" in joined["keys_without_engine_root"]  # the tester reads it; no engine code sets it
    assert "report_map.json" in captured.err
    assert "no dict in the engine or its base classes carries 'ways_wins'" in captured.err


def test_index_prints_a_tree(tmp_path, monkeypatch, capsys):
    game, root, ws = _traced_workspace(tmp_path, monkeypatch)
    capsys.readouterr()
    assert cli.main(["index", "--engine", str(root / "engine.py"), "--key", "line_wins",
                     "--tree", "line_wins"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("line_wins  (1 place(s)")


def test_index_needs_keys(tmp_path, monkeypatch, capsys):
    game, root, ws = _traced_workspace(tmp_path, monkeypatch)
    capsys.readouterr()
    assert cli.main(["index", "--engine", str(root / "engine.py")]) == 2
    assert "run `slotdebug trace-tester` first" in capsys.readouterr().err


def test_trace_tester_writes_all_three_maps(tmp_path, monkeypatch, capsys):
    game, root, ws = _traced_workspace(tmp_path, monkeypatch)
    report = tmp_path / "r.csv"
    report.write_text("Total number of plays\t3\nTotal amount paid\t15.0\nEvent\tMisc\n"
                      "game_total_win\t(15.0, 3)\nRTP (x)\t1\n")
    assert cli.main(["add", str(report)]) == 0
    capsys.readouterr()
    assert _trace(game, "--file", "r.csv") == 0
    err = capsys.readouterr().err
    analysis = ws / "analysis"
    tester_map = json.loads((analysis / "r.tester_map.json").read_text())
    engine_map = json.loads((analysis / "r.engine_map.json").read_text())
    joined = json.loads((analysis / "r.report_map.json").read_text())
    assert engine_map["keys"]["current_winnings"]["status"] == "found"  # keys from this trace
    assert joined["rows"]["game_total_win"]["response_keys"] == ["current_winnings"]
    assert joined["tester_map"] == str(analysis / "r.tester_map.json")
    assert "r.engine_map.json" in err and "r.report_map.json" in err
    assert tester_map["sites"]  # stdout/tester map unchanged by the extra step


def test_trace_tester_no_index_writes_the_tester_map_only(tmp_path, monkeypatch, capsys):
    game, root, ws = _traced_workspace(tmp_path, monkeypatch)
    assert _trace(game, "--no-index") == 0
    assert (ws / "analysis/tester_map.json").exists()
    assert not (ws / "analysis/engine_map.json").exists()
    assert not (ws / "analysis/report_map.json").exists()


def test_trace_tester_outside_a_workspace_writes_maps_beside_out(tmp_path, monkeypatch, capsys):
    import sys
    from engine_indexer.tests.test_tester_map import make_game
    game = make_game(tmp_path)
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "blazing.tester_map.json"
    assert cli.main(["trace-tester", "--tester", str(game / "my-game/tests/volume_tester.py"), "-n", "2",
                     "--python", sys.executable, "--path", str(game), "--out", str(out)]) == 0
    assert (tmp_path / "blazing.engine_map.json").exists()
    assert (tmp_path / "blazing.report_map.json").exists()


def test_every_cli_flag_is_in_the_registry(capsys):
    """The registry feeds the CLAUDE.md / Cursor command reference: a flag missing from it
    is a flag the LLM never learns about."""
    import re
    from slotdebugger.registry import get_registry
    cli._register_all_commands()
    commands = get_registry().get_all_commands()
    for name, meta in commands.items():
        if name == "command-list":
            continue
        with pytest.raises(SystemExit):
            cli.main([name, "-h"])
        help_text = capsys.readouterr().out
        flags = set(re.findall(r"(?<![\w-])--?([A-Za-z][\w-]*)", help_text)) - {"h", "help"}
        if name == "uninstall":
            flags.discard("hook")  # accepted, but only `install` adds a hook
        missing = flags - set(meta.get("args", {}))
        assert not missing, f"{name}: flags missing from the registry: {sorted(missing)}"


# ---- slotdebug map: questions answered from the three maps ----

def test_map_views_answer_from_the_traced_maps(tmp_path, monkeypatch, capsys):
    game, root, ws = _traced_workspace(tmp_path, monkeypatch)
    report = tmp_path / "r.csv"
    report.write_text("Total number of plays\t3\nTotal amount paid\t15.0\nEvent\tMisc\n"
                      "game_total_win\t(15.0, 3)\nmystery_row\t(1.0, 1)\nRTP (x)\t1\n")
    assert cli.main(["add", str(report)]) == 0
    assert _trace(game, "--file", "r.csv") == 0
    for name in ("summary", "tester_summary", "engine_summary"):
        assert (ws / f"analysis/r.{name}.md").exists()
    capsys.readouterr()

    assert cli.main(["map", "tree", "--file", "r.csv", "--row", "game_total_win"]) == 0
    tree = capsys.readouterr().out
    assert tree.startswith("REPORT ROW  game_total_win  [traced]")
    assert "RESPONSE  current_winnings  [present]" in tree and "ENGINE" in tree

    assert cli.main(["map", "problems", "--file", "r.csv", "--json"]) == 0
    kinds = {p["kind"] for p in json.loads(capsys.readouterr().out)}
    assert {"row_unexplained", "response_key_never_returned", "key_not_in_engine"} <= kinds

    assert cli.main(["map", "keys", "--file", "r.csv"]) == 0
    assert "| current_winnings |" in capsys.readouterr().out
    assert cli.main(["map", "method", "--file", "r.csv", "--name", "play"]) == 0
    assert "engine.Engine.play  (game)" in capsys.readouterr().out
    assert cli.main(["map", "method", "--file", "r.csv"]) == 2  # needs --name


def test_map_without_maps_is_a_clear_error(tmp_path, monkeypatch, capsys):
    game, root, ws = _traced_workspace(tmp_path, monkeypatch)
    capsys.readouterr()
    assert cli.main(["map", "tree", "--row", "x"]) == 2
    assert "run `slotdebug trace-tester` first" in capsys.readouterr().err


# ---- editor hooks: session start and post-edit ----

def test_session_start_is_silent_without_a_workspace(tmp_path):
    from slotdebugger import hooks
    assert hooks.session_start(str(tmp_path)) is None


def test_session_start_reports_problems_and_staleness(tmp_path, monkeypatch, capsys):
    from slotdebugger import hooks
    game, root, ws = _traced_workspace(tmp_path, monkeypatch)
    assert "no maps yet" in hooks.session_start(str(root))
    assert _trace(game) == 0
    text = hooks.session_start(str(root))
    assert "maps for `report`" in text and "[response_key_never_returned]" in text
    assert "Stale" not in text
    engine = root / "engine.py"
    engine.write_text(engine.read_text() + "\n# edited\n")
    assert "Stale: changed since the maps were built: engine.py" in hooks.session_start(str(root))


def test_post_edit_reindexes_and_reports_new_problems(tmp_path, monkeypatch, capsys):
    from slotdebugger import hooks
    game, root, ws = _traced_workspace(tmp_path, monkeypatch)
    assert _trace(game) == 0
    engine = root / "engine.py"
    engine.write_text(engine.read_text()
                      .replace('action = "free"', 'win = self.n * 2\n        win = 5\n        action = "free"')
                      .replace('"current_winnings": 5,', '"current_winnings": win,'))
    text = hooks.post_edit(str(root), str(engine))
    assert "re-indexed the engine after the edit to engine.py" in text
    assert "new: [overwritten_by_literal]" in text and "`win = 5`" in text
    doc = json.loads((ws / "analysis/engine_map.json").read_text())
    assert any(d["variable"] == "win" for d in doc["detections"])  # map and summaries rewritten
    assert "overwritten_by_literal" in (ws / "analysis/report.summary.md").read_text()

    # fixing it reports the problem gone
    engine.write_text(engine.read_text().replace("        win = 5\n", ""))
    assert "no longer found: [overwritten_by_literal]" in hooks.post_edit(str(root), str(engine))


def test_post_edit_ignores_files_outside_the_map(tmp_path, monkeypatch, capsys):
    from slotdebugger import hooks
    game, root, ws = _traced_workspace(tmp_path, monkeypatch)
    assert _trace(game) == 0
    other = root / "notes.py"
    other.write_text("x = 1\n")
    assert hooks.post_edit(str(root), str(other)) is None
    assert hooks.post_edit(str(root), str(root / "README.md")) is None
    tester = root / "tests/volume_tester.py"
    assert "--refresh-tester" in hooks.post_edit(str(root), str(tester))


# ---- installing hooks and the Claude Code plugin ----

def test_install_hook_registers_both_events_and_keeps_others(tmp_path):
    settings_path = tmp_path / ".claude/settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"hooks": {
        "SessionStart": [{"hooks": [{"type": "command", "command": "slotdebug think --status"}]},  # old ours
                         {"hooks": [{"type": "command", "command": "echo mine"}]}],                 # theirs
    }}))
    cli.main(["install", "claude", "--scope", "project", "--hook", "--dir", str(tmp_path)])
    hooks = json.loads(settings_path.read_text())["hooks"]
    commands = [h["command"] for e in hooks["SessionStart"] for h in e["hooks"]]
    assert "echo mine" in commands and not any("think --status" in c for c in commands)
    assert any(c.endswith("hook session-start") for c in commands)
    assert hooks["PostToolUse"][0]["matcher"] == "Edit|Write|MultiEdit"
    assert hooks["PostToolUse"][0]["hooks"][0]["command"].endswith("hook post-edit")
    assert hooks["PostToolUse"][1]["matcher"] == "Agent|Task"
    assert hooks["SubagentStop"][0]["matcher"] == "slot-debugger|slotdebug:slot-debugger"
    assert hooks["UserPromptSubmit"][0]["hooks"][0]["command"].endswith("hook prompt")

    cli.main(["install", "claude", "--scope", "project", "--hook", "--dir", str(tmp_path)])  # idempotent
    hooks = json.loads(settings_path.read_text())["hooks"]
    assert len(hooks["SessionStart"]) == 2 and len(hooks["PostToolUse"]) == 2

    cli.main(["uninstall", "claude", "--scope", "project", "--dir", str(tmp_path)])
    assert json.loads(settings_path.read_text()) == {"hooks": {"SessionStart": [
        {"hooks": [{"type": "command", "command": "echo mine"}]}]}}


def test_hook_command_answers_in_claude_hook_json(tmp_path, monkeypatch, capsys):
    import io
    game, root, ws = _traced_workspace(tmp_path, monkeypatch)
    assert _trace(game) == 0
    capsys.readouterr()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(root), "hook_event_name": "SessionStart"})))
    assert cli.main(["hook", "session-start"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "[response_key_never_returned]" in out["hookSpecificOutput"]["additionalContext"]

    engine = root / "engine.py"
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"cwd": str(root), "tool_name": "Edit", "tool_input": {"file_path": str(engine)}})))
    assert cli.main(["hook", "post-edit"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "re-indexed the engine" in out["hookSpecificOutput"]["additionalContext"]


def test_hook_is_silent_outside_a_workspace(tmp_path, monkeypatch, capsys):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(tmp_path)})))
    assert cli.main(["hook", "session-start"]) == 0
    assert capsys.readouterr().out == ""


def test_install_plugin_writes_a_local_marketplace(tmp_path, capsys):
    dest = tmp_path / "market"
    assert cli.main(["install", "plugin", "--dir", str(dest)]) == 0
    market = json.loads((dest / ".claude-plugin/marketplace.json").read_text())
    assert market["plugins"][0] == {"name": "slotdebug", "source": "./plugins/slotdebug",
                                    "description": market["plugins"][0]["description"]}
    plugin = dest / "plugins/slotdebug"
    assert json.loads((plugin / ".claude-plugin/plugin.json").read_text())["name"] == "slotdebug"
    for skill in install.SKILLS:
        assert (plugin / f"skills/{skill}/SKILL.md").exists()
    assert (plugin / "skills/slot-map/SKILL.md").read_text().startswith("---\nname: slot-map")
    tree = (plugin / "commands/tree.md").read_text()
    assert 'allowed-tools: "Bash(' in tree and "map tree $ARGUMENTS 2>&1 || true`" in tree
    yaml = pytest.importorskip("yaml")
    front = yaml.safe_load(tree.split("---")[1])  # the front matter must parse
    assert front["description"].startswith("Show a report row's chain: report row")
    hooks = json.loads((plugin / "hooks/hooks.json").read_text())
    assert set(hooks["hooks"]) == {"SessionStart", "UserPromptSubmit", "PostToolUse", "SubagentStop"}
    agent = (plugin / "agents/slot-debugger.md").read_text()
    assert agent.startswith("---\nname: slot-debugger") and "hooks:" not in agent.split("---")[1]
    assert "Delegate to the slot-debugger agent" in (plugin / "commands/debug.md").read_text()
    inline = (plugin / "commands/diagnose.md").read_text()  # the same diagnosis without the agent
    assert "in this conversation" in inline and "slotdebug report" in inline
    assert "claude plugin install slotdebug@slotdebug" in capsys.readouterr().out

    assert cli.main(["uninstall", "plugin", "--dir", str(dest)]) == 0
    assert not dest.exists()


def test_uninstall_plugin_leaves_a_foreign_directory_alone(tmp_path, capsys):
    other = tmp_path / "other/.claude-plugin"
    other.mkdir(parents=True)
    (other / "marketplace.json").write_text(json.dumps({"name": "someone-else"}))
    assert cli.main(["uninstall", "plugin", "--dir", str(tmp_path / "other")]) == 0
    assert (other / "marketplace.json").exists()


def test_setup_registers_the_slot_map_skill(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["setup", "--name", "g"]) == 0
    assert (tmp_path / ".claude/skills/slot-map/SKILL.md").exists()
    assert (tmp_path / ".cursor/rules/slot-map.mdc").exists()


# ---- the slot-debugger agent: registration, guard, report, gate, hand-off ----

def test_setup_registers_the_agent_with_its_read_only_guard(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["setup", "--name", "g"]) == 0
    agent = (tmp_path / ".claude/agents/slot-debugger.md").read_text()
    front = agent.split("---")[1]
    assert "name: slot-debugger" in front and "tools: Bash, Read, Grep, Glob" in front
    assert "Edit" not in front and "Write" not in front  # diagnose only
    assert "PreToolUse" in front and "hook agent-bash" in front
    yaml = pytest.importorskip("yaml")
    parsed = yaml.safe_load(front)
    assert parsed["hooks"]["PreToolUse"][0]["matcher"] == "Bash"
    cli.main(["uninstall", "claude", "--scope", "project", "--dir", str(tmp_path)])
    assert not (tmp_path / ".claude/agents/slot-debugger.md").exists()


@pytest.mark.parametrize("command, allowed", [
    ("slotdebug map tree --row x", True),
    ("sed -n 80,90p engine.py", True),
    ("grep -n stop engine.py && head -5 defs.py", True),
    ("git log -3 -- engine.py", True),
    ("slotdebug map problems 2>&1 | head", True),
    ("sed -i 's/a/b/' engine.py", False),
    ("echo x > engine.py", False),
    ("grep x f 2>/tmp/err", False),
    ("cat f | tee out", False),
    ("rm -rf slotdebugger", False),
    ("git checkout engine.py", False),
    ("find . -name '*.py' -delete", False),
    ("python3 -c 'open(\"x\",\"w\")'", False),
    ("slotdebug install claude", False),
])
def test_agent_bash_guard(command, allowed):
    from slotdebugger import hooks
    assert (hooks.agent_bash(command) is None) is allowed


def test_hook_agent_bash_refuses_with_exit_2(monkeypatch, capsys):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_input": {"command": "rm engine.py"}})))
    assert cli.main(["hook", "agent-bash"]) == 2
    assert "refused" in capsys.readouterr().err
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_input": {"command": "slotdebug runs"}})))
    assert cli.main(["hook", "agent-bash"]) == 0


def _diagnosed(tmp_path, monkeypatch, capsys, evidence="engine.py:5"):
    game, root, ws = _traced_workspace(tmp_path, monkeypatch)
    assert _trace(game) == 0
    for args in (["--setTargets", "current_winnings"],
                 ["--thought", "the win is constant", "--thoughtNumber", "1", "--totalThoughts", "1",
                  "--nextThoughtNeeded", "true", "--evidence", evidence],
                 ["--finding", "current_winnings is a constant 5", "--target", "current_winnings",
                  "--evidence", evidence, "--fix", "engine.py:5: compute the win from the grid"]):
        assert cli.main(["think", *args]) == 0
    capsys.readouterr()
    return game, root, ws


def test_report_assembles_the_diagnosis(tmp_path, monkeypatch, capsys):
    game, root, ws = _diagnosed(tmp_path, monkeypatch, capsys)
    assert cli.main(["report", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["status"] == "complete"
    finding = doc["findings"][0]
    assert finding["fix"] == "engine.py:5: compute the win from the grid"
    assert finding["evidence_check"][0]["exists"] is True  # engine.py has a line 5
    assert doc["targets"][0]["explained_by"] == [1]
    assert doc["mode"].startswith("diagnosis: fixes are proposed, not applied")
    assert (ws / "analysis/report.agent_report.json").exists() and (ws / "analysis/report.agent_report.md").exists()
    assert any("apply the proposed fix" in s for s in doc["next_steps"])


def test_agent_stop_gate(tmp_path, monkeypatch, capsys):
    import io
    from slotdebugger import hooks
    game, root, ws = _diagnosed(tmp_path, monkeypatch, capsys, evidence="engine.py:9999")
    assert "run `slotdebug report" in hooks.agent_stop(str(root))  # no report written yet
    assert cli.main(["report"]) == 0
    assert "does not resolve" in hooks.agent_stop(str(root))  # engine.py has no line 9999
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(root), "stop_hook_active": False})))
    assert cli.main(["hook", "agent-stop"]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["decision"] == "block"
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(root), "stop_hook_active": True})))
    capsys.readouterr()
    assert cli.main(["hook", "agent-stop"]) == 0
    assert capsys.readouterr().out == ""  # sent back once already: let it finish


def test_agent_stop_passes_a_sound_diagnosis(tmp_path, monkeypatch, capsys):
    from slotdebugger import hooks
    game, root, ws = _diagnosed(tmp_path, monkeypatch, capsys)
    assert cli.main(["report"]) == 0
    assert hooks.agent_stop(str(root)) is None


def test_agent_result_reaches_the_parent(tmp_path, monkeypatch, capsys):
    import io
    game, root, ws = _diagnosed(tmp_path, monkeypatch, capsys)
    assert cli.main(["report"]) == 0
    capsys.readouterr()
    event = {"cwd": str(root), "tool_name": "Agent", "tool_input": {"subagent_type": "slot-debugger"}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    assert cli.main(["hook", "agent-result"]) == 0
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "slot-debugger diagnosis (complete)" in context and "proposed fix: engine.py:5" in context
    event["tool_input"]["subagent_type"] = "Explore"  # another agent: nothing added
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    assert cli.main(["hook", "agent-result"]) == 0
    assert capsys.readouterr().out == ""


def test_prompt_hint_only_for_named_reports(tmp_path, monkeypatch, capsys):
    from slotdebugger import hooks
    game, root, ws = _traced_workspace(tmp_path, monkeypatch)
    (ws / "reports/blazing.csv").write_text("x")
    hint = hooks.prompt_hint(str(root), "why is FG12 low in blazing.csv?")
    assert "in this session" in hint and "slot-debugger agent" in hint  # both ways are offered
    assert hooks.prompt_hint(str(root), "refactor the parser") is None


def test_skill_carries_the_inline_diagnosis():
    """A session without the agent (or Cursor) gets the whole procedure from the skill."""
    from slotdebugger import install
    skill = install.skill_text("slot-debugger")
    section = skill.split("## Diagnose a report", 1)[1].split("\n## ", 1)[0]
    for step in ("slotdebug analyze", "slotdebug diff", "slotdebug trace-tester", "--setTargets",
                 "--conclude", "--finding", "--fix", "slotdebug report"):
        assert step in section, step
    assert section.index("slotdebug report") < section.index("Fix and confirm")


def test_think_fix_belongs_to_a_finding(tmp_path, capsys):
    assert cli.main(["think", "--state", str(tmp_path / "s.json"), "--status", "--fix", "x"]) == 2
