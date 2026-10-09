# How Command Registry Works - Complete Walkthrough

## Overview

The command registry system allows slotdebug to:
1. **Auto-discover** all available commands
2. **Store metadata** about each command (args, description, examples)
3. **Provide introspection** - users can ask "what commands are available?"
4. **Enable future integration** - MCP/Claude plugins can query the registry

---

## Architecture Diagram

```
┌─────────────────────────────────────────┐
│   slotdebugger/registry.py              │
│   ─────────────────────────────────────│
│   CommandRegistry class                 │
│   - _commands: Dict[name → metadata]   │
│   - _functions: Dict[name → callable]  │
│   - register(name, desc, func, args)   │
│   - get_command(name)                  │
│   - list_commands()                    │
│   - describe_all_commands()            │
└─────────────────────────────────────────┘
                    ↑
                    │ imported by
                    │
┌─────────────────────────────────────────┐
│   slotdebugger/cli.py                   │
│   ─────────────────────────────────────│
│   _register_all_commands()              │
│   - Called once at startup              │
│   - Populates registry with:            │
│     • setup                             │
│     • add                               │
│     • analyze                           │
│     • runs                              │
│     • think                             │
│     • command-list                      │
└─────────────────────────────────────────┘
                    ↓
                    │
┌─────────────────────────────────────────┐
│   User facing                           │
│   ─────────────────────────────────────│
│   $ slotdebug command-list              │
│   $ slotdebug command-list --detail     │
│   $ slotdebug analyze --file report.xlsx│
└─────────────────────────────────────────┘
```

---

## Step-by-Step: How It Works

### Step 1: Package Import & Registry Initialization

```python
# When Python loads slotdebugger package:
from slotdebugger.registry import _global_registry

# _global_registry starts empty:
{
    "_commands": {},      # No commands yet
    "_functions": {}      # No functions yet
}
```

### Step 2: CLI Starts (main() called)

```python
# slotdebugger/cli.py - main() function
def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    
    # STEP 1: Register all commands (ONE TIME)
    _register_all_commands()  # ← This populates registry
    
    # Now registry contains all command metadata
```

### Step 3: _register_all_commands() Populates Registry

```python
def _register_all_commands() -> None:
    registry = get_registry()
    
    # Only register once (check if already registered)
    if len(registry.list_commands()) > 0:
        return  # Already done!
    
    # Register SETUP command
    registry.register(
        name="setup",
        description="Create a new slotdebugger workspace for a game",
        func=None,  # Handler in main() switch statement
        args={
            "name": {
                "type": "string",
                "description": "Game name (default: current directory name)",
                "required": False
            },
            "dir": {
                "type": "string",
                "description": "Directory to create workspace in (default: cwd)",
                "required": False
            }
        },
        returns={
            "type": "object",
            "description": "Workspace created with subdirs..."
        }
    )
    
    # Register ANALYZE command
    registry.register(
        name="analyze",
        description="Parse, normalize and analyze an RTP report",
        func=_analyze,  # ← Actual handler function
        args={
            "file": {
                "type": "string",
                "description": "Report file name in workspace reports/",
                "required": True
            },
            "out": {
                "type": "string",
                "description": "Output file to write JSON analysis",
                "required": False
            }
        }
    )
    
    # Register ADD, RUNS, THINK, COMMAND-LIST ...
    # ... similar pattern for each ...
```

After this, registry contains:

```python
_global_registry = {
    "_commands": {
        "setup": {
            "name": "setup",
            "description": "Create a new slotdebugger workspace...",
            "args": {"name": {...}, "dir": {...}},
            "returns": {...},
            "examples": [...]
        },
        "add": {
            "name": "add",
            "description": "Import a report file into workspace...",
            "args": {"file": {...}},
            "returns": {...}
        },
        "analyze": {
            "name": "analyze",
            "description": "Parse, normalize and analyze RTP report...",
            "args": {"file": {...}, "out": {...}},
            "returns": {...}
        },
        "runs": {...},
        "think": {...},
        "command-list": {...}
    },
    "_functions": {
        "analyze": <function _analyze>,
        # Others are None because they're handled in main() switch
    }
}
```

