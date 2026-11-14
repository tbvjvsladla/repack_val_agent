#!/usr/bin/env python3
"""Check a campaign instance or the campaign template (see campaigns/README.md).

The check is read-only. It reads one point in time and never runs a declared
proof.check command.

Rules (name, what fails):
  proof-blank              proof.check or proof.expect is empty
  source-missing           state is done/failed but proof.source is empty
  state-invalid            state is not pending/running/done/failed
  done-not-ok              state is done but proof.ok is not true
  undeclared               a phase file or cell folder the declaration does not name
  cell-summary-incomplete  cell.md lacks one of its six sections, or a section is empty
  journey-invalid          a journey line is not a JSON object, lacks utc/event,
                           or its time goes backward
  pointer-invalid          an evidence pointer has a key outside kind/path/cell/note,
                           lacks kind/path, or its path does not exist
  declared-missing         a declared cell lacks its folder, cell.md or a phase file
  placeholder-left         (instance only) a placeholder or the skeleton cell remains
  no-cells                 (instance only) the declaration has no cells
  done-unrecorded          (instance only) a phase is done but the executor record
                           (by) is absent or its log file is missing
  closed-without-gate      (instance only) a cell is closed but its group's gate is
                           not passed or does not list the cell as a member
  structure-unlogged       (instance only) replaying campaign-open, cell-add,
                           cell-cancel and regroup events does not give the present
                           cell/group layout (skipped when campaign-open is absent)
  auto-cap-exceeded        (instance only) more auto cells than budgets.auto_cells
                           plus the allowed auto-cell-cap answers
  check-duplicate          (instance only) two checks share an id, or share
                           cmd, cwd and env
  ref-invalid              (instance only) a group, check, covers, parent or needs
                           reference names something the declaration lacks
  gate-not-proven          (instance only) a gate is passed but a check result is
                           not PASS/reused/covered or its receipt file is missing

The declaration must be schema_version 2; anything else is unreadable (exit 2).

Template mode applies when the target is campaigns/_template or --template is
given; every other target is checked as an instance. The mode never depends on
file contents, so a copied template is checked as an instance.

Before checking the target the script runs a negative control: it builds a valid
instance from the real template in a temporary directory, requires it to pass,
and requires each single-defect copy to fail with exactly its own rule.

Exit codes: 0 pass, 1 rule failure (or failed control), 2 usage or unreadable input.
Not checked: edits to existing journey lines, whether a proof.check can really
fail, whether a receipt is truthful, and whether review rounds stayed within the
cap. The executor (skills/progress-monitor/scripts/pm.py) writes these files; this
check only reads them.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDER = "<<FILL>>"
SKELETON_CELL = "_cell"
STATES = ("pending", "running", "done", "failed")
REVIEW = ("none", "user-request", "high-impact", "judgment")
SECTIONS = ("목표", "확인된 사실", "적용한 변경", "검증", "막다른 길", "남은 계획")
POINTER_KEYS = {"kind", "path", "cell", "note"}
DECLARATION_TEXT = ("id", "goal", "plan_ref", "checklist_ref", "declared")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
STATUS_SUFFIX = ".status.json"
SCHEMA_VERSION = 2
CELL_STATES = ("open", "closed", "cancelled", "stopped", "waiting")
ORIGINS = ("plan", "auto")
EXECUTOR_TOOL = "progress-monitor"
DEFAULT_AUTO_CELLS = 5
FINAL_GATE = "_final"
RULES = (
    "proof-blank", "source-missing", "state-invalid", "done-not-ok", "undeclared",
    "cell-summary-incomplete", "journey-invalid", "pointer-invalid",
    "declared-missing", "placeholder-left", "no-cells",
    "done-unrecorded", "closed-without-gate", "structure-unlogged", "auto-cap-exceeded",
    "check-duplicate", "ref-invalid", "gate-not-proven",
)


class Unreadable(Exception):
    """The input cannot be interpreted at all (exit code 2)."""


def blank(value: object) -> bool:
    return not isinstance(value, str) or not value.strip()


def load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise Unreadable(f"{path}: missing") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise Unreadable(f"{path}: not JSON ({exc})") from None


def read_declaration(target: Path, template: bool) -> dict:
    decl = load_json(target / "campaign.json")
    if not isinstance(decl, dict):
        raise Unreadable("campaign.json: must be a JSON object")
    if decl.get("schema_version") != SCHEMA_VERSION:
        raise Unreadable(f"campaign.json: schema_version must be {SCHEMA_VERSION}, "
                         f"got {decl.get('schema_version')!r}")
    for key in DECLARATION_TEXT:
        if blank(decl.get(key)):
            raise Unreadable(f"campaign.json: {key} must be a non-empty string")
    phases = decl.get("phases")
    if (not isinstance(phases, list) or not phases or len(set(map(str, phases))) != len(phases)
            or not all(isinstance(p, str) and NAME.match(p) for p in phases)):
        raise Unreadable("campaign.json: phases must be a non-empty list of distinct names")
    review = decl.get("review")
    cap = review.get("round_cap") if isinstance(review, dict) else None
    if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1:
        raise Unreadable("campaign.json: review.round_cap must be a positive integer")
    if not isinstance(decl.get("budgets", {}), dict):
        raise Unreadable("campaign.json: budgets must be an object")
    target_decl = decl.get("target")
    if (not isinstance(target_decl, dict) or blank(target_decl.get("root"))
            or not isinstance(target_decl.get("env_probe", []), list)):
        raise Unreadable("campaign.json: target needs root and an env_probe list")
    checks = decl.get("checks")
    if not isinstance(checks, list):
        raise Unreadable("campaign.json: checks must be a list")
    for entry in checks:
        if not isinstance(entry, dict) or any(blank(entry.get(k)) for k in ("id", "cmd", "expect")):
            raise Unreadable("campaign.json: each check needs id, cmd and expect")
        if not isinstance(entry.get("cwd", "."), str):
            raise Unreadable(f"campaign.json: check {entry['id']!r} cwd must be a string")
        for key in ("inputs", "env", "covers"):
            if not isinstance(entry.get(key, []), list):
                raise Unreadable(f"campaign.json: check {entry['id']!r} {key} must be a list")
    groups = decl.get("groups")
    if not isinstance(groups, list):
        raise Unreadable("campaign.json: groups must be a list")
    group_names = []
    for entry in groups:
        if (not isinstance(entry, dict) or blank(entry.get("group"))
                or not isinstance(entry.get("checks"), list)):
            raise Unreadable("campaign.json: each group needs group and a checks list")
        if PLACEHOLDER not in entry["group"] and not NAME.match(entry["group"]):
            raise Unreadable(f"campaign.json: group name {entry['group']!r} is not a plain file name")
        group_names.append(entry["group"])
    if len(set(group_names)) != len(group_names):
        raise Unreadable("campaign.json: group names must be distinct")
    final = decl.get("final")
    if not isinstance(final, dict) or not isinstance(final.get("checks"), list):
        raise Unreadable("campaign.json: final needs a checks list")
    cells = decl.get("cells")
    if not isinstance(cells, list):
        raise Unreadable("campaign.json: cells must be a list")
    names = []
    for entry in cells:
        if (not isinstance(entry, dict) or blank(entry.get("cell")) or blank(entry.get("goal"))
                or blank(entry.get("group"))):
            raise Unreadable("campaign.json: each cell needs cell, goal and group")
        name = entry["cell"]
        if entry.get("state", "open") not in CELL_STATES:
            raise Unreadable(f"campaign.json: cell {name!r} state must be one of {', '.join(CELL_STATES)}")
        if entry.get("origin", "plan") not in ORIGINS:
            raise Unreadable(f"campaign.json: cell {name!r} origin must be plan or auto")
        if entry.get("parent") is not None and not isinstance(entry.get("parent"), str):
            raise Unreadable(f"campaign.json: cell {name!r} parent must be a cell name or null")
        if PLACEHOLDER not in name and not NAME.match(name):
            raise Unreadable(f"campaign.json: cell name {name!r} is not a plain folder name")
        if entry.get("review", "none") not in REVIEW:
            raise Unreadable(f"campaign.json: cell {name!r} review must be one of {', '.join(REVIEW)}")
        if not isinstance(entry.get("needs", []), list):
            raise Unreadable(f"campaign.json: cell {name!r} needs must be a list")
        names.append(name)
    real = [n for n in names if PLACEHOLDER not in n]
    if len(set(real)) != len(real):
        raise Unreadable("campaign.json: cell names must be distinct")
    return decl


def check_phase_file(path: Path, rel: str, add, instance: Path | None = None) -> None:
    data = load_json(path)
    if not isinstance(data, dict):
        raise Unreadable(f"{rel}: must be a JSON object")
    state = data.get("state")
    proof = data.get("proof") if isinstance(data.get("proof"), dict) else {}
    if state not in STATES:
        add("state-invalid", rel, f"state is {state!r}")
    empty = [key for key in ("check", "expect") if blank(proof.get(key))]
    if empty:
        add("proof-blank", rel, f"proof.{' and proof.'.join(empty)} empty")
    if state in ("done", "failed") and blank(proof.get("source")):
        add("source-missing", rel, f"state is {state} but proof.source is empty")
    if state == "done" and proof.get("ok") is not True:
        add("done-not-ok", rel, "state is done but proof.ok is not true")
    if state == "done" and instance is not None:
        by = data.get("by")
        if not isinstance(by, dict) or by.get("tool") != EXECUTOR_TOOL or blank(by.get("log")):
            add("done-unrecorded", rel, "state is done but the executor record (by) is absent")
        elif not (instance / by["log"]).is_file():
            add("done-unrecorded", rel, f"the executor log does not exist: {by['log']}")


def check_cell_summary(path: Path, rel: str, add) -> None:
    bodies: dict[str, list[str]] = {}
    current = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            bodies.setdefault(current, [])
        elif current is not None:
            bodies[current].append(line)
    for section in SECTIONS:
        if section not in bodies:
            add("cell-summary-incomplete", rel, f"section '{section}' missing")
        elif not "".join(bodies[section]).strip():
            add("cell-summary-incomplete", rel, f"section '{section}' is empty")


def check_cell(cell_dir: Path, rel: str, phases: list[str], add, instance: Path | None = None) -> None:
    summary = cell_dir / "cell.md"
    if summary.is_file():
        check_cell_summary(summary, f"{rel}/cell.md", add)
    else:
        add("declared-missing", f"{rel}/cell.md", "cell.md missing")
    phase_dir = cell_dir / "phases"
    present = {}
    for path in sorted(phase_dir.iterdir()) if phase_dir.is_dir() else []:
        if path.is_file() and path.name.endswith(STATUS_SUFFIX):
            present[path.name[: -len(STATUS_SUFFIX)]] = path
        else:
            add("undeclared", f"{rel}/phases/{path.name}", "not a phase status file")
    for name, path in present.items():
        if name not in phases:
            add("undeclared", f"{rel}/phases/{path.name}", f"phase '{name}' is not declared")
        else:
            check_phase_file(path, f"{rel}/phases/{path.name}", add, instance)
    for phase in phases:
        if phase not in present:
            add("declared-missing", f"{rel}/phases/{phase}{STATUS_SUFFIX}", f"no status file for phase '{phase}'")


def check_journey(path: Path, add) -> None:
    rel = "journey.jsonl"
    if not path.is_file():
        add("journey-invalid", rel, "journey.jsonl missing")
        return
    previous = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        where = f"{rel}:{number}"
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            add("journey-invalid", where, "line is not JSON")
            continue
        if not isinstance(event, dict) or blank(event.get("utc")) or blank(event.get("event")):
            add("journey-invalid", where, "line needs utc and event")
            continue
        try:
            moment = datetime.fromisoformat(event["utc"].replace("Z", "+00:00"))
        except ValueError:
            add("journey-invalid", where, "utc is not an ISO 8601 time")
            continue
        if moment.tzinfo is None:
            add("journey-invalid", where, "utc needs a time zone")
            continue
        if previous is not None and moment < previous:
            add("journey-invalid", where, "time goes backward")
        previous = moment


def check_pointers(path: Path, root: Path, add) -> None:
    rel = "evidence_pointers.json"
    if not path.is_file():
        add("pointer-invalid", rel, "evidence_pointers.json missing")
        return
    data = load_json(path)
    pointers = data.get("pointers") if isinstance(data, dict) else None
    if not isinstance(pointers, list):
        add("pointer-invalid", rel, "pointers must be a list")
        return
    for index, pointer in enumerate(pointers):
        where = f"{rel}#pointers[{index}]"
        if not isinstance(pointer, dict):
            add("pointer-invalid", where, "pointer must be an object")
            continue
        extra = sorted(set(pointer) - POINTER_KEYS)
        if extra:
            add("pointer-invalid", where, f"keys outside kind/path/cell/note: {', '.join(extra)}")
        if blank(pointer.get("kind")) or blank(pointer.get("path")):
            add("pointer-invalid", where, "kind and path are required")
            continue
        location = Path(pointer["path"])
        if not (location if location.is_absolute() else root / location).exists():
            add("pointer-invalid", where, f"path does not exist: {pointer['path']}")


def json_values(node: object):
    """Yield string values, skipping keys that start with '_' (human notes)."""
    if isinstance(node, dict):
        for key, value in node.items():
            if not str(key).startswith("_"):
                yield from json_values(value)
    elif isinstance(node, list):
        for value in node:
            yield from json_values(value)
    elif isinstance(node, str):
        yield node


def check_placeholders(target: Path, add) -> None:
    if (target / "cells" / SKELETON_CELL).exists():
        add("placeholder-left", f"cells/{SKELETON_CELL}", "skeleton cell folder remains")
    for path in sorted(p for p in target.rglob("*") if p.is_file()):
        rel = path.relative_to(target).as_posix()
        if rel.startswith("logs/"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if PLACEHOLDER not in text:
            continue
        if path.suffix == ".json":
            try:
                if not any(PLACEHOLDER in value for value in json_values(json.loads(text))):
                    continue
            except json.JSONDecodeError:
                pass
        add("placeholder-left", rel, "placeholder remains")


def read_events(path: Path) -> list[dict]:
    """Return the journey lines that are JSON objects (malformed lines are another rule's job)."""
    events = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
    return events


def check_refs(decl: dict, add) -> None:
    rel = "campaign.json"
    check_ids = {entry["id"] for entry in decl["checks"]}
    groups = {entry["group"] for entry in decl["groups"]}
    cells = {entry["cell"] for entry in decl["cells"]}
    for entry in decl["checks"]:
        for covered in entry.get("covers", []):
            if covered not in check_ids:
                add("ref-invalid", rel, f"check {entry['id']!r} covers unknown check {covered!r}")
    for entry in decl["groups"]:
        for name in entry["checks"]:
            if name not in check_ids:
                add("ref-invalid", rel, f"group {entry['group']!r} names unknown check {name!r}")
    for name in decl["final"]["checks"]:
        if name not in check_ids:
            add("ref-invalid", rel, f"final names unknown check {name!r}")
    for entry in decl["cells"]:
        if entry["group"] not in groups:
            add("ref-invalid", rel, f"cell {entry['cell']!r} is in unknown group {entry['group']!r}")
        if entry.get("parent") is not None and entry["parent"] not in cells:
            add("ref-invalid", rel, f"cell {entry['cell']!r} has unknown parent {entry['parent']!r}")
        for need in entry.get("needs", []):
            if need not in cells:
                add("ref-invalid", rel, f"cell {entry['cell']!r} needs unknown cell {need!r}")


def check_duplicates(decl: dict, add) -> None:
    seen_ids: set[str] = set()
    seen_commands: dict[tuple, str] = {}
    for entry in decl["checks"]:
        if entry["id"] in seen_ids:
            add("check-duplicate", "campaign.json", f"check id {entry['id']!r} is declared twice")
            continue
        seen_ids.add(entry["id"])
        command = (entry["cmd"], entry.get("cwd", "."), tuple(sorted(map(str, entry.get("env", [])))))
        if command in seen_commands:
            add("check-duplicate", "campaign.json",
                f"checks {seen_commands[command]!r} and {entry['id']!r} have the same cmd, cwd and env")
        else:
            seen_commands[command] = entry["id"]


def check_structure(decl: dict, events: list[dict], add) -> None:
    """Replay the structural events and compare with the declared layout."""
    opened = [event for event in events if event.get("event") == "campaign-open"]
    if not opened:
        return
    layout: dict[str, str] = {}
    cancelled: set[str] = set()
    started = False
    for event in events:
        kind = event.get("event")
        if kind == "campaign-open" and not started:
            started = True
            for item in event.get("cells") or []:
                if isinstance(item, dict) and not blank(item.get("cell")):
                    layout[item["cell"]] = item.get("group")
        elif not started:
            continue
        elif kind == "cell-add" and not blank(event.get("cell")):
            layout[event["cell"]] = event.get("group")
        elif kind == "cell-cancel" and not blank(event.get("cell")):
            cancelled.add(event["cell"])
        elif kind == "regroup" and not blank(event.get("cell")):
            layout[event["cell"]] = event.get("to")
    declared = {entry["cell"]: entry["group"] for entry in decl["cells"]}
    declared_cancelled = {entry["cell"] for entry in decl["cells"] if entry.get("state") == "cancelled"}
    rel = "campaign.json"
    for cell in sorted(set(declared) | set(layout)):
        if cell not in layout:
            add("structure-unlogged", rel, f"cell {cell!r} has no campaign-open or cell-add event")
        elif cell not in declared:
            add("structure-unlogged", rel, f"cell {cell!r} is in the journey but not in the declaration")
        elif declared[cell] != layout[cell]:
            add("structure-unlogged", rel,
                f"cell {cell!r} is in group {declared[cell]!r} but the journey gives {layout[cell]!r}")
    for cell in sorted(declared_cancelled ^ cancelled):
        add("structure-unlogged", rel, f"cell {cell!r}: cancelled state and cell-cancel events disagree")


def check_auto_cap(decl: dict, events: list[dict], add) -> None:
    budget = decl.get("budgets", {}).get("auto_cells", DEFAULT_AUTO_CELLS)
    if not isinstance(budget, int) or isinstance(budget, bool):
        return
    cap_ids = {event.get("id") for event in events
               if event.get("event") == "hitl" and event.get("kind") == "auto-cell-cap"}
    allowed = sum(1 for event in events if event.get("event") == "hitl-answer"
                  and event.get("allow") is True and event.get("id") in cap_ids)
    autos = sum(1 for entry in decl["cells"] if entry.get("origin") == "auto")
    if autos > budget + allowed:
        add("auto-cap-exceeded", "campaign.json",
            f"{autos} auto cells, budget {budget} plus {allowed} allowed")


def load_gate(target: Path, group: str) -> dict | None:
    path = target / "gates" / f"{group}{STATUS_SUFFIX}"
    if not path.is_file():
        return None
    data = load_json(path)
    if not isinstance(data, dict):
        raise Unreadable(f"gates/{path.name}: must be a JSON object")
    return data


def check_gates(target: Path, decl: dict, add) -> None:
    for entry in decl["cells"]:
        if entry.get("state") != "closed":
            continue
        gate = load_gate(target, entry["group"])
        rel = f"gates/{entry['group']}{STATUS_SUFFIX}"
        if gate is None or gate.get("state") != "passed":
            add("closed-without-gate", rel, f"cell {entry['cell']!r} is closed but the gate is not passed")
        elif entry["cell"] not in (gate.get("members") or []):
            add("closed-without-gate", rel, f"cell {entry['cell']!r} is closed but is not a gate member")
    declared = {entry["group"]: entry["checks"] for entry in decl["groups"]}
    declared[FINAL_GATE] = decl["final"]["checks"]
    gates_dir = target / "gates"
    for path in sorted(gates_dir.glob(f"*{STATUS_SUFFIX}")) if gates_dir.is_dir() else []:
        group = path.name[: -len(STATUS_SUFFIX)]
        gate = load_gate(target, group)
        if gate is None or gate.get("state") != "passed":
            continue
        rel = f"gates/{path.name}"
        results = [item for item in gate.get("checks") or [] if isinstance(item, dict)]
        for item in results:
            outcome = item.get("outcome")
            proven = outcome in ("reused", "covered") or (outcome in ("ran", "forced") and item.get("result") == "PASS")
            if not proven:
                add("gate-not-proven", rel,
                    f"check {item.get('check')!r} is {outcome!r}/{item.get('result')!r} in a passed gate")
                continue
            pointer = item.get("via") if outcome == "covered" else item.get("receipt")
            if blank(pointer) or not (target / pointer).is_file():
                add("gate-not-proven", rel, f"check {item.get('check')!r} points at a missing receipt: {pointer!r}")
        recorded = {item.get("check") for item in results}
        for name in declared.get(group, []):
            if name not in recorded:
                add("gate-not-proven", rel, f"check {name!r} has no result in a passed gate")


def check(target: Path, root: Path, template: bool) -> list[dict]:
    """Return findings; raise Unreadable when the target cannot be interpreted."""
    if not target.is_dir():
        raise Unreadable(f"{target}: not a directory")
    findings: list[dict] = []

    def add(rule: str, rel: str, message: str) -> None:
        findings.append({"rule": rule, "path": rel, "message": message})

    decl = read_declaration(target, template)
    phases = decl["phases"]
    cells_dir = target / "cells"
    folders = sorted(p.name for p in cells_dir.iterdir() if p.is_dir()) if cells_dir.is_dir() else []
    if template:
        expected = [SKELETON_CELL]
    else:
        expected = [entry["cell"] for entry in decl["cells"] if PLACEHOLDER not in entry["cell"]]
        if not decl["cells"]:
            add("no-cells", "campaign.json", "the declaration has no cells")
    for name in expected:
        if name in folders:
            check_cell(cells_dir / name, f"cells/{name}", phases, add, None if template else target)
        else:
            add("declared-missing", f"cells/{name}", "cell folder missing")
    for name in folders:
        if name not in expected and (template or name != SKELETON_CELL):
            add("undeclared", f"cells/{name}", "cell folder is not declared")
    check_journey(target / "journey.jsonl", add)
    check_pointers(target / "evidence_pointers.json", root, add)
    if not template:
        check_placeholders(target, add)
        events = read_events(target / "journey.jsonl")
        check_refs(decl, add)
        check_duplicates(decl, add)
        check_structure(decl, events, add)
        check_auto_cap(decl, events, add)
        check_gates(target, decl, add)
    return findings


# --- negative control ---------------------------------------------------------

def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def edit_json(path: Path, change) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    write_json(path, data)


def build_valid_instance(template_dir: Path, root: Path) -> Path:
    """Fill a copy of the real template: every phase state, a passed gate, a receipt,
    and a journey that adds, regroups and cancels cells."""
    instance = root / "campaigns" / "camp-control"
    shutil.copytree(template_dir, instance)
    skeleton = instance / "cells" / SKELETON_CELL
    cell_names = ("alpha", "beta", "gamma", "delta")
    for name in cell_names:
        shutil.copytree(skeleton, instance / "cells" / name)
    shutil.rmtree(skeleton)
    (root / "docs").mkdir()
    (root / "docs" / "evidence.txt").write_text("observed output\n", encoding="utf-8")

    def declare(decl: dict) -> None:
        decl.update(id="camp-control", goal="control instance", plan_ref="docs/evidence.txt",
                    checklist_ref="docs/evidence.txt", declared="2026-01-01T00:00:00+00:00")
        decl["target"] = {"root": ".", "env_probe": []}
        decl["checks"] = [
            {"id": "unit", "cmd": "python3 -B -m unittest", "cwd": ".", "expect": "OK",
             "inputs": ["src"], "env": [], "covers": ["unit-fast"]},
            {"id": "unit-fast", "cmd": "python3 -B -m unittest tests.fast", "cwd": ".", "expect": "OK",
             "inputs": ["src"], "env": [], "covers": []},
            {"id": "style", "cmd": "python3 -B tools/style.py", "cwd": ".", "expect": "style ok",
             "inputs": ["src"], "env": [], "covers": []},
        ]
        decl["groups"] = [{"group": "g-one", "checks": ["unit", "unit-fast"]},
                          {"group": "g-two", "checks": ["style"]},
                          {"group": "g-three", "checks": ["style"]}]
        decl["final"] = {"checks": ["unit", "style"]}
        decl["cells"] = [
            {"cell": "alpha", "goal": "first unit", "group": "g-one", "needs": [], "review": "none",
             "origin": "plan", "parent": None, "state": "closed"},
            {"cell": "beta", "goal": "second unit", "group": "g-two", "needs": ["alpha"], "review": "judgment",
             "origin": "plan", "parent": None, "state": "open"},
            {"cell": "gamma", "goal": "found on the way", "group": "g-three", "needs": [], "review": "none",
             "origin": "auto", "parent": None, "state": "open"},
            {"cell": "delta", "goal": "split from beta, dropped", "group": "g-two", "needs": [],
             "review": "judgment", "origin": "auto", "parent": "beta", "state": "cancelled"},
        ]
    edit_json(instance / "campaign.json", declare)
    phases = json.loads((instance / "campaign.json").read_text(encoding="utf-8"))["phases"]
    (instance / "logs").mkdir()
    states = {"alpha": ["done"] * len(phases), "beta": ["failed"], "gamma": ["running"], "delta": []}
    for name in cell_names:
        planned = states[name]
        cell_dir = instance / "cells" / name
        summary = cell_dir / "cell.md"
        summary.write_text(summary.read_text(encoding="utf-8").replace(PLACEHOLDER, "없음"), encoding="utf-8")
        for index, phase in enumerate(phases):
            state = planned[index] if index < len(planned) else "pending"
            log = f"logs/{name}-{phase}-1.log"
            if state in ("done", "failed"):
                (instance / log).write_text("verification passed\n" if state == "done" else "boom\n",
                                            encoding="utf-8")

            def fill(data: dict, state=state, name=name, log=log) -> None:
                closed = state in ("done", "failed")
                data["cell"] = name
                data["state"] = state
                data["proof"] = {"check": "python3 verify.py", "expect": "verification passed",
                                 "ok": state == "done", "source": log if closed else None}
                data["by"] = {"tool": EXECUTOR_TOOL, "log": log, "executor": "control"} if closed else None
            edit_json(cell_dir / "phases" / f"{phase}{STATUS_SUFFIX}", fill)
    inputs = {"src": "0" * 40}
    (instance / "receipts" / "unit").mkdir(parents=True)
    (instance / "logs" / "unit-1.log").write_text("OK\n", encoding="utf-8")
    write_json(instance / "receipts" / "unit" / "1.json", {
        "schema_version": SCHEMA_VERSION, "check": "unit", "n": 1, "scope": "gate",
        "key": {"cmd": "python3 -B -m unittest", "cwd": ".", "expect": "OK", "env": {}, "inputs": inputs,
                "host": "control", "env_probe": {}},
        "key_sha256": "0" * 64, "reusable": True, "result": "PASS", "exit_code": 0, "error": None,
        "log": "logs/unit-1.log", "started": "2026-01-01T03:00:00+00:00", "ended": "2026-01-01T03:00:01+00:00",
        "seconds": 1.0, "executor": "control", "forced": False, "reason": None, "target": ".",
        "covers": {"unit-fast": {"inputs": inputs}}})
    (instance / "gates").mkdir()
    write_json(instance / "gates" / f"g-one{STATUS_SUFFIX}", {
        "schema_version": SCHEMA_VERSION, "group": "g-one", "state": "passed", "runs": 1,
        "started": "2026-01-01T03:00:00+00:00", "ended": "2026-01-01T03:00:01+00:00",
        "members": ["alpha"], "hitl": None,
        "checks": [{"check": "unit", "outcome": "ran", "result": "PASS",
                    "receipt": "receipts/unit/1.json", "via": None},
                   {"check": "unit-fast", "outcome": "covered", "result": None,
                    "receipt": None, "via": "receipts/unit/1.json"}]})
    journey = [
        {"utc": "2026-01-01T00:00:00+00:00", "event": "campaign-open",
         "cells": [{"cell": "alpha", "group": "g-one"}, {"cell": "beta", "group": "g-two"}],
         "groups": ["g-one", "g-two"], "briefing": {"on_phase": True, "every_hours": None},
         "briefing_source": "default"},
        {"utc": "2026-01-01T01:00:00+00:00", "event": "phase-close", "cell": "alpha", "phase": phases[0],
         "ok": True, "log": f"logs/alpha-{phases[0]}-1.log"},
        {"utc": "2026-01-01T02:00:00+00:00", "event": "cell-add", "cell": "gamma", "group": "g-two",
         "parent": None, "review": "none", "reason": "found on the way"},
        {"utc": "2026-01-01T02:10:00+00:00", "event": "regroup", "cell": "gamma", "from": "g-two",
         "to": "g-three", "reason": "verified by a different check", "group_checks": ["style"]},
        {"utc": "2026-01-01T02:20:00+00:00", "event": "cell-add", "cell": "delta", "group": "g-two",
         "parent": "beta", "review": "judgment", "reason": "split"},
        {"utc": "2026-01-01T02:30:00+00:00", "event": "cell-cancel", "cell": "delta", "reason": "not needed"},
        {"utc": "2026-01-01T03:00:01+00:00", "event": "gate", "group": "g-one", "state": "passed",
         "checks": [{"check": "unit", "outcome": "ran"}, {"check": "unit-fast", "outcome": "covered"}]},
    ]
    (instance / "journey.jsonl").write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in journey), encoding="utf-8")
    edit_json(instance / "evidence_pointers.json", lambda data: data.update(
        pointers=[{"kind": "log", "path": "docs/evidence.txt", "cell": "alpha", "note": "control"}]))
    return instance


