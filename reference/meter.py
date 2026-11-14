#!/usr/bin/env python3
"""The meter, reference implementation. Python 3.9+ standard library only; reads local logs, sends nothing.

    python3 -I reference/meter.py --help
"""

import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)  # -I leaves the script's folder off the path; meterlib lives next to this file


def _statusline_fallback() -> int:
    """If the meter itself fails, the status line must still work: run the chained command unchanged."""
    import subprocess
    args = sys.argv[1:]
    data = sys.stdin.buffer.read()
    if "--then" in args and args.index("--then") + 1 < len(args):
        done = subprocess.run(args[args.index("--then") + 1], shell=True, input=data, check=False,
                              env=dict(os.environ, OPEN_METER_STATUSLINE="1"))
        return done.returncode
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["statusline"]:
        # The status line runs this on every redraw: load only the wrapper, and if even that fails, run the
        # original status line unchanged.
        try:
            from meterlib import statusline
        except Exception:
            sys.exit(_statusline_fallback())
        rest = sys.argv[2:]
        sys.exit(statusline.run(rest[1] if rest[:1] == ["--then"] and len(rest) > 1 else None))
    from meterlib.cli import main
    sys.exit(main())
