"""Tests for the reference implementation, beyond the golden vectors. Standard library only:

    python3 -I -m unittest discover -s reference/tests

They use temp folders for everything (HOME, the meter's home, Claude Code's and Codex's folders) and never touch
the real ones. The dashboard test opens a socket on 127.0.0.1 only; nothing opens a window.
"""

import http.client
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import timezone

HERE = os.path.dirname(os.path.realpath(__file__))
REF = os.path.dirname(HERE)
REPO = os.path.dirname(REF)
sys.path.insert(0, REF)

from meterlib import analysis, mcp, statusline  # noqa: E402
from meterlib.app import Meter, MeterError  # noqa: E402
from meterlib.util import clean_text, parse_time_ms  # noqa: E402


class TempHome(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="meter-test-")
        self.old_env = dict(os.environ)
        os.environ["HOME"] = os.path.join(self.tmp, "home")
        os.environ["OPEN_METER_HOME"] = os.path.join(self.tmp, "meter-home")
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.tmp, "claude")
        os.environ["CODEX_HOME"] = os.path.join(self.tmp, "codex")
        os.makedirs(os.environ["HOME"])
        os.makedirs(os.path.join(self.tmp, "claude", "projects", "-work"))

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def meter(self):
        return Meter()

    def add_claude(self, lines, name="s1.jsonl"):
        path = os.path.join(self.tmp, "claude", "projects", "-work", name)
        with open(path, "a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(json.dumps(line) + "\n")


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def assistant(msg, at, out, session="s1", cwd="/work"):
    return {"type": "assistant", "sessionId": session, "timestamp": at, "cwd": cwd, "requestId": "r-" + msg,
            "message": {"id": msg, "model": "claude-opus-5-5", "usage": {"input_tokens": 0, "output_tokens": out}}}


class TextAndTime(unittest.TestCase):
    def test_clean_text_drops_hidden_characters(self):
        self.assertEqual(clean_text("a\u202eb\u200bc\x00d\ne\U000E0041f\uFE0Fg"), "a b c d e f g")
        self.assertEqual(len(clean_text("x" * 500, 64)), 64)

    def test_parse_time(self):
        self.assertEqual(parse_time_ms("2026-11-10T15:00:00Z"), parse_time_ms("2026-11-10T09:00:00-06:00"))
        self.assertEqual(parse_time_ms("2026-11-10T15:00:00.123456Z") % 1000, 123)
        self.assertIsNone(parse_time_ms("yesterday"))


class MCP(TempHome):
    def rpc(self, meter, *messages):
        stdin = io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in messages))
        stdout = io.StringIO()
        mcp.serve(meter, stdin, stdout)
        return [json.loads(line) for line in stdout.getvalue().splitlines()]

    def test_handshake_list_and_call(self):
        self.add_claude([assistant("m1", "2026-11-10T10:00:00Z", 100000)])
        out = self.rpc(self.meter(),
                       {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}}},
                       {"jsonrpc": "2.0", "method": "notifications/initialized"},
                       {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                       {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "meter_summary", "arguments": {"since": "2026-11-01", "until": "2026-11-30"}}},
                       {"jsonrpc": "2.0", "id": 4, "method": "nope"})
        self.assertEqual(len(out), 4)  # the notification gets no answer
        self.assertEqual(out[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual({t["name"] for t in out[1]["result"]["tools"]},
                         {"meter_summary", "meter_ceiling", "meter_record_quota", "meter_status", "meter_yield", "meter_chart"})
        self.assertEqual(out[2]["result"]["structuredContent"]["total_usd"], "2.00")
        text = out[2]["result"]["content"][0]["text"]
        self.assertTrue(text.startswith("You used"), "the trend sentence leads")
        self.assertIn("Data from this machine's local logs", text)
        self.assertEqual(out[3]["error"]["code"], -32601)

    def test_unknown_version_and_bad_input(self):
        out = self.rpc(self.meter(), {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                      "params": {"protocolVersion": "1999-01-01"}})
        self.assertEqual(out[0]["result"]["protocolVersion"], mcp.PROTOCOL_VERSIONS[0])
        stdout = io.StringIO()
        mcp.serve(self.meter(), io.BytesIO(b"{not json\n[1,2]\n"), stdout)
        codes = [json.loads(line)["error"]["code"] for line in stdout.getvalue().splitlines()]
        self.assertEqual(codes, [-32700, -32600])

    def test_tool_errors_are_plain(self):
        out = self.rpc(self.meter(), {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": "meter_record_quota", "arguments": {"percent": 0}}})
        self.assertTrue(out[0]["result"]["isError"])
        self.assertIn("percent", out[0]["result"]["content"][0]["text"])

    def test_output_is_bounded(self):
        big = {"rows": [{"group": "x" * 100} for _ in range(1000)]}
        fitted = mcp._fit(big)
        self.assertTrue(fitted["truncated"])
        self.assertLessEqual(len(json.dumps(fitted)), mcp.MAX_TEXT)


class Statusline(TempHome):
    INPUT = json.dumps({"session_id": "s1", "rate_limits": {"seven_day": {"used_percentage": 18, "resets_at": 1795258800},
                                                            "five_hour": {"used_percentage": 3, "resets_at": 1794700000}}}).encode()

    def test_capture_appends_only_changes_and_chains(self):
        out = io.BytesIO()
        code = statusline.run("printf 'hello '; cat >/dev/null; printf world; exit 3", io.BytesIO(self.INPUT), out)
        self.assertEqual((code, out.getvalue()), (3, b"hello world"))
        statusline.run(None, io.BytesIO(self.INPUT), io.BytesIO())  # same values: nothing new
        quota = os.path.join(os.environ["OPEN_METER_HOME"], "quota.jsonl")
        lines = [json.loads(line) for line in read_text(quota).splitlines()]
        self.assertEqual(sorted((r["window"], r["percent"], r["session"]) for r in lines),
                         [("five_hour", 3, "s1"), ("seven_day", 18, "s1")])
        self.assertEqual(os.stat(quota).st_mode & 0o777, 0o600)

    def test_broken_input_still_chains(self):
        out = io.BytesIO()
        self.assertEqual(statusline.run("cat", io.BytesIO(b"\xff not json"), out), 0)
        self.assertEqual(out.getvalue(), b"\xff not json")

    def test_own_line_without_a_chain(self):
        out = io.BytesIO()
        statusline.run(None, io.BytesIO(self.INPUT), out)
        self.assertEqual(out.getvalue(), "plan week 18% · 5h 3%\n".encode())

    def test_install_and_uninstall_round_trip(self):
        settings = os.path.join(self.tmp, "claude", "settings.json")
        before = {"model": "opus", "statusLine": {"type": "command", "command": "~/bin/my line.sh --x", "padding": 1}}
        with open(settings, "w") as fh:
            json.dump(before, fh)
        os.chmod(settings, 0o644)
        meter_py = os.path.join(REF, "meter.py")
        plan = statusline.plan_install(meter_py, settings)
        self.assertFalse(plan["already"])
        self.assertEqual(read_json(settings), before)  # a plan changes nothing
        done = statusline.apply_install(meter_py, settings)
        self.assertTrue(os.path.exists(done["backup"]))
        self.assertTrue(done["backup"].startswith(os.path.join(os.environ["OPEN_METER_HOME"], "backups")))
        now = read_json(settings)
        self.assertEqual(now["model"], "opus")
        self.assertEqual(now["statusLine"]["padding"], 1)
        self.assertTrue(statusline.is_ours(now["statusLine"]["command"]))
        self.assertIn("-I", now["statusLine"]["command"])
        self.assertEqual(os.stat(settings).st_mode & 0o777, 0o644)
        self.assertTrue(statusline.apply_install(meter_py, settings)["already"])  # never wraps twice
        statusline.apply_uninstall(settings)
        self.assertEqual(read_json(settings), before)

    def test_wrapper_runs_the_original_through_the_real_entry_point(self):
        settings = os.path.join(self.tmp, "claude", "settings.json")
        with open(settings, "w") as fh:
            json.dump({"statusLine": {"type": "command", "command": "printf '%s' \"it's mine\""}}, fh)
        statusline.apply_install(os.path.join(REF, "meter.py"), settings)
        cmd = read_json(settings)["statusLine"]["command"]
        done = subprocess.run(cmd, shell=True, input=self.INPUT, capture_output=True, timeout=30)
        self.assertEqual(done.stdout, b"it's mine")

    def test_refuses_invalid_settings(self):
        settings = os.path.join(self.tmp, "claude", "settings.json")
        for text in ('{"a": 1,}', '{"a": 1, "a": 2}', '{"a": NaN}'):
            with open(settings, "w") as fh:
                fh.write(text)
            with self.assertRaises(statusline.InstallError):
                statusline.apply_install(os.path.join(REF, "meter.py"), settings)
            self.assertEqual(read_text(settings), text)


class Quota(TempHome):
    def test_record_quota_rules(self):
        m = self.meter()
        with self.assertRaises(MeterError):
            m.record_quota(120, resets_at="2099-01-01T00:00:00Z")
        with self.assertRaises(MeterError):
            m.record_quota(40)  # no reset time known yet
        with self.assertRaises(MeterError):
            m.record_quota(40, resets_at="2020-01-01T00:00:00Z")
        with self.assertRaises(MeterError):
            m.record_quota(40, resets_at="2099-01-01T00:00:00Z")  # more than a week away


class Dashboard(TempHome):
    def test_host_check_and_read_only(self):
        from http.server import HTTPServer
        from meterlib import dashboard
        server = HTTPServer(("127.0.0.1", 0), None)
        port = server.server_address[1]
        server.RequestHandlerClass = dashboard.make_handler(self.meter(), port)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def get(host, method="GET", headers=None):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                conn.request(method, "/", headers=dict({"Host": host}, **(headers or {})))
                resp = conn.getresponse()
                body = resp.read()
                conn.close()
                return resp.status, resp.getheader("Content-Security-Policy"), body
            status, csp, body = get("127.0.0.1:%d" % port)
            self.assertEqual(status, 200)
            self.assertIn("default-src 'none'", csp)
            self.assertNotIn(b"<script", body)
            self.assertEqual(get("evil.example:%d" % port)[0], 421)
            self.assertEqual(get("localhost:%d" % port, headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
            self.assertEqual(get("localhost:%d" % port, method="POST")[0], 405)
            status, csp, _ = get("localhost:%d" % port, method="HEAD")
            self.assertEqual(status, 405)
            self.assertIn("default-src 'none'", csp)
        finally:
            server.shutdown()
            server.server_close()


@unittest.skipUnless(shutil.which("git"), "git isn't installed")
class GitYield(TempHome):
    def git(self, repo, *args, date="2026-11-10T10:00:00Z"):
        env = dict(os.environ, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date, GIT_AUTHOR_NAME="t",
                   GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
        subprocess.run(["git", "-C", repo] + list(args), check=True, capture_output=True, env=env)

    def test_real_git_log(self):
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        self.git(repo, "init", "-q")
        with open(os.path.join(repo, "a.py"), "w") as fh:
            fh.write("x\n" * 10)
        with open(os.path.join(repo, "package-lock.json"), "w") as fh:
            fh.write("{}\n" * 500)
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-q", "-m", "First")
        with open(os.path.join(repo, "b.py"), "w") as fh:
            fh.write("y\n" * 7)
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-q", "-m", "Second", date="2026-11-10T11:00:00Z")
        self.git(repo, "revert", "--no-edit", "HEAD", date="2026-11-10T12:00:00Z")
        self.add_claude([assistant("m1", "2026-11-10T10:30:00Z", 100000, cwd=os.path.realpath(repo))])
        y = self.meter().git_yield(repo, since="2026-11-09", until="2026-11-11T00:00:00Z")
        self.assertEqual((y["commits"], y["reverted_pairs"], y["lines_added"], y["generated_lines_skipped"],
                          y["spend_usd"], y["commits_per_dollar"]), (1, 1, 10, 500, "2.00", "0.50"))

    def test_refuses_a_folder_that_isnt_a_repo(self):
        with self.assertRaises(MeterError):
            self.meter().git_yield(self.tmp)


class Hardening(TempHome):
    """The first review, 2026-11-14: each finding that was fixed has a test here."""

    def rpc(self, *messages):
        stdin = io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in messages))
        stdout = io.StringIO()
        mcp.serve(self.meter(), stdin, stdout)
        return [json.loads(line) for line in stdout.getvalue().splitlines()]

    def call(self, name, args):
        return self.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})[0]

    def test_mcp_arguments_are_checked_against_the_schema(self):
        cases = [("meter_summary", {"group_by": "everything"}), ("meter_summary", {"nope": 1}),
                 ("meter_ceiling", {"count": "4"}), ("meter_ceiling", {"count": 999}),
                 ("meter_record_quota", {}), ("meter_record_quota", {"percent": 10, "window": "x"}),
                 ("meter_record_quota", {"percent": 10, "resets_at": 10 ** 19}),
                 ("meter_summary", {"since": "x" * 1000}), ("meter_unknown\u202e", {})]
        for name, args in cases:
            reply = self.call(name, args)["result"]
            self.assertTrue(reply["isError"], (name, args))
            self.assertNotIn("\u202e", reply["content"][0]["text"])
        self.assertFalse(os.path.exists(os.path.join(os.environ["OPEN_METER_HOME"], "quota.jsonl")))
        reply = self.call("meter_summary", {"since": "2026-13-45"})["result"]
        self.assertIn("since must be", reply["content"][0]["text"])

    def test_a_bad_value_in_a_log_never_stops_the_meter(self):
        self.add_claude([
            {"type": "assistant", "sessionId": "s1", "timestamp": "2026-11-10T10:00:00Z",
             "quotaLimits": {"status": "rejected", "resetsAt": 10 ** 19, "rateLimitType": "seven_day"},
             "message": {"id": "x", "model": "<synthetic>", "usage": {}}},
            dict(assistant("m1", "2026-11-10T10:01:00Z", 100000), cwd={"a": 1}),
            assistant("m2", "2026-11-10T10:02:00Z", 50000)])
        with open(os.path.join(self.tmp, "claude", "projects", "-work", "s1.jsonl"), "a") as fh:
            fh.write('{"usage":' + "[" * 5000 + "\n")
        m = self.meter()
        self.assertEqual(m.summary(since="2026-11-01", until="2026-11-30")["total_usd"], "3.00")
        self.assertEqual(m.status()["bad_lines"], 1)
        self.assertEqual(m.ceiling()["windows"], [])

    def test_quota_file_without_a_final_newline(self):
        from meterlib.util import append_private
        path = os.path.join(os.environ["OPEN_METER_HOME"], "quota.jsonl")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as fh:
            fh.write('{"a": 1}')
        append_private(path, '{"b": 2}')
        self.assertEqual(read_text(path), '{"a": 1}\n{"b": 2}\n')
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_meter_home_is_private(self):
        self.meter().sync()
        self.assertEqual(os.stat(os.environ["OPEN_METER_HOME"]).st_mode & 0o777, 0o700)

    def test_local_days_use_each_dates_own_offset(self):
        import time
        from meterlib.util import day_of, day_start_ms, parse_time_ms
        old = os.environ.get("TZ")
        os.environ["TZ"] = "America/Chicago"
        time.tzset()
        try:
            at = parse_time_ms("2026-10-31T05:30:00Z")  # 00:30 on Oct 31 in Chicago (CDT, before the change)
            self.assertEqual(day_of(at, None), "2026-10-31")
            self.assertEqual(day_start_ms(2026, 10, 31, None), parse_time_ms("2026-10-31T05:00:00Z"))
            self.assertEqual(day_start_ms(2026, 11, 2, None), parse_time_ms("2026-11-02T06:00:00Z"))  # CST
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()

    def test_wrapper_survives_a_moved_clone(self):
        settings = os.path.join(self.tmp, "claude", "settings.json")
        with open(settings, "w") as fh:
            json.dump({"statusLine": {"type": "command", "command": "printf kept"}}, fh)
        clone = os.path.join(self.tmp, "clone")
        shutil.copytree(REF, clone, ignore=shutil.ignore_patterns("__pycache__"))
        statusline.apply_install(os.path.join(clone, "meter.py"), settings)
        cmd = read_json(settings)["statusLine"]["command"]
        run = lambda: subprocess.run(cmd, shell=True, input=Statusline.INPUT, capture_output=True, timeout=30)
        self.assertEqual(run().stdout, b"kept")
        os.rename(clone, clone + "-moved")
        done = run()
        self.assertEqual((done.returncode, done.stdout), (0, b"kept"))

    def test_wrapper_without_original_and_a_moved_clone_prints_nothing(self):
        settings = os.path.join(self.tmp, "claude", "settings.json")
        clone = os.path.join(self.tmp, "clone")
        shutil.copytree(REF, clone, ignore=shutil.ignore_patterns("__pycache__"))
        statusline.apply_install(os.path.join(clone, "meter.py"), settings)
        cmd = read_json(settings)["statusLine"]["command"]
        shutil.rmtree(clone)
        done = subprocess.run(cmd, shell=True, input=Statusline.INPUT, capture_output=True, timeout=30)
        self.assertEqual((done.returncode, done.stdout), (0, b""))

    def test_settings_with_a_huge_number_are_refused(self):
        settings = os.path.join(self.tmp, "claude", "settings.json")
        with open(settings, "w") as fh:
            fh.write('{"x": 1e400}')
        with self.assertRaises(statusline.InstallError):
            statusline.apply_install(os.path.join(REF, "meter.py"), settings)

    def test_mcp_install_for_cursor_round_trip(self):
        from meterlib import mcpconfig
        config = os.path.join(self.tmp, "cursor-mcp.json")
        before = {"mcpServers": {"other": {"command": "x", "env": {"API_KEY": "secret"}}}, "theme": "dark"}
        with open(config, "w") as fh:
            json.dump(before, fh)
        plan = mcpconfig.plan_install("cursor", os.path.join(REF, "meter.py"), config)
        self.assertNotIn("secret", json.dumps(plan))  # only the one entry is shown
        self.assertEqual(read_json(config), before)
        done = mcpconfig.apply_install("cursor", os.path.join(REF, "meter.py"), config)
        self.assertTrue(os.path.exists(done["backup"]))
        now = read_json(config)
        self.assertEqual(now["mcpServers"]["other"], before["mcpServers"]["other"])
        self.assertEqual(now["mcpServers"]["open-meter"]["args"][-1], "mcp")
        self.assertTrue(mcpconfig.apply_install("cursor", os.path.join(REF, "meter.py"), config)["already"])
        mcpconfig.apply_uninstall("cursor", config)
        self.assertEqual(read_json(config), before)

    def test_closed_pipe_runs_the_original_once(self):
        marker = os.path.join(self.tmp, "runs")

        class Closed(io.BytesIO):
            def write(self, data):
                raise BrokenPipeError()

        code = statusline.run("echo x >> %s; printf out" % marker, io.BytesIO(Statusline.INPUT), Closed())
        self.assertEqual(code, 0)
        self.assertEqual(read_text(marker), "x\n")


class LastHoles(TempHome):
    """The security review's last findings, 2026-11-14."""

    def test_deeply_nested_message_is_a_parse_error_and_the_server_goes_on(self):
        stdin = io.BytesIO(b"[" * 200000 + b"\n" + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}).encode() + b"\n")
        stdout = io.StringIO()
        mcp.serve(self.meter(), stdin, stdout)
        replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual([r.get("error", {}).get("code") for r in replies], [-32700, None])
        self.assertEqual(replies[1]["result"], {})

    def test_an_over_long_line_is_skipped_once(self):
        from meterlib import ledger
        path = os.path.join(self.tmp, "claude", "projects", "-work", "s1.jsonl")
        old = ledger.MAX_LINE
        ledger.MAX_LINE = 1000  # small for the test; 32 MiB in use
        try:
            with open(path, "w") as fh:
                fh.write('{"usage": "' + "x" * 3000)  # no newline yet, already too long
            m = self.meter()
            self.assertEqual(m.sync().bad_lines, 1)
            with open(path, "a") as fh:
                fh.write("x" * 500 + '"}\n' + json.dumps(assistant("m1", "2026-11-10T10:00:00Z", 50000)) + "\n")
            stats = m.sync()
            self.assertEqual((stats.bad_lines, stats.new_events), (0, 1))  # the rest of the long line is skipped, not counted again
            self.assertEqual(m.summary(since="2026-11-01", until="2026-11-30")["total_usd"], "1.00")
        finally:
            ledger.MAX_LINE = old

    @unittest.skipUnless(shutil.which("git"), "git isn't installed")
    def test_yield_only_where_sessions_ran(self):
        repo = os.path.join(self.tmp, "elsewhere")
        os.makedirs(repo)
        subprocess.run(["git", "-C", repo, "init", "-q"], check=True, capture_output=True)
        with self.assertRaises(MeterError) as caught:
            self.meter().git_yield(repo)
        self.assertIn("no Claude Code or Codex session ran", str(caught.exception))


class RoundTwo(TempHome):
    """The second review, 2026-11-14."""

    def test_an_empty_original_command_survives_install_and_uninstall(self):
        settings = os.path.join(self.tmp, "claude", "settings.json")
        before = {"statusLine": {"type": "command", "command": "", "padding": 2}}
        with open(settings, "w") as fh:
            json.dump(before, fh)
        statusline.apply_install(os.path.join(REF, "meter.py"), settings)
        statusline.apply_uninstall(settings)
        self.assertEqual(read_json(settings), before)

    def test_configs_get_macos_stable_python(self):
        old = sys.executable
        try:
            sys.executable = "/Applications/Xcode.app/Contents/Developer/usr/bin/python3"
            expected = "/usr/bin/python3" if sys.platform == "darwin" and os.path.exists("/usr/bin/python3") else sys.executable
            self.assertEqual(statusline.stable_python(), expected)
            self.assertIn(expected, statusline.wrapper_command(os.path.join(REF, "meter.py"), None))
        finally:
            sys.executable = old

    def test_the_cli_shows_why_a_change_is_missing(self):
        from meterlib import cli
        case = os.path.join(REPO, "vectors", "cases", "02-ceilings")
        shutil.copytree(os.path.join(case, "claude"), os.path.join(self.tmp, "claude", "projects"), dirs_exist_ok=True)
        os.makedirs(os.environ["OPEN_METER_HOME"], exist_ok=True)
        shutil.copyfile(os.path.join(case, "quota.jsonl"), os.path.join(os.environ["OPEN_METER_HOME"], "quota.jsonl"))
        out = io.StringIO()
        import contextlib
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["ceiling", "--count", "9", "--tz", "UTC"]), 0)
        text = out.getvalue()
        for words in ("account unknown", "Unattributed $", "6.00", "an early reset", "(unknown)", "-15.5%", "not enough data"):
            self.assertIn(words, text)


