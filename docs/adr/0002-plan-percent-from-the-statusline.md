# ADR 0002: The plan's percent comes from Claude Code's statusline input

Date: 2026-11-14 · Status: accepted

## Context

The implied ceiling needs the plan's used percent. Claude Code knows it. There are three ways to get it:

1. **Read the login token** and call the provider's usage endpoint.
2. **Read Claude Code's statusline input.** On every redraw, Claude Code hands the status line program
   `rate_limits.<window>.used_percentage` and `resets_at`.
3. **Ask the person** to type it in.

## Decision

Use (2): a small wrapper that saves each new reading and runs the person's existing status line unchanged. Use
(3) as the fallback, plus two readings that are free:

- the transcript's limit-hit line, which is a 100% reading;
- Codex's own logged readings.

Never use (1).

## Consequences

- **No credentials.** Nothing reads a login token, and no private endpoint is called. A tool that holds a token can
  do anything that token allows.
- **Readings are whole numbers.** The ceiling carries a band, and readings under 20% aren't "firm".
- **The wrapper edits `settings.json`.** So the install is shown first, backed up, written atomically, refuses
  invalid JSON, and has an undo.
- **Readings only come while Claude Code runs.** A week with no Claude Code use has no reading, which is fine: it
  has no spend either.
