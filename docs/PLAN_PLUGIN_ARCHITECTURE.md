# Plugin Architecture Plan: slotdebug as Claude/Cursor Plugin

## Vision
Transform slotdebug from CLI tool to Claude/Cursor-aware plugin system where:
- LLM can discover and invoke commands automatically
- Natural language invocation: "analyze this report" → LLM runs `slotdebug analyze`
- All commands registered as tools for deep integration
- New commands auto-register without code changes

---

## Current State vs. Target State

### Current (CLI-Only)
```
User → CLI command → slotdebug CLI → analysis result
```

### Target (LLM-Integrated)
```
User (NL): "analyze report.xlsx"
    ↓
Claude/Cursor (sees user request)
    ↓
Claude discovers available tools via MCP
    ↓
Claude selects: slotdebug_analyze_report
    ↓
Claude invokes tool with parameters
    ↓
slotdebug executes command
    ↓
Result returned to Claude
    ↓
Claude formats answer to user
```

---

## Architecture: 3-Layer System

### Layer 1: Command Registry (Core)
Central registry of all slotdebug commands

```python
class CommandRegistry:
    """
    Auto-discovers commands from CLI.
    Exposes them as tools.
    Maps NL descriptions to CLI commands.
    """
    
    commands = {
        "analyze": {
            "description": "Parse, normalize and analyze RTP report",
            "cli_cmd": "analyze",
            "args": {
                "file": {
                    "type": "string",
                    "description": "Report file name (CSV/XLSX)",
                    "required": True
                },
                "out": {
                    "type": "string",
                    "description": "Output JSON file",
                    "required": False
                }
            },
            "returns": {
                "type": "object",
                "description": "Analysis result with RTP breakdown"
            }
        },
        "setup": {
            "description": "Create a new slotdebugger workspace",
            "cli_cmd": "setup",
            "args": {
                "name": {
                    "type": "string",
                    "description": "Game name",
                    "required": False
                },
                "dir": {
                    "type": "string",
                    "description": "Directory to create workspace in",
                    "required": False
                }
            }
        },
        "add": {
            "description": "Import report into workspace",
            "cli_cmd": "add",
            "args": {
                "file": {
                    "type": "string",
                    "description": "Report file path",
                    "required": True
                }
            }
        },
        "runs": {
            "description": "List all analysis runs in workspace",
            "cli_cmd": "runs",
            "returns": {
                "type": "array",
                "description": "List of past runs with status"
            }
        }
    }
```

### Layer 2: MCP Server (Integration Layer)
Exposes commands as MCP tools for Claude/Cursor

```
MCP (Model Context Protocol) Server
    ↓
    ├─ Resource Endpoints
    │  └─ /workspace - current workspace
    │  └─ /reports - available reports
    │  └─ /analysis - analysis results
    │
    └─ Tool Endpoints
       ├─ slotdebug_analyze
       ├─ slotdebug_setup
       ├─ slotdebug_add_report
       ├─ slotdebug_list_runs
       └─ [auto-registered from CommandRegistry]
```

### Layer 3: Skill Registration (Claude Discovery)
Auto-register with Claude/Cursor via .claude/skills/

```
~/.claude/skills/
  └─ slotdebugger/
      ├─ .prompt.md
      ├─ .instructions.md
      └─ plugin-spec.json
```

---

## Implementation Approach: 4 Phases

### Phase 1A: Command Registry + Metadata
**Goal:** Centralize command definitions

```python
# slotdebugger/registry.py
from typing import Dict, Any

class CommandRegistry:
    """Central registry for all slotdebug commands."""
    
    def __init__(self):
        self._commands: Dict[str, Dict[str, Any]] = {}
    
    def register(self, command_name: str, **metadata):
        """Register a command with metadata."""
        self._commands[command_name] = metadata
    
    def get_all(self) -> Dict:
        return self._commands
    
    def describe_for_lm(self) -> str:
        """Return LLM-friendly description of all commands."""
        # Format for Claude to understand
        ...

# Global registry instance
_global_registry = CommandRegistry()

def register_command(name: str, description: str, args: Dict, returns: Dict = None):
    """Decorator for command functions."""
    def decorator(func):
        _global_registry.register(name, {
            "description": description,
            "function": func,
            "args": args,
            "returns": returns or {},
            "cli_command": getattr(func, "cli_command", name)
        })
        return func
    return decorator
```

