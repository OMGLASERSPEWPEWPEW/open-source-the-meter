"""The chart page and the sentences that lead the CLI and MCP results (SPEC.md §9.1, §11.3).

The page is the design the user picked: per account, a card for each week (used, tokens, API-equivalent dollars,
each "of" what the week implies) beside one line chart of the weekly limit, week over week, from $0. It is drawn
from analysis.chart_sections(), so every number is a ceiling row's. The same page comes out as HTML or as one SVG.
No scripts: hover is each point's SVG <title>. Dark, as picked, and light under a light system setting.
The dashboard adds the spend chart (2px lines, the categorical slots, light and dark steps) and the tables.
"""

from __future__ import annotations

import html
from datetime import datetime
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
        ".mc .open{fill:var(--mc-surface);stroke-width:2}"
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
    return "%s\u00a0%d" % (dt.strftime("%b"), dt.day)   # a no-break space: a wrap never splits a date


def headline_name(account: str, points: list, tz):
    """The headline account's plain name ("the account that resets Saturday") when more than one account is in view;
    None otherwise, or when the headline's account has no week in view."""
    if not account or len({p["account"] for p in points}) < 2:
        return None
    return account_names(points, tz).get(account)


def ceiling_sentence(h: dict, tz, name=None) -> str:
    if h.get("ceiling_usd") is None:
        if h.get("others_plotted"):
            return "No week with enough data yet for the account in use now (account %s); the chart shows your other accounts." % (h.get("account") or "unknown")
        return "No week with enough data yet: a week's worth shows once your plan reads 20% or more used, with its use tied to your account."
    x = money_words(h["ceiling_usd"])
    lead = "On %s, your plan's week" % name if name else "Your plan's week"   # which line, when there are several
    if h.get("change_pct") is None:
        return "%s is worth about %s of API use (one week so far)." % (lead, x)
    since = date_words(h["since"], tz)
    if h["direction"] == "within rounding":
        return "%s is worth about %s of API use, about the same as on %s (within the plan's rounding)." % (lead, x, since)
    return "%s is worth about %s of API use, %s %s%% since %s." % (
        lead, x, h["direction"], str(abs(Decimal(h["change_pct"]))), since)


def spend_sentence(h: dict) -> str:
    a = "$" + "{:,.2f}".format(_d(h["last_7d_usd"]))
    if h.get("spend_change_pct") is None:
        return "You used %s in the last 7 days; there's no earlier week to compare yet." % a
    if h["spend_direction"] == "flat":
        return "You used %s in the last 7 days, about the same as the 7 days before." % a
    return "You used %s in the last 7 days, %s %s%% from the 7 days before." % (
        a, h["spend_direction"], str(abs(Decimal(h["spend_change_pct"]))))


# --- scales -----------------------------------------------------------------------------------------------------------

def nice_top(value: Decimal, max_steps: int = 5) -> tuple:
    """A clean top for a zero-based axis and its step: at most max_steps steps of 1, 2, 2.5 or 5 x 10^k."""
    if value <= 0:
        return Decimal(1), Decimal("0.25")
    exp = value.adjusted()
    for k in (exp - 1, exp):
        for m in (Decimal(1), Decimal(2), Decimal("2.5"), Decimal(5), Decimal(10)):
            step = m * (Decimal(10) ** k)
            n = (value / step).to_integral_value(rounding="ROUND_CEILING")
            if n <= max_steps:
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


def account_names(points: list, tz) -> dict:
    """Plain names for plotted accounts: "the account that resets Monday", from the weekday its latest week ends on
    in the reader's time zone; "account <id>" when two accounts share that weekday."""
    last = {}
    for p in points:
        at = parse_time_ms(p["resets_at"])
        last[p["account"]] = max(at, last.get(p["account"], at))
    days = {a: local_dt(at, tz).strftime("%A") for a, at in last.items()}
    return {a: ("the account that resets %s" % d if a and list(days.values()).count(d) == 1
                else "account %s" % (a or "unknown")) for a, d in days.items()}


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


# --- the chart page (SPEC.md §11.3) ---------------------------------------------------------------------------------

