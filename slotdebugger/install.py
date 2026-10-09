"""Register the slot-debugger skills (and optional hook) with Claude Code or Cursor.

Used via `slotdebug install|uninstall [claude|cursor|all] [--scope user|project] [--hook] [--dir D]`.
"""

import json
import os
import shutil
from importlib import resources

SKILLS = ("slot-debugger", "sequential-thinking", "aggregate-review")
HOOK_COMMAND = "slotdebug think --status"
HOOK_MARKER = "slotdebug think"
RULE_DESCRIPTIONS = {
    "slot-debugger": "Debug slot-game RTP reports with the slotdebug CLI",
    "aggregate-review": "Check and correct a generated aggregate.json against the game's engine and volume tester",
    "sequential-thinking": "Step-by-step reasoning with persisted plan/thought state via `slotdebug think`; debug a report against the expected report (diff -> targets -> branches -> findings)",
}


def skill_text(name: str) -> str:
    """Packaged skill, rewritten to call the installed `slotdebug think` command."""
    text = resources.files("slotdebugger").joinpath("skills", f"{name}.md").read_text(encoding="utf-8")
    return text.replace("python3 think.py", "slotdebug think")


def claude_root(scope: str, project_dir: str) -> str:
    return os.path.join(project_dir, ".claude") if scope == "project" else os.path.expanduser("~/.claude")


def _load_json(path: str) -> dict:
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def _save_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _has_hook(entries: list) -> bool:
    return any(HOOK_MARKER in h.get("command", "") for e in entries for h in e.get("hooks", []))


def install_claude(scope: str, project_dir: str, hook: bool) -> list:
    root = claude_root(scope, project_dir)
    done = []
    for name in SKILLS:
        skill_dir = os.path.join(root, "skills", name)
        os.makedirs(skill_dir, exist_ok=True)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(skill_text(name))
        done.append(f"skill -> {skill_dir}/SKILL.md")

    if hook:
        settings_path = os.path.join(root, "settings.json")
        settings = _load_json(settings_path)
        entries = settings.setdefault("hooks", {}).setdefault("SessionStart", [])
        if not _has_hook(entries):
            entries.append({"hooks": [{"type": "command", "command": HOOK_COMMAND}]})
            _save_json(settings_path, settings)
        done.append(f"SessionStart hook -> {settings_path}")
    return done


def remove_claude_skill(scope: str, project_dir: str, name: str) -> bool:
    """Delete one registered Claude skill. True if it was there."""
    skill_dir = os.path.join(claude_root(scope, project_dir), "skills", name)
    if os.path.isdir(skill_dir):
        shutil.rmtree(skill_dir)
        return True
    return False


def uninstall_claude(scope: str, project_dir: str) -> list:
    root = claude_root(scope, project_dir)
    done = []
    for name in SKILLS:
        if remove_claude_skill(scope, project_dir, name):
            done.append(f"removed {os.path.join(root, 'skills', name)}")
    settings_path = os.path.join(root, "settings.json")
    settings = _load_json(settings_path)
    entries = settings.get("hooks", {}).get("SessionStart", [])
    kept = [e for e in entries if not _has_hook([e])]
    if len(kept) != len(entries):
        if kept:
            settings["hooks"]["SessionStart"] = kept
        else:
            del settings["hooks"]["SessionStart"]
            if not settings["hooks"]:
                del settings["hooks"]
        _save_json(settings_path, settings)
        done.append(f"removed SessionStart hook from {settings_path}")
    return done


def cursor_rule_path(project_dir: str, name: str) -> str:
    return os.path.join(project_dir, ".cursor", "rules", f"{name}.mdc")


def install_cursor(project_dir: str) -> list:
    done = []
    for name in SKILLS:
        body = skill_text(name)
        if body.startswith("---"):  # drop skill front matter; Cursor rules use their own
            body = body.split("---", 2)[2].lstrip()
        header = f"---\ndescription: {RULE_DESCRIPTIONS[name]}\nalwaysApply: false\n---\n\n"
        path = cursor_rule_path(project_dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(header + body)
        done.append(f"rule -> {path}")
    return done


def remove_cursor_rule(project_dir: str, name: str) -> bool:
    """Delete one registered Cursor rule. True if it was there."""
    path = cursor_rule_path(project_dir, name)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def uninstall_cursor(project_dir: str) -> list:
    done = []
    for name in SKILLS:
        if remove_cursor_rule(project_dir, name):
            done.append(f"removed {cursor_rule_path(project_dir, name)}")
    return done


def run(args) -> int:
    """Entry point for `slotdebug install|uninstall` (args from slotdebugger.cli)."""
    project_dir = args.dir or os.getcwd()
    done = []
    if args.cmd == "install":
        if args.target in ("claude", "all"):
            done += install_claude(args.scope, project_dir, args.hook)
        if args.target in ("cursor", "all"):
            done += install_cursor(project_dir)
    else:
        if args.target in ("claude", "all"):
            done += uninstall_claude(args.scope, project_dir)
        if args.target in ("cursor", "all"):
            done += uninstall_cursor(project_dir)
    for line in done or ["nothing to do"]:
        print(line)
    return 0
