"""Reading Claude Code and Codex logs into events and plan readings (SPEC.md §3 and §6).

Every function here only reads. Nothing writes to a transcript, ever.
"""

from __future__ import annotations

import json
import os
import re

from .util import parse_time_ms

# Lines worth parsing at all. A line without one of these can't hold usage or a plan reading, so the reader skips
# it without parsing: transcripts are mostly tool output, and a heavy user has gigabytes of it.
CLAUDE_MARKERS = (b'"usage"', b'"quotaLimits"', b'"session-owner"')
CODEX_MARKERS = (b'"token_count"', b'"session_meta"', b'"turn_context"')

WINDOW_NAME = re.compile(r"[a-z0-9_]{1,40}")
ACCOUNT = re.compile(r"[A-Za-z0-9_-]{1,64}")
MAX_RESET = 253402300799  # 9999-12-31T23:59:59Z, in Unix seconds


def _account(value) -> str:
    """An account id from a log line, or "" when it isn't a plain id."""
    return value if isinstance(value, str) and ACCOUNT.fullmatch(value) else ""


def window_name(value) -> bool:
    return isinstance(value, str) and WINDOW_NAME.fullmatch(value) is not None


def valid_reset(value) -> bool:
    """A reset time: a whole number of Unix seconds from 1 to the end of year 9999."""
    return type(value) is int and 1 <= value <= MAX_RESET


def _str(value, limit: int = 4096) -> str:
    """A string field from a log line, or "" when it's anything else: not a string, too long, or not valid
    Unicode (a lone surrogate can't be stored or shown, so it would stop every report)."""
    if not isinstance(value, str) or len(value) > limit:
        return ""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return ""
    return value


MAX_COUNT = 10 ** 10


def _int(value) -> int:
    """A token count: a whole number from 0 to 10^10. Anything else (text, decimals, negatives, absurd sizes) is 0."""
    if type(value) is not int or not 0 <= value <= MAX_COUNT:
        return 0
    return value


def claude_roots(env=os.environ) -> list[str]:
    """Where Claude Code keeps transcripts: $CLAUDE_CONFIG_DIR/projects, else ~/.claude/projects and the older
    ~/.config/claude/projects."""
    home = os.path.expanduser("~")
    if env.get("CLAUDE_CONFIG_DIR"):
        bases = [env["CLAUDE_CONFIG_DIR"]]
    else:
        bases = [os.path.join(home, ".claude"), os.path.join(home, ".config", "claude")]
    return [os.path.join(b, "projects") for b in bases]


def codex_roots(env=os.environ) -> list[str]:
    """Where Codex keeps rollouts: $CODEX_HOME (else ~/.codex) /sessions and /archived_sessions."""
    base = env.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    return [os.path.join(base, "sessions"), os.path.join(base, "archived_sessions")]


def jsonl_files(root: str) -> list[str]:
    """Every *.jsonl file under a root, without following symlinked folders, in order of the path relative to the
    root (a plain string sort, '/' as the separator), so every implementation reads them in the same order."""
    found = []
    if not os.path.isdir(root):
        return found
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            if name.endswith(".jsonl"):
                full = os.path.join(dirpath, name)
                if os.path.isfile(full) and not os.path.islink(full):
                    found.append(full)
    return sorted(found, key=lambda p: os.path.relpath(p, root).replace(os.sep, "/"))


def _event(source, key, session, account, project, model, at, **tokens) -> dict:
    ev = {
        "source": source,
        "key": key,
        "session": session if isinstance(session, str) else "",
        "account": account or "",
        "project": project or "",
        "model": model or "unknown",
        "at": at,
        "input": 0,
        "cache_read": 0,
        "cache_write": 0,
        "cache_write_1h": 0,
        "output": 0,
        "reasoning": 0,
        "web_search": 0,
        "speed": "standard",
        "geo": "",
    }
    ev.update(tokens)
    return ev


def claude_line(obj: dict, state: dict):
    """One parsed Claude Code transcript line to ([events], [readings], [owners]). `state` carries the account the
    file's latest session-owner line named; subagent files have none and get theirs through their session."""
    events, readings, owners = [], [], []
    if not isinstance(obj, dict):
        return events, readings, owners
    if obj.get("type") == "session-owner":
        account, session = _account(obj.get("accountUuid")), _str(obj.get("sessionId"), 128)
        if account:
            state["account"] = account
            if session:
                owners.append({"session": session, "account": account})
        return events, readings, owners
    if obj.get("type") != "assistant":
        return events, readings, owners
    at = parse_time_ms(obj.get("timestamp"))
    if at is None:
        return events, readings, owners
    session = _str(obj.get("sessionId"), 128)
    account = state.get("account", "")

    limits = obj.get("quotaLimits")
    if isinstance(limits, dict) and limits.get("status") == "rejected":
        window = limits.get("rateLimitType")
        resets = limits.get("resetsAt")
        if window_name(window) and valid_reset(resets):
            readings.append({"family": "claude", "window": window, "percent": 100, "resets_at": resets,
                             "at": at, "source": "limit_hit", "session": session, "account": account})

    message = obj.get("message")
    if not isinstance(message, dict):
        return events, readings, owners
    usage = message.get("usage")
    model = message.get("model")
    msg_id = message.get("id")
    if (not isinstance(usage, dict) or not _str(model, 200) or model == "<synthetic>"
            or not _str(msg_id, 200)):
        return events, readings, owners

    breakdown = usage.get("cache_creation")
    if isinstance(breakdown, dict):
        write_5m = _int(breakdown.get("ephemeral_5m_input_tokens"))
        write_1h = _int(breakdown.get("ephemeral_1h_input_tokens"))
    else:
        write_5m, write_1h = _int(usage.get("cache_creation_input_tokens")), 0
    server_tools = usage.get("server_tool_use") if isinstance(usage.get("server_tool_use"), dict) else {}
    details = usage.get("output_tokens_details") if isinstance(usage.get("output_tokens_details"), dict) else {}
    request = _str(obj.get("requestId"), 200)
    events.append(_event(
        "claude_code", "cc:%s:%s" % (msg_id, request), session, account, _str(obj.get("cwd")), model, at,
        input=_int(usage.get("input_tokens")),
        cache_read=_int(usage.get("cache_read_input_tokens")),
        cache_write=write_5m,
        cache_write_1h=write_1h,
        output=_int(usage.get("output_tokens")),
        reasoning=_int(details.get("thinking_tokens")),
        web_search=_int(server_tools.get("web_search_requests")),
        speed="fast" if usage.get("speed") == "fast" else "standard",
        geo=_str(usage.get("inference_geo"), 40),
    ))
    return events, readings, owners


