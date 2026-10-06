# Report Format

Supported: `.csv` (**tab-delimited**) and `.xlsx` (first/active sheet). Parsed by [report_parser/parser.py](../report_parser/parser.py).

```
Engine Name	blazing-7s-cashway
...metadata rows (key<TAB>value)...
Win Hit Rate	3.2474
Events
Event	Miscellaneous
base_bonus_win	(34892496.9000, 94967255)      <- component rows: name<TAB>(win, frequency)
...
Round statistics	10000000 plays
RTP (0 - 10000000)	93.9190                 <- parsing stops at the first row starting "RTP ("
```

## Rules

- Metadata keys recognised: Engine Name, Engine Version, Total number of plays, Plays with stake, Total amount staked, Total amount paid, Largest win, Game RTP, Jackpot RTP, Total RTP, Win standard deviation, Win Hit Rate. Values are kept as strings.
- Components are captured after a row whose first cell is `Event`.
- Component value is `(win, frequency)`. Parser swaps only when the first value is an integer and the second has a decimal point; otherwise order is (win, frequency).
- Rows that don't parse as a tuple are skipped silently.
- XLSX: empty cells become `""`; numeric `0` is preserved as `"0"`.
- Rows with fewer than two cells are ignored.

## Output shape

```json
{"metadata": {"Engine Name": "..."}, "components": [{"name": "base_line_win", "rtp": 39105990.6, "frequency": 94967255}]}
```
Note `rtp` here is the raw **win amount**, not a ratio — the normalizer converts it.