def control_cases(phases: list[str]) -> dict[str, tuple[str, object]]:
    """Seeds: case name -> (expected rule, change applied to a clean instance)."""
    first = f"{phases[0]}{STATUS_SUFFIX}"
    last = f"{phases[-1]}{STATUS_SUFFIX}"

    def phase(cell: str, name: str, change):
        return lambda inst: edit_json(inst / "cells" / cell / "phases" / name, change)

    def summary(change):
        def apply(inst: Path) -> None:
            path = inst / "cells" / "beta" / "cell.md"
            path.write_text(change(path.read_text(encoding="utf-8")), encoding="utf-8")
        return apply

    def append(name: str, text: str):
        return lambda inst: (inst / name).open("a", encoding="utf-8").write(text)

    def pointers(change):
        return lambda inst: edit_json(inst / "evidence_pointers.json", change)

    def declaration(change):
        return lambda inst: edit_json(inst / "campaign.json", change)

    def gate(change):
        return lambda inst: edit_json(inst / "gates" / f"g-one{STATUS_SUFFIX}", change)

    def cell_entry(decl: dict, name: str) -> dict:
        return next(entry for entry in decl["cells"] if entry["cell"] == name)

    def check_entry(decl: dict, name: str) -> dict:
        return next(entry for entry in decl["checks"] if entry["id"] == name)

    def no_cells(inst: Path) -> None:
        edit_json(inst / "campaign.json", lambda decl: decl.update(cells=[]))
        shutil.rmtree(inst / "cells")
        (inst / "cells").mkdir()
        (inst / "journey.jsonl").write_text("", encoding="utf-8")  # not opened yet

    def add_unlogged_cell(inst: Path) -> None:
        shutil.copytree(inst / "cells" / "gamma", inst / "cells" / "epsilon")
        edit_json(inst / "campaign.json", lambda decl: decl["cells"].append(
            {"cell": "epsilon", "goal": "slipped in", "group": "g-three", "needs": [], "review": "none",
             "origin": "auto", "parent": None, "state": "open"}))

    return {
        "proof-blank": ("proof-blank", phase("alpha", last, lambda d: d["proof"].update(check=""))),
        "source-missing": ("source-missing", phase("alpha", first, lambda d: d["proof"].update(source=None))),
        "state-invalid": ("state-invalid", phase("alpha", last, lambda d: d.update(state="finished"))),
        "done-not-ok": ("done-not-ok", phase("alpha", first, lambda d: d["proof"].update(ok=False))),
        "undeclared-phase": ("undeclared", lambda inst: shutil.copy(
            inst / "cells" / "alpha" / "phases" / first, inst / "cells" / "alpha" / "phases" / f"deploy{STATUS_SUFFIX}")),
        "undeclared-cell": ("undeclared", lambda inst: shutil.copytree(inst / "cells" / "alpha", inst / "cells" / "stray")),
        "summary-section-missing": ("cell-summary-incomplete", summary(lambda t: t.replace("## 막다른 길", "막다른 길"))),
        "summary-section-empty": ("cell-summary-incomplete", summary(
            lambda t: re.sub(r"(## 남은 계획\n).*", r"\1", t, flags=re.S))),
        "journey-not-json": ("journey-invalid", append("journey.jsonl", "not json\n")),
        "journey-backward": ("journey-invalid", append(
            "journey.jsonl", '{"utc": "2025-12-31T00:00:00+00:00", "event": "late"}\n')),
        "pointer-extra-key": ("pointer-invalid", pointers(lambda d: d["pointers"][0].update(sha256="0" * 64))),
        "pointer-path-absent": ("pointer-invalid", pointers(lambda d: d["pointers"][0].update(path="docs/absent.txt"))),
        "declared-phase-missing": ("declared-missing", lambda inst: (inst / "cells" / "beta" / "phases" / last).unlink()),
        "declared-cell-missing": ("declared-missing", lambda inst: shutil.rmtree(inst / "cells" / "beta")),
        "placeholder-left": ("placeholder-left", append("cells/beta/cell.md", f"\n{PLACEHOLDER}\n")),
        "no-cells": ("no-cells", no_cells),
        "done-without-record": ("done-unrecorded", phase("alpha", first, lambda d: d.update(by=None))),
        "done-log-absent": ("done-unrecorded", lambda inst: (inst / "logs" / f"alpha-{phases[-1]}-1.log").unlink()),
        "closed-gate-not-passed": ("closed-without-gate", gate(lambda d: d.update(state="failed"))),
        "closed-not-member": ("closed-without-gate", gate(lambda d: d.update(members=[]))),
        "structure-add-unlogged": ("structure-unlogged", add_unlogged_cell),
        "structure-cancel-unlogged": ("structure-unlogged", declaration(
            lambda d: cell_entry(d, "beta").update(state="cancelled"))),
        "structure-regroup-unlogged": ("structure-unlogged", declaration(
            lambda d: cell_entry(d, "gamma").update(group="g-two"))),
        "auto-cap-exceeded": ("auto-cap-exceeded", declaration(lambda d: d["budgets"].update(auto_cells=1))),
        "check-duplicate-id": ("check-duplicate", declaration(lambda d: d["checks"].append(
            {"id": "style", "cmd": "python3 -B tools/other.py", "cwd": ".", "expect": "ok",
             "inputs": ["src"], "env": [], "covers": []}))),
        "check-duplicate-command": ("check-duplicate", declaration(lambda d: d["checks"].append(
            {"id": "style-again", "cmd": "python3 -B tools/style.py", "cwd": ".", "expect": "style ok",
             "inputs": ["src"], "env": [], "covers": []}))),
        "ref-needs-unknown": ("ref-invalid", declaration(lambda d: cell_entry(d, "beta").update(needs=["absent"]))),
        "ref-covers-unknown": ("ref-invalid", declaration(lambda d: check_entry(d, "style").update(covers=["absent"]))),
        "ref-group-check-unknown": ("ref-invalid", declaration(lambda d: d["groups"][2].update(checks=["absent"]))),
        "ref-parent-unknown": ("ref-invalid", declaration(lambda d: cell_entry(d, "delta").update(parent="absent"))),
        "ref-final-unknown": ("ref-invalid", declaration(lambda d: d["final"].update(checks=["absent"]))),
        "gate-result-not-pass": ("gate-not-proven", gate(lambda d: d["checks"][0].update(result="FAIL"))),
        "gate-receipt-absent": ("gate-not-proven", lambda inst: (inst / "receipts" / "unit" / "1.json").unlink()),
    }


