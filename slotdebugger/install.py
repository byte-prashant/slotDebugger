"""Register the slot-debugger skills, hooks and plugin with Claude Code or Cursor.

Used via `slotdebug install|uninstall [claude|cursor|all|plugin] [--scope user|project] [--hook] [--dir D]`.

- skills: `.claude/skills/<name>/SKILL.md` and `.cursor/rules/<name>.mdc`
- `--hook` (Claude): SessionStart adds the maps' problems to the context; PostToolUse on
  Edit/Write/MultiEdit re-indexes the engine when an edited file is in its map and
  reports what changed (`slotdebug hook ...`)
- `plugin`: a local Claude Code marketplace holding a `slotdebug` plugin with the skills,
  the hooks and `/slotdebug:tree`-style commands
"""

import json
import os
import re
import shutil
from importlib import resources

SKILLS = ("slot-debugger", "sequential-thinking", "aggregate-review", "slot-map")
# Always-on command reference, generated from the command registry: a Cursor rule with
# alwaysApply, and a marked block in the project's CLAUDE.md (read at every session start).
COMMANDS_RULE = "slotdebug-commands"
RULES = SKILLS + (COMMANDS_RULE,)
BLOCK_START = "<!-- slotdebug:commands:start -->"
BLOCK_END = "<!-- slotdebug:commands:end -->"
# the diagnose-only subagent, registered beside the skills
AGENTS = ("slot-debugger",)
AGENT_MATCHER = "slot-debugger|slotdebug:slot-debugger"  # project/user name, and its name inside the plugin
# what `--hook` registers: (event, matcher, slotdebug arguments, timeout in seconds).
# Any command matching OUR_HOOK is ours (the old `think --status` hook too).
HOOKS = (
    ("SessionStart", None, "hook session-start", 30),
    ("UserPromptSubmit", None, "hook prompt", 15),
    ("PostToolUse", "Edit|Write|MultiEdit", "hook post-edit", 120),
    ("PostToolUse", "Agent|Task", "hook agent-result", 30),
    ("SubagentStop", AGENT_MATCHER, "hook agent-stop", 60),
)
OUR_HOOK = re.compile(r"slotdebug(\.exe)?\s+(hook\s|think --status)")
PLUGIN = "slotdebug"
MARKETPLACE = "slotdebug"
RULE_DESCRIPTIONS = {
    "slot-debugger": "Debug slot-game RTP reports with the slotdebug CLI",
    "aggregate-review": "Check and correct a generated aggregate.json against the game's engine and volume tester",
    "slot-map": "Trace a report row to the volume tester line and the engine code that computes it; "
                "ask the maps (`slotdebug map`) about methods, classes and problems",
    "sequential-thinking": "Step-by-step reasoning with persisted plan/thought state via `slotdebug think`; debug a report against the expected report (diff -> targets -> branches -> findings)",
}


def skill_text(name: str) -> str:
    """Packaged skill, rewritten to call the installed `slotdebug think` command."""
    text = resources.files("slotdebugger").joinpath("skills", f"{name}.md").read_text(encoding="utf-8")
    return text.replace("python3 think.py", "slotdebug think")


def commands_reference() -> str:
    """Every registered command as Markdown: what it does, its arguments, an example or two.

    Built from the registry, so a command registered in cli.py is listed here with no
    other change.
    """
    from slotdebugger.cli import _register_all_commands  # cli imports this module
    from slotdebugger.registry import get_registry
    _register_all_commands()
    lines = [
        "## slotdebug commands",
        "",
        "The `slotdebug` CLI is installed for this project. Generated from its command registry by "
        "`slotdebug setup`; re-run setup after upgrading. `slotdebug command-list --detail` prints the "
        "same, `slotdebug <command> -h` the exact flags.",
        "",
        "Typical report debugging: `add` -> `analyze` -> `diff` -> `trace-tester` -> `aggregate` -> "
        "`think`. Skills with the full workflows: slot-debugger, aggregate-review, sequential-thinking.",
    ]
    for name, meta in get_registry().get_all_commands().items():
        lines += ["", f"### `slotdebug {name}`", "", meta["description"].strip()]
        args = meta.get("args") or {}
        if args:
            lines.append("")
            for arg, info in args.items():
                flag = f"<{arg}>" if info.get("positional") else (f"-{arg}" if len(arg) == 1 else f"--{arg}")
                required = " (required)" if info.get("required") else ""
                lines.append(f"- `{flag}`{required}: {info.get('description', '')}")
        for example in (meta.get("examples") or [])[:2]:
            lines.append(f"- e.g. `{example}`")
    return "\n".join(lines) + "\n"


