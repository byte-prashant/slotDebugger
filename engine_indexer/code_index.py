"""Read a game engine and the framework code it builds on, without importing any of it.

Importing would run the game and need its dependencies, so modules are found on disk
the way Python would find them and parsed as text:

    index = CodeIndex(roots=["/path/to/OGA-5.0.2", game_dir])
    module = index.load("/game/engine.py")
    cls = index.resolve_name(module, "Blazing7sCashwaysGameEngine")
    index.mro(cls)                       # game class, OGLE_Slot, ..., OGLE_Engine, OGA_Engine
    index.find_method(cls, "check_winnings_cap")   # -> (OGLE_Engine, its FunctionDef)

`from OGLE import OGLE_Slot` is followed through the re-export in `OGLE/__init__.py` to
the class's own module. A base class or name that cannot be found is recorded in
`unresolved`, never guessed: pass the framework checkout as a root to resolve it.
"""

import ast
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

MAX_REEXPORT_HOPS = 12


@dataclass
class Module:
    path: str
    name: str
    tree: ast.Module
    source: str = ""
    # local name -> ("module", dotted, level) | ("from", dotted, level, attr)
    imports: Dict[str, Tuple] = field(default_factory=dict)
    classes: Dict[str, "ClassInfo"] = field(default_factory=dict)
    functions: Dict[str, ast.FunctionDef] = field(default_factory=dict)
    constants: Dict[str, ast.Assign] = field(default_factory=dict)


@dataclass
class ClassInfo:
    module: Module
    name: str
    node: ast.ClassDef
    methods: Dict[str, ast.FunctionDef] = field(default_factory=dict)

    @property
    def qualname(self) -> str:
        return f"{self.module.name}.{self.name}"


