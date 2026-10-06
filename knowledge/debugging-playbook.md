# RTP Debugging Playbook

Drill-down order used to build `think.py` plans ("Plan = which component's RTP isn't matching → sub-component → reels, weights, win calc").

1. **Sanity-check the report**: Game RTP ≈ paid/staked; Σ totals ≈ 1.0; plays count plausible; sample size large enough vs. σ.
2. **Find the offending component**: compare actual vs. expected share/RTP per top-level feature; rank by absolute impact (RTP points).
3. **Find the sub-component**: for the worst parent, compare children; also check parent ≠ Σ children (naming/aggregation bug).
4. **Classify the symptom**
   - Frequency off, win-per-hit fine → trigger odds, reel strips/symbol weights, scatter counts.
   - Frequency fine, win-per-hit off → paytable, multipliers, win-line evaluation, feature payouts.
   - Both off → RNG/seed/stop selection, or wrong config loaded.
   - Only in a bonus → retrigger logic, spin counts, bonus-specific reels/weights.
5. **Verify with a tool**: recompute theoretical RTP from config; run a targeted simulation; chi-square the RNG/stop distribution.
6. **Conclude**: root cause, evidence, fix suggestion, residual uncertainty. Branch (`--branchFromThought`) when two hypotheses fit; revise (`--isRevision`) when a tool result contradicts an earlier thought.

## Example plan

```bash
python3 sequential_thinking/think.py --setPlan "Validate report totals,Rank components by RTP gap,Isolate sub-component,Check reels and weights,Check win calculation,Check RNG,Summarise root cause"
```