TITLE = "Claude Code \u2014 weekly usage limit (API-equivalent)"
LEDE = ("How many dollars of pay-as-you-go work each account's week allowed, from this machine's own logs.",
        "A falling line means the plan is giving you less; a rising line, more. Each account is its own plan and its "
        "own line.")
CHART_TITLE = "Weekly usage limit, week over week"
FONT = "-apple-system,'Segoe UI',Verdana,sans-serif"

# Dark is the design as picked; light takes over under a light system setting. Gold is the line and its words.
PAGE_DARK = {"bg": "#18191b", "fg": "#e6e6e6", "lede": "#a9a9a9", "small": "#888888", "gold": "#d4a72c",
             "line": "#d4a72c", "cards": "#1c1e22", "wk": "#cfd1d4", "card": "#282a2f", "edge": "#3a3c42",
             "big": "#dcdcdc", "em": "#8d8f94", "label": "#b9bbc0", "panel": "#2a2a2a", "plot": "#2e2e2e",
             "plotedge": "#444444", "grid": "#3c3c3c", "tick": "#a9a9a9", "xl": "#e6e6e6"}
PAGE_LIGHT = {"bg": "#f6f6f4", "fg": "#1d1d1f", "lede": "#55565a", "small": "#6b6c70", "gold": "#7f5a00",
              "line": "#b8860b", "cards": "#ecece9", "wk": "#3a3b3f", "card": "#ffffff", "edge": "#d6d6d1",
              "big": "#1d1d1f", "em": "#66676b", "label": "#44454a", "panel": "#efefec", "plot": "#fbfbfa",
              "plotedge": "#cfcfca", "grid": "#e3e3de", "tick": "#5f6066", "xl": "#1d1d1f"}

CHART_CSS = (".ct{fill:var(--p-fg);font-size:17px}.plot{fill:var(--p-plot);stroke:var(--p-plotedge)}"
             ".grid{stroke:var(--p-grid)}.tick{fill:var(--p-tick);font-size:12px;font-variant-numeric:tabular-nums}"
             ".ln{fill:none;stroke:var(--p-line);stroke-width:3;stroke-linejoin:round}.dot{fill:var(--p-line)}"
             ".dot.open{fill:var(--p-plot);stroke:var(--p-line);stroke-width:2.5}.hit{fill:transparent}"
             ".val{fill:var(--p-gold);font-size:13px}.xl{fill:var(--p-xl);font-size:12px}.xl.soft{fill:var(--p-tick)}")


def page_vars(selector: str) -> str:
    def block(colors):
        return "".join("--p-%s:%s;" % kv for kv in colors.items())
    return ("%s{color-scheme:dark;%s}@media (prefers-color-scheme:light){%s{color-scheme:light;%s}}"
            % (selector, block(PAGE_DARK), selector, block(PAGE_LIGHT)))


def _esc(text) -> str:
    return html.escape(str(text))


def dollars(text) -> str:
    """'1234.56' -> '1,234.56': a dollar string with thousands commas."""
    return "{:,.2f}".format(_d(text))


def token_words(n: int) -> str:
    """7,656,577,590 -> '7.7B': one decimal, rounded half away from zero, in the largest unit that fits."""
    units = (("B", 10 ** 9), ("M", 10 ** 6), ("K", 10 ** 3))
    for i, (unit, size) in enumerate(units):
        if n >= size or (i + 1 < len(units) and half_up(Decimal(n) / units[i + 1][1], 1) >= 1000):
            return "%s%s" % (half_up(Decimal(n) / size, 1), unit)
    return str(n)


def _num(value) -> str:
    """A percent as the plan showed it: 41, or 37.5."""
    return format(_d(value).normalize(), "f") if _d(value) != _d(value).to_integral_value() else str(int(_d(value)))


def _signed(pct: str) -> str:
    return pct if pct.startswith("-") or Decimal(pct) == 0 else "+" + pct


def _day(iso: str, tz) -> str:
    dt = local_dt(parse_time_ms(iso), tz)
    return "%s %d" % (dt.strftime("%a, %b"), dt.day)


