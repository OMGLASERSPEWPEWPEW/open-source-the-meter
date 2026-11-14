# Open-source the meter

![The meter's chart: a weekly ceiling sliding from $100 to $76 over six weeks while daily use rises from $5 to $6](docs/meter-chart.svg)

*The picture the meter draws from your own logs. This one is made from synthetic test data
([vectors/cases/07-trends](vectors/cases/07-trends)), not anyone's real use.*

When you pay for an AI plan by the month, you're shown a percent and a reset time. You're never told how much you
bought. Meanwhile your AI coding tools keep exact logs on your own computer: every response, every token, every
cache hit.

This repository turns those logs into **a meter you own**. It runs on your machine, reads only your local logs, and
tells you:

- **what your Claude Code and Codex use would have cost** at the providers' API list prices, by day, model and
  project;
- **how many API dollars your plan's week holds** (the *implied ceiling*), week after week, so a change in your
  allowance shows up as a number;
- **for teams (Level 2):** commits and lines per API dollar, from your git history.

It isn't an app you download and trust. It's a written spec ([SPEC.md](SPEC.md)), a set of test cases with exact
expected answers ([vectors/](vectors/)), and a small reference implementation in plain Python ([reference/](reference/)).
**Your own AI reads it, explains it, audits it, and with your yes, sets it up or builds its own version.** Any version
that passes the same tests computes the same numbers.

## Start here if you're new to this

You'll talk to an AI in a terminal (a text window for typing commands). It takes about ten minutes.

1. **Get a terminal app.** [Ghostty](https://ghostty.org) is a good, free one for Mac and Linux. On a Mac, the
   built-in Terminal app works too.
2. **Install an AI coding tool and sign in:** [Claude Code](https://claude.com/claude-code) or
   [Codex](https://developers.openai.com/codex). Follow their own install steps.
3. **Get this repository.** In the terminal, type:

   ```sh
   git clone https://github.com/OMGLASERSPEWPEWPEW/open-source-the-meter.git
   cd open-source-the-meter
   ls -a
   ```

   `ls -a` lists the folder. You should see `AGENTS.md`, `CLAUDE.md`, `LICENSE`, `README.md`, `SPEC.md`, `docs`,
   `prices.json`, `reference`, `vectors` and `.git`, plus `.gitignore`. **If you see `.claude`, `.codex`, `.cursor`
   or `.mcp.json`, stop.** That's not this repository as published: those folders can make an AI tool run commands
   as soon as it starts.

   If the post that sent you here names a commit (a 40-character code), switch to it now, before your AI reads
   anything: `git switch --detach <that code>`. Use the whole code, not the short form.
4. **Start your AI** in that folder: type `claude` (or `codex`).
5. **Ask it to explain, and change nothing yet.** Paste this:

   > Read AGENTS.md. Explain in plain words what this repo would do on my computer and what it would change.
   > Don't change anything yet.

6. **Then, one step at a time, when you're ready:**

   > Audit it the way AGENTS.md says, and tell me what you found.

   > Show me my numbers as a chart, without installing anything.

   > Set it up. Show me each change before you make it.

Your AI asks before every change, and every change has an undo. You can stop after any step.

## Start here if you know your way around

```sh
python3 -B -I -m unittest discover -s reference/tests     # the reference's tests (temp folders only)
python3 -B -I vectors/run.py                              # the golden vectors against the reference
d="$(mktemp -d)"; OPEN_METER_HOME="$d" python3 -B -I reference/meter.py chart --out meter-chart.svg; rm -rf "$d"   # a look: open meter-chart.svg

python3 -B -I reference/meter.py sync                     # first sync into ~/.open-meter (large histories: a minute)
claude mcp add --scope user open-meter -- "$(command -v python3)" -I "$PWD/reference/meter.py" mcp
python3 -B -I reference/meter.py statusline-install       # shows the change; add --apply to make it
python3 -B -I reference/meter.py ceiling
python3 -B -I reference/meter.py chart                    # writes ~/.open-meter/meter-chart.svg (--html adds the tables)
```

- **Requirements:** Python 3.9 or later, standard library only. No packages, no network, nothing running in the
  background.
- **MCP tools:** `meter_summary`, `meter_ceiling`, `meter_chart`, `meter_record_quota`, `meter_status` and `meter_yield`.
- **More:** the CLI has the same reports plus `chart` (one SVG or HTML file of the picture) and `dashboard` (the
  same page on 127.0.0.1). Codex, Cursor and Claude Desktop setup is in [docs/install.md](docs/install.md).
- **Sync early.** The meter keeps every response it has read, even after Claude Code deletes old transcripts (by
  default after 30 days). History it never read is gone, so run the first sync soon.
- **Your own version:** build it in any language from [SPEC.md](SPEC.md), then prove it:
  `python3 -B -I vectors/run.py -- <your command>`.

## What the numbers mean

**Rising or falling, at a glance.** Every report leads with a sentence and a small line chart (a sparkline in the
terminal):

- **Your plan's week:** what it's worth now, and how that compares with your first firm week in the last eight.
- **Your use:** your last 7 days of API-equivalent use against the 7 days before.

The rules for calling a trend are strict:

- **Use** is only "up" or "down" when it moved by 5% or more.
- **The plan's week** is only "up" or "down" when the two weeks' bands don't overlap. Otherwise the meter says
  "within the plan's rounding".

**API-equivalent cost** is what the same tokens would cost through the provider's API at list prices. It counts
every token kind at its own price: input, output, cache reads, and the short-lived and 1-hour cache writes.
[prices.json](prices.json) holds the prices, with their sources and the date they were checked (2026-10-26). It's
not what you paid. It's what the same work costs at the posted price.

**The implied ceiling** is the spend in your plan's window divided by the percent your plan says you've used. With
$10.00 of API-equivalent use and 37% used, the week holds about $27.03. Track it week by week. Every week is priced
with the same price list, so a change in the ceiling is a change in what the plan gives you, not in prices.

Read it with these limits in mind. The meter states them in its reports too.

- **It's a floor, not an exact figure.**
  - **Other use counts toward the percent.** Using the same plan elsewhere (claude.ai, the desktop app, another
    computer, Codex in the cloud) adds to the percent, while the logs here only hold this computer's use. The real
    ceiling is at least this high.
  - **The percent is a whole number.** The meter gives a band: the ceiling for any percent one point either side.
  - **Early readings are rough.** Readings under 20% aren't "firm".
- **Each account is kept apart.** If you switch between Claude accounts, each account's weeks are kept separate.
  Older logs don't say which account they belong to; with more than one account, that spend is shown as
  unattributed rather than guessed.
- **Changes that can't be judged aren't shown.**
  - **Early resets.** When a week overlaps the last one because the plan reset early, the meter says so instead of
    showing a change.
  - **Rounding.** When two weeks' bands overlap, the change may be only rounding, and the meter says that too.
- **New models have no price yet.** Their tokens are counted but get no price until `prices.json` lists them. Every
  report names them.

## Where the plan's percent comes from

- **Claude Code** passes your plan's used percent to its status line program every time the status line redraws.
  The meter's small wrapper saves each new reading, then runs the status line you already had, unchanged. It
  doesn't read your login token and doesn't call any private service.
- **Without the wrapper,** you can type a reading in by hand (`record-quota`).
- **At the limit,** Claude Code's own log records the moment a request was refused, which is a free "100% used"
  reading.
- **Codex** logs its plan's percents itself, so there's nothing to install for it.

## Safety

- **It only reads.** It reads your transcripts and never changes them.
- **It never touches credentials.** It never opens credential files or the Keychain, and it needs no keys.
- **It sends nothing.** The meter itself has no network code. When your AI reads the meter's output, that output
  goes to your AI provider like the rest of your chat.
- **It writes in one place.** Everything goes to `~/.open-meter/`. Each config change is shown first and has an
  undo: one MCP entry (added with Claude Code's or Codex's own command, or by the meter's installer for Cursor and
  Claude Desktop), and, if you want it, Claude Code's status line. Files the meter edits itself are backed up first.
- **Nothing runs in the background.** No services, no login items, no scheduled jobs. It does its work when you ask
  for a report. The one exception is the statusline wrapper, if you install it: it runs whenever Claude Code
  redraws its status line, the way your current status line already does.
- **Use the copy you audited.** The post that sent you here names a commit; `git rev-parse HEAD` shows yours. To
  update, `git fetch`, read `git diff HEAD origin/main` (or have your AI audit it), and only then
  `git merge --ff-only origin/main`. The MCP server and the status line run this folder's files, so a plain
  `git pull` would change what runs before anyone has read it.

The full threat model and the audit checklist: [docs/security.md](docs/security.md).

## Other tools

Good tools already exist. They make different trade-offs:

- **[ccusage](https://github.com/ccusage/ccusage)** reads the same local logs, for Claude Code, Codex and others,
  and has thorough reports. Its README runs it with `npx ccusage@latest`, which downloads and runs whatever version
  is newest at that moment.
- **[claumon](https://github.com/fabioconcina/claumon)** is a live dashboard with forecasts. Its README says its
  limit gauges come from Claude's usage API, read with the login credentials Claude Code stores on your machine.
  That makes them more precise than a whole-number percent, but it means reading those credentials.

This repo is for people who want the rules written down, tested against fixed answers, and running from code their
own AI has read.

## What's in here

| Path | What it is |
|---|---|
| [AGENTS.md](AGENTS.md), [CLAUDE.md](CLAUDE.md) | Instructions for your AI: explain, audit, ask, then build (two identical files) |
| [SPEC.md](SPEC.md) | The contract: sources, counting, prices, readings, ceilings, interfaces |
| [prices.json](prices.json) | API list prices, dated and sourced |
| [vectors/](vectors/) | Test cases with exact expected answers, and the runner for any implementation |
| [reference/](reference/) | The reference meter in Python 3.9+ (standard library only), and its tests |
| [docs/install.md](docs/install.md) | Setting it up in Claude Code, Codex, Cursor and Claude Desktop, and taking it out |
| [docs/level-2.md](docs/level-2.md) | For teams: OpenTelemetry and git yield |
| [docs/security.md](docs/security.md) | Threat model and audit |
| [docs/prd.md](docs/prd.md), [docs/adr/](docs/adr/), [docs/qa.md](docs/qa.md), [docs/decisions.md](docs/decisions.md) | Requirements, decisions, checks |

## License

MIT. See [LICENSE](LICENSE).
