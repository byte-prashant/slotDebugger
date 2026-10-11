"""Run a volume tester unchanged and record every event it emits, with the line that emitted it.

This file runs inside the *game's* interpreter (its own venv, often an older Python),
so it uses only the standard library and works on Python 3.8+. It is copied to a
temporary directory and started as a script by `engine_indexer.tester_map`; nothing
here imports slotdebugger.

    python tracer.py --tester tests/volume_tester.py -n 2000 --out trace.json [--kw stake=1]

The tester is imported as part of its package (so its relative imports work). With
`--source`, the code run is that file instead (a verbatim copy kept in the workspace),
loaded under the original's module name so `from .. import defs` still resolves in the
game's package; its line numbers are the copy's, identical to the original's. The
method every event goes through (`dump_event`, or `emit_event`) is wrapped where it is
defined. The wrapper looks one frame up for the call site and records:

- the concrete key and how often it was produced,
- the simple local variables (str/int/float/bool/None) at that moment, a few distinct
  values each, so the values that built a dynamic key can be read back.

The engine's `play` is wrapped on the tester's engine instance (`tester.e`), so every
result it returns is seen: for each top-level key of `result`, how many results had it
and how many had it empty (None or an empty collection/string). That says whether a
response key the tester reads is ever actually returned by the engine.

What the tester writes to stdout is discarded; the engine's behaviour is not changed.
"""

import argparse
import ast
import collections
import contextlib
import importlib
import importlib.util
import json
import os
import random
import sys
import traceback

EMIT_METHODS = ("dump_event", "emit_event")
SIMPLE = (str, int, float, bool, type(None))
MAX_VALUES = 10   # distinct values kept per local variable per site
MAX_TEXT = 80     # longer strings are not useful as condition values


def load_tester(path, source=None):
    """Import the tester module, as part of its package when it is in one.

    `source`, when given, is the file whose code is executed as that module.
    """
    path = os.path.abspath(path)
    directory, stem = os.path.dirname(path), os.path.splitext(os.path.basename(path))[0]
    parts, top = [stem], directory
    while os.path.isfile(os.path.join(top, "__init__.py")):
        parts.insert(0, os.path.basename(top))
        top = os.path.dirname(top)
    if len(parts) == 1:
        sys.path.insert(0, directory)
        return _import(stem, source)
    sys.path.insert(0, top)
    package = parts[0]
    if not package.isidentifier():  # e.g. `blazing-7s-cashways`: import it under an alias
        package = "_slotdebug_game"
        package_dir = os.path.join(top, parts[0])
        spec = importlib.util.spec_from_file_location(
            package, os.path.join(package_dir, "__init__.py"), submodule_search_locations=[package_dir])
        module = importlib.util.module_from_spec(spec)
        sys.modules[package] = module
        spec.loader.exec_module(module)
    return _import(".".join([package] + parts[1:]), source)


def _import(name, source):
    if source is None:
        return importlib.import_module(name)
    parent = name.rpartition(".")[0]
    if parent:
        importlib.import_module(parent)  # the package the code's relative imports resolve in
    spec = importlib.util.spec_from_file_location(name, os.path.abspath(source))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def game_modules(tester):
    """Source files of the game's own modules that were loaded (engine, defs, ...)."""
    top = os.path.dirname(os.path.abspath(tester))
    while os.path.isfile(os.path.join(os.path.dirname(top), "__init__.py")):
        top = os.path.dirname(top)
    found = set()
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if path and path.endswith(".py") and os.path.abspath(path).startswith(top + os.sep):
            found.add(os.path.abspath(path))
    return sorted(found)


def find_tester_class(module, name=None):
    classes = [v for v in vars(module).values()
               if isinstance(v, type) and v.__module__ == module.__name__
               and any(hasattr(v, m) for m in EMIT_METHODS)]
    if name:
        classes = [c for c in classes if c.__name__ == name]
    # the most derived one: not a base of another candidate
    leaves = [c for c in classes if not any(o is not c and issubclass(o, c) for o in classes)]
    if len(leaves) != 1:
        found = ", ".join(c.__name__ for c in leaves) or "none"
        raise RuntimeError("cannot pick the tester class (found: {}); pass --class".format(found))
    return leaves[0]


def constructor_kwargs(module, source_path, cls, given):
    """Keyword arguments the tester's own `__main__` block passes, where they are module
    globals or constants; `--kw` wins. Command-line driven ones (`args.x`) are skipped."""
    kwargs, skipped = {}, []
    with open(source_path) as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == cls.__name__):
            continue
        for kw in node.keywords:
            if kw.arg is None:
                continue
            value = kw.value
            if isinstance(value, ast.Name) and value.id in vars(module):
                kwargs[kw.arg] = vars(module)[value.id]
            elif isinstance(value, ast.Constant):
                kwargs[kw.arg] = value.value
            else:
                skipped.append(kw.arg)
    for flag in ("per_spin", "per_play"):  # the event map, not the per-spin CSV
        kwargs[flag] = False
        if flag in skipped:
            skipped.remove(flag)
    if "engine" not in kwargs:
        engines = [v for v in vars(module).values()
                   if isinstance(v, type) and hasattr(v, "play") and not issubclass(v, cls)]
        if len(engines) == 1:
            kwargs["engine"] = engines[0]
    if "defs" not in kwargs and "defs" in vars(module):
        kwargs["defs"] = vars(module)["defs"]
    for item in given:  # NAME=text, or NAME:=json for numbers, lists, true/false
        key, _, raw = item.partition("=")
        if key.endswith(":"):
            key, raw = key[:-1], json.loads(raw)
        kwargs[key] = raw
        if key in skipped:
            skipped.remove(key)
    return kwargs, skipped


