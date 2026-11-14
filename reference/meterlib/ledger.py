"""The ledger: a SQLite file that remembers every event and plan reading, synced lazily (SPEC.md §4 and §7).

A sync reads only what was appended to each log since the last sync. It never reads a line that has no newline
yet (the client may still be writing it), so a half-written last line is read whole next time.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import time

from . import sources
from .util import meter_home, now_ms, private_dir

SCHEMA_VERSION = "1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    offset INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS events (
    key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    session TEXT NOT NULL,
    account TEXT NOT NULL,
    project TEXT NOT NULL,
    model TEXT NOT NULL,
    at INTEGER NOT NULL,
    input INTEGER NOT NULL,
    cache_read INTEGER NOT NULL,
    cache_write INTEGER NOT NULL,
    cache_write_1h INTEGER NOT NULL,
    output INTEGER NOT NULL,
    reasoning INTEGER NOT NULL,
    web_search INTEGER NOT NULL,
    speed TEXT NOT NULL,
    geo TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_at ON events(at);
CREATE TABLE IF NOT EXISTS readings (
    family TEXT NOT NULL,
    window TEXT NOT NULL,
    resets_at INTEGER NOT NULL,
    percent REAL NOT NULL,
    session TEXT NOT NULL,
    account TEXT NOT NULL,
    at INTEGER NOT NULL,
    source TEXT NOT NULL,
    from_quota_file INTEGER NOT NULL,
    PRIMARY KEY (family, window, resets_at, percent, session, account)
);
CREATE TABLE IF NOT EXISTS sessions (session TEXT PRIMARY KEY, account TEXT NOT NULL);
"""

EVENT_COLUMNS = ("key", "source", "session", "account", "project", "model", "at", "input", "cache_read", "cache_write",
                 "cache_write_1h", "output", "reasoning", "web_search", "speed", "geo")

CHUNK = 4 * 1024 * 1024
MAX_LINE = 32 * 1024 * 1024  # a longer line is skipped as bad, and never read again


def open_db(path: str | None = None) -> sqlite3.Connection:
    """Open (and create) the ledger, private to this user: the folder 0700, the file 0600."""
    if path is None:
        path = os.path.join(meter_home(), "meter.db")
    parent = os.path.dirname(os.path.abspath(path))
    if os.path.abspath(parent) == os.path.abspath(meter_home()):
        private_dir(parent)  # the meter's home is this user's only (0700)
    else:
        os.makedirs(parent, exist_ok=True)
    if not os.path.exists(path):
        fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
    else:
        mode = stat.S_IMODE(os.stat(path).st_mode)
        if mode & 0o077:
            os.chmod(path, 0o600)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)  # transactions are explicit below
    conn.execute("PRAGMA busy_timeout = 30000")
    for attempt in range(8):  # readers don't wait on a sync in another MCP server; two first opens can collide
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            break
        except sqlite3.OperationalError:
            if attempt == 7:
                raise
            time.sleep(0.05 * (attempt + 1))
    have = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'meta'").fetchone()
    if have:
        version = conn.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
        if version and version[0] != SCHEMA_VERSION:
            conn.close()
            raise LedgerError("%s was made by another version of the meter (schema %s, this one reads %s). "
                              "Move it aside and the meter starts a new ledger." % (path, version[0], SCHEMA_VERSION))
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema', ?)", (SCHEMA_VERSION,))
    return conn


class LedgerError(Exception):
    pass


class SyncStats:
    def __init__(self):
        self.files = 0
        self.new_events = 0
        self.duplicates = 0
        self.new_readings = 0
        self.bad_lines = 0


TOKEN_COLUMNS = ("input", "cache_read", "cache_write", "cache_write_1h", "output", "reasoning", "web_search")


def _add_events(conn, events, stats: SyncStats) -> None:
    """The first event for a key keeps its place, session and time; each token count is the largest any line for
    that key carried (a response's later lines can carry its final output count)."""
    for ev in events:
        exists = conn.execute("SELECT 1 FROM events WHERE key = ?", (ev["key"],)).fetchone()
        conn.execute(
            "INSERT INTO events (%s) VALUES (%s) ON CONFLICT (key) DO UPDATE SET %s" % (
                ", ".join(EVENT_COLUMNS), ", ".join("?" * len(EVENT_COLUMNS)),
                ", ".join("%s = MAX(%s, excluded.%s)" % (c, c, c) for c in TOKEN_COLUMNS)),
            tuple(ev[c] for c in EVENT_COLUMNS),
        )
        if exists:
            stats.duplicates += 1
        else:
            stats.new_events += 1