_TOTAL_FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens",
                 "reasoning_output_tokens", "total_tokens")


def codex_line(obj: dict, state: dict):
    """One parsed Codex rollout line to ([events], [readings], []), carrying session, account, model, cwd and the
    last running total in `state` (saved per file between syncs)."""
    events, readings = [], []
    if not isinstance(obj, dict):
        return events, readings, []
    kind = obj.get("type")
    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
    if kind == "session_meta":
        sid = _str(payload.get("session_id"), 128) or _str(payload.get("id"), 128)
        if sid:
            state["session"] = sid
        if _str(payload.get("cwd")):
            state["cwd"] = payload["cwd"]
        if _str(payload.get("model"), 200):
            state["model"] = payload["model"]
        if _account(payload.get("creator_account_id")):
            state["account"] = payload["creator_account_id"]
        return events, readings, []
    if kind == "turn_context":
        if _str(payload.get("model"), 200):
            state["model"] = payload["model"]
        if _str(payload.get("cwd")):
            state["cwd"] = payload["cwd"]
        return events, readings, []
    if kind != "event_msg" or payload.get("type") != "token_count":
        return events, readings, []
    at = parse_time_ms(obj.get("timestamp"))
    if at is None:
        return events, readings, []
    session, account = state.get("session", ""), state.get("account", "")

    limits = payload.get("rate_limits")
    if isinstance(limits, dict):
        for slot in ("primary", "secondary"):
            win = limits.get(slot)
            if not isinstance(win, dict):
                continue
            pct = win.get("used_percent")
            minutes = win.get("window_minutes")
            if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not 0 <= pct <= 100:
                continue
            if type(minutes) is not int or not 0 < minutes <= 527040:  # at most a year
                continue
            resets = win.get("resets_at")
            if type(resets) is not int:
                secs = win.get("resets_in_seconds")
                resets = at // 1000 + secs if type(secs) is int else None
            if not valid_reset(resets):
                continue
            name = {300: "five_hour", 10080: "seven_day"}.get(minutes, "%dm" % minutes)
            readings.append({"family": "codex", "window": name, "percent": pct, "resets_at": resets,
                             "at": at, "source": "codex", "session": session, "account": account})

    info = payload.get("info")
    if not isinstance(info, dict):
        return events, readings, []
    total = info.get("total_token_usage")
    last = info.get("last_token_usage")
    if not isinstance(total, dict) or not isinstance(last, dict):
        return events, readings, []
    signature = [_int(total.get(f)) for f in _TOTAL_FIELDS]
    if state.get("last_total") == signature:
        return events, readings, []  # a repeat: the running total didn't move
    state["last_total"] = signature

    cached = _int(last.get("cached_input_tokens"))
    written = _int(last.get("cache_write_input_tokens"))
    uncached = max(0, _int(last.get("input_tokens")) - cached - written)
    output = _int(last.get("output_tokens"))
    if uncached + cached + written + output == 0:
        return events, readings, []
    events.append(_event(
        "codex", "cx:%s:%s" % (session, "/".join(str(n) for n in signature)), session, account, state.get("cwd"),
        state.get("model"), at,
        input=uncached, cache_read=cached, cache_write=written, output=output,
        reasoning=_int(last.get("reasoning_output_tokens")),
    ))
    return events, readings, []


def quota_line(obj: dict):
    """One line of quota.jsonl (the statusline wrapper's and manual readings) to a reading, or None."""
    if not isinstance(obj, dict):
        return None
    family, window, pct, resets = obj.get("family"), obj.get("window"), obj.get("percent"), obj.get("resets_at")
    at = parse_time_ms(obj.get("at"))
    if family not in ("claude", "codex") or not window_name(window):
        return None
    if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not 0 <= pct <= 100:
        return None
    if not valid_reset(resets) or at is None:
        return None
    source = obj.get("source") if obj.get("source") in ("statusline", "manual") else "manual"
    session = _str(obj.get("session"), 128)
    return {"family": family, "window": window, "percent": pct, "resets_at": resets, "at": at, "source": source,
            "session": session, "account": _account(obj.get("account"))}


def parse_jsonl_line(raw: bytes, markers) -> object:
    """A complete line's JSON, or None to skip it; raises ValueError when a candidate line isn't valid JSON."""
    if markers and not any(m in raw for m in markers):
        return None
    text = raw.strip()
    if not text:
        return None
    return json.loads(text)
