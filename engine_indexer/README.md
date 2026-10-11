# engine_indexer

`slotdebug trace-tester` builds the first map, report key -> volume tester line -> engine
response key, by running the tester traced; see the package docstring in
[__init__.py](__init__.py) and the slot-debugger skill.

The rest of this file describes the earlier prototype, `volume_tester_tree.py`.

# Feature-rooted Volume Tester dependency tree prototype

This tool statically analyzes the actual Python Volume Tester source, then joins feature roots to the existing hierarchical engine index. It does not change or execute the Volume Tester.

## Run

```bash
python volume_tester_tree.py \
  --tester /path/to/volume_tester.py \
  --engine-index /path/to/zeus_unleashed_engine_index.json \
  --features features.json \
  --feature respin \
  --out output/respin_tree.json \
  --markdown output/respin_tree.md \
  --max-depth 4
```

Update `features.json` with the exact qualified names and event keys used by your game. Use the real `.py` source file, not a rendered Markdown paste, so line numbers map to the source.

## Design

- `tester_nodes`: tester methods, line ranges, branch conditions, result-field reads, event writes, assignments, and call candidates.
- `engine_nodes`: nodes reached from configured engine roots using the existing index's call edges.
- `roots`: explicit feature entry points from the manifest.
- Report event keys are used to select relevant tester handlers.

## Important limitations

- Static call edges are candidates; dynamic dispatch and inherited methods may not resolve.
- Dynamic event names such as `f'respin_total_line_winnings_{prefix}'` are captured as expressions and must be mapped separately.
- This prototype does not infer the full variable dataflow or the RTP aggregator formula.
- It does not alter simulation behavior or add runtime tracing.