### Step 4: User Runs: `slotdebug command-list`

```
$ slotdebug command-list

Flow:
    ↓
main(argv=["command-list"])
    ↓
_register_all_commands()  # Registry now has all commands
    ↓
Check: argv[0] == "command-list" ?  YES
    ↓
if argv[1] == "--detail":
    print(describe_all_registered_commands())
else:
    commands = list_registered_commands()
    print("Registered commands:")
    for cmd in sorted(commands):
        print(f"  {cmd}")
    ↓
Output:
Registered commands:
  add
  analyze
  command-list
  runs
  setup
  think
```

### Step 5: User Runs: `slotdebug command-list --detail`

```
$ slotdebug command-list --detail

Flow:
    ↓
_register_all_commands()
    ↓
describe_all_registered_commands()
    ↓
For each command in registry:
    Call: describe_command(name)
         ↓
         Returns formatted text like:
         
         Command: setup
         Description: Create a new slotdebugger workspace for a game
         
         Arguments:
           name: Game name (default: current directory name)
             (type: string)
           dir: Directory to create workspace in (default: cwd)
             (type: string)
         
         Returns: Workspace created with subdirs...
         
         Examples:
           slotdebug setup --name my_game
           slotdebug setup --dir /path/to/game
    ↓
Output:
Registered Commands
==================================================

Command: add
Description: Import a report file into the workspace's reports/ directory

Arguments:
  file*: Path to report file (CSV/XLSX)
    (type: string)

Returns: Path where report was imported

Examples:
  slotdebug add ../reports/game_rtp.xlsx
  slotdebug add /path/to/report.csv

==================================================

Command: analyze
Description: Parse, normalize and analyze an RTP report (CSV/XLSX);...
...
```

### Step 6: User Runs Normal Command: `slotdebug analyze --file report.xlsx`

```
$ slotdebug analyze --file reports/rich_little_piggies.xlsx

Flow:
    ↓
main(argv=["analyze", "--file", "reports/rich_little_piggies.xlsx"])
    ↓
_register_all_commands()  # Registry populated
    ↓
Check: argv[0] == "command-list" ?  NO
Check: argv[0] == "think" ?  NO  (think has special handling)
    ↓
Continue with normal argparse flow:
    ↓
if args.cmd == "analyze":
    return _analyze(ws, args)
    ↓
_analyze() executes normally
    ↓
Result printed and saved
```

**Key Point:** The registry doesn't interfere with normal CLI execution - all existing commands work exactly as before!

---

## How Registry Enables Future Features

### Future: MCP Server

Once we implement MCP (Phase 2):

```python
# slotdebugger/mcp_server.py

@server.list_tools()
def list_tools():
    """Auto-generate tools from registry."""
    registry = get_registry()
    
    tools = []
    for cmd_name, metadata in registry.get_all_commands().items():
        tool = Tool(
            name=f"slotdebug_{cmd_name}",
            description=metadata["description"],
            inputSchema={
                "type": "object",
                "properties": {
                    arg_name: {"type": arg["type"]}
                    for arg_name, arg in metadata["args"].items()
                }
            }
        )
        tools.append(tool)
    
    return tools  # Claude sees ALL commands automatically!
```

Claude immediately knows about:
- Every command
- What args each takes
- What they return
- Examples of usage

No manual configuration needed!

### Future: Programmatic Access

```python
# Python developers can query registry programmatically:

from slotdebugger.registry import get_registry

registry = get_registry()

# Get all commands:
commands = registry.list_commands()
# → ["setup", "add", "analyze", "runs", "think", "command-list"]

# Get metadata for one command:
metadata = registry.get_command("analyze")
# → {
#     "name": "analyze",
#     "description": "...",
#     "args": {...},
#     "returns": {...}
#   }

# Get the function (for advanced usage):
func = registry.get_function("analyze")
# → <function _analyze>

# Export as JSON (for documentation/tools):
import json
json_registry = registry.to_json()
# → Can be used by web UI, IDE plugins, etc.
```