class Picture(TempHome):
    """The charts and trends (SPEC.md §9.1, §11.3), beyond what vector 07 pins."""

    def test_sparkline_edges(self):
        self.assertEqual(analysis.sparkline([], True), "")
        self.assertEqual(analysis.sparkline([0, 0], True), "\u2581\u2581")
        self.assertEqual(analysis.sparkline([5, 5], False), "\u2584\u2584")
        self.assertEqual(analysis.sparkline([0, 7, 14], False), "\u2581\u2585\u2588")

    def test_spend_direction_thresholds(self):
        from meterlib.util import parse_time_ms
        now = parse_time_ms("2026-11-08T20:00:00Z")
        def trend(last, prior):
            evs = [{"source": "claude_code", "at": now - 3 * 86400000, "cost_nano": last, "key": "a"},
                   {"source": "claude_code", "at": now - 10 * 86400000, "cost_nano": prior, "key": "b"}]
            return analysis.spend_trend(evs, now, None)
        self.assertEqual(trend(105, 100)["direction"], "up")       # +5.0 is up
        self.assertEqual(trend(104, 100)["direction"], "flat")
        self.assertEqual(trend(95, 100)["direction"], "down")      # -5.0 is down
        self.assertIsNone(trend(10, 0)["direction"])               # nothing to compare with
        self.assertEqual(len(trend(1, 1)["days"]), 30)

    def test_within_rounding_is_never_called_a_fall(self):
        rows = [{"family": "claude", "window": "seven_day", "account": "a", "resets_at": "2026-10-04T19:00:00.000Z",
                 "ceiling_nano": 100, "ceiling_usd": "100.00", "band_low_usd": "95.00", "band_high_usd": "105.00",
                 "firm": True, "shown": True, "unattributed_usd": "0.00", "change_note": None},
                {"family": "claude", "window": "seven_day", "account": "a", "resets_at": "2026-10-11T19:00:00.000Z",
                 "ceiling_nano": 97, "ceiling_usd": "97.00", "band_low_usd": "92.00", "band_high_usd": "102.00",
                 "firm": True, "shown": True, "unattributed_usd": "0.00", "change_note": None}]
        t = analysis.ceiling_trends(rows)[0]
        self.assertEqual((t["change_pct"], t["direction"]), ("-3.0", "within rounding"))

    def _seeded(self):
        case = os.path.join(REPO, "vectors", "cases", "07-trends")
        shutil.copytree(os.path.join(case, "claude"), os.path.join(self.tmp, "claude", "projects"), dirs_exist_ok=True)
        os.makedirs(os.environ["OPEN_METER_HOME"], exist_ok=True)
        shutil.copyfile(os.path.join(case, "quota.jsonl"), os.path.join(os.environ["OPEN_METER_HOME"], "quota.jsonl"))
        from meterlib.util import parse_time_ms
        return Meter(tz="UTC", now=parse_time_ms("2026-11-08T20:00:00Z"))

    def test_chart_files_are_private_scriptless_and_exact(self):
        m = self._seeded()
        r = m.chart("svg")
        self.assertEqual(r["path"], os.path.join(os.environ["OPEN_METER_HOME"], "meter-chart.svg"))
        self.assertEqual(os.stat(r["path"]).st_mode & 0o777, 0o600)
        svg = read_text(r["path"])
        self.assertTrue(svg.startswith("<svg") and "<script" not in svg and "http://" not in svg.replace("http://www.w3.org/2000/svg", ""))
        self.assertIn("down 23.6% since Oct\u00a04", svg)
        self.assertIn("$76.36", svg)                      # the last point's tooltip carries the exact value
        self.assertEqual(r["spend_sparkline"], "\u2587" * 23 + "\u2588" * 7)
        page = read_text(m.chart("html")["path"])
        self.assertLess(page.index("<svg"), page.index("<table"), "the picture before the tables")
        self.assertNotIn("<script", page)

    def test_mcp_chart_tool_and_trend_lead(self):
        m = self._seeded()
        out = io.StringIO()
        mcp.serve(m, io.BytesIO(b"".join(json.dumps(x).encode() + b"\n" for x in (
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "meter_chart", "arguments": {"format": "svg"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "meter_ceiling", "arguments": {}}}))), out)
        chart, ceiling = [json.loads(line)["result"] for line in out.getvalue().splitlines()]
        self.assertTrue(chart["structuredContent"]["path"].endswith("meter-chart.svg"))
        self.assertTrue(chart["content"][0]["text"].startswith("Your plan's week is worth about $76"))
        self.assertTrue(ceiling["content"][0]["text"].startswith("Your plan's week is worth about $76 of API use, down 23.6%"))

    def test_cli_leads_with_the_trend(self):
        import contextlib
        from meterlib import cli
        self._seeded()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["chart", "--tz", "UTC", "--out", os.path.join(self.tmp, "c.svg")]), 0)
        self.assertIn("Wrote %s" % os.path.join(self.tmp, "c.svg"), out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["summary", "--tz", "UTC"]), 0)
        self.assertTrue(out.getvalue().startswith("You used"), out.getvalue()[:80])


