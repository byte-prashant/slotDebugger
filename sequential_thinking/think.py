#!/usr/bin/env python3
"""
Sequential thinking state machine (Python version) with Planning Layer.
"""

import json
import os
import re
import argparse
import sys
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

try:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    BASE_DIR = os.getcwd()

# Wording at least this alike (0..1, over words, not characters) is reported as possibly
# already checked. A warning only: alike wording can mean a different check.
SIMILARITY = 0.6
STOPWORDS = {"a", "an", "the", "is", "are", "was", "of", "to", "in", "on", "for", "and", "or",
             "it", "its", "this", "that", "be", "by", "with", "as", "at", "from"}

# Default to the working directory so an installed package never writes into site-packages.
STATE_FILE = os.environ.get("THINK_STATE_FILE") or os.path.join(os.getcwd(), ".think_state.json")


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
        # file:line references or commands the thought is based on
        self.evidence = kwargs.get("evidence") or []
        # on branch thoughts: the diff key the branch explains and the suspected cause
        self.target = kwargs.get("target")
        self.cause = kwargs.get("cause")
        self.forced = kwargs.get("forced", False)

    def to_dict(self):
        return self.__dict__


class State:
    def __init__(self):
        self.thoughtHistory: List[Dict] = []
        self.branches: Dict[str, List[Dict]] = {}
        # NEW: Planning layer
        self.plan: List[str] = []
        self.currentStepIndex: int = 0
        # branchId -> {"verdict": "confirmed"|"refuted", "reason", "evidence", "afterThought"}
        self.conclusions: Dict[str, Dict] = {}
        # final results: {"finding", "evidence", "targets", "afterThought"}
        self.findings: List[Dict] = []
        # keys the session must explain (from `slotdebug diff`'s `open` list)
        self.targets: List[str] = []
        # branchId -> {"target", "cause"}
        self.branchMeta: Dict[str, Dict] = {}


def load_state() -> State:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
            state = State()
            state.thoughtHistory = data.get("thoughtHistory", [])
            state.branches = data.get("branches", {})
            state.plan = data.get("plan", [])
            state.currentStepIndex = data.get("currentStepIndex", 0)
            state.conclusions = data.get("conclusions", {})
            state.findings = data.get("findings", [])
            state.targets = data.get("targets", [])
            state.branchMeta = data.get("branchMeta", {})
            return state
    return State()


def save_state(state: State):
    tmp = f"{STATE_FILE}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w") as f:
            json.dump(state_to_dict(state), f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATE_FILE)  # atomic: a crash never leaves a half-written state file
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def state_to_dict(state: State) -> Dict:
    return {
        "thoughtHistory": state.thoughtHistory,
        "branches": state.branches,
        "plan": state.plan,
        "currentStepIndex": state.currentStepIndex,
        "conclusions": state.conclusions,
        "findings": state.findings,
        "targets": state.targets,
        "branchMeta": state.branchMeta,
    }


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

    about = f"\n🎯 {t['target']} / {t['cause']}" if t.get("target") else ""

    return f"{header}{step_line}{about}\n{t['thought']}{format_evidence(t.get('evidence'))}"


def format_evidence(evidence) -> str:
    return "".join(f"\n🔎 {e}" for e in evidence or [])


def format_conclusion(branch_id, c):
    icon = "✅" if c["verdict"] == "confirmed" else "❌"
    return (f"{icon} Branch {branch_id} {c['verdict']} (after thought {c['afterThought']})\n"
            f"{c['reason']}{format_evidence(c.get('evidence'))}")


def format_finding(n, f):
    explains = f"\n🎯 explains {', '.join(f['targets'])}" if f.get("targets") else ""
    return (f"📌 Finding {n} (after thought {f['afterThought']}){explains}\n"
            f"{f['finding']}{format_evidence(f['evidence'])}")


def open_branches(state: State) -> List[str]:
    return [b for b in state.branches if b not in state.conclusions]


def unexplained_targets(state: State) -> List[str]:
    """Targets no finding explains yet."""
    explained = {key(t) for f in state.findings for t in f.get("targets", [])}
    return [t for t in state.targets if key(t) not in explained]


