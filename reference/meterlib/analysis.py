"""Totals, days, implied ceilings and git yield: everything the meter reports (SPEC.md §8 to §10)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal

from .util import NANO_PER_USD, day_of, decimal_of, format_time_ms, half_up, usd

SPEC_VERSION = "1"

FAMILY_SOURCE = {"claude": "claude_code", "codex": "codex"}
WINDOW_SECONDS = {"five_hour": 5 * 3600, "seven_day": 7 * 86400}
FIRM_PERCENT = 20

TOKEN_FIELDS = ("input", "cache_read", "cache_write", "cache_write_1h", "output", "reasoning", "web_search")


def window_seconds(name: str):
    if name in WINDOW_SECONDS:
        return WINDOW_SECONDS[name]
    m = re.match(r"^(\d+)m$", name)
    return int(m.group(1)) * 60 if m else None


def priced_events(events, prices) -> list[dict]:
    out = []
    for ev in events:
        ev = dict(ev)
        ev["cost_nano"] = prices.cost_nano(ev)
        out.append(ev)
    return out


def short_account(account: str) -> str:
    """Accounts are shown by their first 8 characters: enough to tell two apart."""
    return (account or "")[:8]


def event_out(ev) -> dict:
    out = {k: ev[k] for k in ("source", "key", "session", "project", "model") + TOKEN_FIELDS + ("speed", "geo")}
    out["account"] = short_account(ev["account"])
    out["at"] = format_time_ms(ev["at"])
    out["priced"] = ev["cost_nano"] is not None
    out["cost_nano"] = ev["cost_nano"]
    return out


def totals(events) -> list[dict]:
    """Per (source, model): events, tokens, and cost (null when the model has no price)."""
    groups = {}
    for ev in events:
        g = groups.setdefault((ev["source"], ev["model"]), {"source": ev["source"], "model": ev["model"], "events": 0,
                                                            **{f: 0 for f in TOKEN_FIELDS}, "cost_nano": 0})
        g["events"] += 1
        for f in TOKEN_FIELDS:
            g[f] += ev[f]
        if ev["cost_nano"] is None:
            g["cost_nano"] = None
        elif g["cost_nano"] is not None:
            g["cost_nano"] += ev["cost_nano"]
    out = []
    for key in sorted(groups):
        g = groups[key]
        g["priced"] = g["cost_nano"] is not None
        g["usd"] = usd(g["cost_nano"])
        out.append(g)
    return out


def by_day(events, tz) -> list[dict]:
    """Per (day in the given time zone, source): events, priced cost, and how many events had no price."""
    groups = {}
    for ev in events:
        k = (day_of(ev["at"], tz), ev["source"])
        g = groups.setdefault(k, {"day": k[0], "source": k[1], "events": 0, "cost_nano": 0, "unpriced_events": 0})
        g["events"] += 1
        if ev["cost_nano"] is None:
            g["unpriced_events"] += 1
        else:
            g["cost_nano"] += ev["cost_nano"]
    out = []
    for k in sorted(groups):
        g = groups[k]
        g["usd"] = usd(g["cost_nano"])
        out.append(g)
    return out


def reading_out(r) -> dict:
    return {"family": r["family"], "account": short_account(r.get("account", "")), "window": r["window"], "percent": r["percent"],
            "resets_at": format_time_ms(r["resets_at"] * 1000), "at": format_time_ms(r["at"]), "source": r["source"]}


def _ratio(numerator: int, denominator: Decimal):
    return int(half_up(Decimal(numerator) * 100 / denominator)) if denominator > 0 else None


def ceilings(events, readings, several=frozenset()) -> list[dict]:
    """The implied ceiling of each window: API-equivalent spend in the window up to the latest reading, divided by
    that reading's percent, with a band for a reading that may be off by one point."""
    groups = {}
    for r in readings:
        length = window_seconds(r["window"])
        if length is None or r["family"] not in FAMILY_SOURCE:
            continue
        instance = (r["resets_at"] + 300) // 600 * 600  # readings within ~5 minutes of each other are one window
        groups.setdefault((r["family"], r.get("account", ""), r["window"], instance), []).append(r)
    rows = []
    for (family, account, window, _instance), rs in groups.items():
        chosen = max(rs, key=lambda r: (r["at"], r["percent"]))
        pct = decimal_of(chosen["percent"])
        if pct <= 0:
            continue
        length = window_seconds(window)
        start_ms = (chosen["resets_at"] - length) * 1000
        spend, unpriced, unattributed = 0, 0, 0
        source = FAMILY_SOURCE[family]
        for ev in events:
            if ev["source"] != source or not start_ms <= ev["at"] <= chosen["at"]:
                continue
            if account and ev["account"] != account:
                if not ev["account"] and ev["cost_nano"] is not None:
                    unattributed += ev["cost_nano"]  # no account known: maybe this one's, maybe another's
                continue  # another account's use: its own window
            if not account and family in several and ev["account"]:
                if ev["cost_nano"] is not None:
                    unattributed += ev["cost_nano"]  # the reading's account is unknown: maybe this, maybe not
                continue
            if ev["cost_nano"] is None:
                unpriced += 1
            else:
                spend += ev["cost_nano"]
        ceiling = _ratio(spend, pct)
        low = _ratio(spend, pct + 1)
        high = _ratio(spend, pct - 1) if pct > 1 else None
        rows.append({
            "family": family, "account": short_account(account), "window": window,
            "resets_at": format_time_ms(chosen["resets_at"] * 1000), "start": format_time_ms(start_ms),
            "reading_at": format_time_ms(chosen["at"]), "reading_source": chosen["source"],
            "percent": chosen["percent"],
            "spend_nano": spend, "spend_usd": usd(spend),
            "ceiling_nano": ceiling, "ceiling_usd": usd(ceiling),
            "band_low_usd": usd(low), "band_high_usd": usd(high),
            "firm": pct >= FIRM_PERCENT,
            "unpriced_events": unpriced,
            "unattributed_usd": usd(unattributed),
            "_sort": (short_account(account), account, chosen["resets_at"]), "_acct": account,
            "_low": low, "_high": high,
        })
    rows.sort(key=lambda r: (r["family"], r["window"], r["_sort"]))
    previous = {}
    for row in rows:
        k = (row["family"], row["window"], row["_acct"])
        prev = previous.get(k)
        row["change_pct"], row["change_within_band"], row["change_note"] = None, None, None
        if not row["_acct"] and row["family"] in several:
            row["change_note"] = "account unknown"
        elif prev is None:
            row["change_note"] = "first window"
        elif row["start"] < prev["resets_at"]:
            row["change_note"] = "overlaps the last window (an early reset?)"
        elif row["unattributed_usd"] != "0.00" or prev["unattributed_usd"] != "0.00":
            row["change_note"] = "spend with no known account"
        elif not prev["ceiling_nano"]:
            row["change_note"] = "no spend in the last window"
        else:
            change = (Decimal(row["ceiling_nano"]) - prev["ceiling_nano"]) * 100 / prev["ceiling_nano"]
            row["change_pct"] = str(half_up(change, 1))
            # Bands that overlap mean the change may be only the plan's whole-number rounding.
            hi_now = row["_high"] if row["_high"] is not None else float("inf")
            hi_prev = prev["_high"] if prev["_high"] is not None else float("inf")
            row["change_within_band"] = not (hi_now < prev["_low"] or row["_low"] > hi_prev)
        previous[k] = row
    for row in rows:
        for k in ("_sort", "_acct", "_low", "_high"):
            del row[k]
    return rows


