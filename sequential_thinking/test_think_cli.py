"""CLI-level tests: validation and error reporting."""
import json

import pytest

import think


def run(*args):
    return think.main(list(args))


def thought(n, total=5, text="t", more="true", *extra):
    return run("--thought", text, "--thoughtNumber", str(n), "--totalThoughts", str(total),
               "--nextThoughtNeeded", more, *extra)


def test_sequential_numbers_accepted(isolated_state, capsys):
    assert thought(1) == 0
    assert thought(2) == 0
    assert len(think.load_state().thoughtHistory) == 2


def test_skipped_number_rejected(isolated_state, capsys):
    assert thought(1) == 0
    assert thought(3) == 2
    assert "thoughtNumber must be 2" in capsys.readouterr().err
    assert len(think.load_state().thoughtHistory) == 1


def test_first_thought_must_be_one(isolated_state, capsys):
    assert thought(2) == 2


def test_revision_requires_valid_target(isolated_state, capsys):
    thought(1)
    assert thought(2, 5, "fix", "true", "--isRevision") == 2
    assert thought(2, 5, "fix", "true", "--isRevision", "--revisesThought", "9") == 2
    assert thought(2, 5, "fix", "true", "--revisesThought", "1") == 2
    assert thought(2, 5, "fix", "true", "--isRevision", "--revisesThought", "1") == 0


def test_branch_requires_id_and_valid_origin(isolated_state, capsys):
    thought(1)
    assert thought(2, 5, "alt", "true", "--branchFromThought", "1") == 2
    assert thought(2, 5, "alt", "true", "--branchId", "b") == 2
    assert thought(2, 5, "alt", "true", "--branchFromThought", "7", "--branchId", "b") == 2
    assert thought(2, 5, "alt", "true", "--branchFromThought", "1", "--branchId", "b") == 0
    assert "b" in think.load_state().branches


def test_revision_and_branch_exclusive(isolated_state, capsys):
    thought(1)
    assert thought(2, 5, "x", "true", "--isRevision", "--revisesThought", "1",
                   "--branchFromThought", "1", "--branchId", "b") == 2


def test_invalid_bool_rejected(isolated_state, capsys):
    assert thought(1, 5, "x", "maybe") == 2
    assert "invalid boolean" in capsys.readouterr().err


def test_empty_thought_rejected(isolated_state, capsys):
    assert thought(1, 5, "   ") == 2


def test_nextstep_without_plan_errors(isolated_state, capsys):
    assert run("--nextStep") == 2
    assert "no plan set" in capsys.readouterr().err


def test_nextstep_reports_last_step(isolated_state, capsys):
    run("--setPlan", "A,B")
    run("--nextStep")
    capsys.readouterr()
    assert run("--nextStep") == 0
    assert "'advanced': False" in capsys.readouterr().out


def test_conflicting_modes_rejected(isolated_state, capsys):
    assert run("--setPlan", "A,B", "--status") == 2


def test_bad_plan_json_is_clean_error(isolated_state, capsys):
    assert run("--setPlan", "[oops") == 2


def test_plan_step_attached(isolated_state):
    run("--setPlan", "A,B")
    thought(1)
    assert think.load_state().thoughtHistory[0]["planStep"] == "A"


def test_state_flag_overrides_path(tmp_path):
    p = tmp_path / "custom.json"
    assert run("--state", str(p), "--setPlan", "A") == 0
    assert json.loads(p.read_text())["plan"] == ["A"]
