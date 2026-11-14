"""The command line (SPEC.md §11.2). `python3 reference/meter.py --help` lists every command."""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import VERSION, analysis, ledger
from .app import Meter, MeterError
from .prices import Prices
from .util import parse_time_ms, parse_tz

METER_PY = os.path.realpath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "meter.py"))


def _table(rows, cols) -> str:
    widths = [max([len(c[0])] + [len("–" if r.get(c[1]) is None else str(r.get(c[1]))) for r in rows]) for c in cols]
    out = ["  ".join(c[0].ljust(w) for c, w in zip(cols, widths))]
    for r in rows:
        cells = []
        for (title, key, *align), w in zip(cols, widths):
            v = r.get(key)
            text = "–" if v is None else str(v)
            cells.append(text.rjust(w) if align else text.ljust(w))
        out.append("  ".join(cells))
    return "\n".join(out)


def _print(obj, as_json: bool, text: str) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False) if as_json else text)


def cmd_summary(m: Meter, a) -> None:
    s = m.summary(a.period, a.since, a.until, a.by)
    text = "%s to %s · $%s at API list prices (checked %s) · %d requests" % (
        s["since"][:16], s["until"][:16], s["total_usd"], s["prices_checked"], s["events"])
    if s["cache_read_share_pct"]:
        text += " · %s%% of prompt tokens were cache reads" % s["cache_read_share_pct"]
    text += "\n\n" + _table(s["rows"], [(a.by.title(), "group"), ("Requests", "events", ">"), ("Dollars", "usd", ">")])
    if s["unpriced_models"]:
        text += "\n\nNo price for: %s (their tokens are counted, their dollars aren't)." % ", ".join(s["unpriced_models"])
    _print(s, a.json, text)


def cmd_ceiling(m: Meter, a) -> None:
    c = m.ceiling(a.family, a.window, a.count)
    if not c["windows"]:
        return _print(c, a.json, c["note"])
    rows = []
    for w in c["windows"]:
        note = w["change_note"] or ("within rounding" if w["change_within_band"] else None)
        rows.append(dict(w, used="%s%%%s" % (w["percent"], "" if w["firm"] else " early"),
                         band="%s – %s" % (w["band_low_usd"], w["band_high_usd"] or "∞"),
                         change=(w["change_pct"] + "%") if w["change_pct"] else None, ends=w["resets_at"][:16],
                         who=w["account"] or "(unknown)", note=note))
    cols = [("Account", "who"), ("Window ends", "ends"), ("Used", "used", ">"), ("Spent $", "spend_usd", ">"),
            ("Ceiling $", "ceiling_usd", ">"), ("Band $", "band", ">")]
    if any(w["unattributed_usd"] != "0.00" for w in c["windows"]):
        cols.append(("Unattributed $", "unattributed_usd", ">"))
    text = _table(rows, cols + [("Change", "change", ">"), ("Note", "note")])
    if c["note"]:
        text += "\n\n" + c["note"]
    _print(c, a.json, text)


def cmd_record(m: Meter, a) -> None:
    r = m.record_quota(a.percent, a.window, a.resets_at, a.family)
    _print(r, a.json, "Recorded %s%% for %s %s (resets %s)." % (a.percent, a.family, a.window, r["recorded"]["resets_at"]))


def cmd_status(m: Meter, a) -> None:
    s = m.status()
    lines = ["Ledger: %s" % s["db"], "Plan readings file: %s" % s["quota_file"]]
    for r in s["claude_roots"] + s["codex_roots"]:
        lines.append("Logs: %s (%s)" % (r["path"], "%d files" % r["files"] if r["exists"] else "not here"))
    lines.append("Events: %s" % (", ".join("%s %d" % kv for kv in sorted(s["events"].items())) or "none yet"))
    lines.append("Plan readings: %s" % (", ".join("%s/%s %d" % (r["family"], r["source"], r["count"]) for r in s["readings"]) or "none yet"))
    if s["unpriced_models"]:
        lines.append("No price for: %s" % ", ".join(s["unpriced_models"]))
    lines.append("Lines that weren't valid JSON (skipped): %d" % s["bad_lines"])
    lines.append("Prices checked: %s" % s["prices_checked"])
    _print(s, a.json, "\n".join(lines))


