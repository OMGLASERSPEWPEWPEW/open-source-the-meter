"""The statusline wrapper: saves the plan's used percent from Claude Code's statusline input, then runs the
statusline the person already had, unchanged (SPEC.md §6.2). Also its install and undo, which show the change
first, back up settings.json, and change one key.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

from .util import append_private, format_time_ms, meter_home, now_ms, private_dir

# shlex, shutil, tempfile and datetime load only for install and undo: the wrapper runs on every redraw.

MAX_RESET = 253402300799  # the end of year 9999, in Unix seconds (SPEC.md §3.3)

MAX_PARSE = 1_000_000
MARK = "statusline"  # the subcommand that identifies our wrapper inside a command string
GUARD = "OPEN_METER_STATUSLINE"  # set while the chained command runs, so a wrapper never wraps itself


def settings_path(env=os.environ) -> str:
    base = env.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "settings.json")


# --- the wrapper, run by Claude Code on every status line redraw ---------------------------------------------------

def readings_from_input(data: bytes) -> list:
    """The plan readings in one statusline input: rate_limits.<window>.used_percentage and resets_at."""
    if len(data) > MAX_PARSE:
        return []
    obj = json.loads(data.decode("utf-8"))
    limits = obj.get("rate_limits") if isinstance(obj, dict) else None
    out = []
    if not isinstance(limits, dict):
        return out
    session = obj.get("session_id") if isinstance(obj.get("session_id"), str) and len(obj["session_id"]) <= 128 else ""
    for window, v in limits.items():
        if not isinstance(window, str) or not isinstance(v, dict):
            continue
        if not re.fullmatch(r"[a-z0-9_]{1,40}", window):  # SPEC.md §3.3's window name
            continue
        pct, resets = v.get("used_percentage"), v.get("resets_at")
        if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not 0 <= pct <= 100:
            continue
        if type(resets) is not int or not 1 <= resets <= MAX_RESET:
            continue
        out.append({"window": window, "percent": pct, "resets_at": resets, "session": session})
    return out


def capture(data: bytes, home: str) -> list:
    """Append each reading that changed since this session's last redraw to quota.jsonl. Returns what it appended."""
    state_path = os.path.join(home, "statusline-last.json")
    try:
        with open(state_path, encoding="utf-8") as fh:
            last = json.load(fh)
        if not isinstance(last, dict):
            last = {}
    except (OSError, ValueError):
        last = {}
    appended = []
    now = format_time_ms(now_ms())
    for r in readings_from_input(data):
        slot = "%s@%d@%s" % (r["window"], r["resets_at"], r["session"])
        if last.get(slot) == r["percent"]:
            continue
        line = {"at": now, "source": "statusline", "family": "claude", "window": r["window"],
                "percent": r["percent"], "resets_at": r["resets_at"], "session": r["session"]}
        append_private(os.path.join(home, "quota.jsonl"), json.dumps(line, sort_keys=True))
        last[slot] = r["percent"]
        appended.append(line)
    if appended:
        if len(last) > 64:  # forget old windows: keep the 32 with the latest reset times
            last = dict(sorted(last.items(), key=lambda kv: kv[0].split("@")[1:2])[-32:])
        write_json_atomic(state_path, last, 0o600)
    return appended


def own_line(data: bytes) -> str:
    """What to print when there was no statusline before: the plan's percents, briefly."""
    try:
        rs = {r["window"]: r for r in readings_from_input(data)}
    except Exception:
        return ""
    parts = []
    for window, label in (("seven_day", "week"), ("five_hour", "5h")):
        if window in rs:
            parts.append("%s %s%%" % (label, rs[window]["percent"]))
    return "plan " + " · ".join(parts) if parts else ""


def log_error(home: str, what: str) -> None:
    """errors.log: when and which kind of error, never the input's text. Kept under 1 MB, private."""
    try:
        path = os.path.join(home, "errors.log")
        if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
            os.replace(path, path + ".1")
        append_private(path, "%s statusline: %s" % (format_time_ms(now_ms()), what[:200]))
    except Exception:
        pass


