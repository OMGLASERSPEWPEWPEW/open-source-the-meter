# Setting up the meter, and taking it out

Each step is a separate change, and each needs its own yes. Your AI shows you what will change before it makes the
change. These steps use the reference implementation. A meter your AI built from [SPEC.md](../SPEC.md) follows the
same steps with its own command.

In every command below, `METER` stands for the absolute path to `reference/meter.py` in your clone, and `PY` for
the absolute path to Python 3.9 or later (`command -v python3`; on macOS, `/usr/bin/python3`). Absolute paths and
`-I` mean the meter always runs the code you audited, with no add-ons from your environment.

## 0. Look first (changes nothing)

```sh
d="$(mktemp -d)"
OPEN_METER_HOME="$d" "$PY" -B -I "$METER" chart --out meter-chart.svg   # open meter-chart.svg: the picture
OPEN_METER_HOME="$d" "$PY" -B -I "$METER" status
rm -rf "$d"
```

## 1. The first sync

```sh
"$PY" -B -I "$METER" sync
```

This creates `~/.open-meter/` (only you can read it) and reads your history into the ledger once. With a lot of
history it takes a while: about half a minute for 8 GB of transcripts. Later syncs read only what's new and take a
fraction of a second.

**Undo:** `rm -rf ~/.open-meter` (last, after the other undos).

## 2. The MCP server

Do this for the client you use. Each one runs `PY -I METER mcp`: no port and no network, just stdin and stdout.

### Claude Code

```sh
claude mcp add --scope user open-meter -- "$PY" -B -I "$METER" mcp
```

**Check:** `claude mcp list` shows `open-meter`. In a new session, ask "use meter_status".
**Undo:** `claude mcp remove open-meter --scope user`.

### Codex

```sh
codex mcp add open-meter -- "$PY" -B -I "$METER" mcp
```

**Check:** `codex mcp list`.
**Undo:** `codex mcp remove open-meter`.

### Cursor and Claude Desktop

These have no add command, so the meter's installer edits their config. It works like the statusline install:

```sh
"$PY" -B -I "$METER" mcp-install --client cursor              # shows the one entry and the file; changes nothing
"$PY" -B -I "$METER" mcp-install --client cursor --apply      # backs up the file, then adds only that entry
```

Use `--client claude-desktop` for Claude Desktop. The files:

- **Cursor:** `~/.cursor/mcp.json`.
- **Claude Desktop:** `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS,
  `%APPDATA%\Claude\claude_desktop_config.json` on Windows, `~/.config/Claude/claude_desktop_config.json` on Linux.

What the installer does:

- It never prints the rest of the file, which can hold other tools' keys. Don't open the file to look.
- It refuses a file that isn't valid JSON, or one that already has a different `open-meter` entry.
- After `--apply`, restart the app.

**Undo:** `"$PY" -B -I "$METER" mcp-uninstall --client cursor --apply`. It removes only that entry, after a backup.

## 3. The statusline wrapper (optional, Claude Code)

This records your plan's used percent each time it changes. It reads the numbers Claude Code already hands your
status line: no login token, no private service.

```sh
"$PY" -B -I "$METER" statusline-install            # shows the statusLine entry before and after; changes nothing
"$PY" -B -I "$METER" statusline-install --apply    # makes the change, after a backup
```

What `--apply` does:

1. It backs up `~/.claude/settings.json` (or `$CLAUDE_CONFIG_DIR/settings.json`) to `~/.open-meter/backups/`.
2. It changes only the `statusLine` entry. Any status line you already had becomes the wrapper's `--then`
   command, and runs exactly as before.
3. It refuses a settings file that isn't valid JSON, and changes nothing.

**Check:** your status line looks the same as before. After a reply or two, `"$PY" -B -I "$METER" status` lists
statusline readings.

**Undo:** `"$PY" -B -I "$METER" statusline-uninstall --apply`. It puts the old entry back, after another backup.

**Don't move or delete the clone** while the wrapper is installed: the status line runs `METER` from it. If it does
move, your old status line still shows, because the command falls back to it, but readings stop. Uninstall first.

**Without the wrapper,** record what your plan's usage screen shows, now and then:

```sh
"$PY" -B -I "$METER" record-quota 37 --resets-at 2026-11-20T19:00:00-06:00
```

## 4. The picture, any time

```sh
"$PY" -B -I "$METER" chart            # ~/.open-meter/meter-chart.svg: the headline and both line charts
"$PY" -B -I "$METER" chart --html     # ~/.open-meter/meter-chart.html: the same, with the detail tables
```

Both are one self-contained file with no scripts. Open them in any browser, or with Quick Look on a Mac. In an MCP
client, ask for `meter_chart`.

## 5. The dashboard (optional)

```sh
"$PY" -B -I "$METER" dashboard            # http://127.0.0.1:8787/ until you press Ctrl-C
```

It answers only on your own machine (127.0.0.1) and only to its own address. It runs no scripts and loads nothing
from the internet. Nothing starts it for you.

## 6. Keeping history longer (optional)

Claude Code deletes transcripts older than its `cleanupPeriodDays` setting (30 days by default). The meter's ledger
keeps everything it has already read, so a sync at least once a month loses nothing. To keep the transcripts
themselves longer, raise `cleanupPeriodDays` in Claude Code's settings yourself. The meter never changes that
setting.

## Taking it all out

1. `"$PY" -B -I "$METER" statusline-uninstall --apply`
2. Remove the MCP entries (step 2's undo, for each client: `claude mcp remove`, `codex mcp remove`, `mcp-uninstall`).
3. `rm -rf ~/.open-meter`
4. Delete the clone.
