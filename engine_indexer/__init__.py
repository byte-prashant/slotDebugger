"""Index how a game's code produces its RTP report.

Two maps, joined: report key -> volume tester line -> engine response key (by running the
tester), then response key -> the engine code that computes it (by reading the engine):

    slotdebug trace-tester --file report.xlsx --python <game venv>/bin/python --path <OGA checkout>

runs the volume tester with its event method traced, and joins what the run emitted
with what each emitting line says in the source. Inside a workspace the code that runs
is the verbatim copy in `slotdebugger/tester/` (made at `slotdebug setup`), loaded under
the original's module name; the game's files are never written to.

| Module | Responsibility |
|---|---|
| [tracer.py](tracer.py) | runs inside the game's interpreter (stdlib only, Python 3.8+); records each event's key, line and local values |
| [sites.py](sites.py) | reads a tester's source: each event call's key expression, response key and enclosing conditions |
| [tester_map.py](tester_map.py) | runs the tracer, merges run and source per call site, joins report rows |
| [code_index.py](code_index.py) | find modules on disk without importing them; imports, re-exports, classes, C3 MRO across game + framework |
| [slicer.py](slicer.py) | backward slice: reaching assignments (with conditions and kills), calls, params, attributes, constants |
| [engine_map.py](engine_map.py) | response-key roots, the graph, the join with the tester map (`chain`), text trees |
| [volume_tester_tree.py](volume_tester_tree.py) | earlier prototype: feature-rooted tree from a separately built engine index |
"""

from engine_indexer.tester_map import TraceError, build

__all__ = ["TraceError", "build"]

# `slotdebug index`: engine_map.build(engine, keys, paths, tester_map) -> the second map
