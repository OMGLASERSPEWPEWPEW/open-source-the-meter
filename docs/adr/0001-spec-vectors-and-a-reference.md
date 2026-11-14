# ADR 0001: A spec, test vectors and a small reference, not a package

Date: 2026-11-14 · Status: accepted

## Context

The repo goes to strangers. We don't know their machines: macOS or Linux, Python or not, Node or not, Claude Code,
Codex or both. Each of them has an AI agent that can read, write and run code. The usual answer is a package to
install. That asks them to trust a download, and it ties them to one runtime.

## Decision

The contract is [SPEC.md](../../SPEC.md) plus golden test vectors: synthetic Claude Code and Codex logs, and the
exact tokens, dollars and ceilings expected from them. A small reference in Python 3.9+ standard library passes
them. A person's AI either uses the reference, or builds its own meter in whatever runtime the machine has and
proves it against the same vectors.

## Consequences

- **The numbers are pinned down,** not the code. Two implementations that pass agree to the nano-dollar.
- **Nothing to download,** beyond the repo. No package manager, no `@latest`.
- **The vectors are tied to formats.** They must change when the clients' log formats change, and so must SPEC.md.
- **Agents need guidance.** AGENTS.md must steer a stranger's agent: explain, audit, ask, then build.

## Alternatives

- **An npm or pip package:** one runtime, and an install from the network.
- **Prose only:** every agent would make different choices about dedupe, pricing and rounding, and get different
  numbers.
