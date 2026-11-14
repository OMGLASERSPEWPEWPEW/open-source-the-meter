"""Time, money and rounding rules shared by every part of the meter (SPEC.md §2)."""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

NANO_PER_USD = 1_000_000_000
MAX_TIME_MS = 253402214399999  # 9999-12-30T23:59:59.999Z: a day short of the end, so any zone's day still exists

_ISO = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})[T ]([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]+))?"
    r"(Z|[+-][0-9]{2}:?[0-9]{2})?"
)


def parse_time_ms(text) -> int | None:
    """An ISO 8601 timestamp to whole milliseconds since 1970 UTC, or None.

    A timestamp with no offset is UTC. Digits past the millisecond are dropped (floor).
    """
    if not isinstance(text, str):
        return None
    m = _ISO.fullmatch(text)
    if not m:
        return None
    year, month, day, hour, minute, second = (int(m.group(i)) for i in range(1, 7))
    frac = (m.group(7) or "").ljust(3, "0")[:3]
    try:
        base = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    except (ValueError, OverflowError):
        return None
    ms = int(base.timestamp()) * 1000 + int(frac)
    off = m.group(8)
    if off and off != "Z":
        sign = -1 if off[0] == "-" else 1
        digits = off[1:].replace(":", "")
        hours, minutes = int(digits[:2]), int(digits[2:])
        if hours > 23 or minutes > 59:
            return None
        ms -= sign * (hours * 60 + minutes) * 60_000
    if not 0 <= ms <= MAX_TIME_MS:
        return None  # outside 1970 .. 9999-12-30: no report could write it back
    return ms


def format_time_ms(ms: int) -> str:
    """Milliseconds since 1970 UTC to 'YYYY-MM-DDTHH:MM:SS.mmmZ'."""
    dt = datetime.fromtimestamp(ms // 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + ".%03dZ" % (ms % 1000)


def now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def parse_tz(text):
    """'local' (None: each instant in the machine's own zone, with that day's offset), 'UTC', or a fixed offset like
    '+00:00' or '-06:00'."""
    if not text or text == "local":
        return None
    if text.upper() in ("UTC", "Z"):
        return timezone.utc
    m = re.fullmatch(r"([+-])([0-9]{2}):?([0-9]{2})", text)
    if not m:
        raise ValueError("timezone must be 'local', 'UTC' or an offset like -06:00")
    sign = -1 if m.group(1) == "-" else 1
    return timezone(sign * timedelta(hours=int(m.group(2)), minutes=int(m.group(3))))


def local_dt(ms: int, tz) -> datetime:
    """An instant as a datetime in the report's zone; in 'local', with the offset that applied on that date."""
    if tz is None:
        return datetime.fromtimestamp(ms / 1000).astimezone()
    return datetime.fromtimestamp(ms / 1000, tz=tz)


def day_start_ms(year: int, month: int, day: int, tz) -> int:
    """Midnight starting a calendar day in the report's zone, as ms since 1970 UTC."""
    if tz is None:
        return int(datetime(year, month, day).astimezone().timestamp() * 1000)
    return int(datetime(year, month, day, tzinfo=tz).timestamp() * 1000)


def day_of(ms: int, tz) -> str:
    return local_dt(ms, tz).strftime("%Y-%m-%d")


def half_up(value, places: int = 0):
    """Round a Decimal half away from zero."""
    from decimal import ROUND_HALF_UP, Decimal
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def scale_percent(nano: int, percent: int) -> int:
    """nano x percent / 100, rounded half away from zero (nano is never negative)."""
    return (nano * percent + 50) // 100


def usd(nano: int | None) -> str | None:
    """Integer nano-dollars to a dollars-and-cents string, rounded half away from zero."""
    if nano is None:
        return None
    from decimal import Decimal
    return str(half_up(Decimal(nano) / NANO_PER_USD, 2))


def decimal_of(number):
    """A JSON number as an exact decimal (via its shortest text form)."""
    from decimal import Decimal
    return Decimal(str(number))


def meter_home() -> str:
    return os.environ.get("OPEN_METER_HOME") or os.path.join(os.path.expanduser("~"), ".open-meter")


def _shown(ch: str) -> bool:
    """False for characters that can hide or reorder text: controls, format marks (bidi, zero-width), private use,
    unassigned code points, variation selectors and tag characters."""
    import unicodedata
    cp = ord(ch)
    if 0xFE00 <= cp <= 0xFE0F or 0xE0000 <= cp <= 0xE007F or 0xE0100 <= cp <= 0xE01EF:
        return False
    return unicodedata.category(ch)[0] != "C"


def clean_text(value, limit: int = 160) -> str:
    """Text from a log, made safe to show: hidden and control characters dropped, whitespace collapsed, length
    capped. Logs are data; this keeps them from passing as something else."""
    if value is None:
        return ""
    text = " ".join("".join(ch if _shown(ch) else " " for ch in str(value)).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def private_dir(path: str) -> None:
    """A folder only this user can open (0700)."""
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def append_private(path: str, line: str) -> None:
    """Append one line to a file only this user can read (0600), creating it and its folder (0700). If the file
    doesn't end with a newline (saved by hand), one goes first, so the new line stays whole."""
    private_dir(os.path.dirname(path))
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0), 0o600)
    try:
        if os.fstat(fd).st_mode & 0o077:
            os.fchmod(fd, 0o600)  # a file made by hand is made private too
        size = os.fstat(fd).st_size
        lead = b""
        if size:
            os.lseek(fd, size - 1, os.SEEK_SET)
            if os.read(fd, 1) != b"\n":
                lead = b"\n"
        os.write(fd, lead + (line + "\n").encode("utf-8"))
    finally:
        os.close(fd)
