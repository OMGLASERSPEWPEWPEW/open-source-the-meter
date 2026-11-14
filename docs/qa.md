# QA: the meter

Date: 2026-11-14 · Scope: SPEC.md, `reference/`, `vectors/`, AGENTS.md, the docs

## Automated

Run on macOS's Python 3.9 (`/usr/bin/python3`) and on a newer one:

- [x] `python3 -I vectors/run.py`: 6 of 6 cases pass. That's 8 runs, counting each step.
- [x] `python3 -I -m unittest discover -s reference/tests`: 31 tests pass. They cover:
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

## Regressions to watch

- **Log formats change** with client updates. A sudden drop in tokens, or many bad lines, is the sign. Re-check
  SPEC.md §3 against a fresh transcript, keys and types only.
- **New models** show as unpriced until `prices.json` has their row.
