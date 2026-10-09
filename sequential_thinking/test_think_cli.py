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


def branch(n, branch_id="b", origin="1", *extra):
    return thought(n, 5, "alt", "true", "--branchFromThought", origin, "--branchId", branch_id, *extra)


def test_evidence_stored_and_shown(isolated_state, capsys):
    assert thought(1, 5, "cap applied after sum", "true",
                   "--evidence", "engine.py:143", "--evidence", "slotdebug aggregate --file r.xlsx") == 0
    assert think.load_state().thoughtHistory[0]["evidence"] == ["engine.py:143", "slotdebug aggregate --file r.xlsx"]
    out, err = capsys.readouterr()
    assert "🔎 engine.py:143" in out
    assert "no --evidence" not in err


def test_thought_without_evidence_warns(isolated_state, capsys):
    assert thought(1) == 0
    assert "no --evidence" in capsys.readouterr().err
    assert think.load_state().thoughtHistory[0]["evidence"] == []


def test_evidence_rejected_outside_entries(isolated_state, capsys):
    assert run("--status", "--evidence", "x") == 2
    assert thought(1, 5, "t", "true", "--evidence", "  ") == 2


def test_conclude_branch(isolated_state, capsys):
    thought(1)
    branch(2, "cap")
    assert run("--conclude", "cap", "--verdict", "refuted", "--reason", "cap never hit",
               "--evidence", "report: winnings_cap_applied=0") == 0
    state = think.load_state()
    assert state.conclusions["cap"]["verdict"] == "refuted"
    assert state.conclusions["cap"]["afterThought"] == 2
    assert think.make_status(state)["openBranches"] == []


def test_conclude_requires_branch_verdict_reason_evidence(isolated_state, capsys):
    thought(1)
    branch(2, "cap")
    assert run("--conclude", "nope", "--verdict", "refuted", "--reason", "r", "--evidence", "e") == 2
    assert run("--conclude", "cap", "--reason", "r", "--evidence", "e") == 2
    assert run("--conclude", "cap", "--verdict", "refuted", "--evidence", "e") == 2
    assert run("--conclude", "cap", "--verdict", "refuted", "--reason", "r") == 2
    with pytest.raises(SystemExit):
        run("--conclude", "cap", "--verdict", "maybe", "--reason", "r", "--evidence", "e")
    assert think.load_state().conclusions == {}


def test_concluded_branch_is_closed(isolated_state, capsys):
    thought(1)
    branch(2, "cap")
    run("--conclude", "cap", "--verdict", "confirmed", "--reason", "r", "--evidence", "e")
    assert branch(3, "cap") == 2
    assert run("--conclude", "cap", "--verdict", "refuted", "--reason", "r", "--evidence", "e") == 2
    assert "already confirmed" in capsys.readouterr().err


def test_finding_requires_evidence_and_a_thought(isolated_state, capsys):
    assert run("--finding", "x", "--evidence", "e") == 2
    thought(1)
    assert run("--finding", "x") == 2
    assert run("--finding", "ways rows never link: tester reads ways_wins",
               "--evidence", "volume_tester.py:96", "--evidence", "engine.py:179") == 0
    state = think.load_state()
    assert state.findings[0]["evidence"] == ["volume_tester.py:96", "engine.py:179"]
    assert think.make_status(state)["findings"] == 1


def test_finding_warns_about_open_branches(isolated_state, capsys):
    thought(1)
    branch(2, "cap")
    assert run("--finding", "x", "--evidence", "e") == 0
    assert "branches still open: cap" in capsys.readouterr().err


def test_history_lists_conclusions_and_findings(isolated_state, capsys):
    thought(1)
    branch(2, "cap")
    run("--conclude", "cap", "--verdict", "refuted", "--reason", "never hit", "--evidence", "e")
    run("--finding", "the bug", "--evidence", "engine.py:1")
    capsys.readouterr()
    assert run("--history") == 0
    out = capsys.readouterr().out
    assert "❌ Branch cap refuted" in out and "📌 Finding 1" in out