def claude_md_path(project_dir: str) -> str:
    return os.path.join(project_dir, "CLAUDE.md")


def write_claude_block(project_dir: str) -> str:
    """Put the command reference in the project's CLAUDE.md between markers.

    Only the marked block is ever rewritten; the rest of the file is the user's. The
    file is created when it does not exist.
    """
    path = claude_md_path(project_dir)
    block = f"{BLOCK_START}\n{commands_reference()}{BLOCK_END}\n"
    text = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            text = f.read()
    start, end = text.find(BLOCK_START), text.find(BLOCK_END)
    if start != -1 and end > start:
        text = text[:start] + block + text[end + len(BLOCK_END):].lstrip("\n")
    else:
        text = (text.rstrip("\n") + "\n\n" if text.strip() else "") + block
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def remove_claude_block(project_dir: str) -> bool:
    """Take the marked block out of CLAUDE.md; delete the file only if nothing else is left."""
    path = claude_md_path(project_dir)
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8") as f:
        text = f.read()
    start, end = text.find(BLOCK_START), text.find(BLOCK_END)
    if start == -1 or end < start:
        return False
    rest = (text[:start].rstrip("\n") + "\n\n" + text[end + len(BLOCK_END):].lstrip("\n")).strip()
    if rest:
        with open(path, "w", encoding="utf-8") as f:
            f.write(rest + "\n")
    else:
        os.remove(path)
    return True


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


def slotdebug_command() -> str:
    """The installed `slotdebug` by absolute path: hooks may run without the venv on PATH."""
    return shutil.which("slotdebug") or "slotdebug"


def hook_entries() -> dict:
    """{event: [entry]} in the settings.json / hooks.json shape."""
    exe = slotdebug_command()
    entries: dict = {}
    for event, matcher, args, timeout in HOOKS:
        entry = {"hooks": [{"type": "command", "command": f"{exe} {args}", "timeout": timeout}]}
        if matcher:
            entry = {"matcher": matcher, **entry}
        entries.setdefault(event, []).append(entry)
    return entries


def agent_text(name: str, guard: bool = True) -> str:
    """The packaged agent definition. `guard` adds its read-only Bash hook to the front matter
    (honoured for project/user agents; plugins ignore agent hooks, so theirs go without)."""
    text = resources.files("slotdebugger").joinpath("agents", f"{name}.md").read_text(encoding="utf-8")
    hooks = ""
    if guard:
        hooks = ("hooks:\n  PreToolUse:\n    - matcher: \"Bash\"\n      hooks:\n        - type: command\n"
                 f"          command: {json.dumps(slotdebug_command() + ' hook agent-bash')}\n")
    return text.replace("__HOOKS__", hooks)


def agent_path(scope: str, project_dir: str, name: str) -> str:
    return os.path.join(claude_root(scope, project_dir), "agents", f"{name}.md")


def _ours(entry: dict) -> bool:
    return any(OUR_HOOK.search(h.get("command", "")) for h in entry.get("hooks", []))


def _remove_hooks(settings: dict) -> bool:
    """Drop every hook entry this tool added, in every event. True if anything changed."""
    hooks = settings.get("hooks", {})
    changed = False
    for event in list(hooks):
        kept = [e for e in hooks[event] if not _ours(e)]
        if len(kept) != len(hooks[event]):
            changed = True
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
    if changed and not hooks:
        settings.pop("hooks", None)
    return changed


