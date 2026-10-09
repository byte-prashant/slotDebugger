---
name: slot-debugger
description: Use this skill to debug slot-game RTP reports (CSV/XLSX from a simulation). Trigger when the user shares an RTP report, asks which feature or component's RTP is off, or wants to check that parent totals equal the sum of their children.
---

# Slot Debugger

## Analyze a report

```bash
slotdebug analyze --file report.csv            # JSON to stdout
slotdebug analyze --file report.xlsx --out analysis.json
```

Output JSON has `metadata`, `components` (each with `rtp` as a share of total win and `hit_rate`), `dependency_graph` (`<prefix>_total_*` parent -> `<prefix>_*` children) and `analysis` (`status` OK/FAIL, `issues`, `total_rtp`).

Exits with code 2 and `error:` on stderr for a missing file, unsupported extension, or a report without the metadata/components it needs.

## Match the game's expected report

If the game has an `expected_rtp_report.json` (workspace root, `data/`, `reports/`, beside the report, or `--expected <path>`), the output instead takes **exactly that file's shape, keys, scale and precision**, filled with measured values — so it can be diffed against the expected file directly. The game's feature names (`BG`, `FG12`, ...) are matched to the report's component rows heuristically: **read the `note:` lines on stderr** to check each match, and treat a `null` value as "no component matched this key", not as zero.

## Debug loop

1. Run `slotdebug analyze` and read `analysis.issues`. Each `Mismatch in <parent>` means that parent's RTP differs from the sum of its children by more than 0.001.
2. Plan the investigation with the sequential-thinking skill (`slotdebug think --setPlan "..."`): component -> sub-component -> reels / weights / win calculation / RNG.
3. Record each finding with `slotdebug think --thought ...` and finish with `--nextThoughtNeeded false`.

## Notes

- Report format: tab-delimited even for `.csv`; component rows are `name<TAB>(win, frequency)`.
- Component `rtp` is the share of total win, not RTP against stake.
- `slotdebug think` is the sequential-thinking CLI (see the sequential-thinking skill).
