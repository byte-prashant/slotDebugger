# ==============================
# tests_think.py
# ==============================

"""
Run using: python tests_think.py
"""

import os
from think import (
    load_state,
    save_state,
    parse_bool,
    make_status,
    ThoughtData,
    STATE_FILE,
    get_current_thought,
    set_plan,
    get_current_plan_step,
    advance_plan,
)


def reset():
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)


def test_empty_state():
    reset()
    state = load_state()
    assert len(state.thoughtHistory) == 0


def test_add_thought():
    reset()
    state = load_state()

    t = ThoughtData(
        thought="hello",
        thoughtNumber=1,
        totalThoughts=2,
        nextThoughtNeeded=True
    ).to_dict()

    state.thoughtHistory.append(t)
    save_state(state)

    new_state = load_state()
    assert new_state.thoughtHistory[0]["thought"] == "hello"


def test_branching():
    reset()
    state = load_state()

    t = ThoughtData(
        thought="branch test",
        thoughtNumber=2,
        totalThoughts=3,
        nextThoughtNeeded=True,
        branchFromThought=1,
        branchId="b1"
    ).to_dict()

    state.thoughtHistory.append(t)
    state.branches.setdefault("b1", []).append(t)
    save_state(state)

    new_state = load_state()
    assert "b1" in new_state.branches
    assert len(new_state.branches["b1"]) == 1


def test_revision():
    reset()
    state = load_state()

    t = ThoughtData(
        thought="revision",
        thoughtNumber=2,
        totalThoughts=3,
        nextThoughtNeeded=True,
        isRevision=True,
        revisesThought=1
    ).to_dict()

    state.thoughtHistory.append(t)
    save_state(state)

    new_state = load_state()
    assert new_state.thoughtHistory[0]["isRevision"] is True


def test_current_thought():
    reset()
    state = load_state()

    for i in range(2):
        t = ThoughtData(
            thought=f"t{i}",
            thoughtNumber=i+1,
            totalThoughts=2,
            nextThoughtNeeded=True
        ).to_dict()
        state.thoughtHistory.append(t)

    save_state(state)
    new_state = load_state()

    current = get_current_thought(new_state)
    assert current["thought"] == "t1"


def test_parse_bool():
    assert parse_bool("true") is True
    assert parse_bool("false") is False


# -------- NEW TESTS FOR PLANNING --------

def test_set_plan_and_get_step():
    reset()
    state = load_state()
    steps = ["Check RTP", "Analyze reels", "Validate RNG"]
    set_plan(state, steps)
    save_state(state)

    new_state = load_state()
    assert new_state.plan == steps
    assert get_current_plan_step(new_state) == "Check RTP"


def test_advance_plan():
    reset()
    state = load_state()
    steps = ["A", "B", "C"]
    set_plan(state, steps)

    advance_plan(state)
    assert get_current_plan_step(state) == "B"

    advance_plan(state)
    assert get_current_plan_step(state) == "C"

    # should not overflow
    advance_plan(state)
    assert get_current_plan_step(state) == "C"


def test_thought_attaches_plan_step():
    reset()
    state = load_state()
    steps = ["Step1", "Step2"]
    set_plan(state, steps)

    t = ThoughtData(
        thought="doing step",
        thoughtNumber=1,
        totalThoughts=2,
        nextThoughtNeeded=True,
        planStep=get_current_plan_step(state),
    ).to_dict()

    state.thoughtHistory.append(t)
    save_state(state)

    new_state = load_state()
    assert new_state.thoughtHistory[0]["planStep"] == "Step1"


if __name__ == "__main__":
    test_empty_state()
    test_add_thought()
    test_branching()
    test_revision()
    test_current_thought()
    test_parse_bool()

    test_set_plan_and_get_step()
    test_advance_plan()
    test_thought_attaches_plan_step()

    print("All tests passed ✅")
