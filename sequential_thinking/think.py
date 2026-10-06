#!/usr/bin/env python3
"""
Sequential thinking state machine (Python version) with Planning Layer.
"""

import json
import os
import argparse
import sys
from typing import Dict, List, Optional

try:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    BASE_DIR = os.getcwd()

STATE_FILE = os.environ.get("THINK_STATE_FILE") or os.path.join(BASE_DIR, ".think_state.json")


class ThinkError(ValueError):
    """User-facing validation error; reported without a traceback."""


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


def advance_plan(state: State) -> bool:
    """Move to the next plan step. Returns False if already on the last step."""
    if state.currentStepIndex < len(state.plan) - 1:
        state.currentStepIndex += 1
        return True
    return False


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
    text = str(value).strip().lower()
    if text in ("true", "1", "yes"):
        return True
    if text in ("false", "0", "no"):
        return False
    raise ThinkError(f"invalid boolean {value!r} (use true/false)")


def validate_thought(state: State, thought: Dict) -> None:
    """Raise ThinkError if the thought is inconsistent with the saved state."""
    number = thought["thoughtNumber"]
    expected = len(state.thoughtHistory) + 1

    if not (thought["thought"] or "").strip():
        raise ThinkError("--thought must not be empty")
    if number != expected:
        raise ThinkError(f"thoughtNumber must be {expected} (got {number}); numbers are sequential")
    if thought["totalThoughts"] < 1:
        raise ThinkError("totalThoughts must be >= 1")

    revises = thought["revisesThought"]
    branch_from = thought["branchFromThought"]
    branch_id = thought["branchId"]

    if thought["isRevision"] and revises is None:
        raise ThinkError("--isRevision requires --revisesThought")
    if revises is not None and not thought["isRevision"]:
        raise ThinkError("--revisesThought requires --isRevision")
    if (branch_from is None) != (branch_id is None):
        raise ThinkError("--branchFromThought and --branchId must be used together")
    if thought["isRevision"] and branch_from is not None:
        raise ThinkError("a thought cannot be both a revision and a branch")

    for flag, ref in (("--revisesThought", revises), ("--branchFromThought", branch_from)):
        if ref is not None and not 1 <= ref < number:
            raise ThinkError(f"{flag} {ref} must refer to an earlier thought (1..{number - 1})")


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
            raise ThinkError("Invalid JSON for --setPlan")
    # comma separated
    return [x.strip() for x in value.split(",") if x.strip()]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", help="state file path (default: .think_state.json beside this script, or $THINK_STATE_FILE)")
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

    args = parser.parse_args(argv)

    if args.state:
        global STATE_FILE
        STATE_FILE = args.state

    try:
        run(args, parser)
    except ThinkError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


def run(args, parser):

    if args.reset:
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
        print("reset done")
        return

    state = load_state()

    modes = [bool(args.setPlan), args.nextStep, args.status, bool(args.thought)]
    if sum(modes) > 1:
        raise ThinkError("use only one of --setPlan, --nextStep, --status, --thought per call")

    # set plan
    if args.setPlan:
        steps = parse_plan(args.setPlan)
        if not steps:
            raise ThinkError("--setPlan must contain at least one step")
        set_plan(state, steps)
        save_state(state)
        print({"plan": state.plan, "currentStep": get_current_plan_step(state)})
        return

    # advance plan
    if args.nextStep:
        if not state.plan:
            raise ThinkError("no plan set; use --setPlan first")
        moved = advance_plan(state)
        save_state(state)
        print({"currentStep": get_current_plan_step(state), "advanced": moved})
        return

    if args.status:
        print(make_status(state))
        return

    if not args.thought:
        parser.print_help()
        return

    if args.thoughtNumber is None or args.totalThoughts is None:
        raise ThinkError("--thoughtNumber and --totalThoughts are required")

    next_needed = parse_bool(args.nextThoughtNeeded)
    if next_needed is None:
        raise ThinkError("--nextThoughtNeeded is required (true/false)")

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

    validate_thought(state, thought)
    if not state.plan:
        print("warning: no plan set; thought is not tied to a plan step", file=sys.stderr)

    # append main history
    state.thoughtHistory.append(thought)

    # handle branch
    if args.branchFromThought is not None and args.branchId:
        state.branches.setdefault(args.branchId, []).append(thought)

    save_state(state)

    print(format_thought(thought))


if __name__ == "__main__":
    sys.exit(main())