#!/usr/bin/env python3
"""
Sequential thinking state machine (Python version) with Planning Layer.
"""

import json
import os
import argparse
from typing import Dict, List, Optional

try:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    BASE_DIR = os.getcwd()

STATE_FILE = os.path.join(BASE_DIR, ".think_state.json")


class ThoughtData:
    def __init__(self, **kwargs):
        self.thought = kwargs.get("thought")
        self.thoughtNumber = kwargs.get("thoughtNumber")
        self.totalThoughts = kwargs.get("totalThoughts")
        self.nextThoughtNeeded = kwargs.get("nextThoughtNeeded")
        self.isRevision = kwargs.get("isRevision", False)
        self.revisesThought = kwargs.get("revisesThought")
        self.branchFromThought = kwargs.get("branchFromThought")
        self.branchId = kwargs.get("branchId")
        self.needsMoreThoughts = kwargs.get("needsMoreThoughts", False)
        # NEW: link thought to a plan step
        self.planStep = kwargs.get("planStep")

    def to_dict(self):
        return self.__dict__


class State:
    def __init__(self):
        self.thoughtHistory: List[Dict] = []
        self.branches: Dict[str, List[Dict]] = {}
        # NEW: Planning layer
        self.plan: List[str] = []
        self.currentStepIndex: int = 0


def load_state() -> State:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
            state = State()
            state.thoughtHistory = data.get("thoughtHistory", [])
            state.branches = data.get("branches", {})
            state.plan = data.get("plan", [])
            state.currentStepIndex = data.get("currentStepIndex", 0)
            return state
    return State()


def save_state(state: State):
    with open(STATE_FILE, "w") as f:
        json.dump({
            "thoughtHistory": state.thoughtHistory,
            "branches": state.branches,
            "plan": state.plan,
            "currentStepIndex": state.currentStepIndex,
        }, f, indent=2)


# ----------------------
# Planning Layer Helpers
# ----------------------

def set_plan(state: State, steps: List[str]):
    state.plan = steps
    state.currentStepIndex = 0


def get_current_plan_step(state: State) -> Optional[str]:
    if not state.plan:
        return None
    if state.currentStepIndex >= len(state.plan):
        return None
    return state.plan[state.currentStepIndex]


def advance_plan(state: State):
    if state.currentStepIndex < len(state.plan) - 1:
        state.currentStepIndex += 1


def attach_plan_step(thought: Dict, step: Optional[str]):
    if step:
        thought["planStep"] = step
    return thought


# ----------------------
# Core helpers
# ----------------------

def get_current_thought(state: State):
    if not state.thoughtHistory:
        return None
    return state.thoughtHistory[-1]


def format_thought(t):
    if t.get("isRevision") and t.get("revisesThought"):
        header = f"🔄 Revision {t['thoughtNumber']}/{t['totalThoughts']} (revises {t['revisesThought']})"
    elif t.get("branchFromThought") and t.get("branchId"):
        header = f"🌿 Branch {t['thoughtNumber']}/{t['totalThoughts']} (from {t['branchFromThought']})"
    else:
        header = f"💭 Thought {t['thoughtNumber']}/{t['totalThoughts']}"

    step = t.get("planStep")
    step_line = f"\n📍 Step: {step}" if step else ""

    return f"{header}{step_line}\n{t['thought']}"


def make_status(state: State):
    current = get_current_thought(state)
    return {
        "thoughtNumber": current["thoughtNumber"] if current else 0,
        "totalThoughts": current["totalThoughts"] if current else 0,
        "nextThoughtNeeded": current["nextThoughtNeeded"] if current else True,
        # NEW: expose planning status
        "currentPlanStep": get_current_plan_step(state),
        "planProgress": f"{state.currentStepIndex+1}/{len(state.plan)}" if state.plan else None,
    }


def parse_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    return str(value).lower() in ("true", "1", "yes")


def parse_plan(value: Optional[str]) -> Optional[List[str]]:
    """Accepts comma-separated or JSON list string"""
    if not value:
        return None
    value = value.strip()
    if value.startswith("["):
        try:
            arr = json.loads(value)
            return [str(x) for x in arr]
        except Exception:
            raise ValueError("Invalid JSON for --setPlan")
    # comma separated
    return [x.strip() for x in value.split(",") if x.strip()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--thought")
    parser.add_argument("--thoughtNumber", type=int)
    parser.add_argument("--totalThoughts", type=int)
    parser.add_argument("--nextThoughtNeeded")

    # branching & revision
    parser.add_argument("--isRevision", action="store_true")
    parser.add_argument("--revisesThought", type=int)
    parser.add_argument("--branchFromThought", type=int)
    parser.add_argument("--branchId")

    # NEW: planning controls
    parser.add_argument("--setPlan", help="JSON list or comma-separated steps")
    parser.add_argument("--nextStep", action="store_true", help="Advance to next plan step")

    parser.add_argument("--status", action="store_true")
    parser.add_argument("--reset", action="store_true")

    args = parser.parse_args()

    if args.reset:
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
        print("reset done")
        return

    state = load_state()

    # set plan
    if args.setPlan:
        steps = parse_plan(args.setPlan)
        if not steps:
            raise ValueError("--setPlan must contain at least one step")
        set_plan(state, steps)
        save_state(state)
        print({"plan": state.plan, "currentStep": get_current_plan_step(state)})
        return

    # advance plan
    if args.nextStep:
        advance_plan(state)
        save_state(state)
        print({"currentStep": get_current_plan_step(state)})
        return

    if args.status:
        print(make_status(state))
        return

    if not args.thought:
        parser.print_help()
        return

    if args.thoughtNumber is None or args.totalThoughts is None:
        raise ValueError("thoughtNumber & totalThoughts required")

    next_needed = parse_bool(args.nextThoughtNeeded)
    if next_needed is None:
        raise ValueError("nextThoughtNeeded required")

    current_step = get_current_plan_step(state)

    thought = ThoughtData(
        thought=args.thought,
        thoughtNumber=args.thoughtNumber,
        totalThoughts=args.totalThoughts,
        nextThoughtNeeded=next_needed,
        isRevision=args.isRevision,
        revisesThought=args.revisesThought,
        branchFromThought=args.branchFromThought,
        branchId=args.branchId,
        planStep=current_step,
    ).to_dict()

    # append main history
    state.thoughtHistory.append(thought)

    # handle branch
    if args.branchFromThought and args.branchId:
        state.branches.setdefault(args.branchId, []).append(thought)

    save_state(state)

    print(format_thought(thought))


if __name__ == "__main__":
    main()