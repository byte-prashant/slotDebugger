"""Command registry for slotdebug - centralized command metadata and discovery."""

from typing import Dict, Any, Optional, Callable, List
import json
import sys
from functools import wraps


class CommandMetadata:
    """Metadata for a registered command."""
    
    def __init__(
        self,
        name: str,
        description: str,
        args: Optional[Dict[str, Dict[str, Any]]] = None,
        returns: Optional[Dict[str, Any]] = None,
        examples: Optional[List[str]] = None
    ):
        self.name = name
        self.description = description
        self.args = args or {}
        self.returns = returns or {}
        self.examples = examples or []
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "args": self.args,
            "returns": self.returns,
            "examples": self.examples
        }


class CommandRegistry:
    """Central registry for all slotdebug commands."""
    
    def __init__(self):
        self._commands: Dict[str, Dict[str, Any]] = {}
        self._functions: Dict[str, Callable] = {}
    
    def register(
        self,
        name: str,
        description: str,
        func: Callable,
        args: Optional[Dict[str, Dict[str, Any]]] = None,
        returns: Optional[Dict[str, Any]] = None,
        examples: Optional[List[str]] = None
    ) -> None:
        """Register a command."""
        metadata = CommandMetadata(
            name=name,
            description=description,
            args=args,
            returns=returns,
            examples=examples
        )
        
        self._commands[name] = metadata.to_dict()
        self._functions[name] = func
    
    def get_command(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a command's metadata."""
        return self._commands.get(name)
    
    def get_function(self, name: str) -> Optional[Callable]:
        """Get a command's function."""
        return self._functions.get(name)
    
    def get_all_commands(self) -> Dict[str, Dict[str, Any]]:
        """Get all registered commands."""
        return self._commands.copy()
    
    def list_commands(self) -> List[str]:
        """List all registered command names."""
        return list(self._commands.keys())
    
    def describe_command(self, name: str) -> str:
        """Get human-readable command description."""
        if name not in self._commands:
            return f"Command '{name}' not found"
        
        meta = self._commands[name]
        lines = [
            f"Command: {name}",
            f"Description: {meta['description']}",
        ]
        
        if meta.get("args"):
            lines.append("\nArguments:")
            for arg_name, arg_meta in meta["args"].items():
                required = arg_meta.get("required", False)
                req_mark = "*" if required else ""
                lines.append(f"  {arg_name}{req_mark}: {arg_meta.get('description', '')}")
                if arg_meta.get("type"):
                    lines.append(f"    (type: {arg_meta['type']})")
        
        if meta.get("returns"):
            lines.append(f"\nReturns: {meta['returns'].get('description', 'Result')}")
        
        if meta.get("examples"):
            lines.append("\nExamples:")
            for example in meta["examples"]:
                lines.append(f"  {example}")
        
        return "\n".join(lines)
    
    def describe_all_commands(self) -> str:
        """Get human-readable description of all commands."""
        lines = ["Registered Commands\n" + "=" * 50]
        for name in sorted(self.list_commands()):
            lines.append(f"\n{self.describe_command(name)}")
        return "\n".join(lines)
    
    def to_json(self) -> str:
        """Export registry as JSON (useful for MCP later)."""
        return json.dumps(
            {
                "commands": self._commands,
                "count": len(self._commands)
            },
            indent=2
        )


# Global registry instance
_global_registry = CommandRegistry()


def register_command(
    name: str,
    description: str,
    args: Optional[Dict[str, Dict[str, Any]]] = None,
    returns: Optional[Dict[str, Any]] = None,
    examples: Optional[List[str]] = None
):
    """
    Decorator to register a command.
    
    Usage:
        @register_command(
            "analyze",
            description="Analyze an RTP report",
            args={
                "file": {
                    "type": "string",
                    "description": "Report file path",
                    "required": True
                }
            }
        )
        def analyze_report(ws, args):
            ...
    """
    def decorator(func: Callable) -> Callable:
        _global_registry.register(
            name=name,
            description=description,
            func=func,
            args=args,
            returns=returns,
            examples=examples
        )
        
        # Mark function with registry info (useful for debugging)
        func._registered_command = name
        
        @wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        
        wrapper._registered_command = name
        return wrapper
    
    return decorator


def get_registry() -> CommandRegistry:
    """Get the global command registry."""
    return _global_registry


def get_registered_command(name: str) -> Optional[Callable]:
    """Get a registered command function by name."""
    return _global_registry.get_function(name)


def list_registered_commands() -> List[str]:
    """List all registered command names."""
    return _global_registry.list_commands()


def describe_registered_command(name: str) -> str:
    """Get description of a registered command."""
    return _global_registry.describe_command(name)


def describe_all_registered_commands() -> str:
    """Get descriptions of all registered commands."""
    return _global_registry.describe_all_commands()
