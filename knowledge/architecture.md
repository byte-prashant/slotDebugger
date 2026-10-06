# Architecture

```
report file ──▶ report_parser ──▶ RTPNormalizer ──▶ RTPAnalyzer ──▶ [plan generator] ──▶ [agent loop ⇄ LLM ⇄ think.py ⇄ tools] ──▶ [final report]
                  built             built            built              NOT BUILT               NOT BUILT                          NOT BUILT
```

## Data between stages

| Stage | Input | Output |
|---|---|---|
| `ReaderFactory.get_reader(path)` → `RTPService.process` | file path | `{"metadata": {str: str}, "components": [{name, rtp(win), frequency}]}` |
| `RTPNormalizer(components, {"total_plays", "total_game_win"}).run()` | components + numeric metadata (caller must convert the string metadata) | `{"normalized_components": {name: {rtp, hit_rate}}, "dependency_graph": {parent: {"children": [...]}}}` |
| `RTPAnalyzer(normalized).run()` | normalizer output | `{"status": "OK"/"FAIL", "issues": [...], "total_rtp": float}` |
| `think.py` | CLI flags | prints thought; persists `.think_state.json` (plan, history, branches, step index) |

## Notes

- The caller currently has to map metadata strings (`"Total number of plays"`, `"Total amount paid"`) to the normalizer's `total_plays` / `total_game_win` keys — a glue function is missing.
- `think.py` state lives beside the script and is gitignored; `--reset` deletes it.
- Run all tests from the repo root with `pytest` (config in `pytest.ini`).
- Design intent: the LLM proposes thoughts, `think.py` validates/persists them, tools produce evidence; the plan keeps the LLM on a deterministic drill-down path (see playbook).
