"""The ledger: a SQLite file that remembers every event and plan reading, synced lazily (SPEC.md §4 and §7).

A sync reads only what was appended to each log since the last sync. It never reads a line that has no newline
yet (the client may still be writing it), so a half-written last line is read whole next time.
"""

from __future__ import annotations

import bisect
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


WEEK = 7 * 86400


def _same_phase(a: int, b: int) -> bool:
    d = abs(a - b) % WEEK
    return min(d, WEEK - d) <= 600


class Schedules:
    """Claude Code's accounts told apart by their weekly reset schedules (SPEC.md §3.8).

    Signing in to another account switches every running Claude Code session of that config folder at once, and the
    transcripts don't record it, so a session's owner line can name the wrong account. Each account's week resets at
    its own fixed time, so the reset time a reading shows says whose week it is. A sign-in belongs to one folder, so
    schedules are named, and the rule tested, folder by folder. It applies in a folder only when one of its sessions'
    own readings show two accounts' schedules: direct evidence that the sign-in changed under running sessions."""

    def __init__(self, conn):
        self.phases, self.names, self.lines, self.folders, self.only = [], {}, {}, {}, None
        if len(known_accounts(conn).get("claude_code", ())) < 2:
            return
        rows = conn.execute("SELECT at, resets_at, session, source FROM readings WHERE family = 'claude' "
                            "AND window = 'seven_day' ORDER BY at, resets_at, session").fetchall()
        tagged = [(at, resets_at, session, source, self._phase(resets_at)) for at, resets_at, session, source in rows]
        if len(self.phases) < 2:
            return
        for path, in conn.execute("SELECT path FROM files WHERE kind = 'claude' ORDER BY path"):
            if path.endswith(".jsonl") and os.path.basename(os.path.dirname(path)) != "subagents":
                self.folders.setdefault(os.path.basename(path)[:-len(".jsonl")], os.path.dirname(os.path.dirname(path)))
        if len(set(self.folders.values())) == 1:
            self.only = next(iter(self.folders.values()))
        owners, voted, votes = session_accounts(conn), set(), {}
        for _at, _resets, session, _source, ph in tagged:   # each session votes once, in its own folder
            folder = self.folder(session)
            if session and folder is not None and session not in voted:
                voted.add(session)
                if owners.get(session):
                    tally = votes.setdefault((folder, ph), {})
                    tally[owners[session]] = tally.get(owners[session], 0) + 1
        for key, tally in votes.items():
            ranked = sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
            if len(ranked) == 1 or ranked[0][1] > ranked[1][1]:   # a tie names no one
                self.names[key] = ranked[0][0]
        seen = {}
        for _at, _resets, session, _source, ph in tagged:
            name = self.names.get((self.folder(session), ph))
            if session and name:
                seen.setdefault(session, set()).add(name)
        switched = {self.folder(s) for s, accts in seen.items() if len(accts) > 1}
        for at, _resets, session, source, ph in tagged:   # one timeline per folder where a session saw a switch
            folder = self.folder(session)
            if folder in switched and source != "manual":
                ats, names = self.lines.setdefault(folder, ([], []))
                ats.append(at)
                names.append(self.names.get((folder, ph), ""))   # "": an unnamed schedule, the account is unknown

    def folder(self, session: str):
        """A session's projects folder; a reading with no session belongs to the only folder, when there is one."""
        return self.folders.get(session) if session else self.only

    def _phase(self, resets_at: int) -> int:
        p = resets_at % WEEK
        for i, q in enumerate(self.phases):
            if _same_phase(p, q):
                return i
        self.phases.append(p)
        return len(self.phases) - 1

    @property
    def active(self) -> bool:
        return bool(self.lines)

    def reading_account(self, resets_at: int, session: str) -> str:
        """The account a seven-day reading's schedule is named for in its session's folder, where the rule applies
        there; "" otherwise (then §3.7 decides)."""
        folder = self.folder(session)
        if folder not in self.lines:
            return ""
        p = resets_at % WEEK
        return next((self.names.get((folder, i), "") for i, q in enumerate(self.phases) if _same_phase(p, q)), "")

    def account_at(self, session: str, at: int):
        """The account in use in a session's folder at a time: that of the folder's latest reading at or before it,
        taken at most seven days before (a sign-in outlasts a reset). None when there's none, or when that reading's
        schedule names no one (then §3.7 decides)."""
        line = self.lines.get(self.folder(session))
        if not line:
            return None
        ats, names = line
        i = bisect.bisect_right(ats, at) - 1
        if i < 0 or at - ats[i] > WEEK * 1000 or not names[i]:
            return None
        return names[i]


def _resolve(rows, owners, sole, source_of=lambda r: r["source"], schedules=None) -> list[dict]:
    """A row with no account of its own takes its session's (subagent files, statusline readings), and failing that,
    the machine's only account when it has just one (logs from before the client named accounts). Where Claude
    Code's reset schedules tell its accounts apart, they decide instead (SPEC.md §3.8)."""
    for r in rows:
        if schedules is not None and schedules.active and source_of(r) == "claude_code":
            found = schedules.account_at(r.get("session", ""), r["at"])
            if found is not None:
                r["account"] = found
                continue
        if not r["account"] and r.get("session"):
            r["account"] = owners.get(r["session"], "")
        if not r["account"]:
            r["account"] = sole.get(source_of(r), "")
    return rows


def resolve_events(conn, rows) -> list[dict]:
    """Events as dicts, with their accounts resolved as every report resolves them (SPEC.md §3.7, §3.8)."""
    return _resolve(rows, session_accounts(conn), sole_accounts(conn), schedules=Schedules(conn))


def all_events(conn) -> list[dict]:
    rows = conn.execute("SELECT %s FROM events ORDER BY at, key" % ", ".join(EVENT_COLUMNS)).fetchall()
    return resolve_events(conn, [dict(zip(EVENT_COLUMNS, r)) for r in rows])


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
    return resolve_events(conn, [dict(zip(EVENT_COLUMNS, r)) for r in rows])


def all_readings(conn) -> list[dict]:
    """Every plan reading with its account resolved, each distinct one once at the earliest time it was seen."""
    rows = conn.execute("SELECT family, window, resets_at, percent, session, account, at, source FROM readings").fetchall()
    owners, sole, schedules = session_accounts(conn), sole_accounts(conn), Schedules(conn)
    best = {}
    for family, window, resets_at, percent, session, account, at, source in rows:
        if family == "claude" and schedules.active and source != "manual":
            # the schedule decides: a seven-day reading by its own reset time, any other by the account in use
            found = (schedules.reading_account(resets_at, session) if window == "seven_day"
                     else schedules.account_at(session, at))
            if found:
                account = found
        account = account or owners.get(session, "") or sole.get({"claude": "claude_code"}.get(family, family), "")
        pct = int(percent) if float(percent).is_integer() else percent
        k = (family, account, window, resets_at, pct)
        if k not in best or at < best[k]["at"]:
            best[k] = {"family": family, "account": account, "window": window, "resets_at": resets_at,
                       "percent": pct, "at": at, "source": source}
    return sorted(best.values(), key=lambda r: (r["at"], r["family"], r["account"], r["window"], r["percent"]))
