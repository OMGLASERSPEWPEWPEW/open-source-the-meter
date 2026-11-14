"""The optional dashboard: one page on 127.0.0.1, read-only, no scripts, nothing fetched from outside
(SPEC.md §11.3). It runs in the foreground until Ctrl-C; nothing starts it at login.
"""

from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import charts
from .app import Meter, MeterError

CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"


def _cell(v) -> str:
    return html.escape("–" if v is None else str(v))


def page_from(meter: Meter, data: dict, caption: str) -> str:
    """The page, picture first (SPEC.md §11.3): the headline, the two charts, then the tables for detail. The same
    page is the live dashboard and the file `chart --html` writes. No scripts; nothing from elsewhere."""
    head = charts.ceiling_sentence(data["headline"], meter.tz)
    sub = charts.spend_sentence(data["headline"])
    c1 = charts.ceiling_chart(data["ceiling_points"], meter.tz)
    c2 = charts.spend_chart(data["spend_points"])
    models = meter.summary("7d", group_by="model")
    rows = "".join(
        "<tr><td>%s</td><td class=num>%d</td><td class=num>$%s</td></tr>"
        % (html.escape(r["group"]), r["events"], html.escape(r["usd"] or "0.00")) for r in models["rows"])
    wins = "".join(
        "<tr><td>%s</td><td>%s</td><td class=num>%s%%%s</td><td class=num>$%s</td><td class=num>%s</td>"
        "<td class=num>%s</td><td class=num>$%s</td><td>%s</td></tr>"
        % (_cell(w["account"] or "(unknown)"), _cell(w["resets_at"][:10]), _cell(w["percent"]),
           "" if w["firm"] else " (early)", _cell(w["spend_usd"]),
           ("$" + _cell(w["ceiling_usd"])) if w["shown"] else "not enough data",
           ("$%s – %s" % (_cell(w["band_low_usd"]), _cell(("$" + w["band_high_usd"]) if w["band_high_usd"] else "no limit")))
           if w["shown"] else "",
           _cell(w["unattributed_usd"]),
           _cell((w["change_pct"] + "%" + (" (within rounding)" if w["change_within_band"] else ""))
                 if w["change_pct"] else (w["change_note"] or "–")))
        for w in reversed(data["rows"]))
    chart1 = ('<svg class=mc viewBox="0 0 720 240" role=img aria-label="%s">%s</svg>'
              '<p class=muted>A lower bound: use of the same plan elsewhere (claude.ai, the desktop app, other '
              'machines) isn\'t in these logs.</p>' % (html.escape(head), c1)) if c1 else (
              '<p class=muted>No week with enough data yet.</p>' if data.get("has_readings") else
              '<p class=muted>No plan readings yet: install the statusline wrapper (statusline-install), or record '
              'one with record-quota.</p>')
    chart2 = ('<svg class=mc viewBox="0 0 720 220" role=img aria-label="%s">%s</svg>' % (html.escape(sub), c2)
              if any(d["usd"] != "0.00" for d in data["spend_points"]) else '<p class=muted>Nothing read yet.</p>')
    return """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1"><title>The meter</title>
<style>
:root{--bg:#f9f9f7;--fg:#0b0b0b;--muted:#52514e;--line:#e1e0d9}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme=light])){--bg:#0d0d0d;--fg:#f0efec;--muted:#c3c2b7;--line:#2c2c2a}}
:root[data-theme=dark]{--bg:#0d0d0d;--fg:#f0efec;--muted:#c3c2b7;--line:#2c2c2a}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;margin:0 auto;max-width:760px;padding:24px 16px}
h1{font-size:22px;line-height:1.3;margin:0 0 6px;font-weight:600}.lead{font-size:16px;margin:0 0 20px;color:var(--muted)}
h2{font-size:15px;margin:28px 0 6px}.muted{color:var(--muted);font-size:13px;margin:4px 0 0}
svg.mc{width:100%%;height:auto;display:block}
table{border-collapse:collapse;width:100%%;font-size:14px}td,th{border-bottom:1px solid var(--line);padding:6px 4px;text-align:left}
.num{text-align:right;font-variant-numeric:tabular-nums}.wrap{overflow-x:auto}
%s
</style></head><body>
<h1>%s</h1>
<p class=lead>%s</p>
<h2>What the plan gives you: the weekly ceiling in API dollars</h2>%s
<h2>What you use: API-equivalent dollars per day</h2>%s
<h2>Last 7 days by model: $%s</h2><div class=wrap><table><tr><th>Model</th><th class=num>Requests</th><th class=num>Dollars</th></tr>%s</table></div>
<h2>The weekly ceilings in detail</h2><div class=wrap><table><tr><th>Account</th><th>Week ending</th><th class=num>Used</th><th class=num>Spent</th><th class=num>Ceiling</th><th class=num>Band</th><th class=num>Unattributed</th><th>Change</th></tr>%s</table></div>
<p class=muted>%s</p>
</body></html>""" % (
        charts.style(), html.escape(head), html.escape(sub), chart1, chart2, html.escape(models["total_usd"]), rows,
        wins or "<tr><td colspan=8 class=muted>No plan readings yet.</td></tr>", html.escape(caption))


def page(meter: Meter) -> str:
    return page_from(meter, meter.chart_data(),
                     "The meter, from this machine's local logs · API list prices checked %s" % meter.prices.checked)


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
