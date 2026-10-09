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
    assert "base_total_win" in out["dependency_graph"]


def test_analyze_detects_mismatch(report, capsys):
    report.write_text(REPORT.replace("(90.0, 15)", "(80.0, 15)"))
    cli.main(["analyze", "--file", str(report)])
    out = json.loads(capsys.readouterr().out)
    assert out["analysis"]["status"] == "FAIL"


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
        "bet": 75.0,
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
    assert saved == {"bet": 75.0, "total": 96.32,
                     "components": {"BG": 40.6, "FG": 55.72}}

    # the run index still carries the native status/RTP, whatever shape was written
    cli.main(["runs"])
    runs = json.loads(capsys.readouterr().out)
    assert runs[0]["report"] == "full.csv" and runs[0]["status"] == "OK"


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