---

## Data Flow: New Command Example

### Scenario: Developer Adds `compare` Command

```python
# File: slotdebugger/cli.py

@register_command(
    "compare",
    description="Compare two RTP reports and show differences",
    args={
        "file1": {"type": "string", "description": "First report", "required": True},
        "file2": {"type": "string", "description": "Second report", "required": True}
    }
)
def _compare(ws, args) -> int:
    report1 = pipeline.analyze(args.file1)
    report2 = pipeline.analyze(args.file2)
    # Compare logic...
    return 0
```

**What happens automatically:**

1. ✓ Function decorated with `@register_command`
2. ✓ During `_register_all_commands()`, it's added to registry
3. ✓ User can run: `slotdebug compare --file1 r1.xlsx --file2 r2.xlsx`
4. ✓ Registry now knows about "compare" command
5. ✓ Next MCP server restart: Claude sees `slotdebug_compare` tool
6. ✓ User can say: "Compare these two reports" → Claude does it

**No other changes needed!**

---

## Registry In-Memory State Example

```python
# After _register_all_commands() runs:

registry = {
    "_commands": {
        "analyze": {
            "name": "analyze",
            "description": "Parse, normalize and analyze an RTP report (CSV/XLSX)...",
            "args": {
                "file": {
                    "type": "string",
                    "description": "Report file name in workspace reports/...",
                    "required": True
                },
                "out": {
                    "type": "string",
                    "description": "Output file to write JSON analysis",
                    "required": False
                }
            },
            "returns": {
                "type": "object",
                "description": "Analysis result with metadata, components..."
            },
            "examples": [
                "slotdebug analyze --file reports/game_report.xlsx",
                "slotdebug analyze --file report.xlsx --out analysis/result.json"
            ]
        },
        "setup": {...},
        "add": {...},
        "runs": {...},
        "think": {...},
        "command-list": {...}
    },
    "_functions": {
        "analyze": <function _analyze at 0x...>,
        "setup": None,  # Handled in main() switch
        "add": None,
        "runs": None,
        "think": None,
        "command-list": None
    }
}
```

---

## Key Behaviors

### Behavior 1: Registration Happens Once

```python
# First call to _register_all_commands():
if len(registry.list_commands()) > 0:
    return  # Already registered, skip

# This prevents duplicate registration even if main() is called multiple times
```

### Behavior 2: Commands Still Work Normally

```python
# Registry doesn't replace CLI parsing, just augments it:
# ✓ Old way: argparse handles everything
# ✓ New way: Registry has metadata, argparse still parses commands

# So "slotdebug analyze --file X.xlsx" works exactly as before
# But now we also have searchable metadata about it
```

### Behavior 3: Easy Querying

```python
# Users can discover commands:
$ slotdebug command-list              # List all
$ slotdebug command-list --detail     # Full descriptions

# Developers can query programmatically:
from slotdebugger.registry import get_registry
registry = get_registry()
all_cmds = registry.get_all_commands()
```

### Behavior 4: Future-Proof for MCP

```python
# When we implement MCP server (Phase 2):
# MCP just queries the registry
# No need to maintain separate tool definitions
# Everything auto-discovered!
```

---

## Test It Out

Try these commands to see registry in action:

```bash
# See what commands are available:
slotdebug command-list

# See detailed descriptions:
slotdebug command-list --detail

# Run a normal command (nothing changed):
slotdebug analyze --file report.xlsx

# All commands still work exactly as before!
```

---

## Summary

**The registry system:**
1. **Captures metadata** about commands (description, args, examples)
2. **Stores it centrally** in `_global_registry`
3. **Allows discovery** via `slotdebug command-list`
4. **Doesn't interfere** with normal CLI execution
5. **Prepares for MCP** - when we add it, Claude will auto-discover everything
6. **Auto-scalable** - new commands just get decorated, they're automatically registered

**Zero breaking changes** - all existing commands work exactly as before!