# --- Level 2: git yield (SPEC.md §10) ----------------------------------------------------------------------------

LOCKFILES = {
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock", "bun.lockb", "Cargo.lock",
    "poetry.lock", "uv.lock", "Pipfile.lock", "Gemfile.lock", "composer.lock", "go.sum", "Package.resolved",
    "Podfile.lock", "flake.lock", "mix.lock", "pubspec.lock",
}
GENERATED_SUFFIXES = (".min.js", ".min.css", ".map", ".pb.go", "_pb2.py", ".snap")
GENERATED_DIRS = {"node_modules", "vendor", "dist", "build", ".next", "__generated__"}
_REVERT = re.compile(r'^Revert "(.+)"$')


def generated(path: str) -> bool:
    parts = path.split("/")
    return parts[-1] in LOCKFILES or path.endswith(GENERATED_SUFFIXES) or any(p in GENERATED_DIRS for p in parts[:-1])


def parse_git_log(text: str) -> list[dict]:
    """Output of the SPEC's git log command: records split by \\x1e, header fields by \\x1f, then numstat lines."""
    commits = []
    for record in text.split("\x1e"):
        if not record.strip():
            continue
        header, _, rest = record.partition("\n")
        fields = header.split("\x1f")
        if len(fields) < 3:
            continue
        files = []
        for line in rest.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            added, removed, path = parts
            if added.isdigit() and removed.isdigit():
                files.append((int(added), int(removed), path))
        commits.append({"sha": fields[0], "at": int(fields[1]) if fields[1].isdigit() else 0,
                        "subject": "\x1f".join(fields[2:]), "files": files})
    return commits