### Phase 1B: Annotate Existing Commands
```python
# slotdebugger/cli.py
from .registry import register_command

@register_command(
    "analyze",
    description="Parse, normalize and analyze RTP report (CSV/XLSX)",
    args={
        "file": {"type": "string", "description": "Report file name", "required": True},
        "out": {"type": "string", "description": "Output file", "required": False}
    },
    returns={"type": "object", "description": "Analysis with RTP breakdown"}
)
def _analyze(ws, args) -> int:
    # existing implementation
    ...

# All commands auto-register via decorator
```

### Phase 2: MCP Server Implementation
```python
# slotdebugger/mcp_server.py
from mcp.server import Server
from mcp.types import Tool, TextContent
from .registry import _global_registry

server = Server("slotdebugger")

@server.list_tools()
def list_tools():
    """Auto-generate MCP tools from registry."""
    tools = []
    for cmd_name, meta in _global_registry.get_all().items():
        tool = Tool(
            name=f"slotdebug_{cmd_name}",
            description=meta["description"],
            inputSchema={
                "type": "object",
                "properties": {
                    arg_name: {
                        "type": arg["type"],
                        "description": arg["description"]
                    }
                    for arg_name, arg in meta["args"].items()
                },
                "required": [
                    name for name, arg in meta["args"].items()
                    if arg.get("required", False)
                ]
            }
        )
        tools.append(tool)
    return tools

@server.call_tool()
def call_tool(name: str, arguments: dict):
    """Execute tool when Claude invokes it."""
    cmd_name = name.replace("slotdebug_", "")
    meta = _global_registry.get_all()[cmd_name]
    
    # Convert MCP arguments to argparse-compatible format
    result = meta["function"](arguments)
    
    return [TextContent(type="text", text=json.dumps(result))]
```

### Phase 3: Claude/Cursor Plugin Registration
```json
// ~/.claude/skills/slotdebugger/plugin-spec.json
{
  "name": "slotdebugger",
  "version": "0.1.0",
  "description": "RTP report analysis and slot game debugging",
  "mcp_server": {
    "type": "stdio",
    "command": "python",
    "args": ["-m", "slotdebugger.mcp_server"]
  },
  "tools": [
    {
      "name": "slotdebug_analyze",
      "description": "Analyze RTP report",
      "inputSchema": {
        "type": "object",
        "properties": {
          "file": {"type": "string", "description": "Report file"}
        }
      }
    }
  ],
  "auto_discover": true,
  "activate_patterns": [
    "analyze report",
    "rtp analysis",
    "slot debug",
    "game report"
  ]
}
```

### Phase 4: .claude/instructions.md Integration
```markdown
# slotdebugger Plugin

You have access to the slotdebugger RTP analysis tools via the MCP server.

## Available Tools
- slotdebug_analyze: Analyze an RTP report
- slotdebug_setup: Create a debugging workspace
- slotdebug_add_report: Import a report
- slotdebug_list_runs: Show analysis history

## When to Use
- User asks to "analyze" a report → use slotdebug_analyze
- User wants to debug game RTP → explain and use appropriate tool
- User provides game data → first suggest workspace setup

## Examples
User: "Can you analyze reports/rich_little_piggies.xlsx?"
→ Call: slotdebug_analyze(file="rich_little_piggies.xlsx")
→ Return analysis result and explain findings
```

---

## Integration with Pipeline & New Commands

### Auto-Registration on New Commands

**Rule:** Every new command = auto-registered

```python
# slotdebugger/cli.py

# When adding NEW command:
@register_command(
    "my_new_command",
    description="...",
    args={...}
)
def my_new_command(ws, args):
    ...

# Automatic effects:
# 1. ✓ Registered in CommandRegistry
# 2. ✓ Exposed as MCP tool (slotdebug_my_new_command)
# 3. ✓ Available to Claude immediately
# 4. ✓ CLI still works normally
```

### Pipeline Integration

```python
# slotdebugger/pipeline.py

class AnalysisPipeline:
    """Main analysis pipeline."""
    
    @register_command(
        "analyze",
        description="Analyze RTP report",
        args={"file": {...}, "out": {...}}
    )
    def analyze(file_path: str, output_path: str = None) -> Dict:
        """
        Parse → Normalize → Analyze
        
        Same implementation, but now:
        - Registered and discoverable
        - Can be called from Claude
        - Part of plugin system
        """
        report = parse(file_path)
        normalized = normalize_metadata(report)
        analysis = analyze_rtp(normalized)
        return analysis
```

