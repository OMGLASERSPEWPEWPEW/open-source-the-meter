"""The two line charts, as inline SVG, and the sentences that lead every surface (SPEC.md §9.1, §11.3).

Drawn from analysis.chart_data(), so the picture is exactly the ledger's numbers. No scripts: hover is each point's
SVG <title>, and the tables under the charts hold every value. The marks follow the dataviz method: 2px lines,
8px points with a 2px surface ring, bands as a 10% wash, hairline gridlines, ink-coloured text, a legend for two or
more series, the categorical slots in fixed order with separate light and dark steps.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from decimal import Decimal

from .util import half_up, local_dt, parse_time_ms

# Light and dark steps of the first three categorical slots (blue, orange, aqua), then the chart chrome.
SERIES = (("#2a78d6", "#3987e5"), ("#eb6834", "#d95926"), ("#1baf7a", "#199e70"))
LIGHT = {"surface": "#fcfcfb", "page": "#f9f9f7", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
         "grid": "#e1e0d9", "axis": "#c3c2b7"}
DARK = {"surface": "#1a1a19", "page": "#0d0d0d", "ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781",
        "grid": "#2c2c2a", "axis": "#383835"}


def _vars(chrome: dict, mode: int) -> str:
    lines = ["--mc-%s:%s;" % (k, v) for k, v in chrome.items()]
    lines += ["--mc-s%d:%s;" % (i + 1, pair[mode]) for i, pair in enumerate(SERIES)]
    return "".join(lines)


def style() -> str:
    """The chart classes, with light values, dark values for a dark system setting, and a data-theme override."""
    return (
        ".mc{color-scheme:light;%s}"
        "@media (prefers-color-scheme:dark){:root:where(:not([data-theme=light])) .mc,.mc:root:not([data-theme=light])"
        "{color-scheme:dark;%s}}"
        ":root[data-theme=dark] .mc,.mc:root[data-theme=dark]{color-scheme:dark;%s}"
        ".mc text{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;fill:var(--mc-ink2)}"
        ".mc .tick{font-size:11px;fill:var(--mc-muted);font-variant-numeric:tabular-nums}"
        ".mc .label{font-size:12px;fill:var(--mc-ink2)}"
        ".mc .head{font-size:17px;font-weight:600;fill:var(--mc-ink)}"
        ".mc .sub{font-size:14px;fill:var(--mc-ink2)}"
        ".mc .title{font-size:13px;font-weight:600;fill:var(--mc-ink)}"
        ".mc .note{font-size:12px;fill:var(--mc-ink2)}"
        ".mc .grid{stroke:var(--mc-grid);stroke-width:1}"
        ".mc .axis{stroke:var(--mc-axis);stroke-width:1}"
        ".mc .bg{fill:var(--mc-surface)}"
        ".mc .line{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}"
        ".mc .daily{fill:none;stroke-width:1.5;stroke-linejoin:round;stroke-linecap:round;opacity:.45}"
        ".mc .ring{stroke:var(--mc-surface);stroke-width:2}"
        ".mc .hit{fill:transparent}"
        + "".join(".mc .s%d{stroke:var(--mc-s%d)}.mc .f%d{fill:var(--mc-s%d)}.mc .w%d{fill:var(--mc-s%d);opacity:.1}"
                  % (i, i, i, i, i, i) for i in (1, 2, 3))
    ) % (_vars(LIGHT, 0), _vars(DARK, 1), _vars(DARK, 1))


# --- numbers and words ---------------------------------------------------------------------------------------------

def _d(text) -> Decimal:
    return Decimal(text) if text is not None else Decimal(0)


def money_words(text) -> str:
    """'76.36' -> '$76'; whole dollars, rounded half away from zero, with thousands commas."""
    return "${:,}".format(int(half_up(_d(text))))


def date_words(iso: str, tz) -> str:
    dt = local_dt(parse_time_ms(iso), tz)
    return "%s %d" % (dt.strftime("%b"), dt.day)


def ceiling_sentence(h: dict, tz) -> str:
    if h.get("ceiling_usd") is None:
        return "No week with enough data yet: a week's worth shows once your plan reads 20% or more used, with its use tied to your account."
    x = money_words(h["ceiling_usd"])
    if h.get("change_pct") is None:
        return "Your plan's week is worth about %s of API use (one week so far)." % x
    since = date_words(h["since"], tz)
    if h["direction"] == "within rounding":
        return "Your plan's week is worth about %s of API use, about the same as on %s (within the plan's rounding)." % (x, since)
    return "Your plan's week is worth about %s of API use, %s %s%% since %s." % (
        x, h["direction"], str(abs(Decimal(h["change_pct"]))), since)


def spend_sentence(h: dict) -> str:
    a = "$" + "{:,.2f}".format(_d(h["last_7d_usd"]))
    if h.get("spend_change_pct") is None:
        return "You used %s in the last 7 days; there's no earlier week to compare yet." % a
    if h["spend_direction"] == "flat":
        return "You used %s in the last 7 days, about the same as the 7 days before." % a
    return "You used %s in the last 7 days, %s %s%% from the 7 days before." % (
        a, h["spend_direction"], str(abs(Decimal(h["spend_change_pct"]))))


# --- scales -----------------------------------------------------------------------------------------------------------

def nice_top(value: Decimal) -> tuple:
    """A clean top for a zero-based axis and its step: 4 or 5 ticks of 1, 2, 2.5 or 5 x 10^k."""
    if value <= 0:
        return Decimal(1), Decimal("0.25")
    exp = value.adjusted()
    for k in (exp - 1, exp):
        for m in (Decimal(1), Decimal(2), Decimal("2.5"), Decimal(5), Decimal(10)):
            step = m * (Decimal(10) ** k)
            n = (value / step).to_integral_value(rounding="ROUND_CEILING")
            if n <= 5:
                return step * max(n, 1), step
    step = Decimal(10) ** (exp + 1)
    return step, step


def money_tick(v: Decimal) -> str:
    if v >= 10000:
        return "$%sK" % format(v / 1000, "f").rstrip("0").rstrip(".")
    if v == v.to_integral_value():
        return "${:,}".format(int(v))
    return "$" + format(v.normalize(), "f")


def _f(x) -> str:
    return ("%.1f" % x).rstrip("0").rstrip(".")


# --- the two charts ---------------------------------------------------------------------------------------------------

def _frame(x0, y0, w, h, top, step, label_x=True) -> list:
    out = []
    v = Decimal(0)
    while v <= top:
        y = y0 + h - float(v / top) * h
        out.append('<line class="%s" x1="%s" y1="%s" x2="%s" y2="%s"/>' % ("axis" if v == 0 else "grid", _f(x0), _f(y), _f(x0 + w), _f(y)))
        out.append('<text class="tick" x="%s" y="%s" text-anchor="end">%s</text>' % (_f(x0 - 6), _f(y + 4), money_tick(v)))
        v += step
    return out


def ceiling_chart(points: list, tz, width: int = 720, height: int = 240) -> str:
    """The implied weekly ceiling, week by week, one line per account (trusted weeks only), the band shaded."""
    if not points:
        return ""
    left, right, top_pad, bottom = 56, 92, 34, 28
    w, h = width - left - right, height - top_pad - bottom
    times = sorted({parse_time_ms(p["resets_at"]) for p in points})
    t0, t1 = times[0], times[-1]
    tops = [max(_d(p["ceiling_usd"]), _d(p["band_high_usd"] or p["ceiling_usd"])) for p in points]
    top, step = nice_top(max(tops) * Decimal("1.05"))

    def x(ms):
        return left + (w / 2 if t1 == t0 else (ms - t0) / (t1 - t0) * w)

    def y(v):
        return top_pad + h - float(min(_d(v), top) / top) * h

    accounts = sorted({p["account"] for p in points})
    out = ['<g role="img" aria-label="The implied weekly ceiling, week by week">']
    out += _frame(left, top_pad, w, h, top, step)
    last_x = None
    for ms in times:   # a date label only where it can't touch the last one
        if last_x is not None and x(ms) - last_x < 48:
            continue
        last_x = x(ms)
        out.append('<text class="tick" x="%s" y="%s" text-anchor="middle">%s</text>'
                   % (_f(x(ms)), _f(top_pad + h + 18), date_words(datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(), tz)))
    ends = []
    if len(accounts) > 1:   # a legend for two or more series: a short line key and the account
        lx = left
        for i, acct in enumerate(accounts):
            out.append('<line class="line s%d" x1="%s" y1="14" x2="%s" y2="14"/>' % (i + 1, _f(lx), _f(lx + 16)))
            out.append('<text class="label" x="%s" y="18">account %s</text>' % (_f(lx + 22), html.escape(acct or "unknown")))
            lx += 22 + 8 * len("account " + (acct or "unknown")) + 18
    for i, acct in enumerate(accounts):
        s = i + 1
        ps = [p for p in points if p["account"] == acct]
        upper = ["%s,%s" % (_f(x(parse_time_ms(p["resets_at"]))), _f(y(p["band_high_usd"] if p["band_high_usd"] else top)))
                 for p in ps]
        lower = ["%s,%s" % (_f(x(parse_time_ms(p["resets_at"]))), _f(y(p["band_low_usd"]))) for p in reversed(ps)]
        if len(ps) > 1:
            out.append('<polygon class="w%d" points="%s"/>' % (s, " ".join(upper + lower)))
            out.append('<polyline class="line s%d" points="%s"/>' % (s, " ".join(
                "%s,%s" % (_f(x(parse_time_ms(p["resets_at"]))), _f(y(p["ceiling_usd"]))) for p in ps)))
        for p in ps:
            cx, cy = x(parse_time_ms(p["resets_at"])), y(p["ceiling_usd"])
            tip = "Week ending %s%s: $%s (band $%s to %s)" % (
                date_words(p["resets_at"], tz), (" · account %s" % acct) if len(accounts) > 1 else "",
                p["ceiling_usd"], p["band_low_usd"], ("$" + p["band_high_usd"]) if p["band_high_usd"] else "no limit")
            fill = 'class="f%d ring"' % s
            out.append('<g><title>%s</title><circle class="hit" cx="%s" cy="%s" r="10"/><circle %s cx="%s" cy="%s" r="4"/></g>'
                       % (html.escape(tip), _f(cx), _f(cy), fill, _f(cx), _f(cy)))
        last = ps[-1]
        ends.append([y(last["ceiling_usd"]) + 4, x(parse_time_ms(last["resets_at"])) + 10, money_words(last["ceiling_usd"])])
    ends.sort()
    for j in range(1, len(ends)):   # end labels at least 14px apart, so two close lines keep readable labels
        ends[j][0] = max(ends[j][0], ends[j - 1][0] + 14)
    for ly, lx, text in ends:
        out.append('<text class="label" x="%s" y="%s">%s</text>' % (_f(lx), _f(ly), html.escape(text)))
    out.append("</g>")
    return "\n".join(out)


def spend_chart(days: list, width: int = 720, height: int = 220) -> str:
    """API-equivalent dollars per day, with the 7-day average as the stronger line."""
    if not days:
        return ""
    left, right, top_pad, bottom = 56, 92, 34, 28
    w, h = width - left - right, height - top_pad - bottom
    top, step = nice_top(max(max(_d(d["usd"]) for d in days), max(_d(d["avg7_usd"]) for d in days)) * Decimal("1.05"))
    n = len(days)

    def x(i):
        return left + (w / 2 if n == 1 else i / (n - 1) * w)

    def y(v):
        return top_pad + h - float(_d(v) / top) * h

    out = ['<g role="img" aria-label="API-equivalent dollars per day, with the 7-day average">']
    out += _frame(left, top_pad, w, h, top, step)
    for i, d in enumerate(days):
        if i % 7 == (n - 1) % 7:
            dt = datetime.strptime(d["day"], "%Y-%m-%d")
            out.append('<text class="tick" x="%s" y="%s" text-anchor="middle">%s %d</text>' % (_f(x(i)), _f(top_pad + h + 18), dt.strftime("%b"), dt.day))
    out.append('<line class="daily s1" x1="%s" y1="14" x2="%s" y2="14"/><text class="label" x="%s" y="18">each day</text>'
               % (_f(left), _f(left + 16), _f(left + 22)))
    out.append('<line class="line s1" x1="%s" y1="14" x2="%s" y2="14"/><text class="label" x="%s" y="18">7-day average</text>'
               % (_f(left + 100), _f(left + 116), _f(left + 122)))
    out.append('<polyline class="daily s1" points="%s"/>' % " ".join("%s,%s" % (_f(x(i)), _f(y(d["usd"]))) for i, d in enumerate(days)))
    out.append('<polyline class="line s1" points="%s"/>' % " ".join("%s,%s" % (_f(x(i)), _f(y(d["avg7_usd"]))) for i, d in enumerate(days)))
    for i, d in enumerate(days):
        dt = datetime.strptime(d["day"], "%Y-%m-%d")
        out.append('<g><title>%s %d: $%s (7-day average $%s)</title><rect class="hit" x="%s" y="%s" width="%s" height="%s"/></g>'
                   % (dt.strftime("%b"), dt.day, d["usd"], d["avg7_usd"], _f(x(i) - w / max(n, 1) / 2), _f(top_pad), _f(w / max(n, 1)), _f(h)))
    lx, ly = x(n - 1), y(days[-1]["avg7_usd"])
    out.append('<circle class="f1 ring" cx="%s" cy="%s" r="4"/>' % (_f(lx), _f(ly)))
    out.append('<text class="label" x="%s" y="%s">$%s a day</text>' % (_f(lx + 10), _f(ly + 4), html.escape(days[-1]["avg7_usd"])))
    out.append("</g>")
    return "\n".join(out)


def wrap(text: str, width: int) -> list:
    """Split a sentence at word boundaries into lines of at most `width` characters (SVG text never wraps)."""
    lines, line = [], ""
    for word in text.split(" "):
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = word if not line else line + " " + word
    return lines + [line]


def standalone_svg(data: dict, tz, caption: str, width: int = 720) -> str:
    """One self-contained SVG: the headline, both charts and a caption. Opens in any browser or Quick Look."""
    head, sub = ceiling_sentence(data["headline"], tz), spend_sentence(data["headline"])
    c1, c2 = ceiling_chart(data["ceiling_points"], tz, width), spend_chart(data["spend_points"], width)
    y = 0
    lines = wrap(head, 76)
    body = ['<rect class="bg" width="100%" height="100%"/>']
    for i, line in enumerate(lines):
        body.append('<text class="head" x="24" y="%d">%s</text>' % (38 + 22 * i, html.escape(line)))
    shift = 22 * (len(lines) - 1)
    body.append('<text class="sub" x="24" y="%d">%s</text>' % (62 + shift, html.escape(sub)))
    y = 84 + shift
    body.append('<text class="title" x="24" y="%d">What the plan gives you: the weekly ceiling in API dollars</text>' % (y + 14))
    if c1:
        body.append('<g transform="translate(0,%d)">%s</g>' % (y + 20, c1))
        y += 20 + 240
        body.append('<text class="note" x="24" y="%d">A lower bound: use of the same plan elsewhere (claude.ai, the desktop app, other machines) isn\'t in these logs.</text>' % (y + 14))
        y += 28
    else:
        empty = ("No week with enough data yet." if data.get("has_readings")
                 else "No plan readings yet: install the statusline wrapper, or record one with record-quota.")
        body.append('<text class="note" x="24" y="%d">%s</text>' % (y + 36, empty))
        y += 52
    body.append('<text class="title" x="24" y="%d">What you use: API-equivalent dollars per day</text>' % (y + 18))
    if c2 and any(d["usd"] != "0.00" for d in data["spend_points"]):
        body.append('<g transform="translate(0,%d)">%s</g>' % (y + 24, c2))
        y += 24 + 220
    else:
        body.append('<text class="note" x="24" y="%d">Nothing read yet.</text>' % (y + 40))
        y += 56
    body.append('<text class="note" x="24" y="%d">%s</text>' % (y + 18, html.escape(caption)))
    height = y + 32
    return ('<svg xmlns="http://www.w3.org/2000/svg" class="mc" width="%d" height="%d" viewBox="0 0 %d %d" role="img" '
            'aria-label="%s"><title>%s</title><style>%s</style>%s</svg>\n'
            % (width, height, width, height, html.escape(head + " " + sub), html.escape(head), style(), "\n".join(body)))
