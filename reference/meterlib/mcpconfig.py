"""Adding the meter's MCP server to a client that has no `mcp add` command of its own (Cursor, Claude Desktop), and
taking it out again (SPEC.md §11.4). Like the statusline install: a dry run first, a backup, one key changed,
an atomic write. It prints the one entry and the file's path, never the rest of the file, which can hold keys.
"""

from __future__ import annotations

import os
import sys

from .statusline import InstallError, _load_settings, backup, stable_python, write_json_atomic

NAME = "open-meter"


def config_path(client: str) -> str:
    home = os.path.expanduser("~")
    if client == "cursor":
        return os.path.join(home, ".cursor", "mcp.json")
    if client == "claude-desktop":
        if sys.platform == "darwin":
            return os.path.join(home, "Library", "Application Support", "Claude", "claude_desktop_config.json")
        if sys.platform.startswith("win"):
            return os.path.join(os.environ.get("APPDATA", home), "Claude", "claude_desktop_config.json")
        return os.path.join(home, ".config", "Claude", "claude_desktop_config.json")
    raise InstallError("client must be cursor or claude-desktop (Claude Code and Codex have their own mcp add)")


def entry(meter_py: str, python=None) -> dict:
    return {"command": python or stable_python(), "args": ["-I", os.path.realpath(meter_py), "mcp"]}


def _servers(obj: dict, path: str) -> dict:
    servers = obj.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise InstallError("mcpServers in %s isn't an object; edit it by hand. Nothing was changed." % path)
    return servers


def plan_install(client: str, meter_py: str, path=None, python=None) -> dict:
    path = os.path.realpath(path or config_path(client))
    obj, _raw, _mode = _load_settings(path)
    servers = _servers(obj, path)
    want = entry(meter_py, python)
    if NAME in servers:
        if servers[NAME] == want:
            return {"path": path, "already": True, "entry": want}
        raise InstallError("%s already has an %s entry that runs something else; remove it first. Nothing was changed."
                           % (path, NAME))
    return {"path": path, "already": False, "entry": want}


def apply_install(client: str, meter_py: str, path=None, python=None) -> dict:
    path = os.path.realpath(path or config_path(client))
    plan = plan_install(client, meter_py, path, python)
    if plan["already"]:
        return plan
    obj, raw, mode = _load_settings(path)
    plan["backup"] = backup(path)
    servers = _servers(obj, path)
    servers[NAME] = plan["entry"]
    obj["mcpServers"] = servers
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_json_atomic(path, obj, mode if mode is not None else 0o600, expect=raw)
    return plan


def apply_uninstall(client: str, path=None, dry_run: bool = False) -> dict:
    path = os.path.realpath(path or config_path(client))
    obj, raw, mode = _load_settings(path)
    servers = _servers(obj, path)
    if NAME not in servers:
        return {"path": path, "installed": False}
    result = {"path": path, "installed": True, "removed": servers[NAME]}
    if dry_run:
        return result
    result["backup"] = backup(path)
    del servers[NAME]
    write_json_atomic(path, obj, mode if mode is not None else 0o600, expect=raw)
    return result
