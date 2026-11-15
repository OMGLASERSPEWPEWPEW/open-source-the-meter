# The reference meter

A small implementation of [SPEC.md](../SPEC.md) in Python 3.9 or later, standard library only (`sqlite3`, `json`,
`http.server` for the optional dashboard). It passes every case in [vectors/](../vectors/). It's here so you can use
it as is, or hold your own version to the same answers.

```sh
python3 -B -I reference/meter.py --help
python3 -B -I -m unittest discover -s reference/tests
```

| File | What it does |
|---|---|
| `meter.py` | The entry point. In statusline mode, if anything fails it still runs your original status line |
| `meterlib/util.py` | Time, money and rounding (§2); cleaning text from logs before it's shown |
| `meterlib/sources.py` | Finding and reading Claude Code and Codex logs into events and plan readings (§3, §6) |
| `meterlib/prices.py` | Looking up a model's price and costing one event in nano-dollars (§5) |
| `meterlib/ledger.py` | The SQLite ledger and the lazy, incremental sync (§4, §7) |
| `meterlib/analysis.py` | Totals, days, ceilings, git yield, the conformance document (§8, §10, §12) |
| `meterlib/app.py` | The operations the CLI, MCP server and dashboard share (§9), including the git call |
| `meterlib/mcp.py` | The MCP server over stdin and stdout (§11.1) |
| `meterlib/cli.py` | The command line (§11.2) |
| `meterlib/charts.py` | The chart page as HTML or one SVG (cards and a line chart per account), the dashboard's spend chart, and the trend sentences (§9.1, §11.3) |
| `meterlib/dashboard.py` | The optional page on 127.0.0.1 (§11.3) |
| `meterlib/statusline.py` | The statusline wrapper and its install and undo (§6.2, §11.4) |
| `meterlib/mcpconfig.py` | Adding the MCP server to Cursor or Claude Desktop, and taking it out (§11.4) |
| `tests/test_meter.py` | Tests beyond the vectors, all in temp folders: MCP and its argument checks, the wrapper (a moved clone, a closed pipe), install and undo for the status line and for Cursor, the dashboard, a real `git log`, bad values in logs, the local time zone, a plan reset inside a week, and the chart page |

Only `meter.py`, `meterlib/statusline.py` and `meterlib/app.py` start a process. They run your own original status
line and `git log`. Only `meterlib/dashboard.py` listens, on 127.0.0.1, while you run it.
