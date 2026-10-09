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
    assert info["version"] == 2 and info["game"] == "blazing"
    assert info["created"] == marker["created"]  # creation time preserved
    assert (ws / "state").is_dir()               # directory added by the new version
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
    assert "diff" in [c["name"] if isinstance(c, dict) else c for c in get_registry().list_commands()]
