# ADR 0004: Ceilings per account, and saying what can't be known

Date: 2026-11-14 · Status: accepted; refined the same day by SPEC.md §3.8 (see the last section)

## Context

The first run on real logs showed overlapping weekly windows with different reset times. That's one machine
switching between two Claude accounts. Pooling all spend into each window gave nonsense, such as a change of
+156,500%. Claude Code began writing a `session-owner` line with the account id in November 2026. Subagent files
don't carry the line, and older logs don't have it at all.

## Decision

**Events and readings carry an account,** found in this order:

1. the file's owner line;
2. the session's owner;
3. the machine's only account, when there's just one.

A known account's ceiling counts only its own events. Spend with no known account is reported as **unattributed**,
never guessed.

**A week-over-week change is shown only when it means something.** It isn't shown when a window overlaps the last
one (an early reset), or when either window has unattributed spend. When a change is shown, it's flagged if the two
bands overlap (it may be only the percent's rounding).

**Every Claude ceiling is reported as a lower bound.** The percent also counts use the logs can't see: claude.ai,
the desktop app, other machines.

## Consequences

- **Single-account users** get full attribution, back through history.
- **Multi-account users** get clean figures from the point the client names accounts, and a stated gap before it.

## Refinement: accounts by reset schedule

The first real multi-account run showed the order above isn't enough. Signing in to another account switches every
running Claude Code session of that config folder at once, and the transcripts never record it, so owner lines name
the account a session started with, not the one in use. Every week read "two reset schedules mixed".

Each account's week resets at its own fixed time, so SPEC.md §3.8 now decides first, folder by folder. It names each
schedule for the account its sessions' owner lines mostly name. It applies only where a session's own readings show
two accounts. There, a reading takes its schedule's account and spend takes the account of the folder's latest
reading. Accounts used side by side keep their owner lines. A switch shows only once a reading moves (§13).