---

## Data Flow Examples

### Example 1: Claude Analyzes Report

```
User: "Analyze rich_little_piggies.xlsx for me"
         ↓
Claude (sees request)
         ↓
Claude queries MCP: listTools()
         ↓
MCP returns: [slotdebug_analyze, slotdebug_setup, ...]
         ↓
Claude selects: slotdebug_analyze
         ↓
Claude calls: slotdebug_analyze(file="rich_little_piggies.xlsx")
         ↓
MCP Server executes: CommandRegistry["analyze"](file="...")
         ↓
Result: {metadata: {...}, components: {...}, analysis: {...}}
         ↓
Claude formats answer: "Analysis shows BG RTP of 42.2%, total RTP 96.36%"
```

### Example 2: New Command Auto-Discovered

```
Developer adds:
    @register_command("compare_reports", ...)
    def compare_reports(file1, file2):
         ...
         ↓
On next Claude restart:
    ✓ Command in registry
    ✓ MCP exposes slotdebug_compare_reports
    ✓ Claude discovers it
    ✓ User can use: "Compare these two reports"
```

### Example 3: Chained Operations

```
User: "Setup workspace, import report, analyze it"
         ↓
Claude thinks: "Need to chain 3 commands"
         ↓
Claude executes:
  1. slotdebug_setup(name="game1")
  2. slotdebug_add_report(file="report.xlsx")
  3. slotdebug_analyze(file="report.xlsx")
         ↓
Claude: "Done! Analysis shows..."
```

---

## File Structure After Implementation

```
slotdebugger/
  ├─ __init__.py
  ├─ cli.py                    # Add @register_command decorators
  ├─ pipeline.py               # Add @register_command to analyze()
  ├─ registry.py               # NEW: CommandRegistry class
  ├─ mcp_server.py             # NEW: MCP protocol handler
  ├─ plugin_installer.py       # NEW: Register with ~/.claude
  └─ ...

~/.claude/skills/slotdebugger/
  ├─ .prompt.md
  ├─ .instructions.md
  ├─ plugin-spec.json          # NEW
  └─ mcp-stdio.json            # NEW: MCP config
```

---

## Advantages vs. Current Approach

| Aspect | Current CLI | With Plugin System |
|--------|-----------|------------------|
| **Discovery** | User remembers commands | Claude discovers automatically |
| **Invocation** | Type commands | Natural language to Claude |
| **Integration** | External tool | Deep Claude integration |
| **New Commands** | Manual registration | Auto-register via decorator |
| **Chaining** | User orchestrates | Claude chains automatically |
| **Error Recovery** | User handles | Claude explains & retries |
| **Learning** | Per-command help | Claude explains in context |

---

## Implementation Roadmap

### ✓ Phase 1: Registry + Annotations
- Create CommandRegistry class
- Annotate existing commands with @register_command
- Test that registry captures all commands

### ✓ Phase 2: MCP Server
- Implement MCP server (slotdebugger/mcp_server.py)
- Map registry to MCP tools
- Test tool invocation

### ✓ Phase 3: Claude Registration
- Create ~/.claude/skills/slotdebugger/
- Generate plugin-spec.json from registry
- Create .instructions.md with examples

### ✓ Phase 4: Testing & Refinement
- Test with Claude/Cursor
- Add new test command to verify auto-registration
- Document for future developers

### ✓ Phase 5: Documentation
- Update README with plugin usage
- Document @register_command pattern
- Add examples for new developers

---

## Key Design Decisions

1. **Decorator Pattern** (@register_command)
   - ✓ Zero friction for developers
   - ✓ Existing code barely changes
   - ✓ Self-documenting

2. **MCP Protocol** (not custom)
   - ✓ Standard (Claude supports natively)
   - ✓ Works with Cursor too
   - ✓ Future-proof

3. **Auto-Discovery**
   - ✓ No manual registration lists
   - ✓ New commands work immediately
   - ✓ Less maintenance

4. **Pipeline Integration**
   - ✓ Same code path (CLI or Claude)
   - ✓ Consistency guaranteed
   - ✓ No duplication

---

## Future Enhancements

- **Streaming Results**: Large analyses streamed to Claude
- **Progress Updates**: Claude sees analysis progress
- **Caching**: Claude caches recent analysis results
- **Context Awareness**: Claude remembers workspace state
- **Batch Operations**: Claude orchestrates multi-report analysis
- **Custom Tools**: User-defined tools added to registry