def week_days(w: dict, tz) -> str:
    """'Sat, Oct 10 – Sat, Oct 17', with 'This week · ' before a week still open."""
    return ("This week \u00b7 " if w["open"] else "") + "%s \u2013 %s" % (_day(w["start"], tz), _day(w["resets_at"], tz))


def week_status(w: dict) -> str:
    """The words under the percent: hit the limit, what was left unused, or what is left of a week still open."""
    left = _num(Decimal(100) - _d(w["percent"]))
    if w["open"]:
        return "%s%% left, week still open" % left
    said = "hit the limit" if _d(w["percent"]) >= 100 else "%s%% unused" % left
    return said + (" (use after the reset left out)" if w.get("reset_at") else "")


def section_names(sections: list, tz) -> dict:
    """Each section's heading and the id shown small beside it: "Account that resets Saturday" (aaaaaaaa), from the
    weekday its latest week resets on; "Account <id>" when two share that weekday."""
    days = {s["account"]: local_dt(parse_time_ms(s["weeks"][-1]["resets_at"]), tz).strftime("%A") for s in sections}
    return {a: (("Account that resets %s" % d, a) if list(days.values()).count(d) == 1
                else ("Account %s" % (a or "unknown"), "")) for a, d in days.items()}


def section_sum(section: dict, tz) -> tuple:
    """The header line in two parts: first and last week and the total change; the change week on week."""
    weeks, trend = section["weeks"], section.get("trend") or {}
    first, last = weeks[0], weeks[-1]
    span = "%s \u2192 %s:\u00a0 " % (date_words(first["start"], tz), date_words(last["resets_at"], tz))
    if len(weeks) == 1 or not trend.get("change_pct"):
        return span + "$%s (one week so far)" % dollars(last["ceiling_usd"]), ""
    total = "$%s \u2192 $%s \u00a0(%s%% total%s)" % (
        dollars(first["ceiling_usd"]), dollars(last["ceiling_usd"]), _signed(trend["change_pct"]),
        ", within the plan's rounding" if trend.get("direction") == "within rounding" else "")
    steps = "Week on week:\u00a0 " + " \u00b7 ".join(_signed(w["step_pct"]) + "%" for w in weeks[1:])
    return span + total, steps


def footer(data: dict, tz, now_ms: int, prices_checked: str, caption=None) -> list:
    """The notes under the page: what the figure is, its limits, what's left out, and when it was drawn."""
    dt = local_dt(now_ms, tz)
    lines = ["Weekly usage limit = API-equivalent dollars used in the week, divided by the percent of the week the plan "
             "said was used, at the week's highest reading; when the plan reset a week's usage early, only the use "
             "before the reset counts. A reset can be missed (then the week can read about double) or, from late "
             "stale readings, made up (then a running week shows as finished); a week that reached 100% first is "
             "right either way. It is a lower bound: use of the same plan on "
             "claude.ai, the desktop app or "
             "other machines isn't in these logs. Weeks with no usable reading are left out (under 20% used, no spend "
             "on this machine, spend with no known account, or overlapping another week; the ceiling command lists "
             "each with its reason). Right after switching accounts, spend counts toward the earlier account until a "
             "reading shows the new one, so a week next to a switch can read too high or too low."]
    if data.get("more_accounts"):
        n = data["more_accounts"]
        lines.append("%d more account%s with weeks %s not drawn here; the ceiling command lists every account."
                     % (n, "" if n == 1 else "s", "is" if n == 1 else "are"))
    lines.append("API list prices checked %s. Drawn %s %d, %s %s." % (
        prices_checked, dt.strftime("%b"), dt.day, dt.strftime("%Y %H:%M"), dt.strftime("%Z") or "UTC"))
    if caption:
        lines.append(caption)
    return lines


def empty_note(data: dict) -> str:
    if data.get("has_readings"):
        return ("No week with enough data yet: a week shows once your plan reads 20% or more used, with spend on this "
                "machine tied to your account.")
    return "No plan readings yet: install the statusline wrapper (statusline-install), or record one with record-quota."