def test_old_state_file_loads(isolated_state, capsys):
    with open(isolated_state, "w") as f:
        json.dump({"thoughtHistory": [], "branches": {}, "plan": ["a"], "currentStepIndex": 0}, f)
    state = think.load_state()
    assert state.conclusions == {} and state.findings == []
    assert thought(1) == 0


def test_verdict_without_conclude_rejected(isolated_state, capsys):
    thought(1)
    assert run("--status", "--verdict", "refuted") == 2


# ---- targets, target/cause on branches, similarity, done check ----

def about(n, branch_id, target, cause, text="alt", *extra):
    return branch(n, branch_id, "1", "--target", target, "--cause", cause, *extra) if text == "alt" else \
        thought(n, 5, text, "true", "--branchFromThought", "1", "--branchId", branch_id,
                "--target", target, "--cause", cause, *extra)


def test_set_targets_from_diff_file(isolated_state, tmp_path, capsys):
    diff = tmp_path / "r.diff.json"
    diff.write_text(json.dumps({"open": ["components.FG12", "bet"], "diffs": []}))
    assert run("--setTargets", str(diff)) == 0
    assert think.load_state().targets == ["components.FG12", "bet"]
    assert run("--setTargets", "FG12, BG") == 0
    assert think.load_state().targets == ["FG12", "BG"]


def test_set_targets_rejects_file_without_open_list(isolated_state, tmp_path, capsys):
    bad = tmp_path / "x.json"
    bad.write_text(json.dumps({"diffs": []}))
    assert run("--setTargets", str(bad)) == 2


def test_branch_records_target_and_cause(isolated_state, capsys):
    thought(1)
    assert about(2, "cap", "FG12", "cap") == 0
    assert think.load_state().branchMeta["cap"] == {"target": "FG12", "cause": "cap"}
    assert branch(3, "cap") == 0  # continuing keeps what the branch is about
    assert think.load_state().thoughtHistory[2]["target"] == "FG12"
    assert about(4, "cap", "FG12", "multiplier") == 2  # cannot change the question mid-branch


def test_target_needs_cause_and_a_branch(isolated_state, capsys):
    thought(1)
    assert branch(2, "b", "1", "--target", "FG12") == 2
    assert thought(2, 5, "t", "true", "--target", "FG12", "--cause", "cap") == 2


def test_concluded_target_cause_refused_under_any_branch_name(isolated_state, capsys):
    thought(1)
    about(2, "cap", "FG12", "cap")
    run("--conclude", "cap", "--verdict", "refuted", "--reason", "cap never hit", "--evidence", "e")
    capsys.readouterr()
    assert about(3, "cap-again", "fg12 ", "CAP") == 2
    assert "already refuted in branch 'cap': cap never hit" in capsys.readouterr().err
    assert about(3, "cap-again", "FG12", "cap", "alt", "--force") == 0
    assert think.load_state().thoughtHistory[2]["forced"] is True


def test_same_cause_other_target_is_a_new_question(isolated_state, capsys):
    thought(1)
    about(2, "cap", "FG12", "cap")
    run("--conclude", "cap", "--verdict", "refuted", "--reason", "r", "--evidence", "e")
    assert about(3, "cap-bg", "BG", "cap") == 0


def test_open_duplicate_must_be_continued(isolated_state, capsys):
    thought(1)
    about(2, "cap", "FG12", "cap")
    assert about(3, "cap2", "FG12", "cap") == 2
    assert "continue that branch" in capsys.readouterr().err


def test_branch_needs_target_while_targets_set(isolated_state, capsys):
    run("--setTargets", "FG12")
    thought(1)
    assert branch(2, "b") == 2
    assert about(2, "b", "FG99", "cap") == 2  # not a target
    assert about(2, "b", "FG12", "cap") == 0