def cmd_yield(m: Meter, a) -> None:
    y = m.git_yield(a.repo, a.period, a.since, a.until)
    text = ("%s, %s to %s: %d commits, +%d/-%d lines (%d lockfile or generated lines left out, %d reverted pairs), "
            "$%s spent in sessions inside it → %s commits and %s lines added per dollar."
            % (y["repo"], y["since"][:10], y["until"][:10], y["commits"], y["lines_added"], y["lines_removed"],
               y["generated_lines_skipped"], y["reverted_pairs"], y["spend_usd"], y["commits_per_dollar"] or "–",
               y["lines_added_per_dollar"] or "–"))
    _print(y, a.json, text)


def cmd_conformance(a) -> None:
    claude = [a.claude_dir] if a.claude_dir else []
    codex = [os.path.join(a.codex_dir, "sessions"), os.path.join(a.codex_dir, "archived_sessions")] if a.codex_dir else []
    conn = ledger.open_db(a.db)
    ledger.sync(conn, claude, codex, a.quota_file)
    prices = Prices.load(a.prices)
    events = analysis.priced_events(ledger.all_events(conn), prices)
    yield_case = None
    if a.git_log:
        with open(a.git_log, encoding="utf-8") as fh:
            commits = analysis.parse_git_log(fh.read())
        yield_case = {"commits": commits, "repo": a.repo, "since_ms": parse_time_ms(a.since),
                      "until_ms": parse_time_ms(a.until)}
    doc = analysis.conformance(events, ledger.all_readings(conn), parse_tz(a.tz), yield_case,
                               ledger.several_accounts(conn))
    print(json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="meter.py", description="The meter: API-equivalent cost of your Claude Code "
                                "and Codex use, from local logs only.")
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, json_flag=True):
        sp.add_argument("--db", help="ledger file (default ~/.open-meter/meter.db)")
        sp.add_argument("--prices", help="price list (default prices.json in the repo)")
        sp.add_argument("--tz", default="local", help="'local', 'UTC' or an offset like -06:00, for days")
        if json_flag:
            sp.add_argument("--json", action="store_true", help="print JSON")
        return sp

    s = common(sub.add_parser("summary", help="what your use would have cost at API prices"))
    s.add_argument("--period", default="7d", help="today, week, month, or Nd (default 7d)")
    s.add_argument("--since")
    s.add_argument("--until")
    s.add_argument("--by", default="model", choices=["model", "day", "project", "source", "account"])
    s = common(sub.add_parser("ceiling", help="the plan's window in API dollars, window by window"))
    s.add_argument("--family", default="claude", choices=["claude", "codex"])
    s.add_argument("--window", default="seven_day", choices=["seven_day", "five_hour"])
    s.add_argument("--count", type=int, default=4)
    s = common(sub.add_parser("record-quota", help="record the plan's used percent by hand"))
    s.add_argument("percent", type=float)
    s.add_argument("--window", default="seven_day", choices=["seven_day", "five_hour"])
    s.add_argument("--resets-at", help="when the window resets: 2026-11-20T19:00:00-06:00, or Unix seconds")
    s.add_argument("--family", default="claude", choices=["claude", "codex"])
    common(sub.add_parser("status", help="what the meter reads and where"))
    common(sub.add_parser("sync", help="read new log lines into the ledger now"))
    s = common(sub.add_parser("yield", help="Level 2: commits and lines per API dollar for a repo"))
    s.add_argument("--repo", required=True)
    s.add_argument("--period", default="30d")
    s.add_argument("--since")
    s.add_argument("--until")
    common(sub.add_parser("mcp", help="run the MCP server on stdin/stdout"), json_flag=False)
    s = common(sub.add_parser("dashboard", help="serve the dashboard on 127.0.0.1 until Ctrl-C"), json_flag=False)
    s.add_argument("--port", type=int, default=8787)
    s = sub.add_parser("statusline", help="(run by Claude Code) save plan readings, then run --then")
    s.add_argument("--then", help="the statusline command you had before, run unchanged")
    for name, what in (("statusline-install", "wrap Claude Code's statusline (backs up settings.json)"),
                       ("statusline-uninstall", "put Claude Code's statusline back (backs up settings.json)")):
        s = sub.add_parser(name, help=what)
        s.add_argument("--apply", action="store_true", help="make the change (without it, only show it)")
        s.add_argument("--settings", help="settings.json to edit (default: Claude Code's user settings)")
    for name, what in (("mcp-install", "add the MCP server to Cursor or Claude Desktop (backs up the config)"),
                       ("mcp-uninstall", "take the MCP server out of Cursor or Claude Desktop (backs up the config)")):
        s = sub.add_parser(name, help=what)
        s.add_argument("--client", required=True, choices=["cursor", "claude-desktop"])
        s.add_argument("--apply", action="store_true", help="make the change (without it, only show it)")
        s.add_argument("--config", help="the client's config file (default: its usual place)")
    s = sub.add_parser("conformance", help="print the conformance document for the test vectors")
    s.add_argument("--claude-dir")
    s.add_argument("--codex-dir")
    s.add_argument("--quota-file")
    s.add_argument("--db", required=True)
    s.add_argument("--prices")
    s.add_argument("--tz", default="UTC")
    s.add_argument("--git-log")
    s.add_argument("--repo")
    s.add_argument("--since")
    s.add_argument("--until")
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        if a.cmd == "statusline":
            from . import statusline
            return statusline.run(a.then)
        if a.cmd in ("statusline-install", "statusline-uninstall"):
            from . import statusline
            if a.cmd == "statusline-install":
                plan = statusline.apply_install(METER_PY, a.settings) if a.apply else statusline.plan_install(METER_PY, a.settings)
            else:
                plan = statusline.apply_uninstall(a.settings, dry_run=not a.apply)
            print(json.dumps(plan, indent=2, ensure_ascii=False))
            if not a.apply:
                print("\n(Nothing changed. Run again with --apply to make this change; settings.json is backed up first.)")
            return 0
        if a.cmd in ("mcp-install", "mcp-uninstall"):
            from . import mcpconfig
            if a.cmd == "mcp-install":
                plan = (mcpconfig.apply_install(a.client, METER_PY, a.config) if a.apply
                        else mcpconfig.plan_install(a.client, METER_PY, a.config))
            else:
                plan = mcpconfig.apply_uninstall(a.client, a.config, dry_run=not a.apply)
            print(json.dumps(plan, indent=2, ensure_ascii=False))
            if not a.apply:
                print("\n(Nothing changed. Run again with --apply to make this change; the file is backed up first.)")
            return 0
        if a.cmd == "conformance":
            cmd_conformance(a)
            return 0
        m = Meter(db_path=a.db, prices_path=a.prices, tz=a.tz)
        if a.cmd == "mcp":
            from . import mcp
            mcp.serve(m)
            return 0
        if a.cmd == "dashboard":
            from . import dashboard
            dashboard.serve(m, a.port)
            return 0
        if a.cmd == "sync":
            st = m.sync()
            print("Read %d files: %d new events, %d duplicates skipped, %d plan readings, %d bad lines."
                  % (st.files, st.new_events, st.duplicates, st.new_readings, st.bad_lines))
            return 0
        {"summary": cmd_summary, "ceiling": cmd_ceiling, "record-quota": cmd_record, "status": cmd_status,
         "yield": cmd_yield}[a.cmd](m, a)
        return 0
    except (MeterError, ValueError, ledger.LedgerError) as exc:
        print("meter: %s" % exc, file=sys.stderr)
        return 2
    except Exception as exc:
        from .statusline import InstallError
        if isinstance(exc, InstallError):
            print("meter: %s" % exc, file=sys.stderr)
            return 2
        raise
