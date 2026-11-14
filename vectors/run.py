#!/usr/bin/env python3
"""Runs a meter implementation against the golden vectors (SPEC.md §12).

    python3 vectors/run.py                                  # the reference: python3 -I reference/meter.py
    python3 vectors/run.py -- node my-meter/index.js        # your own build, any language
    python3 vectors/run.py --case 03-codex -- ./my-meter    # one case

Each case is copied to a fresh temp folder with HOME, OPEN_METER_HOME, CLAUDE_CONFIG_DIR and CODEX_HOME pointed
inside it, so a run never touches your real logs or ledger. The implementation is called as

    <command> conformance --db <file> --tz=<tz> --prices <file> [--claude-dir <dir>] [--codex-dir <dir>]
              [--quota-file <file>] [--git-log <file> --repo <path> --since <iso> --until <iso>] [--now <iso>]

and must print one JSON document, compared to the case's expected.json (or expected-<n>.json per step) by value.
Python 3.9+ standard library only. Runs the command you give it; it runs nothing else.
"""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
CASES = os.path.join(HERE, "cases")


def diff(want, got, path="$", out=None):
    """Differences between two JSON values, by value (1 == 1.0), as 'path: want != got' lines."""
    out = [] if out is None else out
    if len(out) >= 20:
        return out
    if isinstance(want, dict) and isinstance(got, dict):
        for k in sorted(set(want) | set(got)):
            if k not in got:
                out.append("%s.%s: missing" % (path, k))
            elif k not in want:
                out.append("%s.%s: not expected (%s)" % (path, k, json.dumps(got[k])[:80]))
            else:
                diff(want[k], got[k], "%s.%s" % (path, k), out)
    elif isinstance(want, list) and isinstance(got, list):
        if len(want) != len(got):
            out.append("%s: %d items expected, got %d" % (path, len(want), len(got)))
        for i, (w, g) in enumerate(zip(want, got)):
            diff(w, g, "%s[%d]" % (path, i), out)
    elif isinstance(want, bool) or isinstance(got, bool):
        if want is not got:
            out.append("%s: %s != %s" % (path, json.dumps(want), json.dumps(got)))
    elif isinstance(want, (int, float)) and isinstance(got, (int, float)):
        if want != got:
            out.append("%s: %s != %s" % (path, want, got))
    elif want != got:
        out.append("%s: %s != %s" % (path, json.dumps(want)[:80], json.dumps(got)[:80]))
    return out


def overlay(src, dst):
    """Copy a step's files over the live folder (a longer file is an append, a shorter one a rewrite)."""
    for root, _dirs, files in os.walk(src):
        for name in files:
            rel = os.path.relpath(os.path.join(root, name), src)
            target = os.path.join(dst, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(os.path.join(root, name), target)


def run_case(name, command, update):
    case_dir = os.path.join(CASES, name)
    with open(os.path.join(case_dir, "case.json"), encoding="utf-8") as fh:
        case = json.load(fh)
    tmp = tempfile.mkdtemp(prefix="meter-vectors-")
    failures = []
    try:
        work = os.path.join(tmp, "case")
        shutil.copytree(case_dir, work, symlinks=False)
        steps = case.get("steps") or [None]
        live = os.path.join(tmp, "live") if case.get("steps") else work
        os.makedirs(live, exist_ok=True)
        for link in case.get("symlinks", []):
            path = os.path.join(live, link["link"])
            os.makedirs(os.path.dirname(path), exist_ok=True)
            os.symlink(link["target"], path)
        home = os.path.join(tmp, "home")
        os.makedirs(home)
        env = dict(os.environ, HOME=home, OPEN_METER_HOME=os.path.join(tmp, "meter-home"),
                   CLAUDE_CONFIG_DIR=os.path.join(home, "no-claude"), CODEX_HOME=os.path.join(home, "no-codex"))
        db = os.path.join(tmp, "meter.db")
        prices = os.path.normpath(os.path.join(case_dir, case.get("prices", "../../prices.json")))
        for i, step in enumerate(steps):
            if step:
                overlay(os.path.join(work, step), live)
            args = list(command) + ["conformance", "--db", db, "--tz=" + case.get("tz", "UTC"), "--prices", prices]
            for key, flag in (("claude_dir", "--claude-dir"), ("codex_dir", "--codex-dir"), ("quota_file", "--quota-file")):
                if case.get(key):
                    args += [flag, os.path.join(live, case[key])]
            if case.get("now"):
                args += ["--now", case["now"]]
            if case.get("yield"):
                y = case["yield"]
                args += ["--git-log", os.path.join(work, y["git_log"]), "--repo", y["repo"], "--since", y["since"],
                         "--until", y["until"]]
            label = "%s%s" % (name, " step %d" % (i + 1) if step else "")
            expected_path = os.path.join(case_dir, "expected-%d.json" % (i + 1) if step else "expected.json")
            try:
                done = subprocess.run(args, capture_output=True, env=env, timeout=60, cwd=tmp)
            except subprocess.TimeoutExpired:
                failures.append("%s: timed out" % label)
                break
            if done.returncode != 0:
                failures.append("%s: exit %d\n%s" % (label, done.returncode, done.stderr.decode("utf-8", "replace")[-2000:]))
                break
            try:
                got = json.loads(done.stdout)
            except ValueError:
                failures.append("%s: output isn't JSON" % label)
                break
            if update:
                with open(expected_path, "w", encoding="utf-8") as fh:
                    fh.write(json.dumps(got, indent=1, sort_keys=True) + "\n")  # ASCII: odd text stays visible as \\u escapes
                continue
            try:
                with open(expected_path, encoding="utf-8") as fh:
                    want = json.load(fh)
            except (OSError, ValueError) as exc:
                failures.append("%s: can't read %s (%s)" % (label, os.path.basename(expected_path), exc.__class__.__name__))
                break
            problems = diff(want, got)
            if problems:
                failures.append("%s:\n  %s" % (label, "\n  ".join(problems)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return failures


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--case", help="run one case by folder name")
    p.add_argument("--update", action="store_true", help="(maintainers) write the expected files from this command")
    p.add_argument("command", nargs=argparse.REMAINDER, help="-- then the implementation's command")
    a = p.parse_args()
    command = a.command[1:] if a.command[:1] == ["--"] else a.command
    if not command:
        command = [sys.executable, "-B", "-I", os.path.join(REPO, "reference", "meter.py")]
    elif len(command) == 1 and " " in command[0]:
        command = shlex.split(command[0])
    names = [a.case] if a.case else sorted(n for n in os.listdir(CASES) if os.path.isdir(os.path.join(CASES, n)))
    failed = 0
    for name in names:
        problems = run_case(name, command, a.update)
        print(("FAIL " if problems else "ok   ") + name)
        for problem in problems:
            print("  " + problem)
        failed += bool(problems)
    print("\n%d of %d cases pass." % (len(names) - failed, len(names)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
