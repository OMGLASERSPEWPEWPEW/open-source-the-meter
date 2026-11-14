# Golden vectors

Each folder in `cases/` is one test of [SPEC.md](../SPEC.md). It holds:

- **made-up logs** in the clients' real formats;
- **`case.json`:** what to run;
- **`expected.json`** (or `expected-1.json`, `expected-2.json`, … for cases with steps): the exact answer.

None of it comes from a real person's logs.

| Case | What it pins down |
|---|---|
| `01-claude-pricing` | One response written as several lines counts once, with the largest output any of its lines carried (a streamed response); a resumed session's copy counts once; every token kind has its price (5-minute and 1-hour cache writes, fast mode, US-only, web search, long prompts); aliases; an unpriced model; a broken line; a subagent's file; days in a `-06:00` zone |
| `02-ceilings` | Implied ceilings per account: the band, firm and early readings, week over week, an early reset, unattributed spend, the limit-hit reading, a manual reading, a five-hour window, a refused reading, and readings with no known account on a machine with two accounts |
| `03-codex` | Codex's running totals and repeats, cached and cache-write input, long prompts, model changes, its own plan readings in both forms, a resumed copy, a context-fill event, an unknown model |
| `04-sync-steps` | Three syncs on one ledger: a half-written line read once it's whole, appends, a new file, a file cut short and read again, the readings file edited by hand |
| `05-yield` | Level 2 git yield from a fixed `git log`: lockfiles and generated files left out, a revert pair, a binary file, spend only from sessions inside the repo |
| `06-hostile` | Hostile input stays data: odd text kept as text, mistyped or absurd counts as 0, a bad account id ignored, a symlinked log never read, reset times past year 9999 or in milliseconds or not numbers refused, a `cwd` that isn't text, a line nested too deep to parse (counted bad, and the sync goes on), and a readings file with refused lines |
| `07-trends` | Trends and the chart: the 7-day spend change and direction, the weekly ceiling's change from its first to its last trusted week, the daily series with its 7-day average, the sparklines, the plotted points and the headline numbers. Cases 01, 02 and 03 also pin their trend and chart parts (a missing earlier week, the trusted filter, Codex) |

The cases use `prices.json` in this folder, a frozen copy of the price list. Updating the repo's prices never
changes an expected answer.

## Running them

```sh
python3 -B -I vectors/run.py                          # the reference
python3 -B -I vectors/run.py -- node meter/index.js   # any implementation, any language
python3 -B -I vectors/run.py --case 03-codex -- ./my-meter
```

The runner copies each case to a temp folder and points `HOME`, `OPEN_METER_HOME`, `CLAUDE_CONFIG_DIR` and
`CODEX_HOME` inside it. It calls your command's `conformance` subcommand (SPEC.md §12) and compares the JSON it
prints with the expected file, by value. It runs the command you give it and nothing else.

## Without Python

Your implementation can check itself. For each case:

1. Copy the case to a temp folder. For a case with steps, copy each `stepN/` over the last.
2. Make any `symlinks` that `case.json` lists.
3. Run your `conformance` command.
4. Compare its output with the expected file: numbers by value, lists in order.

## Hand-checked

The expected files were made by the reference, then checked against arithmetic done by hand, before they were
committed. For example:

- **Case 01:** 26,040,000 nano-dollars for the first response (10 × 4,000 + 50,000 × 200 + 2,000 × 5,000 +
  300 × 20,000).
- **Case 02:** $32.00 and $27.03 for two weeks: $8.00 at 25%, then $10.00 at 37%, a change of −15.5%.
- **Case 05:** 2 commits, 13 lines added and $3.00, for 0.67 commits and 4.33 lines per dollar.
