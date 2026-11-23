#!/usr/bin/env python3
"""Keep the optional skills' discovery links in line with the activation knob.

Two YAML files decide which optional skills are on, the way settings.json and
settings.local.json do:

  skill-activation.yaml        shared defaults, tracked and shipped. Listing a skill
                               here is what makes it optional (knob-managed).
  skill-activation.local.yaml  local overrides, Git-ignored. People and campaigns edit it.

The merged value of a skill is the local value when there is one, else the shared
default. `sync` makes .agents/skills/<name> and .claude/skills/<name> match it:
an on skill gets the relative symlink ../../skills/<name>, an off skill loses it.
Skills that are not listed in the shared knob are never touched.

    python3 scripts/skill_activation.py status
    python3 scripts/skill_activation.py enable <skill> [--apply]
    python3 scripts/skill_activation.py disable <skill> [--apply]
    python3 scripts/skill_activation.py sync [--apply]
    python3 scripts/skill_activation.py check
    python3 scripts/skill_activation.py candidates
    python3 scripts/skill_activation.py campaign-open <instance> --skills a,b|none [--hitl ID]
    python3 scripts/skill_activation.py campaign-close <instance>
    python3 scripts/skill_activation.py campaign-abort <instance>
    python3 scripts/skill_activation.py campaign-record <instance> --skills a,b --retroactive

A campaign keeps its own record in campaigns/<instance>/activation.json: the skills it
turned on (`enabled`) and those already on when it opened (`pre_existing`). Closing it
turns off only the `enabled` skills that are still on.

MCP registrations are never written. For an on skill with an `mcp` entry the script
prints the registration instructions; `check` fails while an off skill is still registered
and prints removal instructions. It reads Claude Code, Codex and Antigravity CLI's
standard user and workspace MCP configuration files.

Output is JSON. Exit codes: 0 ok, 1 a check failed or the request was refused,
2 usage error or an unreadable knob or record.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tomllib

import yaml

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_DIRS = (".agents/skills", ".claude/skills")
SHARED = "skill-activation.yaml"
AGY_PROJECT_MCP = ".agents/mcp_config.json"
AGY_USER_MCP = ".gemini/config/mcp_config.json"
LOCAL = "skill-activation.local.yaml"
RECORD = "activation.json"
NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
LOCAL_HEADER = (
    "# Local overrides of skill-activation.yaml (Git-ignored). Values are on or off.\n"
    "# scripts/skill_activation.py and campaigns rewrite this file; comments are not kept.\n"
)


class Usage(Exception):
    """Bad arguments or an unreadable knob or record (exit 2)."""


class Refused(Exception):
    """A request the current state does not allow (exit 1)."""

    def __init__(self, reason: str, **fields):
        super().__init__(reason)
        self.payload = {"ok": False, "refused": reason, **fields}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- knob files ----------------------------------------------------------------

def read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise Usage(f"{path.name}: cannot read: {exc}") from None
    if not isinstance(data, dict):
        raise Usage(f"{path.name}: must be a mapping")
    return data


BLOCK_ENTRY = re.compile(r"^  ([a-z0-9][a-z0-9-]*):")


def block_names(text: str) -> set[str]:
    """Names on the two-space lines under `skills:`, the form the stdlib readers expect."""
    names, inside = set(), False
    for line in text.splitlines():
        if line.startswith("skills:"):
            inside = True
        elif line and not line.startswith((" ", "#")):
            inside = False
        elif inside and (match := BLOCK_ENTRY.match(line)):
            names.add(match.group(1))
    return names


def check_keys(data: dict, allowed: set[str], where: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise Usage(f"{where}: unknown keys {unknown}")
    if data.get("schema_version") != 1:
        raise Usage(f"{where}: schema_version must be 1")


def check_values(values: object, where: str) -> dict[str, bool]:
    if not isinstance(values, dict):
        raise Usage(f"{where}: skills must be a mapping of name to on/off")
    for name, value in values.items():
        if not isinstance(name, str) or not NAME.match(name):
            raise Usage(f"{where}: bad skill name {name!r}")
        if not isinstance(value, bool):
            raise Usage(f"{where}: {name} must be on or off, not {value!r}")
    return dict(values)


def load_shared(root: Path) -> dict:
    path = root / SHARED
    if not path.is_file():
        raise Usage(f"{SHARED} missing at {root}")
    data = read_yaml(path)
    check_keys(data, {"schema_version", "skills", "mcp", "setup"}, SHARED)
    skills = check_values(data.get("skills"), SHARED)
    if not skills:
        raise Usage(f"{SHARED}: skills is empty")
    if block_names(path.read_text(encoding="utf-8")) != set(skills):
        raise Usage(f"{SHARED}: write skills as a block with one '  <name>: on|off' line per skill "
                    f"(the adapter check and the public export read those lines)")
    for name in skills:
        if not (root / "skills" / name / "SKILL.md").is_file():
            raise Usage(f"{SHARED}: skills/{name}/SKILL.md does not exist")
    mcp = data.get("mcp") or {}
    if not isinstance(mcp, dict):
        raise Usage(f"{SHARED}: mcp must be a mapping")
    for name, spec in mcp.items():
        if name not in skills:
            raise Usage(f"{SHARED}: mcp names {name!r}, which is not in skills")
        if (not isinstance(spec, dict) or set(spec) != {"server", "command"} or not isinstance(spec["server"], str)
                or not NAME.match(spec["server"]) or not isinstance(spec["command"], list) or not spec["command"]
                or not all(isinstance(part, str) and part for part in spec["command"])):
            raise Usage(f"{SHARED}: mcp.{name} needs server (a name) and command (a list of strings)")
    setup = data.get("setup") or {}
    if not isinstance(setup, dict) or not all(
            name in skills and isinstance(steps, list) and all(isinstance(s, str) for s in steps)
            for name, steps in setup.items()):
        raise Usage(f"{SHARED}: setup must map listed skills to lists of strings")
    return {"skills": skills, "mcp": mcp, "setup": setup}


def load_local(root: Path, shared: dict) -> dict[str, bool]:
    path = root / LOCAL
    if not path.exists():
        return {}
    data = read_yaml(path)
    check_keys(data, {"schema_version", "skills"}, LOCAL)
    values = check_values(data.get("skills") or {}, LOCAL)
    unknown = sorted(set(values) - set(shared["skills"]))
    if unknown:
        raise Usage(f"{LOCAL}: {unknown} are not listed in {SHARED}")
    return values


def merge(shared: dict, local: dict[str, bool]) -> dict[str, bool]:
    return {name: local.get(name, default) for name, default in shared["skills"].items()}


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_local(root: Path, values: dict[str, bool]) -> None:
    lines = [LOCAL_HEADER, "schema_version: 1\n", "skills:\n" if values else "skills: {}\n"]
    lines += [f"  {name}: {'on' if values[name] else 'off'}\n" for name in sorted(values)]
    write_atomic(root / LOCAL, "".join(lines))


class Snapshot:
    """Bytes of the files a request changes, so a failed request can put them back."""

    def __init__(self, *paths: Path):
        self.saved = {path: path.read_bytes() if path.exists() else None for path in paths}

    def restore(self) -> None:
        for path, data in self.saved.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                write_atomic(path, data.decode("utf-8"))


# --- links ---------------------------------------------------------------------

def link_state(root: Path, base: str, name: str) -> str:
    path = root / base / name
    if not os.path.lexists(path):
        return "absent"
    if path.is_symlink() and os.readlink(path) == f"../../skills/{name}":
        return "linked"
    return "foreign"


def sync(root: Path, shared: dict, values: dict[str, bool], apply: bool) -> dict:
    actions, errors = [], []
    for name in sorted(shared["skills"]):
        for base in ADAPTER_DIRS:
            rel = f"{base}/{name}"
            state = link_state(root, base, name)
            if state == "foreign":
                errors.append(f"{rel} exists but is not the symlink ../../skills/{name}; not modified")
            elif values[name] and state == "absent":
                if apply:
                    (root / base).mkdir(parents=True, exist_ok=True)
                    (root / rel).symlink_to(f"../../skills/{name}", target_is_directory=True)
                actions.append({"path": rel, "action": "created" if apply else "would-create"})
            elif not values[name] and state == "linked":
                if apply:
                    (root / rel).unlink()
                actions.append({"path": rel, "action": "removed" if apply else "would-remove"})
    on = [name for name in sorted(shared["mcp"]) if values[name]]
    return {"actions": actions, "errors": errors, "mcp_register": {n: register_commands(root, shared, n) for n in on}}


# --- MCP (read only) -------------------------------------------------------------

def home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def mcp_registrations(root: Path) -> tuple[dict[str, list[str]], list[str]]:
    """Map each registered MCP server name to where it is registered; also unreadable files."""
    found: dict[str, list[str]] = {}
    unreadable = []

    def add(names, where):
        if isinstance(names, dict):
            for server in names:
                found.setdefault(server, []).append(where)

    claude = home() / ".claude.json"
    if claude.is_file():
        try:
            data = json.loads(claude.read_text(encoding="utf-8"))
            add(data.get("mcpServers"), "~/.claude.json (user)")
            project = (data.get("projects") or {}).get(str(root.resolve())) or {}
            add(project.get("mcpServers"), "~/.claude.json (local)")
        except (OSError, ValueError, AttributeError):
            unreadable.append("~/.claude.json")
    for path, where in ((root / ".mcp.json", ".mcp.json (project)"),
                        (root / AGY_PROJECT_MCP, f"{AGY_PROJECT_MCP} (project)"),
                        (home() / AGY_USER_MCP, f"~/{AGY_USER_MCP} (user)")):
        if path.is_file():
            try:
                config_text = path.read_text(encoding="utf-8")
                if path.name == "mcp_config.json" and not config_text:
                    continue  # Antigravity CLI treats an empty file as no registrations.
                add(json.loads(config_text).get("mcpServers"), where)
            except (OSError, ValueError, AttributeError):
                unreadable.append(where.split(" ", 1)[0])
    codex_home = os.environ.get("CODEX_HOME")
    codex_user = Path(codex_home) if codex_home else home() / ".codex"
    codex_user_label = "$CODEX_HOME/config.toml" if codex_home else "~/.codex/config.toml"
    for path, where in ((root / ".codex/config.toml", ".codex/config.toml (project)"),
                        (codex_user / "config.toml", f"{codex_user_label} (user)")):
        if path.is_file():
            try:
                add(tomllib.loads(path.read_text(encoding="utf-8")).get("mcp_servers"), where)
            except (OSError, ValueError):
                unreadable.append(where)
    return found, unreadable


def server_command(root: Path, shared: dict, name: str) -> list[str]:
    return [str(root.resolve() / part) if part.startswith("skills/") else part for part in shared["mcp"][name]["command"]]


def register_commands(root: Path, shared: dict, name: str) -> dict:
    server = shared["mcp"][name]["server"]
    command = server_command(root, shared, name)
    toml_args = ", ".join(json.dumps(part) for part in command[1:])
    return {
        "server": server,
        "claude": f"claude mcp add --scope local {server} -- {shlex.join(command)}",
        "codex": (f"[mcp_servers.{server}]\ncommand = {json.dumps(command[0])}\nargs = [{toml_args}]\n"
                  f"cwd = {json.dumps(str(root.resolve()))}"),
        "agy": {
            "path": AGY_PROJECT_MCP,
            "config": {"mcpServers": {
                server: {"command": command[0], "args": command[1:], "cwd": str(root.resolve())},
            }},
        },
        "note": "a person registers it; for agy, merge mcpServers into the specified file and start a new session",
    }


def remove_commands(server: str, places: list[str]) -> list[str]:
    hints = []
    for where in places:
        if where.startswith("~/.claude.json (local)"):
            hints.append(f"claude mcp remove --scope local {server}")
        elif where.startswith("~/.claude.json (user)"):
            hints.append(f"claude mcp remove --scope user {server}")
        elif where.startswith(".mcp.json"):
            hints.append(f"claude mcp remove --scope project {server}")
        elif where in (f"{AGY_PROJECT_MCP} (project)", f"~/{AGY_USER_MCP} (user)"):
            hints.append(f"remove mcpServers.{server} from {where.split(' ')[0]}")
        else:
            hints.append(f"delete the [mcp_servers.{server}] table from {where.split(' ')[0]}")
    return hints


# --- campaign records -------------------------------------------------------------

def instance_dir(campaigns: Path, instance: str) -> Path:
    if not NAME.match(instance):
        raise Usage(f"not an instance name: {instance!r}")
    path = campaigns / instance
    if not path.is_dir():
        raise Usage(f"{path}: instance folder missing")
    return path


def read_record(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Usage(f"{path}: cannot read: {exc}") from None
    if not isinstance(data, dict) or data.get("schema_version") != 1 or data.get("state") not in ("open", "closed") \
            or not all(isinstance(data.get(k), list) for k in ("requested", "enabled", "pre_existing")):
        raise Usage(f"{path}: not an activation record")
    return data


def records(campaigns: Path) -> dict[str, dict]:
    found = {}
    if campaigns.is_dir():
        for folder in sorted(p for p in campaigns.iterdir() if p.is_dir() and (p / RECORD).is_file()):
            found[folder.name] = read_record(folder / RECORD)
    return found


def active_instance(campaigns: Path) -> str:
    path = campaigns / "ACTIVE"
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def write_record(path: Path, record: dict) -> None:
    write_atomic(path, json.dumps(record, ensure_ascii=False, indent=2) + "\n")


def parse_skills(text: str, shared: dict) -> list[str]:
    if text.strip() == "none":
        return []
    names = []
    for part in text.split(","):
        name = part.strip()
        if not name:
            raise Usage("--skills: empty name; use a comma list or none")
        if name not in shared["skills"]:
            raise Usage(f"--skills: {name!r} is not an optional skill; choose from {sorted(shared['skills'])} or none")
        if name not in names:
            names.append(name)
    return names


def apply_change(root: Path, shared: dict, local: dict[str, bool], snapshot: Snapshot, after=None) -> dict:
    """Write the local knob, run `after` (record write), sync; restore everything on failure."""
    try:
        write_local(root, local)
        if after:
            after()
        result = sync(root, shared, merge(shared, local), apply=True)
    except OSError as exc:
        snapshot.restore()
        sync(root, shared, merge(shared, load_local(root, shared)), apply=True)
        raise Refused("write-failed", error=str(exc)) from None
    if result["errors"]:
        snapshot.restore()
        undo = sync(root, shared, merge(shared, load_local(root, shared)), apply=True)
        raise Refused("sync-failed", errors=result["errors"], restored=True, undo=undo["actions"])
    return result


def campaign_open(root: Path, campaigns: Path, instance: str, skills: str, hitl: str | None) -> dict:
    folder = instance_dir(campaigns, instance)
    shared = load_shared(root)
    requested = parse_skills(skills, shared)
    for name, record in records(campaigns).items():
        if name != instance and record["state"] == "open":
            raise Refused("another-campaign-open", campaign=name,
                          hint=f"close it first: pm.py close (with ACTIVE={name}) or "
                               f"python3 scripts/skill_activation.py campaign-close {name}")
    path = folder / RECORD
    if path.exists():
        raise Refused("already-recorded", record=str(path),
                      hint=f"if an earlier open failed half way, undo it: python3 scripts/skill_activation.py "
                           f"campaign-abort {instance}")
    local = load_local(root, shared)
    values = merge(shared, local)
    record = {"schema_version": 1, "campaign": instance, "state": "open", "requested": requested,
              "enabled": [n for n in requested if not values[n]], "pre_existing": [n for n in requested if values[n]],
              "hitl": hitl, "retroactive": False, "opened_utc": now(), "closed_utc": None,
              "turned_off": [], "left_as_changed": []}
    new_local = {**local, **{n: True for n in record["enabled"]}}
    result = apply_change(root, shared, new_local, Snapshot(root / LOCAL, path), lambda: write_record(path, record))
    return {"ok": True, "record": record, **result}


def campaign_close(root: Path, campaigns: Path, instance: str, abort: bool = False) -> dict:
    folder = instance_dir(campaigns, instance)
    path = folder / RECORD
    if not path.is_file():
        raise Refused("no-record", hint="nothing to turn off")
    record = read_record(path)
    if record["state"] != "open":
        raise Refused("already-closed")
    shared = load_shared(root)
    local = load_local(root, shared)
    values = merge(shared, local)
    enabled = [n for n in record["enabled"] if n in shared["skills"]]
    turned_off = [n for n in enabled if values[n]]
    left = [n for n in record["enabled"] if n not in turned_off]
    new_local = {**local, **{n: False for n in turned_off}}
    if abort:
        result = apply_change(root, shared, new_local, Snapshot(root / LOCAL, path), lambda: path.unlink())
        return {"ok": True, "aborted": instance, "turned_off": turned_off, **result}
    record.update(state="closed", closed_utc=now(), turned_off=turned_off, left_as_changed=left)
    result = apply_change(root, shared, new_local, Snapshot(root / LOCAL, path), lambda: write_record(path, record))
    return {"ok": True, "record": record, **result}


def campaign_record(root: Path, campaigns: Path, instance: str, skills: str) -> dict:
    folder = instance_dir(campaigns, instance)
    shared = load_shared(root)
    requested = parse_skills(skills, shared)
    for name, record in records(campaigns).items():
        if name != instance and record["state"] == "open":
            raise Refused("another-campaign-open", campaign=name)
    path = folder / RECORD
    if path.exists():
        raise Refused("already-recorded", record=str(path))
    values = merge(shared, load_local(root, shared))
    off = [n for n in requested if not values[n]]
    if off:
        raise Refused("not-on", skills=off, hint="a retroactive record lists skills that are on already")
    record = {"schema_version": 1, "campaign": instance, "state": "open", "requested": requested,
              "enabled": requested, "pre_existing": [], "hitl": None, "retroactive": True, "opened_utc": now(),
              "closed_utc": None, "turned_off": [], "left_as_changed": []}
    write_record(path, record)
    return {"ok": True, "record": record}


# --- reports -----------------------------------------------------------------------

def status(root: Path) -> dict:
    shared = load_shared(root)
    local = load_local(root, shared)
    values = merge(shared, local)
    registered, unreadable = mcp_registrations(root)
    rows = []
    for name in sorted(shared["skills"]):
        row = {"skill": name, "on": values[name], "shared": shared["skills"][name], "local": local.get(name),
               "links": {base: link_state(root, base, name) for base in ADAPTER_DIRS}}
        if name in shared["mcp"]:
            row["mcp"] = {"server": shared["mcp"][name]["server"],
                          "registered": registered.get(shared["mcp"][name]["server"], [])}
        rows.append(row)
    return {"skills": rows, "unreadable_mcp_config": unreadable}


def check(root: Path, campaigns: Path) -> dict:
    shared = load_shared(root)
    values = merge(shared, load_local(root, shared))
    problems = []
    plan = sync(root, shared, values, apply=False)
    problems += plan["errors"]
    problems += [f"{a['path']} does not match the knob ({a['action']}); run: python3 scripts/skill_activation.py "
                 f"sync --apply" for a in plan["actions"]]
    registered, unreadable = mcp_registrations(root)
    problems += [f"cannot read {where} to check MCP registrations" for where in unreadable]
    for name, spec in sorted(shared["mcp"].items()):
        places = registered.get(spec["server"], [])
        if not values[name] and places:
            problems.append(f"{name} is off but MCP server {spec['server']!r} is registered in {places}; "
                            f"remove it: {' ; '.join(remove_commands(spec['server'], places))}")
    active = active_instance(campaigns)
    for name, record in records(campaigns).items():
        if record["state"] == "open" and name != active:
            problems.append(f"campaigns/{name}/{RECORD} is open but ACTIVE is {active or 'empty'}; "
                            f"close it: python3 scripts/skill_activation.py campaign-close {name}")
    return {"status": "FAIL" if problems else "PASS", "problems": problems,
            "on": sorted(n for n, v in values.items() if v)}


def toggle(root: Path, name: str, on: bool, apply: bool) -> dict:
    shared = load_shared(root)
    if name not in shared["skills"]:
        raise Usage(f"not an optional skill: {name!r}; choose from {sorted(shared['skills'])}")
    local = load_local(root, shared)
    new_local = {**local, name: on}
    if apply:
        result = apply_change(root, shared, new_local, Snapshot(root / LOCAL))
    else:
        result = sync(root, shared, merge(shared, new_local), apply=False)
    out = {"mode": "apply" if apply else "dry-run", "skill": name, "on": on, **result}
    if on:
        out["next_steps"] = shared["setup"].get(name, [f"read skills/{name}/SKILL.md"])
    elif name in shared["mcp"]:
        server = shared["mcp"][name]["server"]
        places = mcp_registrations(root)[0].get(server, [])
        if places:
            out["mcp_remove"] = remove_commands(server, places)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--campaigns", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "check", "candidates"):
        sub.add_parser(name)
    p = sub.add_parser("sync")
    p.add_argument("--apply", action="store_true")
    for name in ("enable", "disable"):
        p = sub.add_parser(name)
        p.add_argument("skill")
        p.add_argument("--apply", action="store_true")
    p = sub.add_parser("campaign-open")
    p.add_argument("instance")
    p.add_argument("--skills", required=True)
    p.add_argument("--hitl", default=None)
    for name in ("campaign-close", "campaign-abort"):
        sub.add_parser(name).add_argument("instance")
    p = sub.add_parser("campaign-record")
    p.add_argument("instance")
    p.add_argument("--skills", required=True)
    p.add_argument("--retroactive", action="store_true", required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code not in (0, None) else 0
    root = args.root.resolve()
    campaigns = (args.campaigns or root / "campaigns").resolve()
    try:
        if args.command == "status":
            result = status(root)
        elif args.command == "check":
            result = check(root, campaigns)
        elif args.command == "candidates":
            shared = load_shared(root)
            values = merge(shared, load_local(root, shared))
            result = {"candidates": [{"skill": n, "on": values[n], "default": shared["skills"][n]}
                                     for n in sorted(shared["skills"])],
                      "open_records": sorted(n for n, r in records(campaigns).items() if r["state"] == "open")}
        elif args.command == "sync":
            shared = load_shared(root)
            result = sync(root, shared, merge(shared, load_local(root, shared)), apply=args.apply)
            result["mode"] = "apply" if args.apply else "dry-run"
        elif args.command in ("enable", "disable"):
            result = toggle(root, args.skill, args.command == "enable", args.apply)
        elif args.command == "campaign-open":
            result = campaign_open(root, campaigns, args.instance, args.skills, args.hitl)
        elif args.command in ("campaign-close", "campaign-abort"):
            result = campaign_close(root, campaigns, args.instance, abort=args.command == "campaign-abort")
        else:
            result = campaign_record(root, campaigns, args.instance, args.skills)
    except Usage as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    except Refused as exc:
        print(json.dumps(exc.payload, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    failed = result.get("status") == "FAIL" or bool(result.get("errors"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