def install_claude(scope: str, project_dir: str, hook: bool) -> list:
    root = claude_root(scope, project_dir)
    done = []
    for name in SKILLS:
        skill_dir = os.path.join(root, "skills", name)
        os.makedirs(skill_dir, exist_ok=True)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(skill_text(name))
        done.append(f"skill -> {skill_dir}/SKILL.md")
    for name in AGENTS:
        path = agent_path(scope, project_dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(agent_text(name))
        done.append(f"agent -> {path}")

    if scope == "project":  # a user-scope install must not edit every project's context
        done.append(f"commands -> {write_claude_block(project_dir)}")

    if hook:
        settings_path = os.path.join(root, "settings.json")
        settings = _load_json(settings_path)
        _remove_hooks(settings)  # replace ours (including an older version), keep everyone else's
        for event, entries in hook_entries().items():
            settings.setdefault("hooks", {}).setdefault(event, []).extend(entries)
        _save_json(settings_path, settings)
        done.append(f"hooks (SessionStart, UserPromptSubmit, PostToolUse on edits and on the agent, "
                    f"SubagentStop for slot-debugger) -> {settings_path}")
    return done


def remove_claude_skill(scope: str, project_dir: str, name: str) -> bool:
    """Delete one registered Claude skill. True if it was there."""
    skill_dir = os.path.join(claude_root(scope, project_dir), "skills", name)
    if os.path.isdir(skill_dir):
        shutil.rmtree(skill_dir)
        return True
    return False


def remove_claude_agent(scope: str, project_dir: str, name: str) -> bool:
    path = agent_path(scope, project_dir, name)
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False


def uninstall_claude(scope: str, project_dir: str) -> list:
    root = claude_root(scope, project_dir)
    done = []
    for name in SKILLS:
        if remove_claude_skill(scope, project_dir, name):
            done.append(f"removed {os.path.join(root, 'skills', name)}")
    for name in AGENTS:
        if remove_claude_agent(scope, project_dir, name):
            done.append(f"removed {agent_path(scope, project_dir, name)}")
    if scope == "project" and remove_claude_block(project_dir):
        done.append(f"removed the slotdebug commands block from {claude_md_path(project_dir)}")
    settings_path = os.path.join(root, "settings.json")
    settings = _load_json(settings_path)
    if _remove_hooks(settings):
        _save_json(settings_path, settings)
        done.append(f"removed slotdebug hooks from {settings_path}")
    return done


# ---- Claude Code plugin (local marketplace) ----

PLUGIN_COMMANDS = {
    "debug": ("Diagnose an RTP report with the slot-debugger agent (diagnose only; you apply the fixes)",
              "<report> [what looks wrong]",
              "runs",
              "Delegate to the slot-debugger agent: diagnose $ARGUMENTS. When it returns, read "
              "slotdebugger/analysis/<report>.agent_report.json, review each finding's evidence and "
              "proposed fix, and apply the fixes you agree with; then re-run `slotdebug trace-tester` "
              "to confirm."),
    "diagnose": ("Diagnose an RTP report in this session, without the agent",
                 "<report> [what looks wrong]",
                 "runs",
                 "Diagnose $ARGUMENTS yourself, in this conversation: follow 'Diagnose a report' in the "
                 "slot-debugger skill (analyze, diff, trace-tester, a `slotdebug think` session with "
                 "evidence, then `slotdebug report`). Change no file before `slotdebug report` is written; "
                 "then show the findings and proposed fixes and apply only those the user agrees with."),
    "tree": ("Show a report row's chain: report row -> volume tester line -> response key -> engine code",
             "[--file <report>] --row <row> | --key <response key> | --site <file:line>",
             "map tree $ARGUMENTS",
             "Explain this chain: which tester line emits the row and under which conditions, whether the "
             "engine returned the response key, and how the engine computes it. Cite file:line."),
    "problems": ("List everything suspicious in the maps, ranked, with file:line",
                 "[--file <report>]",
                 "map problems $ARGUMENTS",
                 "Go through the high problems first. Open each file:line to confirm before calling it a bug."),
    "method": ("Where an engine method is defined, what its result depends on, unused parameters",
               "--name <method> [--file <report>]",
               "map method $ARGUMENTS",
               "Summarise where this method comes from (game or OGLE/OGA) and what its result depends on."),
    "summary": ("The debugger's summary of the current maps",
                "[--file <report>]",
                "map summary $ARGUMENTS",
                "Summarise the state and propose the next step."),
}


def default_marketplace_dir() -> str:
    return os.path.join(os.path.expanduser("~/.local/share"), "slotdebug", "claude-marketplace")


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("slotdebugger")
    except Exception:  # running from a source tree that is not installed
        return "0.0.0"


def install_plugin(dest: str) -> list:
    """A local marketplace at `dest` holding the `slotdebug` plugin: skills, hooks, commands.

    Install it in Claude Code with
    `claude plugin marketplace add <dest>` then `claude plugin install slotdebug@slotdebug`.
    Re-running rewrites the plugin, so it picks up a newer slotdebugger.
    """
    dest = os.path.abspath(dest)
    plugin = os.path.join(dest, "plugins", PLUGIN)
    if os.path.isdir(plugin):
        shutil.rmtree(plugin)  # only ever our own generated directory
    exe = slotdebug_command()
    description = ("Slot RTP debugging: report row -> volume tester line -> engine code maps, "
                   "problems, and step-by-step investigation (needs the slotdebugger package)")
    _save_json(os.path.join(dest, ".claude-plugin", "marketplace.json"), {
        "name": MARKETPLACE,
        "owner": {"name": "slotdebugger"},
        "description": "Local marketplace generated by `slotdebug install plugin`",
        "plugins": [{"name": PLUGIN, "source": f"./plugins/{PLUGIN}", "description": description}],
    })
    _save_json(os.path.join(plugin, ".claude-plugin", "plugin.json"),
               {"name": PLUGIN, "version": _version(), "description": description,
                "author": {"name": "slotdebugger"}})
    for name in AGENTS:
        path = os.path.join(plugin, "agents", f"{name}.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(agent_text(name, guard=False))
    for name in SKILLS:
        path = os.path.join(plugin, "skills", name, "SKILL.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(skill_text(name))
    os.makedirs(os.path.join(plugin, "commands"), exist_ok=True)
    for name, (summary, hint, args, ask) in PLUGIN_COMMANDS.items():
        with open(os.path.join(plugin, "commands", f"{name}.md"), "w", encoding="utf-8") as f:
            # values are JSON strings, which YAML reads as quoted scalars: a ':' in them is safe
            f.write(f"---\ndescription: {json.dumps(summary)}\nargument-hint: {json.dumps(hint)}\n"
                    f"allowed-tools: {json.dumps(f'Bash({exe} *)')}\n---\n\n"
                    f"!`{exe} {args} 2>&1 || true`\n\n{ask}\n")
    _save_json(os.path.join(plugin, "hooks", "hooks.json"), {"hooks": hook_entries()})
    return [f"plugin -> {plugin}", f"marketplace -> {dest}",
            f"install in Claude Code: claude plugin marketplace add {dest}",
            f"                        claude plugin install {PLUGIN}@{MARKETPLACE}",
            f"commands: " + ", ".join(f"/{PLUGIN}:{n}" for n in PLUGIN_COMMANDS),
            f"agent: {PLUGIN}:slot-debugger (diagnose only)"]


def uninstall_plugin(dest: str) -> list:
    """Remove the generated marketplace, but only if it is ours."""
    dest = os.path.abspath(dest)
    manifest = os.path.join(dest, ".claude-plugin", "marketplace.json")
    if _load_json(manifest).get("name") != MARKETPLACE:
        return []
    shutil.rmtree(os.path.join(dest, "plugins", PLUGIN), ignore_errors=True)
    os.remove(manifest)
    for d in (os.path.join(dest, ".claude-plugin"), os.path.join(dest, "plugins"), dest):
        if os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
    return [f"removed the slotdebug plugin marketplace {dest} "
            f"(also run: claude plugin uninstall {PLUGIN}@{MARKETPLACE})"]



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
    path = cursor_rule_path(project_dir, COMMANDS_RULE)
    with open(path, "w", encoding="utf-8") as f:
        f.write("---\ndescription: slotdebug CLI commands available in this project\n"
                "alwaysApply: true\n---\n\n" + commands_reference())
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
    for name in RULES:
        if remove_cursor_rule(project_dir, name):
            done.append(f"removed {cursor_rule_path(project_dir, name)}")
    return done


def run(args) -> int:
    """Entry point for `slotdebug install|uninstall` (args from slotdebugger.cli)."""
    project_dir = args.dir or os.getcwd()
    done = []  # for `plugin`, --dir is where the marketplace goes
    if args.target == "plugin":
        dest = args.dir or default_marketplace_dir()
        done += install_plugin(dest) if args.cmd == "install" else uninstall_plugin(dest)
    elif args.cmd == "install":
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