def run(then, stdin=None, stdout=None) -> int:
    """The wrapper. Whatever happens in capture, the chained statusline still runs and its output is passed on
    byte for byte, with its exit code."""
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    data = stdin.read()
    if not os.environ.get(GUARD):
        try:
            home = meter_home()
            try:
                capture(data, home)
            except Exception as exc:
                log_error(home, exc.__class__.__name__)
        except Exception:
            pass
    if then:
        env = dict(os.environ, **{GUARD: "1"})
        done = subprocess.run(then, shell=True, input=data, stdout=subprocess.PIPE, env=env, check=False)
        _write(stdout, done.stdout)
        return done.returncode
    line = own_line(data)
    if line:
        _write(stdout, (line + "\n").encode("utf-8"))
    return 0


def _write(stdout, data: bytes) -> None:
    """Pass output on. A reader that has gone away (a closed pipe) is not an error worth a second run: stdout is
    pointed at /dev/null so Python's own flush at exit can't fail either, and the original's exit code stands."""
    try:
        stdout.write(data)
        stdout.flush()
    except (BrokenPipeError, OSError, ValueError):
        try:
            null = os.open(os.devnull, os.O_WRONLY)
            os.dup2(null, stdout.fileno())
            os.close(null)
        except (OSError, ValueError, AttributeError):
            pass


# --- install and undo ---------------------------------------------------------------------------------------------

class InstallError(Exception):
    pass


def _no_constants(name):
    raise ValueError("%s isn't JSON" % name)


def _no_duplicates(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise ValueError("the key %r appears twice" % k)
        seen[k] = v
    return seen


def _finite(text: str) -> float:
    value = float(text)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError("the number %s is too large to keep exactly" % text[:40])
    return value


def strict_json(text: str):
    """JSON that refuses what readers disagree on: duplicate keys, NaN, Infinity, and numbers too large for a float
    (which would be written back as Infinity, breaking the file)."""
    return json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constants, parse_float=_finite)