def _week_dates(w: dict, tz) -> tuple:
    """A week's dates under its point: 'Oct 10–17' or 'Oct 31–Nov 7', and the same on two lines."""
    a, b = local_dt(parse_time_ms(w["start"]), tz), local_dt(parse_time_ms(w["resets_at"]), tz)
    ends = ("%d" % b.day) if (a.year, a.month) == (b.year, b.month) else "%s %d" % (b.strftime("%b"), b.day)
    return ("%s %d\u2013%s" % (a.strftime("%b"), a.day, ends),
            ("%s %d\u2013" % (a.strftime("%b"), a.day), "%s %d" % (b.strftime("%b"), b.day)))


def week_chart(weeks: list, tz, width: int = 560, height: int = 640) -> str:
    """One account's weekly limit, week over week: a line from $0, each week a point labelled with its figure. A week
    still open is hollow and says "so far" under its dates; each point's <title> carries its band."""
    left, right, top, bottom = 64, 60, 54, 60
    pw, ph = width - left - right, height - top - bottom
    peak = max(_d(w["ceiling_usd"]) for w in weeks)
    top_v, step = nice_top(peak * Decimal("1.1"), max_steps=6)
    n, inset = len(weeks), 30
    gap = (pw - 2 * inset) / (n - 1) if n > 1 else pw

    def x(i):
        return left + pw / 2 if n == 1 else left + inset + i * gap

    def y(v):
        return top + ph - float(_d(v) / top_v) * ph

    out = ['<text class="ct" x="%s" y="28" text-anchor="middle">%s</text>' % (_f(width / 2), _esc(CHART_TITLE)),
           '<rect class="plot" x="%s" y="%s" width="%s" height="%s"/>' % (left, top, pw, ph)]
    v = Decimal(0)
    while v <= top_v:
        gy = y(v)
        out.append('<line class="grid" x1="%s" y1="%s" x2="%s" y2="%s"/>' % (left, _f(gy), left + pw, _f(gy)))
        out.append('<text class="tick" x="%s" y="%s" text-anchor="end">%s</text>' % (left - 8, _f(gy + 4), money_tick(v)))
        v += step
    if n > 1:
        out.append('<polyline class="ln" points="%s"/>' % " ".join(
            "%s,%s" % (_f(x(i)), _f(y(w["ceiling_usd"]))) for i, w in enumerate(weeks)))
    values = [_d(w["ceiling_usd"]) for w in weeks]
    labels, placed = {}, []
    for i in [n - 1] + ([0] if n > 1 else []) + list(range(n - 2, 0, -1)):   # the newest week's figure first
        cx, cy = x(i), y(values[i])
        text = "$" + dollars(values[i])
        wide = 7.6 * len(text)   # about the label's width at 13px
        # Where the line isn't: above a peak, under a dip, beside a point the line runs through. Failing that, above
        # or under; a figure that would touch another is left to its card and its tooltip.
        near = [values[j] for j in (i - 1, i + 1) if 0 <= j < n]
        up, down = any(v > values[i] for v in near), any(v < values[i] for v in near)
        above, under = (cx, cy - 14, "middle"), (cx, cy + 26, "middle")
        first = above
        if up and down and 0 < i < n - 1:
            rising = values[i + 1] > values[i]
            first = (cx + (-11 if rising else 11), cy - 9, "end" if rising else "start")
        elif up:
            first = under
        for lx, ly, anchor in (first, above, under):
            x0 = {"start": lx, "end": lx - wide}.get(anchor, lx - wide / 2)
            box = (x0, x0 + wide, ly - 13, ly + 3)
            fits = top + 2 <= box[2] and box[3] <= top + ph - 2 and 2 <= box[0] and box[1] <= width - 2
            if fits and not any(box[0] < b[1] and b[0] < box[1] and box[2] < b[3] and b[2] < box[3] for b in placed):
                placed.append(box)
                labels[i] = (lx, ly, anchor, text)
                break
    dates = [_week_dates(w, tz) for w in weeks]
    two = n > 1 and max(6.6 * len(d[0]) for d in dates) > gap - 6   # too wide for their places: two lines each
    every = 1
    while n > 1 and two and max(6.6 * len(t) for d in dates for t in d[1]) > gap * every - 6:
        every += 1   # still too wide: the dates of every other week (or fewer), the newest always
    for i, w in enumerate(weeks):
        cx, cy = x(i), y(values[i])
        band = "band $%s to %s" % (dollars(w["band_low_usd"]),
                                   ("$" + dollars(w["band_high_usd"])) if w["band_high_usd"] else "no limit")
        tip = "%s%s: $%s (%s)%s" % ("This week so far, " if w["open"] else "", week_days(dict(w, open=False), tz),
                                     dollars(w["ceiling_usd"]), band,
                                     "; the plan reset early on %s, and use after the reset is left out"
                                     % date_words(w["reset_at"], tz) if w.get("reset_at") else "")
        out.append('<g><title>%s</title><circle class="hit" cx="%s" cy="%s" r="14"/>'
                   '<circle class="dot%s" cx="%s" cy="%s" r="6"/></g>'
                   % (_esc(tip), _f(cx), _f(cy), " open" if w["open"] else "", _f(cx), _f(cy)))
        if i in labels:
            lx, ly, anchor, text = labels[i]
            out.append('<text class="val" x="%s" y="%s" text-anchor="%s">%s</text>' % (_f(lx), _f(ly), anchor, _esc(text)))
        lines = (list(dates[i][1]) if two else [dates[i][0]]) if (n - 1 - i) % every == 0 else []
        for j, text in enumerate(lines + (["so far"] if w["open"] else [])):
            out.append('<text class="xl%s" x="%s" y="%s" text-anchor="middle">%s</text>'
                       % (" soft" if text == "so far" else "", _f(cx), _f(top + ph + 20 + 15 * j), _esc(text)))
    return "\n".join(out)