def instance_states(instance: Path) -> list[str]:
    return sorted({json.loads(path.read_text(encoding="utf-8")).get("state")
                   for path in instance.glob(f"cells/*/phases/*{STATUS_SUFFIX}")})


def instance_contents(instance: Path) -> dict:
    """Count what the valid instance carries beyond phase states."""
    passed = sum(1 for path in instance.glob(f"gates/*{STATUS_SUFFIX}")
                 if json.loads(path.read_text(encoding="utf-8")).get("state") == "passed")
    events = read_events(instance / "journey.jsonl")
    return {"gates_passed": passed,
            "receipts": sum(1 for path in instance.glob("receipts/*/*.json") if path.stem.isdigit()),
            "cell_add_events": sum(1 for event in events if event.get("event") == "cell-add"),
            "cell_cancel_events": sum(1 for event in events if event.get("event") == "cell-cancel"),
            "regroup_events": sum(1 for event in events if event.get("event") == "regroup")}


def negative_control(template_dir: Path) -> tuple[dict, list[str]]:
    """Return (report, problems). Problems are empty when the checker is sound.

    The report carries, per case, the rule expected and the rules observed, and
    the states present in the valid instance (all four must appear, so that a
    correct done and a correct failed phase are shown to pass).
    """
    cases: dict[str, dict] = {}
    report = {"valid_instance_states": [], "valid_instance": {}, "cases": cases}
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        leftover = check(template_dir, template_dir.parents[1], template=True)
        if leftover:
            problems.append(f"the template does not pass: {leftover}")
            return report, problems
        clean_root = base / "clean"
        clean_root.mkdir()
        clean = build_valid_instance(template_dir, clean_root)
        found = check(clean, clean_root, template=False)
        if found:
            problems.append(f"the valid instance does not pass: {found}")
            return report, problems
        report["valid_instance_states"] = instance_states(clean)
        report["valid_instance"] = instance_contents(clean)
        if not all(report["valid_instance"].values()):
            problems.append(f"the valid instance must carry a passed gate, a receipt and structural events, "
                            f"got {report['valid_instance']}")
        if report["valid_instance_states"] != sorted(STATES):
            problems.append(f"the valid instance must show every state, got {report['valid_instance_states']}")
        phases = json.loads((clean / "campaign.json").read_text(encoding="utf-8"))["phases"]

        def judge(name: str, rule: str, findings: list[dict]) -> None:
            rules = sorted({f["rule"] for f in findings})
            cases[name] = {"expected": rule, "observed": rules,
                           "outcome": "detected" if rules == [rule] else "missed"}
            if rules != [rule]:
                problems.append(f"{name}: expected only {rule}, got {rules}")

        for name, (rule, seed) in control_cases(phases).items():
            case_root = base / name
            shutil.copytree(clean_root, case_root)
            instance = case_root / "campaigns" / "camp-control"
            seed(instance)
            judge(name, rule, check(instance, case_root, template=False))
        copy_root = base / "unfilled-copy"
        shutil.copytree(template_dir, copy_root / "campaigns" / "camp-copy")
        judge("unfilled-copy", "placeholder-left", check(copy_root / "campaigns" / "camp-copy", copy_root, template=False))
        broken = base / "template-defect" / "campaigns" / "_template"
        shutil.copytree(template_dir, broken)
        edit_json(broken / "cells" / SKELETON_CELL / "phases" / f"{phases[0]}{STATUS_SUFFIX}",
                  lambda d: d["proof"].update(expect=""))
        judge("template-defect", "proof-blank", check(broken, broken.parents[1], template=True))
    return report, problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", type=Path, help="campaign folder (default: campaigns/_template)")
    parser.add_argument("--template", action="store_true", help="check the target as a template")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root for relative pointer paths")
    parser.add_argument("--list-control", action="store_true", help="print the negative control outcome per rule")
    args = parser.parse_args()
    root = args.root.resolve()
    template_dir = (ROOT / "campaigns" / "_template").resolve()
    target = (args.path or template_dir).resolve()
    template = args.template or target == template_dir
    try:
        report, problems = negative_control(template_dir)
        if args.list_control:
            cases = report["cases"]
            per_rule = {}
            for rule in RULES:
                outcomes = [case["outcome"] for case in cases.values() if case["expected"] == rule]
                per_rule[rule] = "detected" if outcomes and all(o == "detected" for o in outcomes) else "missed"
            ok = not problems and all(v == "detected" for v in per_rule.values())
            print(json.dumps({"status": "PASS" if ok else "FAIL",
                              "valid_instance_states": report["valid_instance_states"],
                              "valid_instance": report["valid_instance"],
                              "rules": per_rule, "cases": cases, "problems": problems},
                             ensure_ascii=False, indent=2))
            return 0 if ok else 1
        if problems:
            print(json.dumps({"status": "FAIL", "negative_control": problems}, ensure_ascii=False, indent=2))
            return 1
        findings = check(target, root, template)
    except Unreadable as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    try:
        shown = target.relative_to(root).as_posix()
    except ValueError:
        shown = str(target)
    print(json.dumps({"status": "FAIL" if findings else "PASS", "negative_control": "PASS",
                      "mode": "template" if template else "instance", "target": shown,
                      "findings": findings}, ensure_ascii=False, indent=2))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