def write_json_atomic(path: str, obj, mode: int, expect: bytes = None) -> None:
    """Write JSON through a new temp file in the same folder (never a fixed name, never followed), fsync, set the
    mode, then rename over the old file. With `expect`, refuse if the file changed since it was read."""
    import tempfile
    folder = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".meter-", dir=folder)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write((json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
            os.fchmod(fh.fileno(), mode)
        if expect is not None:
            with open(path, "rb") as fh:
                if fh.read() != expect:
                    raise InstallError("%s changed while the meter was editing it. Nothing was changed; try again." % path)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_settings(path: str):
    """settings.json as (object, raw bytes, mode). A missing file is an empty object."""
    if not os.path.exists(path):
        return {}, None, None
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8")
        obj = strict_json(text) if text.strip() else {}
    except ValueError as exc:
        raise InstallError("%s isn't valid JSON (%s). Fix it first; nothing was changed." % (path, exc))
    if not isinstance(obj, dict):
        raise InstallError("%s doesn't hold a JSON object. Nothing was changed." % path)
    return obj, raw, os.stat(path).st_mode & 0o777


def is_ours(command) -> bool:
    import shlex
    if not isinstance(command, str):
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    return MARK in words and any(w.endswith("meter.py") for w in words[:words.index(MARK)])


def stable_python() -> str:
    """The Python to write into a config: macOS's /usr/bin/python3 rather than the Xcode or Command Line Tools path
    it runs from, which moves when Xcode does."""
    exe = sys.executable
    if sys.platform == "darwin" and exe.startswith(("/Applications/Xcode", "/Library/Developer/CommandLineTools")) \
            and os.path.exists("/usr/bin/python3"):
        return "/usr/bin/python3"
    return exe


def wrapper_command(meter_py: str, original, python=None) -> str:
    """The statusLine command: run the meter's wrapper if Python and meter.py are still where they were; if either
    has moved or gone, run the original status line by itself, so the status line never breaks. Every part is
    shell-quoted."""
    import shlex
    q = shlex.quote
    py, meter = python or stable_python(), os.path.realpath(meter_py)
    guard = "[ -x %s ] && [ -r %s ] && exec %s -I %s %s" % (q(py), q(meter), q(py), q(meter), MARK)
    if original:
        return "%s --then %s ; exec /bin/sh -c %s" % (guard, q(original), q(original))
    return "%s ; exit 0" % guard


def plan_install(meter_py: str, path=None, python=None) -> dict:
    """What install would change, without changing anything."""
    path = os.path.realpath(path or settings_path())
    obj, _raw, _mode = _load_settings(path)
    current = obj.get("statusLine")
    if isinstance(current, dict) and is_ours(current.get("command")):
        return {"path": path, "already": True, "before": current, "after": current}
    if current is not None and not (isinstance(current, dict) and current.get("type", "command") == "command"
                                    and isinstance(current.get("command", ""), str)):
        raise InstallError("statusLine in %s isn't a command; edit it by hand. Nothing was changed." % path)
    original = current.get("command") if isinstance(current, dict) else None
    after = dict(current or {})
    after["type"] = "command"
    after["command"] = wrapper_command(meter_py, original, python)
    return {"path": path, "already": False, "before": current, "after": after}


def backup(path: str):
    """A dated private copy of the file in ~/.open-meter/backups/."""
    import shutil
    from datetime import datetime, timezone
    if not os.path.exists(path):
        return None
    folder = os.path.join(meter_home(), "backups")
    private_dir(meter_home())
    private_dir(folder)
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = os.path.join(folder, "%s.%s" % (os.path.basename(path), stamp))
    dest, n = base, 1
    while os.path.exists(dest):
        n += 1
        dest = "%s-%d" % (base, n)
    shutil.copyfile(path, dest)
    os.chmod(dest, 0o600)
    return dest


def apply_install(meter_py: str, path=None, python=None) -> dict:
    path = os.path.realpath(path or settings_path())  # a symlinked settings.json is edited where it really lives
    plan = plan_install(meter_py, path, python)
    if plan["already"]:
        return plan
    obj, raw, mode = _load_settings(path)
    plan["backup"] = backup(path)
    private_dir(meter_home())
    write_json_atomic(os.path.join(meter_home(), "statusline-original.json"),
                      {"settings": path, "statusLine": plan["before"]}, 0o600)
    obj["statusLine"] = plan["after"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_json_atomic(path, obj, mode if mode is not None else 0o600, expect=raw)
    return plan


def apply_uninstall(path=None, dry_run: bool = False) -> dict:
    path = os.path.realpath(path or settings_path())
    obj, raw, mode = _load_settings(path)
    current = obj.get("statusLine")
    if not (isinstance(current, dict) and is_ours(current.get("command"))):
        return {"path": path, "installed": False}
    # The chained command inside our wrapper is the truth; the saved original adds any other keys it had.
    import shlex
    words = shlex.split(current["command"])
    chained = words[words.index("--then") + 1] if "--then" in words and words.index("--then") + 1 < len(words) else None
    restored = dict(current, command=chained) if chained else None
    try:
        with open(os.path.join(meter_home(), "statusline-original.json"), encoding="utf-8") as fh:
            record = json.load(fh)
        saved = record.get("statusLine") if isinstance(record, dict) and record.get("settings") == path else None
        # The saved entry wins when it agrees with the wrapper: the same command, or no command (an empty one).
        if isinstance(saved, dict) and (saved.get("command") == chained or (chained is None and not saved.get("command"))):
            restored = saved
    except (OSError, ValueError):
        pass
    result = {"path": path, "installed": True, "before": current, "after": restored}
    if dry_run:
        return result
    result["backup"] = backup(path)
    if restored is None:
        obj.pop("statusLine", None)
    else:
        obj["statusLine"] = restored
    write_json_atomic(path, obj, mode if mode is not None else 0o600, expect=raw)
    return result