def add_readings(conn, readings, stats: SyncStats | None = None, from_quota_file: bool = False) -> None:
    """Keep each distinct reading once, at the earliest time it was seen."""
    for r in readings:
        cur = conn.execute(
            "INSERT INTO readings (family, window, resets_at, percent, session, account, at, source, from_quota_file) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (family, window, resets_at, percent, session, account) DO UPDATE SET "
            "source = CASE WHEN excluded.at < readings.at THEN excluded.source ELSE readings.source END, "
            "at = MIN(readings.at, excluded.at)",
            (r["family"], r["window"], r["resets_at"], float(r["percent"]), r.get("session", ""),
             r.get("account", ""), r["at"], r["source"], 1 if from_quota_file else 0),
        )
        if stats is not None and cur.rowcount:
            stats.new_readings += 1


def _add_owners(conn, owners) -> None:
    for o in owners:
        conn.execute("INSERT INTO sessions (session, account) VALUES (?, ?) "
                     "ON CONFLICT (session) DO UPDATE SET account = excluded.account", (o["session"], o["account"]))


def _sync_file(conn, path: str, kind: str, stats: SyncStats) -> None:
    row = conn.execute("SELECT offset, state FROM files WHERE path = ?", (path,)).fetchone()
    offset, state = (row[0], json.loads(row[1])) if row else (0, {})
    try:
        size = os.stat(path).st_size
    except OSError:
        return
    if size < offset:  # the file was replaced or cut: read it again; keys stop double counting
        offset, state = 0, {}
    if size == offset:
        return
    if kind == "claude":
        parse, markers = sources.claude_line, sources.CLAUDE_MARKERS
    else:
        parse, markers = sources.codex_line, sources.CODEX_MARKERS
    events, readings, owners = [], [], []
    try:
        fd = os.open(path, os.O_RDONLY | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0))
    except OSError:
        return  # gone, or a symlink: never followed
    skipping = bool(state.pop("skip_to_newline", False))  # inside an over-long line from the last sync
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            return
        fh.seek(offset)
        pending = b""
        while True:
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            if skipping:
                newline = chunk.find(b"\n")
                if newline < 0:
                    offset += len(chunk)
                    continue
                offset += newline + 1
                chunk = chunk[newline + 1:]
                skipping = False
            pending += chunk
            lines = pending.split(b"\n")
            pending = lines.pop()  # no newline yet: leave it for the next sync
            for raw in lines:
                offset += len(raw) + 1
                try:
                    obj = sources.parse_jsonl_line(raw, markers)
                    if obj is None:
                        continue
                    if len(raw) > MAX_LINE:
                        raise ValueError("line too long")
                    trial = dict(state)  # a line that fails part-way leaves the file's state as it was
                    evs, rds, own = parse(obj, trial)
                    json.dumps([evs, rds, own], ensure_ascii=False).encode("utf-8")  # storable, or a bad line
                except Exception:  # any line that can't be read is a bad line, never a stopped sync
                    stats.bad_lines += 1
                    continue
                state = trial
                events.extend(evs)
                readings.extend(rds)
                owners.extend(own)
            if len(pending) > MAX_LINE:
                stats.bad_lines += 1
                offset += len(pending)
                pending = b""
                skipping = True
    if skipping:
        state["skip_to_newline"] = True
    _add_owners(conn, owners)
    _add_events(conn, events, stats)
    add_readings(conn, readings, stats)
    conn.execute(
        "INSERT INTO files (path, kind, offset, state) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (path) DO UPDATE SET offset = excluded.offset, state = excluded.state",
        (path, kind, offset, json.dumps(state, sort_keys=True)),
    )
    stats.files += 1


def _sync_quota_file(conn, path: str, stats: SyncStats) -> None:
    """quota.jsonl is small and the person may edit it, so when it changes it's read again whole and its readings
    replace the ones it gave before."""
    try:
        st = os.stat(path)
    except OSError:
        st = None
    mark = "%d:%d" % (st.st_size, st.st_mtime_ns) if st else ""
    row = conn.execute("SELECT state FROM files WHERE path = ?", (path,)).fetchone()
    if row and json.loads(row[0]).get("mark") == mark:
        return
    readings = []
    if st:
        with open(path, "rb") as fh:
            for raw in fh:
                if not raw.endswith(b"\n"):
                    break  # still being written
                try:
                    obj = sources.parse_jsonl_line(raw, None)
                    if obj is None:
                        continue
                    reading = sources.quota_line(obj)
                except Exception:
                    stats.bad_lines += 1
                    continue
                if reading:
                    readings.append(reading)
                else:
                    stats.bad_lines += 1
    conn.execute("DELETE FROM readings WHERE from_quota_file = 1")
    add_readings(conn, readings, stats, from_quota_file=True)
    conn.execute(
        "INSERT INTO files (path, kind, offset, state) VALUES (?, 'quota', 0, ?) "
        "ON CONFLICT (path) DO UPDATE SET state = excluded.state", (path, json.dumps({"mark": mark})))
    stats.files += 1