PAGE_CSS = """body{margin:0;background:var(--p-bg);color:var(--p-fg);font:15px/1.4 %s;padding:24px 16px}
main{max-width:1180px;margin:0 auto}
h1{font-weight:400;font-size:22px;text-align:center;margin:0 0 4px}
.lede{text-align:center;color:var(--p-lede);margin:0 0 28px}
section{margin:0 0 40px}
h2{font-weight:400;font-size:19px;text-align:center;margin:0 0 4px}h2 small{color:var(--p-small);font-size:13px}
.sum{color:var(--p-gold);text-align:center;margin:0 0 14px;font-size:16px}
.row{display:flex;gap:20px;flex-wrap:wrap;align-items:stretch}
.cards{flex:1 1 480px;background:var(--p-cards);padding:6px 0 14px;min-width:0}
.panel{flex:1 1 440px;background:var(--p-panel);padding:10px;display:flex;align-items:flex-start;min-width:0}
.panel svg{display:block;width:100%%;height:auto}
.wk{text-align:center;color:var(--p-wk);font-size:16px;margin:16px 0 10px}
.card{display:flex;gap:8px;background:var(--p-card);border:1px solid var(--p-edge);border-radius:8px;margin:0 16px;padding:18px 22px}
.card div{flex:1;min-width:0}.card b{display:block;font-size:26px;color:var(--p-big);white-space:nowrap}
.card em{font-style:normal;font-weight:400;color:var(--p-em);font-size:20px}
.card span{display:block;color:var(--p-label)}.card i{display:block;font-style:normal;color:var(--p-em);font-size:14px}
.card i.g{color:var(--p-gold)}
.note{text-align:center;color:var(--p-lede)}
.foot{color:var(--p-em);font-size:13px;max-width:900px}
@media (max-width:540px){.card{padding:14px 12px;margin:0 8px}.card div:last-child{flex:1.3}.card b{font-size:18px}.card em{font-size:14px}.card i{font-size:13px}}
""" % FONT


