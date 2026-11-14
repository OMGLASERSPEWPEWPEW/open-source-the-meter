# ADR 0003: Lazy sync into a local ledger; MCP over stdio first

Date: 2026-11-14 · Status: accepted

## Context

A heavy user's transcripts run to gigabytes: 8 GB on the machine this was built on. Claude Code deletes transcripts
after 30 days by default. The person wants answers inside their AI tool, without running services.

## Decision

- **A local ledger.** It's SQLite in `~/.open-meter/`, keeps every event once read, and outlives the transcripts.
- **Lazy, incremental sync.**
  - Every report first reads what was appended since the last sync, by byte offset per file. The first sync is
    about half a minute for 8 GB; later ones take a fraction of a second.
  - A line with no newline yet is left for the next sync.
  - A shorter file is read again; dedupe keys keep counts right.
- **Interfaces in this order:**
  1. an MCP server over stdio (no port; it works in Claude Code, Codex, Cursor and Claude Desktop);
  2. a CLI;
  3. an optional dashboard on 127.0.0.1, run in the foreground.
- **No daemons, LaunchAgents or cron.**

## Consequences

- **The first call after a long gap reads more.** The first sync of all should be run from the CLI, where its
  progress shows.
- **Two clients may sync at once.** They're safe: one transaction per file, WAL, and dedupe keys.
- **Every event is priced at query time** from the current price list. Weeks then compare at the same prices.