def make_status(state: State):
    current = get_current_thought(state)
    return {
        "thoughtNumber": current["thoughtNumber"] if current else 0,
        "totalThoughts": current["totalThoughts"] if current else 0,
        "nextThoughtNeeded": current["nextThoughtNeeded"] if current else True,
        # NEW: expose planning status
        "currentPlanStep": get_current_plan_step(state),
        "planProgress": f"{state.currentStepIndex+1}/{len(state.plan)}" if state.plan else None,
        "openBranches": open_branches(state),
        "concludedBranches": {b: c["verdict"] for b, c in state.conclusions.items()},
        "findings": len(state.findings),
        "targets": len(state.targets),
        "unexplainedTargets": unexplained_targets(state),
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

    if branch_id in state.conclusions:
        raise ThinkError(f"branch {branch_id!r} is already {state.conclusions[branch_id]['verdict']}; "
                         "start a new --branchId to reopen the question")


def clean_evidence(values) -> List[str]:
    evidence = [e.strip() for e in values or [] if e.strip()]
    if values and not evidence:
        raise ThinkError("--evidence must not be empty")
    return evidence


def key(text: str) -> str:
    """How targets and causes are compared: `FG12 ` and `fg12` are the same key."""
    return " ".join(str(text).split()).casefold()


def check_targets(state: State, targets: List[str]) -> None:
    if not state.targets:
        return
    known = {key(t) for t in state.targets}
    unknown = [t for t in targets if key(t) not in known]
    if unknown:
        raise ThinkError(f"unknown target(s) {', '.join(unknown)}; targets are: {', '.join(state.targets)}")


def branch_about(state: State, args) -> Tuple[Optional[str], Optional[str]]:
    """The (target, cause) a branch thought is about, checked against the branches before it.

    A cause already concluded for a target is refused, whatever the branch is called:
    that question has an answer. An open branch on the same pair is to be continued, not
    duplicated. `--force` overrides both.
    """
    target, cause = (args.target or "").strip() or None, (args.cause or "").strip() or None
    if (target is None) != (cause is None):
        raise ThinkError("--target and --cause must be used together")
    if target is None and args.branchId is None:
        return None, None
    if args.branchId is None:
        raise ThinkError("--target/--cause describe a branch; use them with --branchFromThought/--branchId")
    meta = state.branchMeta.get(args.branchId)
    if meta:  # continuing a branch: it keeps what it is about
        if target is not None and (key(target), key(cause)) != (key(meta["target"]), key(meta["cause"])):
            raise ThinkError(f"branch {args.branchId!r} is about {meta['target']} / {meta['cause']}; "
                             "start a new --branchId for a different question")
        return meta["target"], meta["cause"]
    if target is None:
        if state.targets:
            raise ThinkError("a new branch needs --target KEY --cause CAUSE while targets are set")
        return None, None
    check_targets(state, [target])
    for other, m in state.branchMeta.items():
        if (key(m["target"]), key(m["cause"])) != (key(target), key(cause)) or args.force:
            continue
        if other in state.conclusions:
            c = state.conclusions[other]
            raise ThinkError(f"{target} / {cause} was already {c['verdict']} in branch {other!r}: "
                             f"{c['reason']} (use --force if the evidence has changed)")
        raise ThinkError(f"{target} / {cause} is open in branch {other!r}; continue that branch "
                         "(use --force to open a second one)")
    return target, cause


def done_problems(state: State) -> List[str]:
    """Why the session cannot end yet."""
    problems = []
    if open_branches(state):
        problems.append(f"open branches: {', '.join(open_branches(state))} (--conclude them)")
    if unexplained_targets(state):
        problems.append(f"targets with no finding: {', '.join(unexplained_targets(state))} "
                        "(--finding ... --target KEY)")
    return problems


def words(text: str) -> List[str]:
    """Words of a thought, for comparing wording. `engine.py:143` stays one word, so it
    is not mistaken for `engine.py:136` as character-level matching would."""
    found = re.findall(r"[a-z0-9_][a-z0-9_.:/\-]*", text.casefold())
    return [w.rstrip(".:") for w in found if w.rstrip(".:") not in STOPWORDS]


def similar(state: State, text: str, evidence: List[str], target: Optional[str] = None,
            branch: Optional[str] = None) -> List[Dict]:
    """Earlier entries that may already cover `text`: alike wording or shared evidence.

    Entries about a different target are skipped: the same wording about another key
    is another check. So are the earlier thoughts of `branch`, which a thought continues.
    """
    mine = words(text)
    cited = {key(e) for e in evidence}
    entries = []
    for t in state.thoughtHistory:
        b = t.get("branchId")
        entries.append({"thought": t["thoughtNumber"], "branch": b, "text": t["thought"],
                        "evidence": t.get("evidence") or [], "target": t.get("target"),
                        "verdict": state.conclusions.get(b, {}).get("verdict")})
    for b, c in state.conclusions.items():
        entries.append({"thought": None, "branch": b, "text": c["reason"], "evidence": c["evidence"],
                        "target": state.branchMeta.get(b, {}).get("target"), "verdict": c["verdict"]})
    matches = []
    for e in entries:
        if target and e["target"] and key(target) != key(e["target"]):
            continue
        if branch and e["branch"] == branch:
            continue
        score = SequenceMatcher(None, mine, words(e["text"])).ratio() if mine else 0.0
        shared = [ev for ev in e["evidence"] if key(ev) in cited]
        if score >= SIMILARITY or shared:
            matches.append({**{k: e[k] for k in ("thought", "branch", "verdict", "text")},
                            "score": round(score, 2), "sharedEvidence": shared})
    matches.sort(key=lambda m: (bool(m["sharedEvidence"]), m["score"]), reverse=True)
    return matches[:3]


def format_match(m: Dict) -> str:
    where = f"thought {m['thought']}" if m["thought"] else "conclusion"
    if m["branch"]:
        where += f" [branch {m['branch']}{', ' + m['verdict'] if m['verdict'] else ''}]"
    why = f"score {m['score']}" + (f", same evidence {', '.join(m['sharedEvidence'])}" if m["sharedEvidence"] else "")
    return f"{where} ({why}): {m['text']}"


def parse_targets(value: str) -> List[str]:
    """A `slotdebug diff` JSON file (its `open` keys), or comma-separated keys."""
    if os.path.isfile(value):
        try:
            with open(value) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise ThinkError(f"{value}: cannot read diff ({e})")
        if not isinstance(data, dict) or not isinstance(data.get("open"), list):
            raise ThinkError(f"{value}: no `open` list; pass the file `slotdebug diff` wrote")
        return [str(k) for k in data["open"]]
    return [x.strip() for x in value.split(",") if x.strip()]


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
    parser.add_argument("--state", help="state file path (default: ./.think_state.json, or $THINK_STATE_FILE)")
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

    # evidence and outcomes
    parser.add_argument("--evidence", action="append", metavar="REF",
                        help="file:line or command backing this entry (repeatable)")
    parser.add_argument("--conclude", metavar="BRANCH_ID", help="close a branch with --verdict and --reason")
    parser.add_argument("--verdict", choices=("confirmed", "refuted"))
    parser.add_argument("--reason", help="why the branch is confirmed or refuted")
    parser.add_argument("--finding", help="record a final finding (requires --evidence)")

    # what the session must explain, and what each branch is about
    parser.add_argument("--setTargets", metavar="DIFF_OR_KEYS",
                        help="keys to explain: a `slotdebug diff` JSON file (its open keys) or comma-separated")
    parser.add_argument("--target", action="append", metavar="KEY",
                        help="on a new branch: the key it explains (with --cause); on --finding: keys it explains (repeatable)")
    parser.add_argument("--cause", help="on a new branch: the suspected cause (e.g. cap, multiplier, mapping)")
    parser.add_argument("--similar", metavar="TEXT", help="list earlier entries that may already cover TEXT")
    parser.add_argument("--force", action="store_true",
                        help="record despite a concluded/open duplicate branch, or end with work still open")

    parser.add_argument("--status", action="store_true")
    parser.add_argument("--history", action="store_true", help="print all recorded thoughts")
    parser.add_argument("--export", metavar="PATH", help="write full state (plan, thoughts, branches) to PATH as JSON")
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

    modes = [bool(args.setPlan), args.nextStep, args.status, args.history, bool(args.export), bool(args.thought),
             bool(args.conclude), bool(args.finding), bool(args.setTargets), bool(args.similar)]
    if sum(modes) > 1:
        raise ThinkError("use only one of --setPlan, --nextStep, --status, --history, --export, --thought, "
                         "--conclude, --finding, --setTargets, --similar per call")
    if args.evidence and not (args.thought or args.conclude or args.finding or args.similar):
        raise ThinkError("--evidence applies only to --thought, --conclude, --finding or --similar")
    if (args.verdict or args.reason) and not args.conclude:
        raise ThinkError("--verdict and --reason apply only to --conclude")
    if args.target and not (args.thought or args.finding or args.similar):
        raise ThinkError("--target applies only to --thought (a branch), --finding or --similar")
    if args.cause and not args.thought:
        raise ThinkError("--cause applies only to --thought (a branch)")
    if args.force and not args.thought:
        raise ThinkError("--force applies only to --thought")

    if args.setTargets:
        targets = parse_targets(args.setTargets)
        state.targets = list(dict.fromkeys(targets))
        save_state(state)
        print({"targets": state.targets, "unexplainedTargets": unexplained_targets(state)})
        return

    if args.similar:
        if args.target and len(args.target) > 1:
            raise ThinkError("--similar takes at most one --target")
        matches = similar(state, args.similar, clean_evidence(args.evidence),
                          args.target[0] if args.target else None)
        print(json.dumps(matches, indent=2, ensure_ascii=False))
        return

    if args.history:
        for t in state.thoughtHistory:
            print(format_thought(t))
        for branch_id, c in state.conclusions.items():
            print(format_conclusion(branch_id, c))
        for n, f in enumerate(state.findings, 1):
            print(format_finding(n, f))
        return

    if args.conclude:
        conclude(state, args)
        return

    if args.finding:
        record_finding(state, args)
        return

    if args.export:
        with open(args.export, "w") as f:
            json.dump(state_to_dict(state), f, indent=2)
        print({"exported": args.export, "thoughts": len(state.thoughtHistory)})
        return

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
    evidence = clean_evidence(args.evidence)
    if args.target and len(args.target) > 1:
        raise ThinkError("a branch has one --target")
    args.target = args.target[0] if args.target else None

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
        evidence=evidence,
    ).to_dict()

    validate_thought(state, thought)
    thought["target"], thought["cause"] = branch_about(state, args)
    if not next_needed:
        problems = done_problems(state)
        if problems and not args.force:
            raise ThinkError("cannot finish: " + "; ".join(problems) + " (or --force)")
        for p in problems:
            print(f"warning: finished with {p}", file=sys.stderr)
    thought["forced"] = args.force
    if not state.plan:
        print("warning: no plan set; thought is not tied to a plan step", file=sys.stderr)
    if not evidence:
        print("warning: no --evidence; cite the file:line or command this thought rests on", file=sys.stderr)
    for m in similar(state, thought["thought"], evidence, thought["target"], thought["branchId"]):
        print(f"warning: may already be covered by {format_match(m)}", file=sys.stderr)

    # append main history
    state.thoughtHistory.append(thought)

    # handle branch
    if args.branchFromThought is not None and args.branchId:
        state.branches.setdefault(args.branchId, []).append(thought)
        if thought["target"] and args.branchId not in state.branchMeta:
            state.branchMeta[args.branchId] = {"target": thought["target"], "cause": thought["cause"]}

    save_state(state)

    print(format_thought(thought))