def page_html(data: dict, tz, now_ms: int, prices_checked: str, caption=None, extra_html: str = "",
              extra_css: str = "") -> str:
    """The chart page as one HTML file: no scripts, nothing loaded from elsewhere, every value escaped."""
    sections = data.get("sections") or []
    names = section_names(sections, tz)
    parts = []
    for s in sections:
        name, ident = names[s["account"]]
        totals, steps = section_sum(s, tz)
        cards = []
        for w in s["weeks"]:
            cards.append(
                '<div class="wk">%s</div>\n<div class="card"><div><b>%s%%</b><span>Used</span><i class="g">%s</i></div>'
                '<div><b>%s</b><span>Tokens</span><i>of %s</i></div>'
                '<div><b>%s <em>$</em></b><span>API-equivalent</span><i>of %s $</i></div></div>'
                % (_esc(week_days(w, tz)), _esc(_num(w["percent"])), _esc(week_status(w)), _esc(token_words(w["tokens"])),
                   _esc(token_words(w["tokens_ceiling"])), _esc(dollars(w["spend_usd"])), _esc(dollars(w["ceiling_usd"]))))
        chart = ('<svg viewBox="0 0 560 640" role="img" aria-label="%s">%s</svg>'
                 % (_esc("%s: %s" % (CHART_TITLE, name)), week_chart(s["weeks"], tz)))
        parts.append('<section><h2>%s%s</h2>\n<p class="sum">%s</p>\n<div class="row"><div class="cards">%s</div>'
                     '<div class="panel">%s</div></div></section>'
                     % (_esc(name), (" <small>(%s)</small>" % _esc(ident)) if ident else "",
                        _esc(totals + ("\u00a0\u00a0\u00a0 " + steps if steps else "")), "\n".join(cards), chart))
    if not parts:
        parts.append('<p class="note">%s</p>' % _esc(empty_note(data)))
    foot = "<br>\n".join(_esc(line) for line in footer(data, tz, now_ms, prices_checked, caption))
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Weekly usage limit</title>\n'
            '<style>\n%s\n%s%s%s</style></head><body><main>\n<h1>%s</h1>\n<p class="lede">%s</p>\n%s\n%s'
            '<p class="foot">%s</p></main></body></html>\n'
            % (page_vars(":root"), PAGE_CSS, CHART_CSS, extra_css, _esc(TITLE), "<br>\n".join(_esc(t) for t in LEDE),
               "\n".join(parts), extra_html, foot))


SVG_CSS = (".mp text{font-family:%s}.mp .bg{fill:var(--p-bg)}.mp .h1{fill:var(--p-fg);font-size:22px}"
           ".mp .lede{fill:var(--p-lede);font-size:15px}.mp .h2{fill:var(--p-fg);font-size:19px}"
           ".mp .small{fill:var(--p-small);font-size:13px}.mp .sum{fill:var(--p-gold);font-size:16px}"
           ".mp .cards{fill:var(--p-cards)}.mp .panel{fill:var(--p-panel)}.mp .wk{fill:var(--p-wk);font-size:16px}"
           ".mp .card{fill:var(--p-card);stroke:var(--p-edge)}.mp .big{fill:var(--p-big);font-size:26px;font-weight:700}"
           ".mp .em{fill:var(--p-em);font-size:20px;font-weight:400}.mp .lab{fill:var(--p-label);font-size:15px}"
           ".mp .sub{fill:var(--p-em);font-size:14px}.mp .sub.g{fill:var(--p-gold)}.mp .note{fill:var(--p-lede);font-size:15px}"
           ".mp .foot{fill:var(--p-em);font-size:13px}" % FONT)


def _lines(cls: str, x, y, lines: list, step: int, anchor: str = "start") -> str:
    """One <text> with a <tspan> per line (SVG text never wraps)."""
    spans = "".join('<tspan x="%s" dy="%s">%s</tspan>' % (_f(x), 0 if i == 0 else step, _esc(t)) for i, t in enumerate(lines))
    return '<text class="%s" x="%s" y="%s" text-anchor="%s">%s</text>' % (cls, _f(x), _f(y), anchor, spans)