def sync(conn, claude_roots, codex_roots, quota_file: str | None) -> SyncStats:
    """Read everything new. One transaction per file, so a crash loses nothing and two syncs at once are safe."""
    stats = SyncStats()
    jobs = []
    for root in claude_roots:
        jobs += [(p, "claude") for p in sources.jsonl_files(root)]
    for root in codex_roots:
        jobs += [(p, "codex") for p in sources.jsonl_files(root)]
    if quota_file:
        jobs.append((quota_file, "quota"))
    for path, kind in jobs:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if kind == "quota":
                _sync_quota_file(conn, path, stats)
            else:
                _sync_file(conn, path, kind, stats)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    conn.execute("BEGIN IMMEDIATE")
    bad = int((conn.execute("SELECT value FROM meta WHERE key = 'bad_lines'").fetchone() or ["0"])[0])
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('bad_lines', ?)", (str(bad + stats.bad_lines),))
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('last_sync', ?)", (str(now_ms()),))
    conn.execute("COMMIT")
    return stats


def session_accounts(conn) -> dict:
    return dict(conn.execute("SELECT session, account FROM sessions").fetchall())


def known_accounts(conn) -> dict:
    """Per source, every account the logs have named: Claude Code's sessions map and events, Codex's events."""
    known = {"claude_code": {a for (a,) in conn.execute("SELECT DISTINCT account FROM sessions")}, "codex": set()}
    for source, account in conn.execute("SELECT DISTINCT source, account FROM events WHERE account != ''"):
        known.setdefault(source, set()).add(account)
    return known


def sole_accounts(conn) -> dict:
    """Per source, the one account this machine has ever used, when there is exactly one."""
    return {source: next(iter(accts)) for source, accts in known_accounts(conn).items() if len(accts) == 1}


def several_accounts(conn) -> frozenset:
    """The families (claude, codex) whose source has named more than one account."""
    family = {"claude_code": "claude", "codex": "codex"}
    return frozenset(family[s] for s, accts in known_accounts(conn).items() if s in family and len(accts) > 1)


def _resolve(rows, owners, sole, source_of=lambda r: r["source"]) -> list[dict]:
    """A row with no account of its own takes its session's (subagent files, statusline readings), and failing that,
    the machine's only account when it has just one (logs from before the client named accounts)."""
    for r in rows:
        if not r["account"] and r.get("session"):
            r["account"] = owners.get(r["session"], "")
        if not r["account"]:
            r["account"] = sole.get(source_of(r), "")
    return rows


def all_events(conn) -> list[dict]:
    rows = conn.execute("SELECT %s FROM events ORDER BY at, key" % ", ".join(EVENT_COLUMNS)).fetchall()
    return _resolve([dict(zip(EVENT_COLUMNS, r)) for r in rows], session_accounts(conn), sole_accounts(conn))


def events_between(conn, start_ms: int | None, end_ms: int | None) -> list[dict]:
    sql = "SELECT %s FROM events WHERE 1=1" % ", ".join(EVENT_COLUMNS)
    args = []
    if start_ms is not None:
        sql += " AND at >= ?"
        args.append(start_ms)
    if end_ms is not None:
        sql += " AND at <= ?"
        args.append(end_ms)
    rows = conn.execute(sql + " ORDER BY at, key", args).fetchall()
    return _resolve([dict(zip(EVENT_COLUMNS, r)) for r in rows], session_accounts(conn), sole_accounts(conn))


def all_readings(conn) -> list[dict]:
    """Every plan reading with its account resolved, each distinct one once at the earliest time it was seen."""
    rows = conn.execute("SELECT family, window, resets_at, percent, session, account, at, source FROM readings").fetchall()
    owners, sole = session_accounts(conn), sole_accounts(conn)
    best = {}
    for family, window, resets_at, percent, session, account, at, source in rows:
        account = account or owners.get(session, "") or sole.get({"claude": "claude_code"}.get(family, family), "")
        pct = int(percent) if float(percent).is_integer() else percent
        k = (family, account, window, resets_at, pct)
        if k not in best or at < best[k]["at"]:
            best[k] = {"family": family, "account": account, "window": window, "resets_at": resets_at,
                       "percent": pct, "at": at, "source": source}
    return sorted(best.values(), key=lambda r: (r["at"], r["family"], r["account"], r["window"], r["percent"]))
