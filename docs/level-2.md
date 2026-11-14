# Level 2: the meter for a team

Level 1 is one person, one machine: [SPEC.md](../SPEC.md) and the reference. Level 2 is for a team lead who wants
the same questions answered across seats:

- what is our AI use worth at list prices;
- what are we getting from the plans;
- what does it produce.

The work splits in two:

- **Git yield** is part of the meter. It runs on each machine.
- **OpenTelemetry** is the clients' own export. You turn it on, and it reports to a collector your organization
  runs. The meter neither sends nor receives it.

## Git yield (in the meter)

```sh
python3 -I reference/meter.py yield --repo ~/code/our-app --period 30d
```

Or the MCP tool `meter_yield`. It runs one read-only `git log` over the period. The repo's own config can't run
anything through it (SPEC.md §10). It reports:

- commits and lines added and removed;
- **leaving out:** lockfiles, generated and vendored files, and pairs of a commit and its revert;
- **spend:** the API-equivalent spend of the sessions whose working folder was inside the repo;
- commits and lines added **per dollar**.

**Read it as a trend, never a score.** Lines of code are a weak measure of value. The number means something only
compared with itself: the same repo, the same team, the same way of working, over time. It's useful when a tool,
model or plan changes. Did the same kind of work start costing more?

## OpenTelemetry (the clients' own export)

**Claude Code** can export metrics and events with OpenTelemetry. You turn it on with environment variables. Set
them for each seat, for example through your organization's managed settings:

```sh
CLAUDE_CODE_ENABLE_TELEMETRY=1
OTEL_METRICS_EXPORTER=otlp
OTEL_EXPORTER_OTLP_PROTOCOL=grpc            # or http/protobuf
OTEL_EXPORTER_OTLP_ENDPOINT=https://collector.internal:4317  # your own collector, over TLS
```

The metrics include token use by type and model (`claude_code.token.usage`), Claude Code's own cost estimate
(`claude_code.cost.usage`), and counts of sessions, commits and lines changed. Check Claude Code's monitoring
documentation for the current names and attributes before you build on them.

**Codex** has its own OpenTelemetry settings in `~/.codex/config.toml`. See Codex's configuration documentation for
the current keys.

**To get the meter's numbers from that data:**

- **API-equivalent cost:** price the token counts with [prices.json](../prices.json), as SPEC.md §5 does. That keeps
  every seat and every week on the same price list.
- **The implied ceiling** needs each seat's plan readings. Those come from each person's own meter (SPEC.md §6), so
  share them only if the person chooses to.
- **Yield** per repo comes from git, as above.

**Keep it in-house.** Point the exporters at a collector your organization runs and controls. The data names people,
repositories and models. Leave prompt logging off: Claude Code's `OTEL_LOG_USER_PROMPTS` unset, and Codex's
equivalent off. Token counts are enough for everything here.