def page_svg(data: dict, tz, now_ms: int, prices_checked: str, caption=None, width: int = 1170) -> str:
    """The same page as one self-contained SVG, laid out by hand: it opens in any browser or Quick Look."""
    sections = data.get("sections") or []
    names = section_names(sections, tz)
    mid = width / 2
    body = ['<rect class="bg" width="100%" height="100%"/>',
            '<text class="h1" x="%s" y="44" text-anchor="middle">%s</text>' % (_f(mid), _esc(TITLE)),
            _lines("lede", mid, 74, list(LEDE), 20, "middle")]
    y = 136
    cards_x, cards_w, panel_x = 24, 540, 584
    panel_w = width - 24 - panel_x
    for s in sections:
        name, ident = names[s["account"]]
        body.append('<text class="h2" x="%s" y="%s" text-anchor="middle">%s%s</text>' % (
            _f(mid), y, _esc(name), ('<tspan class="small" dx="8">(%s)</tspan>' % _esc(ident)) if ident else ""))
        totals, steps = section_sum(s, tz)
        joined = totals + ("\u00a0\u00a0\u00a0 " + steps if steps else "")
        sums = [joined] if len(joined) <= 120 else [totals, steps]
        body.append(_lines("sum", mid, y + 28, sums, 21, "middle"))
        top = y + 28 + 21 * (len(sums) - 1) + 18
        cards, cy = [], top + 6
        for w in s["weeks"]:
            status = wrap(week_status(w), 24)
            ch = 98 + 17 * (len(status) - 1)
            x0 = cards_x + 16
            cols = (x0 + 22, x0 + 192, x0 + 330)
            cards.append('<text class="wk" x="%s" y="%s" text-anchor="middle">%s</text>'
                         % (_f(cards_x + cards_w / 2), cy + 28, _esc(week_days(w, tz))))
            cards.append('<rect class="card" x="%s" y="%s" width="%s" height="%s" rx="8"/>' % (x0, cy + 40, cards_w - 32, ch))
            base = cy + 40
            cards.append('<text class="big" x="%s" y="%s">%s%%</text>' % (cols[0], base + 40, _esc(_num(w["percent"]))))
            cards.append('<text class="big" x="%s" y="%s">%s</text>' % (cols[1], base + 40, _esc(token_words(w["tokens"]))))
            cards.append('<text class="big" x="%s" y="%s">%s<tspan class="em" dx="6">$</tspan></text>'
                         % (cols[2], base + 40, _esc(dollars(w["spend_usd"]))))
            for col, label in zip(cols, ("Used", "Tokens", "API-equivalent")):
                cards.append('<text class="lab" x="%s" y="%s">%s</text>' % (col, base + 62, label))
            cards.append(_lines("sub g", cols[0], base + 83, status, 17))
            cards.append('<text class="sub" x="%s" y="%s">of %s</text>' % (cols[1], base + 83, _esc(token_words(w["tokens_ceiling"]))))
            cards.append('<text class="sub" x="%s" y="%s">of %s $</text>' % (cols[2], base + 83, _esc(dollars(w["ceiling_usd"]))))
            cy += 40 + ch + 8
        cards_h = cy - top + 8
        chart_h = max(420, min(cards_h - 20, 640))
        col_h = max(cards_h, chart_h + 20)
        body.append('<rect class="cards" x="%s" y="%s" width="%s" height="%s"/>' % (cards_x, top, cards_w, col_h))
        body.append('<rect class="panel" x="%s" y="%s" width="%s" height="%s"/>' % (panel_x, top, panel_w, col_h))
        body += cards
        body.append('<g transform="translate(%s,%s)" role="img" aria-label="%s">%s</g>' % (
            panel_x, top + 10, _esc("%s: %s" % (CHART_TITLE, name)), week_chart(s["weeks"], tz, panel_w, chart_h)))
        y = top + col_h + 52
    if not sections:
        body.append('<text class="note" x="%s" y="%s" text-anchor="middle">%s</text>' % (_f(mid), y, _esc(empty_note(data))))
        y += 40
    for line in footer(data, tz, now_ms, prices_checked, caption):
        wrapped = wrap(line, 150)
        body.append(_lines("foot", 24, y, wrapped, 18))
        y += 18 * len(wrapped) + 6
    height = y + 14
    return ('<svg xmlns="http://www.w3.org/2000/svg" class="mp" width="%d" height="%d" viewBox="0 0 %d %d" role="img" '
            'aria-label="%s"><title>%s</title><style>%s%s%s</style>\n%s\n</svg>\n'
            % (width, height, width, height, _esc(TITLE), _esc(TITLE), page_vars(".mp"), SVG_CSS,
               "".join(".mp " + rule + "}" for rule in CHART_CSS.split("}") if rule), "\n".join(body)))
