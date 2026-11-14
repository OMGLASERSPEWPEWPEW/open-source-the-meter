# QA: the meter

Date: 2026-11-14 · Scope: SPEC.md, `reference/`, `vectors/`, AGENTS.md, the docs

## Automated

Run on macOS's Python 3.9 (`/usr/bin/python3`) and on a newer one:

- [x] `python3 -B -I vectors/run.py`: 7 of 7 cases pass. That's 9 runs, counting each step.
- [x] `python3 -B -I -m unittest discover -s reference/tests`: 47 tests pass. They cover:
  - the MCP handshake and errors, its argument checks, and output limits;
  - the wrapper (a moved clone, a closed pipe), its install and undo, and strict JSON;
  - `mcp-install` for Cursor, round trip, never printing other entries;
  - the dashboard's Host, Sec-Fetch-Site and method checks, with headers on error pages;
  - a real `git log` in a temp repo, manual-reading rules, bad values in logs, the local zone across a DST change.
- [x] Expected files checked against hand arithmetic (see vectors/README.md).
- [x] AGENTS.md and CLAUDE.md are identical and plain ASCII. The audit's grep matches only the files AGENTS.md names.

## On a real machine (run once per release)

Use a throwaway home first: `OPEN_METER_HOME="$(mktemp -d)"`.

- [ ] **`status`:**
  - lists the Claude Code and Codex folders, with their file counts;
  - shows 0 bad lines, or a small count;
  - names any unpriced models.
- [ ] **`summary --period 7d`:**
  - totals look plausible next to the person's sense of their use;
  - the cache-read share is high (90% or more is typical for agent work).
- [ ] **`sync`, run twice:** the second reports 0 new events.
- [ ] **`statusline-install`:**
  - the dry run shows the right before and after;
  - after `--apply`, the status line looks exactly as before;
  - after a reply, `status` shows a statusline reading;
  - `statusline-uninstall --apply` restores the file. Compare it with the backup.
- [ ] **`ceiling`:**
  - the windows match the plan's own reset times;
  - accounts are separated when there's more than one;
  - the lower-bound note is shown.
- [ ] **The MCP server:**
  - `claude mcp list` shows `open-meter` connected;
  - in a session, `meter_status`, `meter_summary` and `meter_ceiling` answer;
  - `lsof -nP -i -a -p <pid>` prints nothing while the server runs.
- [ ] **The dashboard:**
  - the page loads at `http://127.0.0.1:8787/`;
  - `curl -H 'Host: evil.example:8787' http://127.0.0.1:8787/` gives 421.
- [ ] **Undo, all of it:** `statusline-uninstall --apply`, remove the MCP entries, `rm -rf ~/.open-meter`. Nothing
  of the meter is left outside the clone.

## The picture (FR-42 – FR-45)

- [ ] **The vectors.** `python3 -B -I vectors/run.py --case 07-trends` passes. That case pins:
  - the 7-day spend change and its direction;
  - the ceiling's change from its first to its last trusted week ("within rounding" when the bands overlap is
    pinned by the unit tests);
  - the daily series with its 7-day average;
  - both sparklines.
- [ ] **The dashboard's opening.** With a temp home synced from case 07's logs, the page opens with:
  - the headline: the ceiling with "down", its percent and the first week's date, then the 7-day spend sentence;
  - the ceiling chart: one line per account, the band shaded, only trusted weeks, the lower-bound note under it;
  - the spend chart: 30 days, with the 7-day average as the stronger line;
  - the tables, below the charts. (A person checks the look in light and dark mode.)
- [ ] **Hover.** Hovering a point shows its date and value, through the browser's own tooltip.
- [ ] **No scripts.** The page still has no `<script>` and loads nothing from elsewhere.
- [ ] **Empty states.** With no readings, the ceiling chart's place says how to get them. With no events, the spend
  chart's place says nothing has been read yet.
- [ ] **CLI.** `summary` and `ceiling` start with a trend sentence and a sparkline. `chart` prints them alone.
  `chart --out <file>` writes one SVG that opens in a browser, with no scripts.
- [ ] **MCP.** The `meter_summary` and `meter_ceiling` results carry `trend`: the sentence, the sparkline and the
  numbers. Their text starts with the sentence.
- [ ] **README.** The README opens with the synthetic chart image, captioned as synthetic, and its "show me my
  numbers" step leads to the chart.

## Plain numbers (FR-46, from the stranger test)

- [ ] A week under 20% used, or one mostly made of spend with no known account, reads "not enough data", never a
  dollar figure, and isn't on the chart.
- [ ] Overlapping weeks for one account say "an early reset" or "two reset schedules mixed", not a question.
- [ ] `sync` and `status` give the same number of plan readings.
- [ ] `sync` says "repeated log lines for one response, skipped so nothing counts twice".
- [ ] After the README's test commands (with `-B`), `find . -name __pycache__` prints nothing, and the audit's
  no-bytecode check passes.

## Regressions to watch

- **Log formats change** with client updates. A sudden drop in tokens, or many bad lines, is the sign. Re-check
  SPEC.md §3 against a fresh transcript, keys and types only.
- **New models** show as unpriced until `prices.json` has their row.
