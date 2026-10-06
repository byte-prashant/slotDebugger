# RTP Concepts

- **RTP (Return To Player)** = total paid / total staked over many plays. Report example: staked 100,000,000, paid 93,964,971 → RTP 93.965%.
- **Hit rate** = plays with a win / total plays. In this repo `hit_rate` per component = `frequency / total_plays` (how often that component paid).
- **Component** = one named win source in the report (`base_line_win`, `ultraboost_cash_win`, …). Each has a total win amount and a frequency.
- **Total component** = a name containing `_total` (`base_total_win`). It is expected to equal the sum of its children.
- **Share of win** (what `RTPNormalizer` calls `rtp`) = `component_win / total_game_win`. It is *not* component RTP against stake. To get component RTP points: `component_win / total_staked`, or `share × Game RTP`.

## Invariants the analyzer relies on

1. Σ share of all `*_total*` components ≈ 1.0 (every win belongs to exactly one top-level feature).
2. For each total: share(parent) ≈ Σ share(children), tolerance currently absolute 0.001.
3. Game RTP in metadata ≈ `Total amount paid / Total amount staked` (not yet checked in code).

## Naming convention (inferred, not configurable yet)

`<feature>_<sub-component>_win`, with `<feature>_total_win` as the parent. Children are every component starting with `<feature>_` that is not itself a total. Typos in names (e.g. `utraboosst_…`) silently orphan a child, which shows up as a parent mismatch.

## Statistical caveat

Simulated RTP has sampling error. Std dev of win per play is in the report (`Win standard deviation`); the standard error of RTP after N plays is ≈ σ/√N. At 100M plays and σ≈4.9 that is ≈0.0005 (0.05 RTP points). Deviations well beyond that are real; within it they are noise. Expected-vs-actual comparison (not built) should use this.
