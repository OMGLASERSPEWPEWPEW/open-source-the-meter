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
| `08-account-switch` | Accounts by reset schedule (SPEC.md §3.8): one sign-in for the machine moves every running session while the owner lines stay put; each reading's schedule says whose week it is, and spend, a subagent's too, follows the latest reading's account |
| `09-plan-reset` | A plan that resets a week's usage early under the same reset time (SPEC.md §8): only the readings and spend before the reset count, at the week's highest reading (the first 100%, a limit hit, over a later 100% whose reset time moved 2 seconds), and the row's `reset_at` says when; a stale lower reading in an ordinary week is passed over; a firm week with no spend on this machine has no dollar figure and is never plotted; `tokens` counts cache reads |
| `10-reset-seen-once` | A reset only a new session saw (SPEC.md §8): two sessions showed every percent from 0% to 60%, so the climb after the reset leaves no new distinct reading; the readings as recorded hold the new session's 1%, the reset is found, and 60% with the use before it stands for the week, though the climb passes 80% |

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
- **Case 09:** $34.30 up to the 98% reading, so $35.00 (the stale 91% after it would have given $35.30 / 0.91 =
  $38.79); $40.00 of spend up to the limit, so $40.00 at 100%, a change of +14.3% (all $70.00 at the stale 93% last
  reading would have given $75.27); 6,665,000 tokens for the first week (5,000,000 of them cache reads).
- **Case 10:** $1.00 an hour; 60 hours to the 60% reading, so $60.00 at 60% is $100.00. The distinct readings alone
  would miss the reset and take the 80% reading at hour 120: $120.00 at 80%, $150.00.