def git_yield(commits, events, repo: str, since_ms: int, until_ms: int) -> dict:
    """Commits and lines (without lockfiles, generated files, or reverted work) per API-equivalent dollar spent by
    sessions whose working folder is the repo or inside it, over the same period."""
    excluded = set()
    for c in commits:
        m = _REVERT.match(c["subject"])
        if not m or c["sha"] in excluded:
            continue
        for other in commits:
            if other["sha"] not in excluded and other["sha"] != c["sha"] and other["subject"] == m.group(1):
                excluded.update((c["sha"], other["sha"]))
                break
    kept = [c for c in commits if c["sha"] not in excluded]
    added = removed = skipped = 0
    for c in kept:
        for a, r, path in c["files"]:
            if generated(path):
                skipped += a + r
            else:
                added += a
                removed += r
    root = repo.rstrip("/") or "/"
    prefix = root if root.endswith("/") else root + "/"
    spend, unpriced = 0, 0
    for ev in events:
        p = ev["project"]
        if since_ms <= ev["at"] <= until_ms and (p == root or p.startswith(prefix)):
            if ev["cost_nano"] is None:
                unpriced += 1
            else:
                spend += ev["cost_nano"]

    def per_dollar(n):
        return str(half_up(Decimal(n) * NANO_PER_USD / spend, 2)) if spend else None

    return {
        "repo": root, "since": format_time_ms(since_ms), "until": format_time_ms(until_ms),
        "commits": len(kept), "reverted_pairs": len(excluded) // 2,
        "lines_added": added, "lines_removed": removed, "generated_lines_skipped": skipped,
        "spend_nano": spend, "spend_usd": usd(spend), "unpriced_events": unpriced,
        "commits_per_dollar": per_dollar(len(kept)), "lines_added_per_dollar": per_dollar(added),
    }


def git_log_command(git: str, repo: str, since_ms: int, until_ms: int) -> list:
    """The exact git command, as an argument list (never a shell string). The -c settings and --no-* flags stop a
    repo's own config from running programs or reaching the network: fsmonitor, hooks, pagers, external diff,
    textconv, signature checks, fetches."""
    def stamp(ms):
        return datetime.fromtimestamp(ms // 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S +0000")
    return [
        git, "--no-pager", "-C", repo,
        "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", "core.pager=cat",
        "-c", "core.quotepath=off", "-c", "diff.external=", "-c", "log.showSignature=false",
        "-c", "safe.bareRepository=explicit", "-c", "protocol.allow=never", "-c", "credential.helper=",
        "log", "--no-merges", "--no-renames", "--no-ext-diff", "--no-textconv", "--no-show-signature", "--no-color",
        "--numstat", "--max-count=20000", "--format=%x1e%H%x1f%ct%x1f%s",
        "--since=" + stamp(since_ms), "--until=" + stamp(until_ms),
    ]


def conformance(events, readings, tz, yield_case=None, several=frozenset()) -> dict:
    """The document a conforming implementation prints for `conformance` (SPEC.md §12)."""
    doc = {
        "spec": SPEC_VERSION,
        "events": [event_out(e) for e in sorted(events, key=lambda e: (e["at"], e["key"]))],
        "totals": totals(events),
        "by_day": by_day(events, tz),
        "readings": [reading_out(r) for r in readings],
        "ceilings": ceilings(events, readings, several),
    }
    if yield_case is not None:
        doc["yield"] = git_yield(yield_case["commits"], events, yield_case["repo"], yield_case["since_ms"],
                                 yield_case["until_ms"])
    return doc
