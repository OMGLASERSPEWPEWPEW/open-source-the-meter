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


DETAIL_CSS = """.detail{max-width:900px;margin:0 auto 40px}.detail h2{text-align:left;margin:28px 0 6px}
.muted{color:var(--p-lede);font-size:13px;margin:4px 0 0}svg.mc{width:100%;height:auto;display:block}
table{border-collapse:collapse;width:100%;font-size:14px}td,th{border-bottom:1px solid var(--p-edge);padding:6px 4px;text-align:left}
.num{text-align:right;font-variant-numeric:tabular-nums}.wrap{overflow-x:auto}
"""


def detail(meter: Meter, data: dict) -> str:
    """Below the chart page, on the dashboard only: what you use per day, spend by model, and every ceiling row with
    its note and unattributed spend."""
    sub = charts.spend_sentence(data["headline"])
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
           _cell(((w["change_pct"] + "%" + (" (within rounding)" if w["change_within_band"] else ""))
                  if w["change_pct"] else (w["change_note"] or "–"))
                 + (" · the plan reset early; use after the reset isn't counted" if w.get("reset_at") else "")))
        for w in reversed(data["rows"]))
    chart2 = ('<svg class=mc viewBox="0 0 720 220" role=img aria-label="%s">%s</svg>' % (html.escape(sub), c2)
              if any(d["usd"] != "0.00" for d in data["spend_points"]) else '<p class=muted>Nothing read yet.</p>')
    return ("""<div class="detail mc">
<h2>What you use: API-equivalent dollars per day</h2><p class=muted>%s</p>%s
<h2>Last 7 days by model: $%s</h2><div class=wrap><table><tr><th>Model</th><th class=num>Requests</th><th class=num>Dollars</th></tr>%s</table></div>
<h2>The weekly ceilings in detail</h2><div class=wrap><table><tr><th>Account</th><th>Week ending</th><th class=num>Used</th><th class=num>Spent</th><th class=num>Ceiling</th><th class=num>Band</th><th class=num>Unattributed</th><th>Change</th></tr>%s</table></div>
</div>
""" % (html.escape(sub), chart2, html.escape(models["total_usd"]), rows,
       wins or "<tr><td colspan=8 class=muted>No plan readings yet.</td></tr>"))


def page(meter: Meter) -> str:
    """The dashboard: the chart page (SPEC.md §11.3), then the spend chart and the tables. No scripts; nothing from
    elsewhere; every value escaped."""
    data = meter.chart_data()
    return charts.page_html(data, meter.tz, meter.now(), meter.prices.checked, extra_html=detail(meter, data),
                            extra_css=charts.style() + DETAIL_CSS)


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
