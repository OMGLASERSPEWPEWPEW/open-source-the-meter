"""The meter's operations, shared by the CLI, the MCP server and the dashboard. Each one syncs first (lazily)."""

from __future__ import annotations

import json
import os
import re
from decimal import Decimal

from . import analysis, ledger, sources
from .prices import Prices
from .util import (append_private, clean_text, day_of, day_start_ms, format_time_ms, half_up, local_dt, meter_home,
                   now_ms, parse_time_ms, parse_tz, usd)

DAY_MS = 86_400_000


class MeterError(Exception):
    """A mistake in what was asked for, said plainly (shown to the person, never a stack trace)."""


class Meter:
    def __init__(self, db_path=None, claude_roots=None, codex_roots=None, quota_file=None, prices_path=None, tz=None,
                 now=None):
        self.home = meter_home()
        self.db_path = db_path or os.path.join(self.home, "meter.db")
        self.claude_roots = claude_roots if claude_roots is not None else sources.claude_roots()
        self.codex_roots = codex_roots if codex_roots is not None else sources.codex_roots()
        self.quota_file = quota_file or os.path.join(self.home, "quota.jsonl")
        self.prices = Prices.load(prices_path)
        self.tz = parse_tz(tz)
        self.fixed_now = now   # ms; for a reproducible picture (the README's image), else the clock
        self._conn = None

    @property
    def conn(self):
        if self._conn is None:
            self._conn = ledger.open_db(self.db_path)
        return self._conn

    def sync(self):
        return ledger.sync(self.conn, self.claude_roots, self.codex_roots, self.quota_file)

    def _events(self, start_ms=None, end_ms=None):
        return analysis.priced_events(ledger.events_between(self.conn, start_ms, end_ms), self.prices)

    # --- what was spent -------------------------------------------------------------------------------------------

    def _bound(self, text, end: bool):
        """An ISO time, or a plain date (the start of that day, or for an end, the end of it) in the meter's zone."""
        if not isinstance(text, str) or len(text) > 40:
            return None
        m = re.fullmatch(r"([0-9]{4})-([0-9]{2})-([0-9]{2})", text)
        if m:
            from datetime import date, timedelta
            try:
                day = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                if end:
                    day += timedelta(days=1)
                return day_start_ms(day.year, day.month, day.day, self.tz) - (1 if end else 0)
            except (ValueError, OverflowError):
                return None
        return parse_time_ms(text)

    def period(self, period="7d", since=None, until=None):
        now = self.now()
        end = self._bound(until, True) if until else now
        if end is None:
            raise MeterError("until must be a date like 2026-11-14 or a time like 2026-11-14T09:00:00Z")
        if since:
            start = self._bound(since, False)
            if start is None:
                raise MeterError("since must be a date like 2026-11-07 or a time like 2026-11-07T09:00:00Z")
            return start, end
        m = re.fullmatch(r"([0-9]{1,4})d", period) if isinstance(period, str) else None
        if period == "today":
            today = local_dt(now, self.tz)
            return day_start_ms(today.year, today.month, today.day, self.tz), end
        if period in ("week", "month"):
            return end - (7 if period == "week" else 30) * DAY_MS, end
        if m:
            return end - int(m.group(1)) * DAY_MS, end
        raise MeterError("period must be today, week, month, or a number of days like 7d")

    def summary(self, period="7d", since=None, until=None, group_by="model") -> dict:
        self.sync()
        start, end = self.period(period, since, until)
        events = self._events(start, end)
        if group_by not in ("model", "day", "project", "source", "account"):
            raise MeterError("group_by must be model, day, project, source or account")
        groups = {}
        for ev in events:
            if group_by == "model":
                key = (ev["source"], ev["model"])
            elif group_by == "day":
                key = (day_of(ev["at"], self.tz),)
            elif group_by == "project":
                key = (ev["project"] or "(unknown)",)
            elif group_by == "account":
                key = (ev["source"], analysis.short_account(ev["account"]) or "(unknown)")
            else:
                key = (ev["source"],)
            g = groups.setdefault(key, {"group": " · ".join(clean_text(k) for k in key), "events": 0, "cost_nano": 0,
                                        "unpriced_events": 0, "input": 0, "cache_read": 0, "cache_write": 0,
                                        "output": 0})
            g["events"] += 1
            g["input"] += ev["input"]
            g["cache_read"] += ev["cache_read"]
            g["cache_write"] += ev["cache_write"] + ev["cache_write_1h"]
            g["output"] += ev["output"]
            if ev["cost_nano"] is None:
                g["unpriced_events"] += 1
            else:
                g["cost_nano"] += ev["cost_nano"]
        rows = sorted(groups.values(), key=lambda g: (g["group"] if group_by == "day" else -g["cost_nano"], g["group"]))
        total = sum(g["cost_nano"] for g in rows)
        prompt = sum(g["input"] + g["cache_read"] + g["cache_write"] for g in rows)
        cache_share = None
        if prompt:
            cache_share = str(half_up(Decimal(sum(g["cache_read"] for g in rows)) * 100 / prompt, 1))
        for g in rows:
            g["usd"] = usd(g["cost_nano"])
        unpriced = sorted({clean_text(ev["model"]) for ev in events if ev["cost_nano"] is None})
        from . import charts
        spend = analysis.spend_trend(self._events(self.now() - 40 * DAY_MS, None), self.now(), self.tz)
        trend = {"sentence": charts.spend_sentence({"last_7d_usd": spend["last_7d_usd"],
                                                    "spend_change_pct": spend["change_pct"],
                                                    "spend_direction": spend["direction"]}),
                 "sparkline": spend["sparkline"], "last_7d_usd": spend["last_7d_usd"],
                 "prior_7d_usd": spend["prior_7d_usd"], "change_pct": spend["change_pct"],
                 "direction": spend["direction"]}
        return {
            "trend": trend,
            "since": format_time_ms(start), "until": format_time_ms(end), "group_by": group_by,
            "total_usd": usd(total), "total_nano": total, "events": len(events),
            "cache_read_share_pct": cache_share, "unpriced_models": unpriced,
            "rows": rows[:25], "rows_omitted": max(0, len(rows) - 25),
            "prices_checked": self.prices.checked,
        }

    # --- the plan's allowance in dollars --------------------------------------------------------------------------

    def ceiling(self, family="claude", window="seven_day", count=4) -> dict:
        self.sync()
        if family not in ("claude", "codex"):
            raise MeterError("family must be claude or codex")
        readings = [r for r in ledger.all_readings(self.conn) if r["family"] == family and r["window"] == window]
        if not readings:
            return {"family": family, "window": window, "windows": [], "note": self._no_readings_note(family)}
        length = analysis.window_seconds(window) or 0
        start = min(r["resets_at"] for r in readings) * 1000 - length * 1000
        keep = max(1, min(int(count), 52))
        rows, per_account = [], {}
        all_rows = analysis.ceilings(self._events(start, None), readings, ledger.several_accounts(self.conn))
        for row in reversed(all_rows):
            n = per_account.get(row["account"], 0)
            if n < keep:
                per_account[row["account"]] = n + 1
                rows.append(row)
        rows.reverse()
        elsewhere = ("claude.ai, the desktop app, other machines" if family == "claude"
                     else "other machines, Codex in the cloud")
        from . import charts
        trends = analysis.ceiling_trends(all_rows)
        who = analysis.short_account(self._current_account(family))
        mine = next((t for t in trends if t["account"] == who), None)   # the account in use now, never another
        headline = {"ceiling_usd": mine["last_ceiling_usd"] if mine else None,
                    "change_pct": mine["change_pct"] if mine else None,
                    "direction": mine["direction"] if mine else None, "since": mine["first_resets_at"] if mine else None}
        name = None
        if mine and family == "claude" and window == "seven_day":
            name = charts.headline_name(who, [{"account": t["account"], "resets_at": t["last_resets_at"]}
                                              for t in trends], self.tz)
        sentence = charts.ceiling_sentence(headline, self.tz, name)
        if family == "codex":
            sentence = sentence.replace("Your plan's week", "Your Codex plan's %s" % ("week" if window == "seven_day" else "window"))
        elif window != "seven_day":
            sentence = sentence.replace("Your plan's week", "Your plan's five-hour window")
        return {"family": family, "window": window,
                "trend": {"sentence": sentence, "sparkline": mine["sparkline"] if mine else "",
                          "account": mine["account"] if mine else who},
                "trends": trends, "windows": rows, "accounts": sorted(per_account),
                "current_account": analysis.short_account(self._current_account(family)),
                "note": "Lower bound if you also use this plan elsewhere (%s)." % elsewhere}

    # --- the picture ---------------------------------------------------------------------------------------------

    def now(self) -> int:
        return self.fixed_now if self.fixed_now is not None else now_ms()

    def chart_data(self) -> dict:
        """What the charts plot, exactly the ledger's numbers (SPEC.md §11.3)."""
        self.sync()
        now = self.now()
        readings = [r for r in ledger.all_readings(self.conn) if r["family"] == "claude" and r["window"] == "seven_day"]
        start = min([r["resets_at"] * 1000 - 8 * DAY_MS for r in readings] + [now - 40 * DAY_MS])
        events = self._events(start, None)
        rows = analysis.ceilings(events, readings, ledger.several_accounts(self.conn))
        data = analysis.chart_data(events, rows, now, self.tz)
        per = {}
        for r in rows:
            per.setdefault(r["account"], []).append(r)
        data["rows"] = [r for acct in sorted(per) for r in per[acct][-6:]]   # the last 6 weeks of each account
        return data

    def chart(self, fmt: str = "svg", out=None, caption=None) -> dict:
        """Write the headline and both charts to one self-contained file (SVG, or HTML with the tables too) in the
        meter's home, and say where it is."""
        from . import charts
        if fmt not in ("svg", "html"):
            raise MeterError("format must be svg or html")
        data = self.chart_data()
        path = os.path.abspath(os.path.expanduser(out)) if out else os.path.join(self.home, "meter-chart.%s" % fmt)
        if (out and out.endswith(("/", os.sep))) or os.path.isdir(path):   # a folder: the file goes inside it
            path = os.path.join(path, "meter-chart.%s" % fmt)
        if os.path.splitext(path)[1].lower() != "." + fmt:
            raise MeterError("--out must end in .%s%s" % (fmt, "" if fmt == "html" else " (or .html with --html)"))
        caption = caption or "The meter, from this machine's local logs · API list prices checked %s · drawn %s" % (
            self.prices.checked, format_time_ms(self.now())[:16].replace("T", " ") + " UTC")
        if fmt == "svg":
            text = charts.standalone_svg(data, self.tz, caption, now_ms=self.now())
        else:
            from . import dashboard
            text = dashboard.page_from(self, data, caption)
        try:
            write_private(path, text)
        except OSError as exc:
            raise MeterError("couldn't write %s (%s)" % (path, exc.strerror or exc.__class__.__name__))
        return {"path": path, "format": fmt,
                "headline": charts.ceiling_sentence(data["headline"], self.tz, charts.headline_name(
                    data["headline"].get("account"), data["ceiling_points"], self.tz)),
                "spend": charts.spend_sentence(data["headline"]),
                "spend_sparkline": data["spend_sparkline"], "ceiling_sparkline": data["ceiling_sparkline"]}

    def _current_account(self, family) -> str:
        """The account of the most recent request, resolved as every report resolves it (SPEC.md §3.7)."""
        source = analysis.FAMILY_SOURCE[family]
        row = self.conn.execute("SELECT %s FROM events WHERE source = ? AND at <= ? ORDER BY at DESC, key DESC LIMIT 1"
                                % ", ".join(ledger.EVENT_COLUMNS), (source, self.now())).fetchone()
        if not row:
            return ""
        return ledger.resolve_events(self.conn, [dict(zip(ledger.EVENT_COLUMNS, row))])[0]["account"]

    def _no_readings_note(self, family):
        if family == "codex":
            return "No Codex plan readings yet: Codex logs them itself once you use it while signed in to ChatGPT."
        return ("No plan readings yet. Install the statusline wrapper (statusline-install) or record one by hand "
                "(record-quota) with the percent and reset time your plan's usage screen shows.")

    def record_quota(self, percent, window="seven_day", resets_at=None, family="claude") -> dict:
        """A reading typed in by hand (SPEC.md §6.5)."""
        if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not 0 < percent <= 100:
            raise MeterError("percent must be a number above 0 and at most 100")
        if family not in ("claude", "codex") or window not in ("seven_day", "five_hour"):
            raise MeterError("family must be claude or codex, and window seven_day or five_hour")
        now = now_ms()
        length_ms = analysis.window_seconds(window) * 1000
        self.sync()
        if resets_at is None:
            later = [r["resets_at"] for r in ledger.all_readings(self.conn)
                     if r["family"] == family and r["window"] == window and now < r["resets_at"] * 1000 <= now + length_ms]
            if not later:
                raise MeterError("resets_at is needed: when does this window reset? (an ISO time, e.g. "
                                 "2026-11-20T19:00:00-06:00)")
            resets = max(later)
        elif type(resets_at) is int:
            resets = resets_at
        elif isinstance(resets_at, str) and re.fullmatch(r"[0-9]{1,12}", resets_at):
            resets = int(resets_at)
        else:
            ms = parse_time_ms(resets_at) if isinstance(resets_at, str) else None
            if ms is None:
                raise MeterError("resets_at must be an ISO time like 2026-11-20T19:00:00-06:00, or Unix seconds")
            resets = ms // 1000
        if resets * 1000 <= now:
            raise MeterError("resets_at is in the past; give the next reset time")
        if resets * 1000 > now + length_ms:
            raise MeterError("resets_at is more than one window away; give the next reset time")
        line = {"at": format_time_ms(now), "source": "manual", "family": family, "window": window,
                "percent": percent, "resets_at": resets, "account": self._current_account(family)}
        append_private(self.quota_file, json.dumps(line, sort_keys=True))
        result = self.ceiling(family, window, 2)
        result["recorded"] = analysis.reading_out(dict(line, at=now))
        result["note_account"] = ("Recorded for the account your latest request used (%s). If you use several "
                                  "accounts, record each one's percent while using it." % result["recorded"]["account"])
        return result

    # --- Level 2 ----------------------------------------------------------------------------------------------------

    def git_yield(self, repo: str, period="30d", since=None, until=None) -> dict:
        """Level 2. Runs one read-only git log in a repo this user owns, with a clean environment."""
        import shutil
        import subprocess
        real = os.path.realpath(os.path.expanduser(repo or ""))
        if not os.path.isdir(real) or not os.path.exists(os.path.join(real, ".git")):
            raise MeterError("repo must be the top folder of a git repository on this machine")
        if hasattr(os, "getuid") and os.stat(real).st_uid != os.getuid():
            raise MeterError("that repository belongs to another user; the meter only reads your own")
        git = shutil.which("git")
        if not git:
            raise MeterError("git isn't installed")
        self.sync()
        prefix = real.rstrip("/") + "/"
        ran_here = self.conn.execute(
            "SELECT 1 FROM events WHERE project = ? OR substr(project, 1, ?) = ? LIMIT 1",
            (real, len(prefix), prefix)).fetchone()
        if not ran_here:
            raise MeterError("no Claude Code or Codex session ran in that folder, so there's no spend to measure it by")
        start, end = self.period(period, since, until)
        env = {
            "PATH": os.pathsep.join(["/usr/bin", "/bin", "/usr/sbin", "/sbin", os.path.dirname(git)]),
            "HOME": os.path.expanduser("~"), "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_LAZY_FETCH": "1", "GIT_CEILING_DIRECTORIES": os.path.dirname(real), "GIT_PAGER": "cat",
            "PAGER": "cat",
        }
        try:
            done = subprocess.run(analysis.git_log_command(git, real, start, end), stdin=subprocess.DEVNULL,
                                  capture_output=True, env=env, timeout=60, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MeterError("git log didn't finish: %s" % exc.__class__.__name__)
        if done.returncode != 0:
            raise MeterError("git refused: %s" % clean_text(done.stderr.decode("utf-8", "replace"), 200))
        if len(done.stdout) > 64 * 1024 * 1024:
            raise MeterError("the log is too large; use a shorter period")
        commits = analysis.parse_git_log(done.stdout.decode("utf-8", "replace"))
        return analysis.git_yield(commits, self._events(start, end), real, start, end)

    # --- what the meter sees ----------------------------------------------------------------------------------------

    def status(self) -> dict:
        stats = self.sync()
        conn = self.conn
        counts = dict(conn.execute("SELECT source, COUNT(*) FROM events GROUP BY source").fetchall())
        tally = {}
        for r in ledger.all_readings(conn):   # distinct readings, as sync counts them (SPEC.md §6.6)
            tally[(r["family"], r["source"])] = tally.get((r["family"], r["source"]), 0) + 1
        readings = [(f, s, n) for (f, s), n in sorted(tally.items())]
        models = [m for (m,) in conn.execute("SELECT DISTINCT model FROM events").fetchall()]
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())

        def root_info(root):
            files = sources.jsonl_files(root)
            return {"path": root, "exists": os.path.isdir(root), "files": len(files)}

        return {
            "home": self.home, "db": self.db_path, "quota_file": self.quota_file,
            "claude_roots": [root_info(r) for r in self.claude_roots],
            "codex_roots": [root_info(r) for r in self.codex_roots],
            "events": counts, "readings": [{"family": f, "source": s, "count": n} for f, s, n in readings],
            "unpriced_models": sorted(clean_text(m) for m in models if self.prices.lookup(m) is None),
            "bad_lines": int(meta.get("bad_lines", "0")),
            "last_sync": format_time_ms(int(meta["last_sync"])) if meta.get("last_sync") else None,
            "this_sync": {"files_read": stats.files, "new_events": stats.new_events, "new_readings": stats.new_readings},
            "prices_checked": self.prices.checked,
        }


def write_private(path: str, text: str) -> None:
    """Write a whole file only this user can read (0600), atomically: a temp file in the same folder, then rename."""
    import tempfile
    folder = os.path.dirname(os.path.abspath(path))
    if os.path.abspath(folder) == os.path.abspath(meter_home()):
        from .util import private_dir
        private_dir(folder)
    else:
        os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".meter-", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