def test_finding_targets_must_be_known(isolated_state, capsys):
    run("--setTargets", "FG12,BG")
    thought(1)
    assert run("--finding", "f", "--evidence", "e", "--target", "FG99") == 2
    assert run("--finding", "f", "--evidence", "e", "--target", "FG12") == 0
    assert think.make_status(think.load_state())["unexplainedTargets"] == ["BG"]


def test_cannot_finish_with_open_branch_or_unexplained_target(isolated_state, capsys):
    run("--setTargets", "FG12")
    thought(1)
    about(2, "cap", "FG12", "cap")
    capsys.readouterr()
    assert thought(3, 3, "done", "false") == 2
    err = capsys.readouterr().err
    assert "open branches: cap" in err and "targets with no finding: FG12" in err
    run("--conclude", "cap", "--verdict", "confirmed", "--reason", "r", "--evidence", "e")
    assert thought(3, 3, "done", "false") == 2
    run("--finding", "cap clips FG12", "--evidence", "engine.py:143", "--target", "FG12")
    assert thought(3, 3, "done", "false") == 0


def test_force_finishes_with_warning(isolated_state, capsys):
    thought(1)
    branch(2, "b")
    assert thought(3, 3, "give up", "false", "--force") == 0
    assert "finished with open branches: b" in capsys.readouterr().err


def test_alike_wording_warns_but_records(isolated_state, capsys):
    thought(1, 5, "the winnings cap clips current winnings on big spins")
    capsys.readouterr()
    assert thought(2, 5, "winnings cap clips current winnings on large spins") == 0
    assert "may already be covered by thought 1" in capsys.readouterr().err
    assert len(think.load_state().thoughtHistory) == 2


def test_shared_evidence_warns(isolated_state, capsys):
    thought(1, 5, "bonus is multiplied", "true", "--evidence", "engine.py:132")
    capsys.readouterr()
    thought(2, 5, "something unrelated entirely", "true", "--evidence", "engine.py:132")
    assert "same evidence engine.py:132" in capsys.readouterr().err


def test_line_numbers_are_whole_words(isolated_state, capsys):
    """engine.py:143 and engine.py:136 are different lines, not near-identical text."""
    assert think.words("see engine.py:143.") == ["see", "engine.py:143"]
    thought(1, 5, "engine.py:143")
    capsys.readouterr()
    thought(2, 5, "engine.py:136")
    assert "may already be covered" not in capsys.readouterr().err


def test_other_target_is_not_similar(isolated_state, capsys):
    thought(1)
    about(2, "a", "FG12", "cap", "cap clips the FG12 total")
    capsys.readouterr()
    about(3, "b", "BG", "cap", "cap clips the BG total")
    assert "may already be covered" not in capsys.readouterr().err


def test_continuing_a_branch_is_not_flagged_against_itself(isolated_state, capsys):
    thought(1)
    branch(2, "b", "1")
    capsys.readouterr()
    branch(3, "b", "1")  # same text "alt", same branch
    assert "may already be covered" not in capsys.readouterr().err


def test_similar_lists_matches_with_verdicts(isolated_state, capsys):
    thought(1)
    about(2, "cap", "FG12", "cap", "winnings cap clips the FG12 total")
    run("--conclude", "cap", "--verdict", "refuted", "--reason", "cap applies after the event value",
        "--evidence", "engine.py:143")
    capsys.readouterr()
    assert run("--similar", "does the winnings cap clip the FG12 total", "--target", "FG12") == 0
    matches = json.loads(capsys.readouterr().out)
    assert matches[0]["branch"] == "cap" and matches[0]["verdict"] == "refuted"
    assert run("--similar", "x", "--evidence", "engine.py:143") == 0
    assert json.loads(capsys.readouterr().out)[0]["sharedEvidence"] == ["engine.py:143"]


def test_new_flags_rejected_out_of_place(isolated_state, capsys):
    assert run("--status", "--target", "x") == 2
    assert run("--status", "--cause", "x") == 2
    assert run("--status", "--force") == 2