class StrangerTest(TempHome):
    """Findings from a fresh session following the README on real logs, 2026-11-14."""

    def row(self, **kw):
        base = {"family": "claude", "window": "seven_day", "account": "", "resets_at": 0, "percent": 50, "at": 0,
                "source": "statusline"}
        base.update(kw)
        return base

    def test_an_early_or_mostly_unattributed_week_is_not_enough_data(self):
        from meterlib.util import parse_time_ms
        r = parse_time_ms("2026-11-13T19:00:00Z") // 1000
        ev = lambda acct, cost, key: {"source": "claude_code", "account": acct, "at": (r - 86400) * 1000, "cost_nano": cost, "key": key}
        rows = analysis.ceilings([ev("a", 1_000_000_000, "1"), ev("", 50_000_000_000, "2")],
                                 [self.row(account="a", resets_at=r, percent=100, at=(r - 3600) * 1000)])
        self.assertEqual((rows[0]["shown"], rows[0]["ceiling_usd"], rows[0]["band_low_usd"]), (False, None, None))
        self.assertIn("not enough data", rows[0]["change_note"])
        early = analysis.ceilings([ev("a", 1_000_000_000, "1")], [self.row(account="a", resets_at=r, percent=6, at=(r - 3600) * 1000)])
        self.assertEqual((early[0]["shown"], early[0]["ceiling_usd"]), (False, None))
        self.assertIn("under 20%", early[0]["change_note"])

    def test_an_early_reset_is_told_apart_from_two_schedules(self):
        from meterlib.util import parse_time_ms
        def r(s):
            return parse_time_ms(s) // 1000
        ev = [{"source": "claude_code", "account": "a", "at": (r("2026-11-01T00:00:00Z") + i * 21600) * 1000,
               "cost_nano": 1_000_000_000, "key": str(i)} for i in range(120)]
        def notes(resets):
            rows = analysis.ceilings(ev, [self.row(account="a", resets_at=x, percent=50, at=(x - 3600) * 1000) for x in resets])
            return [x["change_note"] for x in rows]
        early = notes([r("2026-11-07T11:00:00Z"), r("2026-11-09T13:00:00Z"), r("2026-11-16T13:00:00Z")])
        self.assertIn("early reset", early[1])
        mixed = notes([r("2026-11-07T11:00:00Z"), r("2026-11-09T13:00:00Z"), r("2026-11-14T11:00:00Z"), r("2026-11-16T13:00:00Z")])
        self.assertTrue(all("two reset schedules" in n for n in mixed[1:]), mixed)
        # A real early reset with two weeks on each side: Sundays, then a Wednesday reset, then Wednesdays.
        two_sides = notes([r("2026-10-04T19:00:00Z"), r("2026-10-11T19:00:00Z"), r("2026-10-18T19:00:00Z"),
                           r("2026-10-21T19:00:00Z"), r("2026-10-28T19:00:00Z"), r("2026-11-04T19:00:00Z")])
        self.assertIn("early reset", two_sides[3])
        # A reset time that moves a few seconds across the 10-minute grouping is one window, read twice.
        wobble = notes([r("2026-11-07T11:04:58Z"), r("2026-11-07T11:05:02Z")])
        self.assertIn("same window", wobble[1])

    def test_sync_and_status_count_readings_the_same_way_and_say_repeats_plainly(self):
        import contextlib
        from meterlib import cli
        self.add_claude([assistant("m1", "2026-11-10T10:00:00Z", 1000)] * 3)
        quota = os.path.join(os.environ["OPEN_METER_HOME"], "quota.jsonl")
        os.makedirs(os.path.dirname(quota), exist_ok=True)
        line = {"at": "2026-11-10T10:00:00Z", "source": "statusline", "family": "claude", "window": "seven_day",
                "percent": 30, "resets_at": 1795258800, "session": "s1"}
        with open(quota, "w") as fh:
            fh.write(json.dumps(line) + "\n" + json.dumps(line) + "\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["sync"])
        text = out.getvalue()
        self.assertIn("2 repeated log lines for one response, skipped so nothing counts twice", text)
        self.assertIn("1 plan reading", text)
        self.assertEqual(sum(r["count"] for r in self.meter().status()["readings"]), 1)


class RoundThree(TempHome):
    def test_out_takes_a_folder_and_only_the_right_ending(self):
        from meterlib.util import parse_time_ms
        m = Picture._seeded(self) if False else None
        case = os.path.join(REPO, "vectors", "cases", "07-trends")
        shutil.copytree(os.path.join(case, "claude"), os.path.join(self.tmp, "claude", "projects"), dirs_exist_ok=True)
        m = Meter(tz="UTC", now=parse_time_ms("2026-11-08T20:00:00Z"))
        self.assertTrue(m.chart("svg", os.path.join(self.tmp, "newdir") + "/")["path"].endswith("newdir/meter-chart.svg"))
        for bad in ("keep.txt", "meter.DB", "x.svg"):
            with self.assertRaises(MeterError):
                m.chart("html" if bad == "x.svg" else "svg", os.path.join(self.tmp, bad))

    def test_headline_names_the_account_when_the_chart_shows_others(self):
        from meterlib import charts
        text = charts.ceiling_sentence({"ceiling_usd": None, "others_plotted": True, "account": "bbbbbbbb"}, None)
        self.assertIn("account bbbbbbbb", text)
        svg = charts.ceiling_chart([{"account": "aaaaaaaa", "resets_at": "2026-11-06T19:00:00.000Z", "ceiling_usd": "32.00",
                                     "band_low_usd": "30.00", "band_high_usd": "34.00", "firm": True}], None,
                                   headline_account="bbbbbbbb")
        self.assertIn(">resets Friday<", svg, "a legend says whose line it is, in plain words")
        two = charts.account_names([{"account": "aaaaaaaa", "resets_at": "2026-11-06T19:00:00.000Z"},
                                    {"account": "bbbbbbbb", "resets_at": "2026-11-13T02:00:00.000Z"}], timezone.utc)
        self.assertEqual(two, {"aaaaaaaa": "account aaaaaaaa", "bbbbbbbb": "account bbbbbbbb"}, "a shared weekday names ids")
        pts = [{"account": "aaaaaaaa", "resets_at": "2026-11-09T13:00:00.000Z"},
               {"account": "bbbbbbbb", "resets_at": "2026-11-14T11:00:00.000Z"}]
        name = charts.headline_name("bbbbbbbb", pts, timezone.utc)
        self.assertEqual(name, "the account that resets Saturday")
        self.assertTrue(charts.ceiling_sentence({"ceiling_usd": "33.33", "change_pct": None}, timezone.utc, name)
                        .startswith("On the account that resets Saturday, your plan's week is worth about $33"))
        self.assertIsNone(charts.headline_name("aaaaaaaa", pts[:1], timezone.utc), "one account: no name needed")

    def test_a_week_still_open_is_drawn_as_so_far(self):
        from meterlib import charts
        from meterlib.util import parse_time_ms
        pts = [{"account": "aaaaaaaa", "resets_at": r, "ceiling_usd": c, "band_low_usd": c, "band_high_usd": c, "firm": True}
               for r, c in (("2026-11-07T11:00:00.000Z", "1904.24"), ("2026-11-14T11:00:00.000Z", "2537.37"),
                            ("2026-11-21T11:00:00.000Z", "2539.00"))]
        svg = charts.ceiling_chart(pts, timezone.utc, now_ms=parse_time_ms("2026-11-14T19:00:00Z"))
        self.assertEqual(svg.count('class="open s1"'), 1, "only the week still running is hollow")
        self.assertIn("$2,539 so far", svg)
        self.assertIn("This week so far, resetting Nov\u00a021", svg)
        self.assertNotIn("so far", charts.ceiling_chart(pts, timezone.utc), "no time given: nothing marked")


class AccountSwitch(TempHome):
    """SPEC.md §3.8: one sign-in moves every running session; reset schedules tell the accounts apart."""

    def _ledger(self):
        from meterlib import ledger
        conn = ledger.open_db(os.path.join(self.tmp, "switch.db"))
        conn.execute("INSERT INTO sessions VALUES ('s1', 'A'), ('s2', 'B'), ('s3', 'C')")
        conn.executemany("INSERT INTO files (path, kind, offset) VALUES (?, 'claude', 0)",
                         [("/x/projects/p/s1.jsonl",), ("/x/projects/q/s2.jsonl",), ("/y/projects/p/s3.jsonl",)])
        return ledger, conn

    def _read(self, conn, at, resets, session, pct=10):
        from meterlib.util import parse_time_ms
        conn.execute("INSERT INTO readings VALUES ('claude', 'seven_day', ?, ?, ?, '', ?, 'statusline', 1)",
                     (parse_time_ms(resets) // 1000, pct, session, parse_time_ms(at)))

    def _event(self, conn, key, session, at):
        from meterlib.util import parse_time_ms
        conn.execute("INSERT INTO events VALUES (?, 'claude_code', ?, '', 'p', 'claude-opus-5-5', ?, 0, 0, 0, 0, 1, 0, 0, "
                     "'standard', 'global')", (key, session, parse_time_ms(at)))

    def accounts(self, ledger, conn):
        return {e["key"]: e["account"] for e in ledger.all_events(conn)}

    def test_owner_lines_stand_until_a_session_sees_its_account_change(self):
        ledger, conn = self._ledger()
        self._read(conn, "2026-11-03T10:00:00Z", "2026-11-09T13:00:00Z", "s1")   # A: Mondays
        self._read(conn, "2026-11-08T10:00:00Z", "2026-11-14T11:00:00Z", "s2")   # B: Saturdays
        self._event(conn, "e1", "s1", "2026-11-08T12:00:00Z")
        self.assertFalse(ledger.Schedules(conn).active, "two accounts side by side: their owner lines are right")
        self.assertEqual(self.accounts(ledger, conn)["e1"], "A")
        self._read(conn, "2026-11-08T11:00:00Z", "2026-11-14T11:00:00Z", "s1")   # s1 now on B's schedule
        self.assertTrue(ledger.Schedules(conn).active)
        self.assertEqual(self.accounts(ledger, conn)["e1"], "B", "spend follows the latest reading's account")

    def test_two_folders_sharing_a_schedule_stay_apart(self):
        ledger, conn = self._ledger()   # s1 (A) in /x, s3 (C) in /y; then an early reset puts both on one schedule
        conn.execute("INSERT INTO sessions VALUES ('s4', 'A'), ('s5', 'A'), ('s6', 'C')")
        conn.executemany("INSERT INTO files (path, kind, offset) VALUES (?, 'claude', 0)",
                         [("/x/projects/p/s4.jsonl",), ("/x/projects/p/s5.jsonl",), ("/y/projects/p/s6.jsonl",)])
        self._read(conn, "2026-11-03T10:00:00Z", "2026-11-09T13:00:00Z", "s1")
        self._read(conn, "2026-11-03T11:00:00Z", "2026-11-05T08:00:00Z", "s3")
        for s in ("s4", "s5"):
            self._read(conn, "2026-11-11T19:00:00Z", "2026-11-18T18:00:00Z", s)
        self._read(conn, "2026-11-11T19:30:00Z", "2026-11-18T18:00:00Z", "s6")
        self._read(conn, "2026-11-11T20:00:00Z", "2026-11-18T18:00:00Z", "s3", 9)
        self._event(conn, "c_after", "s3", "2026-11-11T21:00:00Z")
        self.assertFalse(ledger.Schedules(conn).active, "each folder names its own schedules")
        self.assertEqual(self.accounts(ledger, conn)["c_after"], "C")
        self.assertEqual({r["account"] for r in ledger.all_readings(conn) if r["percent"] == 9}, {"C"})

    def test_an_unnamed_schedule_ends_a_run_and_manual_readings_change_nothing(self):
        ledger, conn = self._ledger()
        self._read(conn, "2026-11-03T10:00:00Z", "2026-11-09T13:00:00Z", "s1")   # A
        self._read(conn, "2026-11-04T10:00:00Z", "2026-11-07T11:00:00Z", "s2")   # B
        self._read(conn, "2026-11-04T11:00:00Z", "2026-11-07T11:00:00Z", "s1")   # s1 on B: a switch
        self._read(conn, "2026-11-05T09:00:00Z", "2026-11-11T09:00:00Z", "s1")   # A after an early reset: no voter
        self._event(conn, "unnamed", "s1", "2026-11-05T10:00:00Z")
        conn.execute("INSERT INTO readings VALUES ('claude', 'seven_day', ?, 50, '', 'A', ?, 'manual', 1)",
                     (1794229200, 1793794800000))   # A's schedule, typed at 2026-11-04T12:20Z
        self._event(conn, "after_manual", "s2", "2026-11-04T12:30:00Z")
        got = self.accounts(ledger, conn)
        self.assertEqual((got["unnamed"], got["after_manual"]), ("A", "B"), "§3.7 decides; a typed reading moves nothing")

    def test_a_folder_named_subagents_above_the_logs_is_fine(self):
        ledger, conn = self._ledger()
        conn.execute("UPDATE files SET path = '/home/u/subagents/.claude' || substr(path, 3) WHERE path LIKE '/x/%'")
        self._read(conn, "2026-11-03T10:00:00Z", "2026-11-09T13:00:00Z", "s1")
        self._read(conn, "2026-11-04T10:00:00Z", "2026-11-07T11:00:00Z", "s2")
        self._read(conn, "2026-11-04T11:00:00Z", "2026-11-07T11:00:00Z", "s1")
        sch = ledger.Schedules(conn)
        self.assertEqual(sch.folder("s1"), "/home/u/subagents/.claude/projects")
        self.assertTrue(sch.active)

    def test_a_reading_counts_for_seven_days_and_a_folder_keeps_its_own_sign_in(self):
        ledger, conn = self._ledger()
        self._read(conn, "2026-11-03T10:00:00Z", "2026-11-09T13:00:00Z", "s1")
        self._read(conn, "2026-11-08T10:00:00Z", "2026-11-14T11:00:00Z", "s2")
        self._read(conn, "2026-11-08T11:00:00Z", "2026-11-14T11:00:00Z", "s1")
        self._event(conn, "after_reset", "s1", "2026-11-14T12:00:00Z")   # B's week reset; B is still signed in
        self._event(conn, "stale", "s1", "2026-11-15T12:00:01Z")         # over seven days after the last reading
        self._event(conn, "other_folder", "s3", "2026-11-08T12:00:00Z")  # /y is signed in on its own
        got = self.accounts(ledger, conn)
        self.assertEqual((got["after_reset"], got["stale"], got["other_folder"]), ("B", "A", "C"))


class Vectors(unittest.TestCase):
    def test_reference_passes_every_vector(self):
        done = subprocess.run([sys.executable, "-B", "-I", os.path.join(REPO, "vectors", "run.py")], capture_output=True,
                              timeout=300)
        self.assertEqual(done.returncode, 0, done.stdout.decode() + done.stderr.decode())


class Sources(unittest.TestCase):
    def test_git_log_parser_and_generated_paths(self):
        commits = analysis.parse_git_log("\x1e" + "a" * 40 + "\x1f1\x1fOne\n\n3\t1\tsrc/x.py\n-\t-\tlogo.png\n")
        self.assertEqual(commits[0]["files"], [(3, 1, "src/x.py")])
        self.assertTrue(analysis.generated("web/node_modules/x/index.js"))
        self.assertTrue(analysis.generated("Cargo.lock"))
        self.assertFalse(analysis.generated("src/lockfile.py"))


if __name__ == "__main__":
    unittest.main()
