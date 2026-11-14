#!/usr/bin/env python3
"""Draws docs/meter-chart.svg, the README's picture, from the synthetic case 07-trends (never anyone's real logs).

    python3 -I vectors/readme_chart.py

It copies the case into a temp folder, reads it with the reference meter at the case's own `now`, and writes the
same SVG that `meter.py chart` writes. Standard library only; runs nothing else.
"""

import json
import os
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True  # leave no __pycache__ in the clone
HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "reference"))

from meterlib.app import Meter  # noqa: E402
from meterlib.util import parse_time_ms  # noqa: E402

CASE = os.path.join(HERE, "cases", "07-trends")


def main() -> int:
    with open(os.path.join(CASE, "case.json"), encoding="utf-8") as fh:
        case = json.load(fh)
    tmp = tempfile.mkdtemp(prefix="meter-readme-")
    try:
        shutil.copytree(os.path.join(CASE, "claude"), os.path.join(tmp, "projects"))
        os.environ["OPEN_METER_HOME"] = os.path.join(tmp, "home")
        meter = Meter(db_path=os.path.join(tmp, "meter.db"), claude_roots=[os.path.join(tmp, "projects")], codex_roots=[],
                      quota_file=os.path.join(CASE, "quota.jsonl"), prices_path=os.path.join(HERE, "prices.json"),
                      tz="UTC", now=parse_time_ms(case["now"]))
        out = os.path.join(REPO, "docs", "meter-chart.svg")
        result = meter.chart("svg", out, caption="Synthetic data (vectors/cases/07-trends), not anyone's real use. "
                                                 "Draw yours with: python3 -B -I reference/meter.py chart")
        print("wrote", os.path.relpath(result["path"], REPO))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