def _empty(value):
    return value is None or (hasattr(value, "__len__") and not isinstance(value, (int, float)) and len(value) == 0)


class Recorder:
    def __init__(self):
        self.sites = {}
        self.results = 0
        self.response_keys = {}

    def wrap_engine(self, tester):
        """Count the keys of every result the engine returns. False when there is no `tester.e.play`."""
        engine = getattr(tester, "e", None)
        play = getattr(engine, "play", None)
        if play is None:
            return False
        recorder = self

        def traced_play(*args, **kwargs):
            response = play(*args, **kwargs)
            recorder.record_result(getattr(response, "result", None))
            return response
        engine.play = traced_play
        return True

    def record_result(self, result):
        if not isinstance(result, dict):
            return
        self.results += 1
        for key, value in result.items():
            counts = self.response_keys.setdefault(str(key), {"present": 0, "empty": 0})
            counts["present"] += 1
            if _empty(value):
                counts["empty"] += 1

    def wrap(self, cls):
        """Wrap each emit method where it is defined, so every subclass goes through it."""
        wrapped = []
        for name in EMIT_METHODS:
            owner = next((k for k in cls.__mro__ if name in vars(k)), None)
            if owner is None:
                continue
            original = vars(owner)[name]
            setattr(owner, name, self._wrapper(original))
            wrapped.append("{}.{}".format(owner.__name__, name))
        return wrapped

    def _wrapper(self, original):
        recorder = self

        def traced(self, key, *args, **kwargs):
            recorder.record(sys._getframe(1), key)
            return original(self, key, *args, **kwargs)
        return traced

    def record(self, frame, key):
        site_id = "{}:{}".format(os.path.abspath(frame.f_code.co_filename), frame.f_lineno)
        site = self.sites.get(site_id)
        if site is None:
            site = self.sites[site_id] = {"file": os.path.abspath(frame.f_code.co_filename),
                                          "line": frame.f_lineno, "function": frame.f_code.co_name,
                                          "keys": collections.Counter(), "values": {}}
        site["keys"][str(key)] += 1
        for name, value in frame.f_locals.items():
            if isinstance(value, SIMPLE) and not (isinstance(value, str) and len(value) > MAX_TEXT):
                seen = site["values"].setdefault(name, [])
                if value not in seen and len(seen) < MAX_VALUES:
                    seen.append(value)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--tester", required=True)
    parser.add_argument("--source", help="file whose code runs as the tester module (a verbatim copy)")
    parser.add_argument("-n", type=int, default=1000)
    parser.add_argument("--out", required=True)
    parser.add_argument("--class", dest="cls")
    parser.add_argument("--kw", action="append", default=[], metavar="NAME=TEXT|NAME:=JSON")
    parser.add_argument("--seed", type=int)
    args = parser.parse_args(argv)

    if args.seed is not None:
        random.seed(args.seed)
    result = {"tester": os.path.abspath(args.tester), "requested": args.n, "completed": 0,
              "source": os.path.abspath(args.source) if args.source else None,
              "python": sys.version.split()[0], "error": None}
    recorder = Recorder()
    try:
        module = load_tester(args.tester, args.source)
        cls = find_tester_class(module, args.cls)
        kwargs, skipped = constructor_kwargs(module, args.source or args.tester, cls, args.kw)
        result.update({"class": cls.__name__, "wrapped": recorder.wrap(cls),
                       "kwargs": sorted(kwargs), "skipped_kwargs": skipped})
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink):
            tester = cls(**kwargs)  # the tester binds its output file to stdout here
            result["engine_wrapped"] = recorder.wrap_engine(tester)
            for _ in range(args.n):
                tester.gameplay()
                result["completed"] += 1
    except Exception as e:  # report how far it got; the sites recorded so far still count
        result["error"] = "{}: {}".format(type(e).__name__, e)
        result["traceback"] = traceback.format_exc().splitlines()[-12:]
    result["sites"] = [dict(s, keys=dict(s["keys"])) for s in recorder.sites.values()]
    result["game_modules"] = game_modules(args.tester)
    result["results_seen"] = recorder.results
    result["response_keys"] = recorder.response_keys
    with open(args.out, "w") as f:
        json.dump(result, f, indent=1, default=str)
    return 0 if result["error"] is None else 1


if __name__ == "__main__":
    sys.exit(main())