class CodeIndex:
    def __init__(self, roots: List[str]):
        self.roots = [os.path.abspath(r) for r in roots]
        self.modules: Dict[str, Module] = {}
        self.unresolved: List[Dict] = []
        self.parse_errors: List[Dict] = []
        self._mro: Dict[str, List[ClassInfo]] = {}

    # ---- modules ----
    def load(self, path: str) -> Optional[Module]:
        path = os.path.abspath(path)
        if path in self.modules:
            return self.modules[path]
        try:
            with open(path, encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source, filename=path)
        except (OSError, SyntaxError, UnicodeDecodeError) as e:
            self.parse_errors.append({"file": path, "error": f"{type(e).__name__}: {e}"})
            return None
        module = Module(path, self._dotted(path), tree, source)
        for node in _module_level(tree.body):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[0]
                    target = alias.name if alias.asname else alias.name.split(".")[0]
                    module.imports[local] = ("module", target, 0)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name != "*":
                        module.imports[alias.asname or alias.name] = ("from", node.module or "", node.level, alias.name)
            elif isinstance(node, ast.ClassDef):
                info = ClassInfo(module, node.name, node)
                info.methods = {n.name: n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
                module.classes[node.name] = info
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                module.functions[node.name] = node
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        module.constants[target.id] = node
        self.modules[path] = module
        return module

    def module_file(self, dotted: str, level: int = 0, importer: Optional[Module] = None) -> Optional[str]:
        """The file `import dotted` (or `from .{level} dotted import`) refers to."""
        if level:
            base = os.path.dirname(importer.path)
            for _ in range(level - 1):
                base = os.path.dirname(base)
            roots = [base]
        else:
            roots = self.roots
        parts = dotted.split(".") if dotted else []
        for root in roots:
            stem = os.path.join(root, *parts) if parts else root
            for candidate in (stem + ".py", os.path.join(stem, "__init__.py")):
                if parts or candidate.endswith("__init__.py"):
                    if os.path.isfile(candidate):
                        return os.path.abspath(candidate)
        return None

    # ---- names ----
    def resolve_name(self, module: Module, name: str, hops: int = 0):
        """What `name` means in `module`: a ClassInfo, ("function", Module, node),
        ("module", Module), ("constant", Module, assign), or None."""
        if hops > MAX_REEXPORT_HOPS:
            return None
        if name in module.classes:
            return module.classes[name]
        if name in module.functions:
            return ("function", module, module.functions[name])
        if name in module.imports:
            spec = module.imports[name]
            if spec[0] == "module":
                path = self.module_file(spec[1])
                target = self.load(path) if path else None
                return ("module", target) if target else None
            _, dotted, level, attr = spec
            path = self.module_file(dotted, level, module)
            target = self.load(path) if path else None
            if target is not None:
                found = self.resolve_name(target, attr, hops + 1)  # a re-export: follow it
                if found is not None:
                    return found
            # `from pkg import sub` can name a submodule rather than something in pkg/__init__
            sub = self.module_file(f"{dotted}.{attr}" if dotted else attr, level, module)
            loaded = self.load(sub) if sub else None
            return ("module", loaded) if loaded else None
        if name in module.constants:
            return ("constant", module, module.constants[name])
        return None

    def resolve_expr(self, module: Module, node: ast.AST):
        """`Name` or `a.b.C` in `module` -> what it refers to (see resolve_name)."""
        if isinstance(node, ast.Name):
            return self.resolve_name(module, node.id)
        if isinstance(node, ast.Attribute):
            base = self.resolve_expr(module, node.value)
            if isinstance(base, tuple) and base[0] == "module":
                return self.resolve_name(base[1], node.attr)
        return None

    # ---- classes ----
    def bases(self, cls: ClassInfo) -> List[ClassInfo]:
        found = []
        for base in cls.node.bases:
            target = self.resolve_expr(cls.module, base)
            if isinstance(target, ClassInfo):
                found.append(target)
            elif not (isinstance(base, ast.Name) and base.id == "object"):
                entry = {"class": cls.qualname, "base": ast.unparse(base), "file": cls.module.path,
                         "line": cls.node.lineno}
                if entry not in self.unresolved:
                    self.unresolved.append(entry)
        return found

    def mro(self, cls: ClassInfo) -> List[ClassInfo]:
        """C3 linearization over the bases that could be found."""
        key = cls.qualname
        if key in self._mro:
            return self._mro[key]
        self._mro[key] = [cls]  # guards against a cycle while computing
        seqs = [list(self.mro(b)) for b in self.bases(cls)] + [list(self.bases(cls))]
        result = [cls]
        while any(seqs):
            seqs = [s for s in seqs if s]
            for seq in seqs:
                head = seq[0]
                if not any(head in s[1:] for s in seqs):
                    break
            else:  # inconsistent hierarchy: fall back to depth-first order
                rest = [c for s in seqs for c in s]
                result += [c for i, c in enumerate(rest) if c not in result and c not in rest[:i]]
                break
            result.append(head)
            seqs = [s[1:] if s[0] is head else s for s in seqs]
        self._mro[key] = result
        return result

    def find_method(self, cls: ClassInfo, name: str) -> Optional[Tuple[ClassInfo, ast.FunctionDef]]:
        for klass in self.mro(cls):
            if name in klass.methods:
                return klass, klass.methods[name]
        return None

    # ---- helpers ----
    def _dotted(self, path: str) -> str:
        for root in self.roots:
            if path.startswith(root.rstrip(os.sep) + os.sep):
                rel = os.path.relpath(path, root)
                break
        else:
            rel = os.path.basename(path)
        parts = rel[:-3].split(os.sep) if rel.endswith(".py") else rel.split(os.sep)
        if parts[-1] == "__init__":
            parts = parts[:-1] or ["__init__"]
        return ".".join(parts)


def _module_level(body):
    """Module-level statements, including those inside `if`/`try`/`with` blocks: frameworks
    import conditionally (`if PACKAGE_MODE: from OGLE.slot.engine import OGLE_Slot`)."""
    for node in body:
        if isinstance(node, ast.If):
            yield from _module_level(node.body)
            yield from _module_level(node.orelse)
        elif isinstance(node, ast.Try):
            for part in (node.body, *(h.body for h in node.handlers), node.orelse, node.finalbody):
                yield from _module_level(part)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            yield from _module_level(node.body)
        else:
            yield node


def default_roots(engine: str, extra: List[str]) -> List[str]:
    """Given paths first, then the engine's directory and every directory above it."""
    roots = [os.path.abspath(p) for p in extra]
    directory = os.path.dirname(os.path.abspath(engine))
    while True:
        roots.append(directory)
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    return list(dict.fromkeys(roots))
