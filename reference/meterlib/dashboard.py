"""The optional dashboard: one page on 127.0.0.1, read-only, no scripts, nothing fetched from outside
(SPEC.md §11.3). It runs in the foreground until Ctrl-C; nothing starts it at login.
"""

from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from .app import Meter, MeterError

CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"


def _bars(days) -> str:
    """Daily spend as an inline SVG bar chart."""
    if not days:
        return "<p class=muted>No events in this period.</p>"
    peak = max(d["cost_nano"] for d in days) or 1
    w, h, gap = 640, 160, 2
    bw = max(2, (w - gap * len(days)) // len(days))
    bars = []
    for i, d in enumerate(days):
        bh = int(d["cost_nano"] * (h - 20) / peak)
        x = i * (bw + gap)
        bars.append('<rect x="%d" y="%d" width="%d" height="%d"><title>%s · $%s</title></rect>'
                    % (x, h - bh, bw, bh, html.escape(d["group"]), html.escape(d["usd"] or "0.00")))
    return ('<svg viewBox="0 0 %d %d" role="img" aria-label="API-equivalent dollars per day" class=chart>%s</svg>'
            % (w, h, "".join(bars)))


def page(meter: Meter) -> str:
    days = meter.summary("30d", group_by="day")
    models = meter.summary("7d", group_by="model")
    ceiling = meter.ceiling("claude", "seven_day", 6)
    rows = "".join(
        "<tr><td>%s</td><td class=num>%d</td><td class=num>$%s</td></tr>"
        % (html.escape(r["group"]), r["events"], html.escape(r["usd"] or "0.00")) for r in models["rows"])
    def note(w):
        if w["change_pct"]:
            return w["change_pct"] + "%" + (" (within rounding)" if w["change_within_band"] else "")
        return w["change_note"] or "–"
    wins = "".join(
        "<tr><td>%s</td><td>%s</td><td class=num>%s%%%s</td><td class=num>$%s</td><td class=num>$%s</td>"
        "<td class=num>$%s – %s</td><td class=num>$%s</td><td>%s</td></tr>"
        % (html.escape(w["account"] or "(unknown)"), html.escape(w["resets_at"][:10]), html.escape(str(w["percent"])),
           "" if w["firm"] else " (early)", html.escape(w["spend_usd"]), html.escape(w["ceiling_usd"] or "–"),
           html.escape(w["band_low_usd"] or "–"), html.escape(w["band_high_usd"] or "∞"),
           html.escape(w["unattributed_usd"]), html.escape(note(w)))
        for w in reversed(ceiling["windows"]))
    return """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1"><title>The meter</title>
<style>
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a65;--bar:#2f6f5e;--line:#e4e1d8}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--fg:#ecebe6;--muted:#a3a29b;--bar:#6fbfa6;--line:#2c2b28}}
body{background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,system-ui,sans-serif;margin:0 auto;max-width:720px;padding:24px 16px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px}.muted{color:var(--muted)}
table{border-collapse:collapse;width:100%%}td,th{border-bottom:1px solid var(--line);padding:6px 4px;text-align:left}
.num{text-align:right;font-variant-numeric:tabular-nums}.chart{width:100%%;height:auto}.chart rect{fill:var(--bar)}
.wrap{overflow-x:auto}
</style></head><body>
<h1>The meter</h1>
<p class=muted>API-equivalent dollars from this machine's Claude Code and Codex logs, at list prices checked %s. Nothing here leaves this machine.</p>
<h2>Last 30 days: $%s</h2>%s
<h2>Last 7 days by model: $%s</h2><div class=wrap><table><tr><th>Model</th><th class=num>Requests</th><th class=num>Dollars</th></tr>%s</table></div>
<h2>Claude's weekly ceiling</h2>
<p class=muted>Spend in the week divided by the plan's used percent. %s</p>
<div class=wrap><table><tr><th>Account</th><th>Week ending</th><th class=num>Used</th><th class=num>Spent</th><th class=num>Ceiling</th><th class=num>Band</th><th class=num>Unattributed</th><th>Change</th></tr>%s</table></div>
</body></html>""" % (
        html.escape(models["prices_checked"] or "?"), html.escape(days["total_usd"]), _bars(days["rows"]),
        html.escape(models["total_usd"]), rows,
        html.escape(ceiling["note"]), wins or "<tr><td colspan=8 class=muted>No plan readings yet.</td></tr>")


def make_handler(meter: Meter, port: int):
    allowed = {"127.0.0.1:%d" % port, "localhost:%d" % port}

    class Handler(BaseHTTPRequestHandler):
        server_version = "open-meter"
        sys_version = ""
        timeout = 10

        def end_headers(self):
            # On every response, error pages included.
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def _send(self, code, body: str, ctype: str):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.headers.get("Host") not in allowed:  # DNS rebinding: only our own address may ask
                return self._send(421, "wrong host\n", "text/plain; charset=utf-8")
            if self.headers.get("Sec-Fetch-Site", "none") not in ("none", "same-origin"):
                return self._send(403, "not from another site\n", "text/plain; charset=utf-8")
            try:
                if self.path == "/":
                    return self._send(200, page(meter), "text/html; charset=utf-8")
                if self.path == "/summary.json":
                    return self._send(200, json.dumps(meter.summary("30d", group_by="day")), "application/json")
            except MeterError as exc:
                return self._send(400, str(exc) + "\n", "text/plain; charset=utf-8")
            return self._send(404, "not found\n", "text/plain; charset=utf-8")

        def do_POST(self):
            self._send(405, "read-only\n", "text/plain; charset=utf-8")

        do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = do_TRACE = do_CONNECT = do_POST

        def log_message(self, fmt, *args):
            pass

    return Handler


def serve(meter: Meter, port: int = 8787) -> None:
    try:
        server = HTTPServer(("127.0.0.1", port), make_handler(meter, port))  # one thread: one SQLite connection
    except OSError:
        raise MeterError("port %d is taken on 127.0.0.1; try --port with another number" % port)
    print("The meter: http://127.0.0.1:%d/ (Ctrl-C stops it)" % port, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