def conclude(state: State, args):
    branch_id = args.conclude
    if branch_id not in state.branches:
        known = ", ".join(state.branches) or "none"
        raise ThinkError(f"no branch {branch_id!r} (branches: {known})")
    if branch_id in state.conclusions:
        raise ThinkError(f"branch {branch_id!r} is already {state.conclusions[branch_id]['verdict']}")
    if not args.verdict:
        raise ThinkError("--conclude requires --verdict confirmed|refuted")
    if not (args.reason or "").strip():
        raise ThinkError("--conclude requires --reason")
    evidence = clean_evidence(args.evidence)
    if not evidence:
        raise ThinkError("--conclude requires --evidence")
    state.conclusions[branch_id] = {"verdict": args.verdict, "reason": args.reason.strip(),
                                    "evidence": evidence, "afterThought": len(state.thoughtHistory)}
    save_state(state)
    print(format_conclusion(branch_id, state.conclusions[branch_id]))


def record_finding(state: State, args):
    if not state.thoughtHistory:
        raise ThinkError("record at least one --thought before a --finding")
    evidence = clean_evidence(args.evidence)
    if not evidence:
        raise ThinkError("--finding requires --evidence")
    targets = [t.strip() for t in args.target or [] if t.strip()]
    check_targets(state, targets)
    finding = {"finding": args.finding.strip(), "evidence": evidence, "targets": targets,
               "afterThought": len(state.thoughtHistory)}
    if not finding["finding"]:
        raise ThinkError("--finding must not be empty")
    pending = open_branches(state)
    if pending:
        print(f"warning: branches still open: {', '.join(pending)}; conclude them with --conclude",
              file=sys.stderr)
    state.findings.append(finding)
    save_state(state)
    print(format_finding(len(state.findings), finding))


if __name__ == "__main__":
    sys.exit(main())