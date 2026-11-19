#!/usr/bin/env python3
"""progress-monitor executor for a two-layer campaign.

It runs only the checks the campaign declares, and writes phase states, gate
records, receipts and journey events. It also decides whether a Stop hook should
keep the main agent going. It never judges acceptance, and it is not a trust
boundary: the owner of the files can still edit them by hand.

Usage: pm.py [--campaigns <dir>] [--executor <who>] <command> ...
Exit codes: 0 ok, 1 a check failed or the request was refused, 2 usage or
unreadable input. `stop-hook` always exits 0.

The rules this file implements are written in ../references/ (gate.md,
monitor.md, direction.md). File formats are in campaigns/README.md.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import platform
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TOOL = "progress-monitor"
SCHEMA = 2
SUFFIX = ".status.json"
FINAL = "_final"
PM = "python3 -B skills/progress-monitor/scripts/pm.py"
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SECTIONS = ("목표", "확인된 사실", "적용한 변경", "검증", "막다른 길", "남은 계획")
REVIEW = ("none", "user-request", "high-impact", "judgment")
DEFAULT_BUDGETS = {
    "stall_stops": 2, "stop_block_cap": 6, "stall_retries": 1, "phase_minutes": 90,
    "auto_cells": 5, "forced_reruns_per_check": 1, "forced_reruns_total": 3,
    "exec_retries": 2, "check_timeout_s": 1800, "explore_candidates": 3, "explore_rounds": 6,
}
DEFAULT_MONITOR = {
    "session": None, "digest": None, "unchanged": 0, "blocks": 0, "released": False,
    "stalls": {}, "refused_since": 0, "total_blocks": 0, "last_block_utc": None, "last_brief_utc": None,
}
# Events only the executor may write; `event` refuses them.
RESERVED_EVENTS = {
    "campaign-open", "phase-start", "phase-retry", "phase-close", "check", "gate", "final",
    "gate-reopen", "cell-add", "cell-cancel", "regroup", "cell-reopen", "cell-stop", "cell-resume",
    "cell-close", "review", "hitl", "hitl-answer", "invalidate", "explore-open", "hang",
    "monitor-release", "brief", "campaign-close",
}
# Skill activation: the repository's activation script and shared knob, when both exist.
ACTIVATION_ENV = "PM_SKILL_ACTIVATION"
ACTIVATION_SCRIPT = Path("scripts") / "skill_activation.py"
ACTIVATION_KNOB = "skill-activation.yaml"
GROUP_CANCELLED, GROUP_STOPPED, GROUP_WAITING = "취소", "멈춤", "기다림"
GROUP_PASSED, GROUP_FAILED, GROUP_ACTIVE = "통과", "실패", "진행"


class Refused(Exception):
    """The request is understood but not allowed now (exit 1)."""

    def __init__(self, refused: str, **detail):
        super().__init__(refused)
        self.payload = {"ok": False, "refused": refused, **detail}


class Usage(Exception):
    """Bad arguments or unreadable input (exit 2)."""


# --- small helpers --------------------------------------------------------------

def now() -> datetime:
    return datetime.now(timezone.utc)


def stamp(moment: datetime | None = None) -> str:
    return (moment or now()).isoformat(timespec="microseconds")


def parse_time(text: object) -> datetime | None:
    if not isinstance(text, str):
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def read_json(path: Path, default: object = None) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if default is not None:
            return json.loads(json.dumps(default))
        raise Usage(f"{path}: missing") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise Usage(f"{path}: not JSON ({exc})") from None


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def git(root: Path, *args: str) -> tuple[int, str]:
    try:
        done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return done.returncode, done.stdout.strip()


def execute(cmd: str, cwd: Path, timeout: float, log_path: Path) -> dict:
    """Run one shell command, keep its output in log_path, and classify the end."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = now()
    code, error = None, None
    try:
        with log_path.open("wb") as log:
            proc = subprocess.Popen(cmd, shell=True, cwd=str(cwd), stdout=log, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
            try:
                code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                error = "timeout"
    except OSError as exc:
        error = "spawn"
        with log_path.open("ab") as log:
            log.write(f"[{TOOL}] could not start the command: {exc}\n".encode())
    ended = now()
    if error is None and code is not None:
        if code < 0 or 128 < code <= 128 + 64:
            error = "signal"      # the shell reports 128+N for a command killed by signal N
        elif code in (126, 127):
            error = "not-found"
    try:
        output = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        output = ""
    return {"exit_code": None if error == "timeout" else code, "error": error, "output": output,
            "started": stamp(started), "ended": stamp(ended),
            "seconds": round((ended - started).total_seconds(), 3)}


# --- campaign state -------------------------------------------------------------

class Campaign:
    def __init__(self, base: Path, executor: str):
        self.base = base
        self.executor = executor
        active = base / "ACTIVE"
        if not active.is_file():
            raise Usage(f"{active}: no active campaign")
        name = active.read_text(encoding="utf-8").strip()
        if not name or not NAME.match(name):
            raise Usage(f"{active}: not an instance name")
        self.dir = base / name
        if not self.dir.is_dir():
            raise Usage(f"{self.dir}: instance folder missing")
        self._probe: dict[str, dict] = {}
        self._ids: dict[tuple, object] = {}
        self._events: list[dict] | None = None
        self.reload()

    # -- declaration
    def reload(self) -> None:
        decl = read_json(self.dir / "campaign.json")
        if not isinstance(decl, dict) or decl.get("schema_version") != SCHEMA:
            raise Usage(f"campaign.json: schema_version must be {SCHEMA}")
        for key, kind in (("target", dict), ("phases", list), ("checks", list), ("groups", list),
                          ("final", dict), ("cells", list)):
            if not isinstance(decl.get(key), kind):
                raise Usage(f"campaign.json: {key} is missing or malformed")
        self.decl = decl
        self._events = None
        self._ids = {}
        root = Path(str(decl["target"].get("root", "")))
        self.root = root if root.is_absolute() else (self.base.parent / root)

    @property
    def pm(self) -> str:
        """The command prefix to print in guidance, with --campaigns when it is not the default."""
        if self.base == (REPO / "campaigns").resolve():
            return PM
        try:
            shown = self.base.relative_to(REPO.resolve()).as_posix()
        except ValueError:
            shown = str(self.base)
        return f"{PM} --campaigns {shown}"

    def save_decl(self) -> None:
        write_json(self.dir / "campaign.json", self.decl)

    def budget(self, name: str) -> int:
        value = (self.decl.get("budgets") or {}).get(name, DEFAULT_BUDGETS[name])
        return value if isinstance(value, int) and not isinstance(value, bool) else DEFAULT_BUDGETS[name]

    @property
    def phases(self) -> list[str]:
        return self.decl["phases"]

    def cell(self, name: str) -> dict:
        for entry in self.decl["cells"]:
            if entry.get("cell") == name:
                entry.setdefault("state", "open")
                entry.setdefault("origin", "plan")
                entry.setdefault("parent", None)
                entry.setdefault("review", "none")
                entry.setdefault("needs", [])
                return entry
        raise Usage(f"unknown cell {name!r}")

    def cells(self) -> list[dict]:
        return [self.cell(entry["cell"]) for entry in self.decl["cells"]]

    def group(self, name: str) -> dict:
        for entry in self.decl["groups"]:
            if entry.get("group") == name:
                return entry
        raise Usage(f"unknown group {name!r}")

    def has_group(self, name: str) -> bool:
        return any(entry.get("group") == name for entry in self.decl["groups"])

    def check(self, name: str) -> dict:
        for entry in self.decl["checks"]:
            if entry.get("id") == name:
                return entry
        raise Usage(f"unknown check {name!r}")

    def members(self, group: str, live: bool = True) -> list[dict]:
        return [c for c in self.cells() if c["group"] == group and (not live or c["state"] != "cancelled")]

    # -- lock
    @contextlib.contextmanager
    def lock(self, wait: float | None = None):
        """Hold the instance lock while state is read and written. Never held while a check runs."""
        wait = float(os.environ.get("PM_LOCK_WAIT_S", "30")) if wait is None else wait
        handle = (self.dir / ".lock").open("a+")
        deadline = time.monotonic() + wait
        try:
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise Usage(f"{self.dir / '.lock'}: could not take the lock in {wait:g}s") from None
                    time.sleep(0.05)
            self.reload()
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()

    # -- journey
    def events(self) -> list[dict]:
        if self._events is None:
            found = []
            path = self.dir / "journey.jsonl"
            if path.is_file():
                for line in path.read_text(encoding="utf-8").splitlines():
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict):
                        found.append(event)
            self._events = found
        return self._events

    def emit(self, name: str, **fields) -> dict:
        events = self.events()
        moment = now()
        last = parse_time(events[-1].get("utc")) if events else None
        if last is not None and moment < last:
            moment = last
        event = {"utc": stamp(moment), "event": name, **fields}
        with (self.dir / "journey.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        events.append(event)
        return event

    # -- phase files
    def phase_path(self, cell: str, phase: str) -> Path:
        return self.dir / "cells" / cell / "phases" / f"{phase}{SUFFIX}"

    def phase(self, cell: str, phase: str) -> dict:
        if phase not in self.phases:
            raise Usage(f"unknown phase {phase!r}")
        data = read_json(self.phase_path(cell, phase))
        if not isinstance(data, dict) or not isinstance(data.get("proof"), dict):
            raise Usage(f"{self.phase_path(cell, phase)}: malformed phase file")
        data.setdefault("attempt", 1)
        data.setdefault("retries", 0)
        return data

    def save_phase(self, cell: str, phase: str, data: dict) -> None:
        write_json(self.phase_path(cell, phase), data)

    # -- gates
    def gate(self, group: str) -> dict:
        blank = {"schema_version": SCHEMA, "group": group, "state": "pending", "runs": 0, "started": None,
                 "ended": None, "members": [], "hitl": None, "checks": []}
        data = read_json(self.dir / "gates" / f"{group}{SUFFIX}", blank)
        if not isinstance(data, dict):
            raise Usage(f"gates/{group}{SUFFIX}: malformed gate record")
        return data

    def save_gate(self, group: str, data: dict) -> None:
        write_json(self.dir / "gates" / f"{group}{SUFFIX}", data)

    def group_state(self, group: str) -> str:
        cells = self.members(group)
        if not cells:
            return GROUP_CANCELLED
        if any(c["state"] == "stopped" for c in cells):
            return GROUP_STOPPED
        gate = self.gate(group)["state"]
        if gate == "waiting" or any(c["state"] == "waiting" for c in cells):
            return GROUP_WAITING
        return {"passed": GROUP_PASSED, "failed": GROUP_FAILED}.get(gate, GROUP_ACTIVE)

    # -- monitor
    def monitor(self) -> dict:
        data = read_json(self.dir / "monitor.json", DEFAULT_MONITOR)
        if not isinstance(data, dict):
            raise Usage("monitor.json: malformed")
        return {**json.loads(json.dumps(DEFAULT_MONITOR)), **data}

    def save_monitor(self, data: dict) -> None:
        write_json(self.dir / "monitor.json", data)

    # -- receipts
    def receipts(self, check: str) -> list[dict]:
        folder = self.dir / "receipts" / check
        found = []
        for path in folder.glob("*.json") if folder.is_dir() else []:
            if path.stem.isdigit():
                data = read_json(path)
                if isinstance(data, dict):
                    data["_path"] = f"receipts/{check}/{path.name}"
                    found.append(data)
        return sorted(found, key=lambda r: r.get("n", 0))

    def all_receipts(self) -> list[dict]:
        folder = self.dir / "receipts"
        names = sorted(p.name for p in folder.iterdir() if p.is_dir()) if folder.is_dir() else []
        return [receipt for name in names for receipt in self.receipts(name)]

    def invalidated_at(self, check: str) -> datetime | None:
        path = self.dir / "receipts" / check / "invalidated.json"
        if not path.is_file():
            return None
        data = read_json(path)
        return parse_time(data.get("utc")) if isinstance(data, dict) else None

    def usable(self, receipt: dict, check: str | None = None) -> bool:
        """A receipt (or its covers entry for `check`) written before an invalidation is not used."""
        cut = self.invalidated_at(check or receipt.get("check", ""))
        ended = parse_time(receipt.get("ended"))
        return cut is None or (ended is not None and ended >= cut)

    # -- fingerprints
    def input_ids(self, root: Path, inputs: list) -> dict:
        ids = {}
        for item in inputs:
            path = str(item).strip().strip("/") or "."
            key = (str(root), path)
            if key not in self._ids:
                code, out = git(root, "rev-parse", "--verify", "--quiet", "HEAD:./" + ("" if path == "." else path))
                self._ids[key] = out if code == 0 and out else None
            ids[str(item)] = self._ids[key]
        return ids

    def probe(self, root: Path) -> dict:
        key = str(root)
        if key not in self._probe:
            outputs = {}
            for cmd in self.decl["target"].get("env_probe") or []:
                try:
                    done = subprocess.run(str(cmd), shell=True, cwd=str(root), capture_output=True, text=True,
                                          timeout=60, stdin=subprocess.DEVNULL)
                    outputs[str(cmd)] = (done.stdout + done.stderr).strip() if done.returncode == 0 \
                        else f"exit {done.returncode}: {(done.stdout + done.stderr).strip()}"
                except (OSError, subprocess.TimeoutExpired) as exc:
                    outputs[str(cmd)] = f"error: {type(exc).__name__}"
            self._probe[key] = outputs
        return self._probe[key]

    def fingerprint(self, check: dict, root: Path) -> dict:
        """Rule 1: say whether a result at this input can be reused, and give the key."""
        inputs = list(check.get("inputs") or [])
        why = None
        if not inputs:
            why = "no-inputs"
        elif git(root, "rev-parse", "--is-inside-work-tree")[0] != 0:
            why = "not-git"
        else:
            code, out = git(root, "status", "--porcelain", "--", *[str(item) for item in inputs])
            if code != 0 or out:
                why = "uncommitted-input"
            elif any(value is None for value in self.input_ids(root, inputs).values()):
                why = "unresolved-input"  # not in HEAD: a glob, a missing or ignored path, a folder of another repo
        key = {"cmd": check["cmd"], "cwd": check.get("cwd", "."), "expect": check["expect"],
               "env": {str(name): os.environ.get(str(name)) for name in check.get("env") or []},
               "inputs": self.input_ids(root, inputs) if why != "not-git" else {str(i): None for i in inputs},
               "host": platform.node(), "env_probe": self.probe(root) if root.is_dir() else {}}
        digest = hashlib.sha256(json.dumps(key, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return {"key": key, "sha": digest, "reusable": why is None, "why": why}

    # -- hitl bookkeeping
    def hitls(self) -> list[dict]:
        answers = {e.get("id"): e for e in self.events() if e.get("event") == "hitl-answer"}
        return [{**e, "answer": answers.get(e.get("id"))} for e in self.events() if e.get("event") == "hitl"]

    def open_hitl(self, kind: str, question: str, **target) -> str:
        for item in self.hitls():
            if item["answer"] is None and item.get("kind") == kind and all(item.get(k) == v for k, v in target.items()):
                return item["id"]
        ident = f"h{sum(1 for e in self.events() if e.get('event') == 'hitl') + 1}"
        self.emit("hitl", id=ident, kind=kind, question=question, **target)
        return ident

    def allows(self, kind: str, **target) -> int:
        return sum(1 for item in self.hitls() if item.get("kind") == kind and item["answer"] is not None
                   and item["answer"].get("allow") is True and all(item.get(k) == v for k, v in target.items()))


# --- deciding and running one check ---------------------------------------------

def decide(camp: Campaign, check: dict, root: Path, scope: str, force: bool) -> dict:
    """Apply the rule table (first match). Returns action run|skip|refuse with the outcome."""
    fp = camp.fingerprint(check, root)
    if not fp["reusable"]:
        return {"action": "run", "outcome": "ran", "rule": 1, "fp": fp, "reason": fp["why"]}
    if force:
        return {"action": "run", "outcome": "forced", "rule": 2, "fp": fp}
    same = [r for r in camp.receipts(check["id"])
            if r.get("reusable") and r.get("key_sha256") == fp["sha"] and camp.usable(r)
            and (scope != "final" or r.get("scope") == "final")]
    verdicts = [r for r in same if r.get("result") in ("PASS", "FAIL")]
    if verdicts:
        last = verdicts[-1]
        if last["result"] == "PASS":
            return {"action": "skip", "outcome": "reused", "rule": 3, "fp": fp, "receipt": last["_path"]}
        return {"action": "refuse", "outcome": "refused", "rule": 4, "fp": fp, "receipt": last["_path"],
                "reason": "same-input-fail"}
    cap = camp.budget("exec_retries") + 1 + camp.allows("exec-retry-cap", check=check["id"])
    if len(same) >= cap:
        return {"action": "refuse", "outcome": "refused", "rule": 5, "fp": fp, "receipt": same[-1]["_path"],
                "reason": "exec-retry-cap"}
    if scope != "final":
        for wider in camp.decl["checks"]:
            if check["id"] not in (wider.get("covers") or []) or wider["id"] == check["id"]:
                continue
            wide_fp = camp.fingerprint(wider, root)
            if not wide_fp["reusable"]:
                continue
            wide = [r for r in camp.receipts(wider["id"])
                    if r.get("reusable") and r.get("key_sha256") == wide_fp["sha"] and camp.usable(r)
                    and r.get("result") in ("PASS", "FAIL")]
            if not wide or wide[-1]["result"] != "PASS":
                continue
            entry = (wide[-1].get("covers") or {}).get(check["id"])
            if isinstance(entry, dict) and entry.get("inputs") == fp["key"]["inputs"] \
                    and camp.usable(wide[-1], check["id"]):
                return {"action": "skip", "outcome": "covered", "rule": 6, "fp": fp, "via": wide[-1]["_path"]}
    return {"action": "run", "outcome": "ran", "rule": 7, "fp": fp}


def covered_inputs(camp: Campaign, check: dict, root: Path) -> dict:
    """The inputs of the checks this one covers, as they are before the run starts."""
    covers = {}
    for narrow in check.get("covers") or []:
        with contextlib.suppress(Usage):
            fp = camp.fingerprint(camp.check(narrow), root)
            if fp["reusable"]:      # uncommitted or unresolved inputs cannot be said to be covered
                covers[narrow] = {"inputs": fp["key"]["inputs"]}
    return covers


def run_check(camp: Campaign, check: dict, root: Path, scope: str, plan: dict,
              reason: str | None = None, explore: str | None = None) -> dict:
    """Run a check without the lock, then write its receipt and event under the lock."""
    cwd = root / str(check.get("cwd", "."))
    timeout = check.get("timeout_s") or camp.budget("check_timeout_s")
    temp = camp.dir / "logs" / f".{check['id']}-{os.getpid()}-{time.monotonic_ns()}.tmp"
    done = execute(check["cmd"], cwd, timeout, temp)
    if done["error"]:
        result = "ERROR"
    else:
        result = "PASS" if done["exit_code"] == 0 and check["expect"] in done["output"] else "FAIL"
    with camp.lock():
        number = max([r.get("n", 0) for r in camp.receipts(check["id"])] + [0]) + 1
        log = f"logs/{check['id']}-{number}.log"
        if temp.exists():
            os.replace(temp, camp.dir / log)
        else:
            (camp.dir / log).write_text("", encoding="utf-8")
        covers = plan.get("covers") or {}
        reusable, note = plan["fp"]["reusable"], plan.get("reason")
        if reusable and camp.fingerprint(check, root)["sha"] != plan["fp"]["sha"]:
            reusable, note, covers = False, "input-changed-during-run", {}   # nobody knows which input it read
        forced = plan["outcome"] == "forced"
        receipt = {
            "schema_version": SCHEMA, "check": check["id"], "n": number, "scope": scope,
            "key": plan["fp"]["key"], "key_sha256": plan["fp"]["sha"], "reusable": reusable,
            "result": result, "exit_code": done["exit_code"], "error": done["error"], "log": log,
            "started": done["started"], "ended": done["ended"], "seconds": done["seconds"],
            "executor": camp.executor, "forced": forced, "reason": reason if forced else note,
            "target": str(root), "explore": explore, "covers": covers,
        }
        path = f"receipts/{check['id']}/{number}.json"
        write_json(camp.dir / path, receipt)
        outcome = "error" if result == "ERROR" else plan["outcome"]
        camp.emit("check", check=check["id"], outcome=outcome, scope=scope, receipt=path,
                  reason=receipt["reason"])
    return {"check": check["id"], "outcome": outcome, "result": result, "receipt": path, "via": None,
            "reason": receipt["reason"]}


def skip_check(camp: Campaign, check: dict, scope: str, plan: dict) -> dict:
    """Record a check that was not run (reused, covered or refused). The lock is held by the caller."""
    pointer = plan.get("via") or plan.get("receipt")
    camp.emit("check", check=check["id"], outcome=plan["outcome"], scope=scope, receipt=pointer,
              reason=plan.get("reason"))
    if plan.get("reason") == "same-input-fail":
        monitor = camp.monitor()
        monitor["refused_since"] += 1
        camp.save_monitor(monitor)
    result = {"reused": "PASS", "covered": None}.get(plan["outcome"], "FAIL" if plan.get("rule") == 4 else None)
    return {"check": check["id"], "outcome": plan["outcome"], "result": result,
            "receipt": plan.get("receipt"), "via": plan.get("via"), "reason": plan.get("reason")}


def ordered(camp: Campaign, names: list[str]) -> list[dict]:
    """Checks of one gate, covering checks before the checks they cover."""
    checks = [camp.check(name) for name in dict.fromkeys(names)]
    placed: list[dict] = []
    waiting = list(checks)
    while waiting:
        ready = [c for c in waiting
                 if not any(c["id"] in (w.get("covers") or []) for w in waiting if w is not c)]
        for item in ready or waiting[:1]:
            placed.append(item)
            waiting.remove(item)
    return placed


def force_cap(camp: Campaign, check: str) -> str | None:
    """Return which forced-rerun cap is reached, or None."""
    forced = [r for r in camp.all_receipts() if r.get("forced")]
    per_check = sum(1 for r in forced if r.get("check") == check)
    if per_check >= camp.budget("forced_reruns_per_check") + camp.allows("forced-rerun-cap", check=check, cap="per-check"):
        return "per-check"
    if len(forced) >= camp.budget("forced_reruns_total") + camp.allows("forced-rerun-cap", cap="total"):
        return "total"
    return None


def gate_state(results: list[dict]) -> str:
    if any(r.get("reason") == "exec-retry-cap" for r in results):
        return "waiting"
    if any(r["result"] == "FAIL" or r.get("reason") == "same-input-fail" for r in results):
        return "failed"
    if any(r["outcome"] == "error" for r in results):
        return "pending"
    return "passed"


def run_checks(camp: Campaign, names: list[str], scope: str, force: str | None,
               reason: str | None, hitl_target: dict) -> tuple[list[dict], str | None]:
    """Evaluate every check of a gate in order; a failure does not stop the rest."""
    results, hitl = [], None
    for check in ordered(camp, names):
        with camp.lock():
            plan = decide(camp, check, camp.root, scope, force == check["id"])
            if plan["action"] == "run":
                plan["covers"] = covered_inputs(camp, check, camp.root)
            else:
                results.append(skip_check(camp, check, scope, plan))
                if plan.get("reason") == "exec-retry-cap":
                    hitl = camp.open_hitl("exec-retry-cap",
                                          f"검사 {check['id']}의 실행이 같은 입력에서 거듭 실패했다. 환경을 고친 뒤 "
                                          f"다시 돌려도 되는가?", check=check["id"], cap="per-check", **hitl_target)
                continue
        results.append(run_check(camp, check, camp.root, scope, plan, reason=reason))
    return results, hitl


def reset_final(camp: Campaign) -> None:
    gate = camp.gate(FINAL)
    if gate["state"] in ("passed", "failed"):
        gate["state"] = "pending"
        camp.save_gate(FINAL, gate)


def reopen_gate(camp: Campaign, group: str, reason: str, states: tuple = ("passed",)) -> bool:
    """Leave a settled gate: gate to pending, closed members back to open."""
    gate = camp.gate(group)
    if gate["state"] not in states:
        return False
    gate["state"] = "pending"
    camp.save_gate(group, gate)
    for cell in camp.members(group):
        if cell["state"] == "closed":
            cell["state"] = "open"
    camp.save_decl()
    camp.emit("gate-reopen", group=group, reason=reason)
    return True


def gate_blockers(camp: Campaign, group: str) -> list[str]:
    cells = camp.members(group)
    if not cells:
        return ["the group has no live member"]
    last = camp.phases[-1]
    problems = [f"{c['cell']} is {c['state']}" for c in cells if c["state"] != "open"]
    problems += [f"{c['cell']}/{last} is {camp.phase(c['cell'], last)['state']}" for c in cells
                 if camp.phase(c["cell"], last)["state"] != "done"]
    return problems


def member_attempts(camp: Campaign, group: str) -> dict:
    last = camp.phases[-1]
    return {c["cell"]: camp.phase(c["cell"], last)["attempt"] for c in camp.members(group)}


def running_elsewhere(gate: dict) -> bool:
    return bool(gate.get("started")) and not gate.get("ended") and gate.get("pid") != os.getpid() \
        and alive(gate.get("pid"))


# --- skill activation -------------------------------------------------------------

def activation_script(camp: Campaign) -> Path | None:
    """The activation script for this campaigns folder, or None when the repository has no knob."""
    root = camp.base.parent
    override = os.environ.get(ACTIVATION_ENV)
    script = Path(override) if override else root / ACTIVATION_SCRIPT
    return script if script.is_file() and (root / ACTIVATION_KNOB).is_file() else None


def activation(camp: Campaign, script: Path, *args: str) -> tuple[int, dict]:
    proc = subprocess.run([sys.executable, "-B", str(script), "--root", str(camp.base.parent),
                           "--campaigns", str(camp.base), *args], capture_output=True, text=True, timeout=300)
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        out = {"ok": False, "error": (proc.stderr or proc.stdout)[-2000:]}
    return proc.returncode, out if isinstance(out, dict) else {"ok": False, "error": str(out)}


def activation_refusal(code: int, out: dict, step: str) -> Exception:
    if code == 1:
        detail = {k: v for k, v in out.items() if k not in ("ok", "refused")}
        return Refused(f"skill-activation-{out.get('refused') or 'failed'}", step=step, **detail)
    return Usage(f"skill activation ({step}): {out.get('error') or out}")


def select_skills(camp: Campaign, script: Path, choice: str | None) -> tuple[dict, dict]:
    """Ask for the selection (hitl) or turn the chosen optional skills on for this campaign."""
    if choice is None:
        code, out = activation(camp, script, "candidates")
        if code:
            raise activation_refusal(code, out, "candidates")
        candidates = out.get("candidates") or []
        blocking = [name for name in out.get("open_records") or [] if name != camp.dir.name]
        if blocking:
            raise Refused("skill-activation-another-campaign-open", campaign=blocking[0],
                          hint=f"close it first: python3 scripts/skill_activation.py campaign-close {blocking[0]}")
        listed = ", ".join(f"{c['skill']} ({'on' if c['on'] else 'off'})" for c in candidates)
        question = (f"Which optional skills should this campaign turn on? Candidates: {listed}. "
                    f"Answer by running {camp.pm} open --skills <a,b> or --skills none.")
        ident = camp.open_hitl("skill-selection", question)
        raise Refused("selection-required", hitl=ident, candidates=candidates,
                      next=f"{camp.pm} open --skills <a,b|none>")
    pending = [h for h in camp.hitls() if h.get("kind") == "skill-selection" and h["answer"] is None]
    hitl = pending[-1]["id"] if pending else None
    code, out = activation(camp, script, "campaign-open", camp.dir.name, "--skills", choice,
                           *(("--hitl", hitl) if hitl else ()))
    if code:
        raise activation_refusal(code, out, "campaign-open")
    if hitl:
        # The agent relays the person's answer; the ref says where it came in.
        camp.emit("hitl-answer", id=hitl, ref=f"{camp.pm} open --skills {choice}", allow=False)
    record = out.get("record") or {}
    return {k: record.get(k, []) for k in ("requested", "enabled", "pre_existing")}, out.get("mcp_register") or {}


# --- commands: open, close, phases -----------------------------------------------

def cmd_open(camp: Campaign, args) -> dict:
    with camp.lock():
        if any(e.get("event") == "campaign-open" for e in camp.events()):
            raise Refused("already-open")
        groups = [g["group"] for g in camp.decl["groups"]]
        ids = [c["id"] for c in camp.decl["checks"]]
        for cell in camp.cells():
            if cell["group"] not in groups:
                raise Usage(f"cell {cell['cell']!r} is in unknown group {cell['group']!r}")
            for phase in camp.phases:
                camp.phase(cell["cell"], phase)
        for group in camp.decl["groups"]:
            for name in group["checks"]:
                if name not in ids:
                    raise Usage(f"group {group['group']!r} names unknown check {name!r}")
        script = activation_script(camp)
        if script is None and args.skills is not None:
            raise Usage(f"--skills needs {ACTIVATION_SCRIPT} and {ACTIVATION_KNOB} in the repository")
        if script is None:
            return finish_open(camp, args, groups, None, {})
        record = camp.dir / "activation.json"
        had_record = record.exists()
        try:
            skills, mcp_register = select_skills(camp, script, args.skills)
            return finish_open(camp, args, groups, skills, mcp_register)
        except BaseException as exc:
            # Undo a campaign-open that went through when a later step failed (not a refusal before it).
            if not had_record and record.exists() and not isinstance(exc, Refused):
                code, out = activation(camp, script, "campaign-abort", camp.dir.name)
                if code:
                    raise Usage(f"open failed ({exc}) and undoing the skill activation also failed: "
                                f"{out.get('error') or out.get('refused')}; run python3 scripts/skill_activation.py "
                                f"campaign-abort {camp.dir.name}") from exc
            raise


def finish_open(camp: Campaign, args, groups: list[str], skills: dict | None, mcp_register: dict) -> dict:
    briefing = {"on_phase": True, "every_hours": None, **(camp.decl.get("briefing") or {})}
    source = "default"
    if args.briefing_on_phase is not None:
        briefing["on_phase"] = args.briefing_on_phase == "true"
        source = "user"
    if args.briefing_every_hours is not None:
        briefing["every_hours"] = args.briefing_every_hours if args.briefing_every_hours > 0 else None
        source = "user"
    camp.decl["briefing"] = briefing
    camp.save_decl()
    for folder in ("gates", "receipts", "logs"):
        (camp.dir / folder).mkdir(exist_ok=True)
    camp.save_monitor(camp.monitor())
    extra = {"skills": skills} if skills is not None else {}
    camp.emit("campaign-open", cells=[{"cell": c["cell"], "group": c["group"]} for c in camp.cells()],
              groups=groups, briefing=briefing, briefing_source=source, **extra)
    return {"ok": True, "campaign": camp.decl.get("id"), "briefing": briefing, "briefing_source": source,
            "skills": skills, "mcp_register": mcp_register or None}


def cmd_close(camp: Campaign, args) -> dict:
    """Turn off the optional skills this campaign turned on, then clear ACTIVE."""
    with camp.lock():
        if not any(e.get("event") == "campaign-open" for e in camp.events()):
            raise Refused("not-open")
        final = camp.gate(FINAL)
        if final.get("state") != "passed" and not (args.reason and args.reason.strip()):
            raise Refused("final-not-passed", state=final.get("state"),
                          hint="run final run first, or close anyway with --reason <why>")
        script = activation_script(camp)
        skills, note = None, None
        record = read_json(camp.dir / "activation.json", {})
        if isinstance(record, dict) and record.get("state") == "closed":
            note = "the activation record was already closed; nothing to turn off"
        elif script is not None and (camp.dir / "activation.json").is_file():
            code, out = activation(camp, script, "campaign-close", camp.dir.name)
            if code:
                raise activation_refusal(code, out, "campaign-close")
            record = out.get("record") or {}
            skills = {"turned_off": record.get("turned_off", []), "left_as_changed": record.get("left_as_changed", [])}
        else:
            note = "no activation record; no optional skill was turned off"
        camp.emit("campaign-close", reason=args.reason, final=final.get("state"), **({"skills": skills} if skills else {}))
        (camp.base / "ACTIVE").unlink(missing_ok=True)
        return {"ok": True, "closed": camp.dir.name, "skills": skills, "note": note, "active": None}


def start_problems(camp: Campaign, cell: dict, phase: str) -> list[str]:
    if cell["state"] != "open":
        return [f"cell is {cell['state']}"]
    index = camp.phases.index(phase)
    problems = [f"{earlier} is not done" for earlier in camp.phases[:index]
                if camp.phase(cell["cell"], earlier)["state"] != "done"]
    if phase == camp.phases[-1]:
        for need in cell.get("needs") or []:
            other = camp.cell(need)
            if other["state"] == "cancelled":
                continue
            if other["group"] == cell["group"]:
                if camp.phase(need, phase)["state"] != "done":
                    problems.append(f"needs {need}: its {phase} is not done")
            elif camp.gate(other["group"])["state"] != "passed":
                problems.append(f"needs {need}: gate {other['group']} is not passed")
    return problems


def cmd_phase_start(camp: Campaign, args) -> dict:
    with camp.lock():
        cell = camp.cell(args.cell)
        data = camp.phase(args.cell, args.phase)
        if data["state"] != "pending":
            raise Refused("phase-not-pending", state=data["state"])
        problems = start_problems(camp, cell, args.phase)
        if problems:
            raise Refused("conditions-unmet", problems=problems)
        data.update(state="running", started=stamp(), ended=None)
        camp.save_phase(args.cell, args.phase, data)
        camp.emit("phase-start", cell=args.cell, phase=args.phase, attempt=data["attempt"])
        return {"ok": True, "cell": args.cell, "phase": args.phase, "state": "running", "attempt": data["attempt"]}


def cmd_phase_retry(camp: Campaign, args) -> dict:
    with camp.lock():
        cell = camp.cell(args.cell)
        data = camp.phase(args.cell, args.phase)
        if cell["state"] != "open" or data["state"] != "running":
            raise Refused("phase-not-running", cell_state=cell["state"], state=data["state"])
        if data["retries"] >= camp.budget("stall_retries"):
            raise Refused("stall-retries-spent", retries=data["retries"])
        data.update(attempt=data["attempt"] + 1, retries=data["retries"] + 1, started=stamp(), ended=None)
        camp.save_phase(args.cell, args.phase, data)
        camp.emit("phase-retry", cell=args.cell, phase=args.phase, attempt=data["attempt"])
        return {"ok": True, "cell": args.cell, "phase": args.phase, "state": "running", "attempt": data["attempt"]}


def cmd_phase_close(camp: Campaign, args) -> dict:
    with camp.lock():
        cell = camp.cell(args.cell)
        data = camp.phase(args.cell, args.phase)
        if cell["state"] != "open":
            raise Refused("cell-not-open", state=cell["state"])
        if data["state"] not in ("running", "failed"):
            raise Refused("phase-not-running", state=data["state"],
                          hint="phase start first" if data["state"] == "pending" else None)
        proof = data["proof"]
        if not str(proof.get("check") or "").strip() or not str(proof.get("expect") or "").strip():
            raise Usage("proof.check and proof.expect must be filled")
        index, log = 0, f"logs/{args.cell}-{args.phase}-{data['attempt']}.log"
        while (camp.dir / log).exists():
            index += 1
            log = f"logs/{args.cell}-{args.phase}-{data['attempt']}-{index}.log"
        (camp.dir / "logs").mkdir(exist_ok=True)
        (camp.dir / log).write_text("", encoding="utf-8")
        attempt = data["attempt"]
    done = execute(proof["check"], camp.root, camp.budget("check_timeout_s"), camp.dir / log)
    ok = done["error"] is None and done["exit_code"] == 0 and proof["expect"] in done["output"]
    with camp.lock():
        data = camp.phase(args.cell, args.phase)
        if data["attempt"] != attempt or data["state"] not in ("running", "failed"):
            raise Refused("phase-changed-meanwhile", state=data["state"], attempt=data["attempt"])
        data.update(state="done" if ok else "failed", ended=done["ended"])
        data["proof"].update(ok=ok, source=log)
        data["by"] = {"tool": TOOL, "log": log, "executor": camp.executor}
        if done["error"]:
            data["by"]["error"] = done["error"]
        camp.save_phase(args.cell, args.phase, data)
        camp.emit("phase-close", cell=args.cell, phase=args.phase, ok=ok, log=log)
    result = {"ok": ok, "cell": args.cell, "phase": args.phase, "state": data["state"], "log": log,
              "exit_code": done["exit_code"], "error": done["error"]}
    if not ok:
        raise Refused("proof-failed", **{k: v for k, v in result.items() if k != "ok"})
    return result


# --- commands: gates ------------------------------------------------------------

def check_force(camp: Campaign, names: list[str], force: str | None, reason: str | None, **target) -> None:
    if force is None:
        return
    if force not in names:
        raise Usage(f"--force names {force!r}, which this run does not include")
    if not reason or not reason.strip():
        raise Usage("--force needs --reason")
    if not camp.fingerprint(camp.check(force), camp.root)["reusable"]:
        return  # rule 1 runs it anyway; that is not a forced rerun
    cap = force_cap(camp, force)
    if cap:
        ident = camp.open_hitl("forced-rerun-cap",
                               f"검사 {force}의 강제 재실행이 상한({cap})에 닿았다. 한 번 더 허용하는가?",
                               check=force, cap=cap)
        raise Refused("forced-rerun-cap", check=force, cap=cap, hitl=ident)


def cmd_gate_run(camp: Campaign, args) -> dict:
    group = args.group
    with camp.lock():
        names = camp.group(group)["checks"]
        gate = camp.gate(group)
        if gate["state"] not in ("pending", "failed"):
            raise Refused(f"gate-{gate['state']}",
                          hint="gate reopen first" if gate["state"] == "passed" else "hitl answer first")
        if running_elsewhere(gate):
            raise Refused("gate-running", pid=gate.get("pid"))
        problems = gate_blockers(camp, group)
        if problems:
            raise Refused("members-not-done", problems=problems)
        if not names:
            raise Refused("no-checks")
        check_force(camp, names, args.force, args.reason)
        members = member_attempts(camp, group)
        gate.update(started=stamp(), ended=None, pid=os.getpid(), runs=gate.get("runs", 0) + 1)
        camp.save_gate(group, gate)
    results, hitl = run_checks(camp, names, "gate", args.force, args.reason, {"group": group})
    with camp.lock():
        gate = camp.gate(group)
        state = gate_state(results)
        note = None
        if gate_blockers(camp, group) or member_attempts(camp, group) != members:
            state, note = "pending", "members-changed"
        ended = now()
        seconds = round((ended - (parse_time(gate.get("started")) or ended)).total_seconds(), 3)
        gate.update(state=state, ended=stamp(ended), checks=results, hitl=hitl if state == "waiting" else None)
        gate.pop("pid", None)
        closed = []
        if state == "passed":
            gate["members"] = list(members)
            for cell in camp.members(group):
                if cell["review"] == "none":
                    cell["state"] = "closed"
                    closed.append(cell["cell"])
            camp.save_decl()
        camp.save_gate(group, gate)
        for name in closed:
            camp.emit("cell-close", cell=name)
        camp.emit("gate", group=group, state=state, seconds=seconds, note=note,
                  checks=[{"check": r["check"], "outcome": r["outcome"]} for r in results])
    out = {"ok": state == "passed", "group": group, "state": state, "checks": results, "closed": closed,
           "hitl": hitl, "note": note}
    if state != "passed":
        raise Refused(f"gate-{state}", **{k: v for k, v in out.items() if k != "ok"})
    return out


def cmd_gate_reopen(camp: Campaign, args) -> dict:
    with camp.lock():
        camp.group(args.group)
        if camp.gate(args.group)["state"] != "passed":
            raise Refused("gate-not-passed", state=camp.gate(args.group)["state"])
        reopen_gate(camp, args.group, args.reason)
        reset_final(camp)
        return {"ok": True, "group": args.group, "state": "pending"}


def final_settled(camp: Campaign) -> list[str]:
    return [f"{g['group']} is {camp.group_state(g['group'])}" for g in camp.decl["groups"]
            if camp.group_state(g["group"]) not in (GROUP_PASSED, GROUP_STOPPED, GROUP_CANCELLED)]


def final_current(camp: Campaign, gate: dict) -> bool:
    """True when every result of a passed final run still stands for the present input."""
    names = list(dict.fromkeys(camp.decl["final"]["checks"]))
    entries = {item.get("check"): item for item in gate.get("checks") or []}
    for name in names:
        item = entries.get(name)
        if not item or not item.get("receipt"):
            return False
        path = camp.dir / item["receipt"]
        if not path.is_file():
            return False
        receipt = read_json(path)
        fp = camp.fingerprint(camp.check(name), camp.root)
        if not (receipt.get("reusable") and fp["reusable"] and receipt.get("key_sha256") == fp["sha"]
                and receipt.get("result") == "PASS" and camp.usable(receipt)):
            return False
    return True


def cmd_final_run(camp: Campaign, args) -> dict:
    with camp.lock():
        names = camp.decl["final"]["checks"]
        gate = camp.gate(FINAL)
        if gate["state"] == "waiting":
            raise Refused("final-waiting", hint="hitl answer first")
        if running_elsewhere(gate):
            raise Refused("final-running", pid=gate.get("pid"))
        unsettled = final_settled(camp)
        if unsettled:
            raise Refused("groups-not-settled", problems=unsettled)
        if not names:
            raise Refused("no-checks")
        if gate["state"] == "passed" and args.force is None and final_current(camp, gate):
            return {"ok": True, "state": "passed", "note": "already-passed", "checks": gate.get("checks")}
        check_force(camp, names, args.force, args.reason)
        gate.update(started=stamp(), ended=None, pid=os.getpid(), runs=gate.get("runs", 0) + 1)
        camp.save_gate(FINAL, gate)
    results, hitl = run_checks(camp, names, "final", args.force, args.reason, {"final": True})
    with camp.lock():
        gate = camp.gate(FINAL)
        state = gate_state(results)
        note = None
        if final_settled(camp):
            state, note = "pending", "groups-changed"
        ended = now()
        seconds = round((ended - (parse_time(gate.get("started")) or ended)).total_seconds(), 3)
        gate.update(state=state, ended=stamp(ended), checks=results, hitl=hitl if state == "waiting" else None)
        gate.pop("pid", None)
        gate.pop("members", None)
        camp.save_gate(FINAL, gate)
        camp.emit("final", state=state, seconds=seconds, note=note,
                  checks=[{"check": r["check"], "outcome": r["outcome"]} for r in results])
    out = {"ok": state == "passed", "state": state, "checks": results, "hitl": hitl, "note": note}
    if state != "passed":
        raise Refused(f"final-{state}", **{k: v for k, v in out.items() if k != "ok"})
    return out


def cmd_check_invalidate(camp: Campaign, args) -> dict:
    with camp.lock():
        camp.check(args.check)
        event = camp.emit("invalidate", check=args.check, reason=args.reason)
        write_json(camp.dir / "receipts" / args.check / "invalidated.json",
                   {"utc": event["utc"], "reason": args.reason})
        return {"ok": True, "check": args.check, "utc": event["utc"]}


# --- commands: exploration ------------------------------------------------------

def explorations(camp: Campaign) -> dict[str, dict]:
    found = {e["id"]: {**e, "open": True} for e in camp.events() if e.get("event") == "explore-open" and e.get("id")}
    for event in camp.events():
        if event.get("event") == "direction" and event.get("id") in found:
            found[event["id"]]["open"] = False
    return found


def cmd_explore_open(camp: Campaign, args) -> dict:
    with camp.lock():
        cell = camp.cell(args.cell)
        if cell["state"] != "open":
            raise Refused("cell-not-open", state=cell["state"])
        known = explorations(camp)
        if len(known) >= camp.budget("explore_rounds"):
            raise Refused("explore-rounds-spent", rounds=len(known))
        ident = f"x{len(known) + 1}"
        camp.emit("explore-open", id=ident, cell=args.cell, reason=args.reason)
        return {"ok": True, "id": ident, "cell": args.cell}


def cmd_check_run(camp: Campaign, args) -> dict:
    target = Path(args.target).resolve()
    with camp.lock():
        check = camp.check(args.check)
        found = explorations(camp).get(args.explore)
        if not found or not found["open"]:
            raise Refused("no-open-exploration", explore=args.explore)
        cell = camp.cell(found["cell"])
        if args.check not in camp.group(cell["group"])["checks"]:
            raise Refused("check-not-in-group", group=cell["group"])
        if not target.is_dir():
            raise Usage(f"{target}: not a directory")
        base = camp.root.resolve()
        seen = {r.get("target") for r in camp.all_receipts() if r.get("explore") == args.explore}
        seen.discard(str(base))
        if target != base and str(target) not in seen and len(seen) >= camp.budget("explore_candidates"):
            raise Refused("explore-candidates-spent", candidates=sorted(seen))
        plan = decide(camp, check, target, "explore", False)
        if plan["action"] == "run":
            plan["covers"] = covered_inputs(camp, check, target)
        else:
            result = skip_check(camp, check, "explore", plan)
    if plan["action"] == "run":
        result = run_check(camp, check, target, "explore", plan, explore=args.explore)
    ok = result["outcome"] in ("reused", "covered") or result["result"] == "PASS"
    out = {"ok": ok, "explore": args.explore, "target": str(target), **result}
    if not ok:
        raise Refused(f"check-{result['outcome']}", **{k: v for k, v in out.items() if k != "ok"})
    return out


def cmd_explore_close(camp: Campaign, args) -> dict:
    with camp.lock():
        found = explorations(camp).get(args.id)
        if not found or not found["open"]:
            raise Refused("no-open-exploration", explore=args.id)
        camp.emit("direction", id=args.id, cell=found["cell"], selected=args.selected, ref=args.ref, note=args.note)
        return {"ok": True, "id": args.id, "selected": args.selected, "ref": args.ref}


# --- commands: cells ------------------------------------------------------------

def review_round(camp: Campaign, cell: dict) -> int:
    rounds = [e.get("round", 0) for e in camp.events() if e.get("event") == "review" and e.get("cell") == cell["cell"]]
    return max([int(cell.get("review_base") or 0)] + [r for r in rounds if isinstance(r, int)])


def ensure_group(camp: Campaign, group: str, checks: list[str] | None) -> list[str] | None:
    if camp.has_group(group):
        return None
    if not NAME.match(group):
        raise Usage(f"group name {group!r} is not a plain file name")
    if not checks:
        raise Usage(f"group {group!r} is not declared; give its checks")
    for name in checks:
        camp.check(name)
    camp.decl["groups"].append({"group": group, "checks": list(checks)})
    return list(checks)


def clear_stalls(camp: Campaign, cell: str | None = None, group: str | None = None, final: bool = False) -> None:
    monitor = camp.monitor()
    keep = {}
    for key, count in monitor["stalls"].items():
        kind, key_cell, _phase, key_group = (key.split("|") + ["", "", "", ""])[:4]
        if (cell and key_cell == cell) or (group and key_group == group) or (final and kind == "final"):
            continue
        keep[key] = count
    if keep != monitor["stalls"]:
        monitor["stalls"] = keep
        camp.save_monitor(monitor)


def cmd_cell_add(camp: Campaign, args) -> dict:
    spec = read_json(Path(args.spec))
    if not isinstance(spec, dict):
        raise Usage("the spec must be a JSON object")
    name, goal, group = spec.get("cell"), spec.get("goal"), spec.get("group")
    if not isinstance(name, str) or not NAME.match(name) or name.startswith("_"):
        raise Usage("spec.cell must be a plain folder name")
    for key, value in (("goal", goal), ("group", group), ("reason", spec.get("reason"))):
        if not isinstance(value, str) or not value.strip():
            raise Usage(f"spec.{key} must be a non-empty string")
    with camp.lock():
        if any(c["cell"] == name for c in camp.decl["cells"]):
            raise Refused("cell-exists", cell=name)
        for phase in camp.phases:
            proof = spec.get(phase)
            if not isinstance(proof, dict) or not all(isinstance(proof.get(k), str) and proof[k].strip()
                                                      for k in ("check", "expect")):
                raise Usage(f"spec.{phase} needs check and expect")
        parent = spec.get("parent")
        needs = spec.get("needs") or []
        for other in ([parent] if parent else []) + list(needs):
            camp.cell(other)
        autos = sum(1 for c in camp.cells() if c["origin"] == "auto")
        if autos >= camp.budget("auto_cells") + camp.allows("auto-cell-cap"):
            ident = camp.open_hitl("auto-cell-cap", "스스로 만드는 cell이 상한에 닿았다. 하나 더 허용하는가?")
            raise Refused("auto-cell-cap", autos=autos, hitl=ident)
        created = ensure_group(camp, group, spec.get("group_checks"))
        review, base = "none", 0
        if parent:
            review = camp.cell(parent)["review"]
            base = review_round(camp, camp.cell(parent))
        folder = camp.dir / "cells" / name
        (folder / "phases").mkdir(parents=True, exist_ok=True)
        body = [f"# {name}", "", "이 파일만 읽고 이 cell을 이어갈 수 있게 쓴다. 항목이 비면 \"없음\"이라고 적는다.", ""]
        for section in SECTIONS:
            body += [f"## {section}", "", goal if section == "목표" else "없음", ""]
        (folder / "cell.md").write_text("\n".join(body), encoding="utf-8")
        for phase in camp.phases:
            camp.save_phase(name, phase, {
                "schema_version": SCHEMA, "cell": name, "phase": phase, "state": "pending", "attempt": 1,
                "started": None, "ended": None,
                "proof": {"check": spec[phase]["check"], "expect": spec[phase]["expect"], "ok": False, "source": None},
                "by": None})
        entry = {"cell": name, "goal": goal, "group": group, "needs": list(needs), "review": review,
                 "origin": "auto", "parent": parent, "state": "open"}
        if base:
            entry["review_base"] = base
        camp.decl["cells"].append(entry)
        camp.save_decl()
        fields = {"cell": name, "group": group, "parent": parent, "review": review, "reason": spec["reason"]}
        if created:
            fields["group_checks"] = created
        camp.emit("cell-add", **fields)
        reopened = reopen_gate(camp, group, f"member added: {name}")
        reset_final(camp)
        return {"ok": True, "cell": name, "group": group, "review": review, "group_created": bool(created),
                "gate_reopened": reopened}


def cmd_cell_cancel(camp: Campaign, args) -> dict:
    with camp.lock():
        cell = camp.cell(args.cell)
        if cell["state"] != "open":
            raise Refused("cell-not-open", state=cell["state"])
        cell["state"] = "cancelled"
        camp.save_decl()
        camp.emit("cell-cancel", cell=args.cell, reason=args.reason)
        return {"ok": True, "cell": args.cell, "state": "cancelled"}


def cmd_cell_move(camp: Campaign, args) -> dict:
    with camp.lock():
        cell = camp.cell(args.cell)
        if cell["state"] != "open":
            raise Refused("cell-not-open", state=cell["state"],
                          hint="gate reopen first" if cell["state"] == "closed" else None)
        source = cell["group"]
        if source == args.group:
            raise Refused("same-group")
        created = ensure_group(camp, args.group, args.checks.split(",") if args.checks else None)
        cell["group"] = args.group
        camp.save_decl()
        fields = {"cell": args.cell, "from": source, "to": args.group, "reason": args.reason}
        if created:
            fields["group_checks"] = created
        camp.emit("regroup", **fields)
        reopened = [g for g in (source, args.group) if reopen_gate(camp, g, f"member moved: {args.cell}")]
        reset_final(camp)
        return {"ok": True, "cell": args.cell, "from": source, "to": args.group, "gates_reopened": reopened}


def cmd_cell_reopen(camp: Campaign, args) -> dict:
    with camp.lock():
        cell = camp.cell(args.cell)
        last = camp.phases[-1]
        data = camp.phase(args.cell, last)
        if cell["state"] not in ("open", "closed"):
            raise Refused("cell-not-reopenable", state=cell["state"])
        if data["state"] != "done":
            raise Refused("phase-not-done", phase=last, state=data["state"])
        data.update(state="pending", attempt=data["attempt"] + 1, retries=0, started=None, ended=None, by=None)
        data["proof"].update(ok=False, source=None)
        camp.save_phase(args.cell, last, data)
        cell["state"] = "open"
        camp.save_decl()
        camp.emit("cell-reopen", cell=args.cell, reason=args.reason)
        reopened = reopen_gate(camp, cell["group"], f"cell reopened: {args.cell}", ("passed", "failed"))
        reset_final(camp)
        return {"ok": True, "cell": args.cell, "phase": last, "attempt": data["attempt"], "gate_reopened": reopened}


def cmd_cell_stop(camp: Campaign, args) -> dict:
    with camp.lock():
        cell = camp.cell(args.cell)
        if cell["state"] != "open":
            raise Refused("cell-not-open", state=cell["state"])
        cell["state"] = "stopped"
        camp.save_decl()
        camp.emit("cell-stop", cell=args.cell, reason=args.reason)
        return {"ok": True, "cell": args.cell, "state": "stopped", "group": cell["group"],
                "group_state": camp.group_state(cell["group"])}


def cmd_cell_resume(camp: Campaign, args) -> dict:
    if not args.ref or not args.ref.strip():
        raise Usage("cell resume needs --ref (where the user's approval is recorded)")
    with camp.lock():
        cell = camp.cell(args.cell)
        if cell["state"] != "stopped":
            raise Refused("cell-not-stopped", state=cell["state"])
        cell["state"] = "open"
        camp.save_decl()
        for phase in camp.phases:
            data = camp.phase(args.cell, phase)
            if data["retries"]:
                data["retries"] = 0
                camp.save_phase(args.cell, phase, data)
        camp.emit("cell-resume", cell=args.cell, ref=args.ref)
        clear_stalls(camp, cell=args.cell)
        return {"ok": True, "cell": args.cell, "state": "open"}


def cmd_cell_close(camp: Campaign, args) -> dict:
    with camp.lock():
        cell = camp.cell(args.cell)
        gate = camp.gate(cell["group"])
        if cell["review"] == "none":
            raise Refused("cell-not-marked", hint="an unmarked cell closes when its gate passes")
        if cell["state"] != "open" or gate["state"] != "passed":
            raise Refused("conditions-unmet", cell_state=cell["state"], gate_state=gate["state"])
        passed_at = parse_time(gate.get("ended"))
        reviews = [e for e in camp.events() if e.get("event") == "review" and e.get("cell") == args.cell]
        latest = reviews[-1] if reviews else None
        when = parse_time(latest.get("utc")) if latest else None
        if not latest or passed_at is None or when is None or when <= passed_at \
                or latest.get("ran") is not True or latest.get("clean") is not True:
            raise Refused("no-clean-review-after-gate", latest_round=latest.get("round") if latest else None)
        cell["state"] = "closed"
        if args.cell not in gate["members"]:
            gate["members"].append(args.cell)
            camp.save_gate(cell["group"], gate)
        camp.save_decl()
        camp.emit("cell-close", cell=args.cell)
        return {"ok": True, "cell": args.cell, "state": "closed"}


def cmd_review_record(camp: Campaign, args) -> dict:
    review = read_json(Path(args.review))
    if not isinstance(review, dict):
        raise Usage("the review must be a JSON object")
    for key, kind in (("round", int), ("ran", bool), ("blocking", list), ("non_blocking", list), ("clean", bool)):
        if not isinstance(review.get(key), kind) or (kind is int and isinstance(review.get(key), bool)):
            raise Usage(f"review.{key} must be {kind.__name__}")
    with camp.lock():
        cell = camp.cell(args.cell)
        if cell["state"] != "open":
            raise Refused("cell-not-open", state=cell["state"])
        expected = review_round(camp, cell) + 1
        if review["round"] != expected:
            raise Refused("round-out-of-order", expected=expected)
        cap = (camp.decl.get("review") or {}).get("round_cap", 3)
        if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1:
            raise Usage("campaign.json: review.round_cap must be a positive integer")
        camp.emit("review", cell=args.cell, round=review["round"], ran=review["ran"],
                  blocking=review["blocking"], non_blocking=review["non_blocking"], clean=review["clean"])
        stopped = False
        if review["round"] >= cap and review["blocking"] and not review["clean"]:
            cell["state"] = "stopped"
            camp.save_decl()
            camp.emit("cell-stop", cell=args.cell, reason="review-cap")
            stopped = True
        return {"ok": True, "cell": args.cell, "round": review["round"], "stopped": stopped}


# --- commands: handing over to a person -----------------------------------------

def cmd_hitl_open(camp: Campaign, args) -> dict:
    with camp.lock():
        if args.cell:
            cell = camp.cell(args.cell)
            if cell["state"] != "open":
                raise Refused("cell-not-open", state=cell["state"])
            ident = camp.open_hitl(args.kind, args.question, cell=args.cell)
            cell["state"] = "waiting"
            camp.save_decl()
            return {"ok": True, "id": ident, "cell": args.cell, "state": "waiting"}
        group = FINAL if args.final else args.group
        if not args.final:
            camp.group(group)
        gate = camp.gate(group)
        if running_elsewhere(gate):
            raise Refused("gate-running", pid=gate.get("pid"))
        allowed = ("pending", "failed", "passed") if args.final else ("pending", "failed")
        if gate["state"] not in allowed:
            raise Refused(f"gate-{gate['state']}",
                          hint="use --cell for a passed gate" if gate["state"] == "passed" else None)
        target = {"final": True} if args.final else {"group": group}
        ident = camp.open_hitl(args.kind, args.question, **target)
        gate.update(state="waiting", hitl=ident)
        camp.save_gate(group, gate)
        return {"ok": True, "id": ident, **target, "state": "waiting"}


def cmd_hitl_answer(camp: Campaign, args) -> dict:
    if not args.ref or not args.ref.strip():
        raise Usage("hitl answer needs --ref (where the user's answer is recorded)")
    with camp.lock():
        item = next((h for h in camp.hitls() if h.get("id") == args.id), None)
        if item is None:
            raise Usage(f"unknown hitl id {args.id!r}")
        if item["answer"] is not None:
            raise Refused("already-answered")
        camp.emit("hitl-answer", id=args.id, ref=args.ref, allow=bool(args.allow))
        released = {}
        if item.get("group"):
            gate = camp.gate(item["group"])
            if gate["state"] == "waiting":
                gate.update(state="pending", hitl=None)
                camp.save_gate(item["group"], gate)
                released["group"] = item["group"]
            clear_stalls(camp, group=item["group"])
        elif item.get("final"):
            gate = camp.gate(FINAL)
            if gate["state"] == "waiting":
                gate.update(state="pending", hitl=None)
                camp.save_gate(FINAL, gate)
                released["final"] = True
            clear_stalls(camp, final=True)
        elif item.get("cell"):
            cell = camp.cell(item["cell"])
            if cell["state"] == "waiting":
                cell["state"] = "open"
                camp.save_decl()
                released["cell"] = item["cell"]
            clear_stalls(camp, cell=item["cell"])
        return {"ok": True, "id": args.id, "allow": bool(args.allow), "released": released}


def cmd_event(camp: Campaign, args) -> dict:
    try:
        data = json.loads(args.json)
    except json.JSONDecodeError as exc:
        raise Usage(f"the event is not JSON ({exc})") from None
    if not isinstance(data, dict) or not isinstance(data.get("event"), str) or not data["event"].strip():
        raise Usage("the event needs an event name")
    if data["event"] in RESERVED_EVENTS:
        raise Usage(f"the executor writes {data['event']!r} itself")
    data.pop("utc", None)
    name = data.pop("event")
    with camp.lock():
        return {"ok": True, "event": camp.emit(name, **data)}


# --- status, actionable list, metrics -------------------------------------------

def stale_checks(camp: Campaign, gate: dict) -> list[str]:
    """Checks of a passed gate whose recorded input differs from the present one."""
    stale = []
    for item in gate.get("checks") or []:
        pointer = item.get("via") if item.get("outcome") == "covered" else item.get("receipt")
        if not pointer or not (camp.dir / pointer).is_file():
            continue
        receipt = read_json(camp.dir / pointer)
        with contextlib.suppress(Usage):
            check = camp.check(item["check"])
            if not receipt.get("reusable"):
                continue
            current = camp.input_ids(camp.root, list(check.get("inputs") or []))
            recorded = ((receipt.get("covers") or {}).get(item["check"]) or {}).get("inputs") \
                if item.get("outcome") == "covered" else (receipt.get("key") or {}).get("inputs")
            if recorded != current:
                stale.append(item["check"])
    return stale


def uncommitted_checks(camp: Campaign, gate: dict) -> list[str]:
    """Checks of a passed gate whose inputs were changed and not committed. Display only, like STALE."""
    found = []
    for item in gate.get("checks") or []:
        with contextlib.suppress(Usage):
            inputs = [str(i) for i in camp.check(item["check"]).get("inputs") or []]
            code, out = git(camp.root, "status", "--porcelain", "--", *inputs) if inputs else (0, "")
            if code == 0 and out:
                found.append(item["check"])
    return found


def item_key(kind: str, cell: str = "", phase: str = "", group: str = "") -> str:
    return "|".join((kind, cell, phase, group))


def actionable(camp: Campaign) -> list[dict]:
    """What the main agent can do without a person, in the order the skill gives."""
    items: list[dict] = []
    pm = camp.pm
    held = {g["group"]: camp.group_state(g["group"]) in (GROUP_STOPPED, GROUP_WAITING) for g in camp.decl["groups"]}
    cells = [c for c in camp.cells() if c["state"] == "open" and not held.get(c["group"], False)]
    phases = {c["cell"]: {p: camp.phase(c["cell"], p) for p in camp.phases} for c in cells}

    def add(order: int, kind: str, text: str, command: str, **where) -> None:
        items.append({"order": order, "kind": kind, "text": text, "command": command, **where,
                      "key": item_key(kind, where.get("cell", ""), where.get("phase", ""), where.get("group", ""))})

    for cell in cells:
        for phase in camp.phases:
            if phases[cell["cell"]][phase]["state"] == "running":
                add(1, "phase-running", f"실행 중인 단계 {cell['cell']}/{phase}를 끝낸다",
                    f"{pm} phase close {cell['cell']} {phase}", cell=cell["cell"], phase=phase,
                    started=phases[cell["cell"]][phase].get("started"),
                    retries=phases[cell["cell"]][phase].get("retries", 0))
    for group in camp.decl["groups"]:
        name = group["group"]
        if not held[name] and camp.gate(name)["state"] == "pending" and not gate_blockers(camp, name):
            add(2, "gate-run", f"묶음 {name}의 gate를 돌린다", f"{pm} gate run {name}", group=name)
    for cell in cells:
        if cell["review"] != "none" and camp.gate(cell["group"])["state"] == "passed":
            add(3, "review", f"cell {cell['cell']}의 새 맥락 검토를 받고 닫는다",
                f"{pm} review record {cell['cell']} <review.json> 뒤 {pm} cell close {cell['cell']}",
                cell=cell["cell"], group=cell["group"])
    for group in camp.decl["groups"]:
        name = group["group"]
        if not held[name] and camp.gate(name)["state"] == "failed" and camp.members(name):
            add(4, "gate-failed", f"실패한 gate {name}의 원인을 가르고 지목한 cell을 다시 연다",
                f"{pm} cell reopen <cell> --reason <원인>", group=name)
    for cell in cells:
        for phase in camp.phases:
            if phases[cell["cell"]][phase]["state"] == "failed":
                add(5, "phase-failed", f"실패한 단계 {cell['cell']}/{phase}를 고쳐 다시 닫는다",
                    f"{pm} phase close {cell['cell']} {phase}", cell=cell["cell"], phase=phase)
    for cell in cells:
        for phase in camp.phases:
            if phases[cell["cell"]][phase]["state"] == "pending":
                if not start_problems(camp, cell, phase):
                    add(6, "phase-start", f"단계 {cell['cell']}/{phase}를 시작한다",
                        f"{pm} phase start {cell['cell']} {phase}", cell=cell["cell"], phase=phase)
                break
    final = camp.gate(FINAL)
    if camp.decl["final"]["checks"] and camp.decl["groups"] and not final_settled(camp):
        if final["state"] == "failed":
            add(7, "final", "마지막 전체 검증이 실패했다. 원인을 가려 그 묶음의 cell을 다시 연다",
                f"{pm} cell reopen <cell> --reason <원인>")
        elif final["state"] == "pending" or (final["state"] == "passed" and stale_checks(camp, final)):
            add(7, "final", "마지막 전체 검증을 돌린다", f"{pm} final run")
    return sorted(items, key=lambda item: item["order"])


def briefing_due(camp: Campaign) -> dict:
    events = camp.events()
    briefing = camp.decl.get("briefing") or {}
    opened = next((e for e in events if e.get("event") == "campaign-open"), None)
    last_index = max([i for i, e in enumerate(events) if e.get("event") == "brief"], default=-1)
    since = events[last_index + 1:]
    on_phase = bool(briefing.get("on_phase")) and any(e.get("event") in ("phase-close", "gate", "final") for e in since)
    by_hours = False
    hours = briefing.get("every_hours")
    anchor = parse_time(events[last_index].get("utc")) if last_index >= 0 else \
        (parse_time(opened.get("utc")) if opened else None)
    if isinstance(hours, (int, float)) and not isinstance(hours, bool) and hours > 0 and anchor is not None:
        by_hours = now() - anchor >= timedelta(hours=hours)
    return {"on_phase": on_phase, "hours": by_hours}


def build_status(camp: Campaign) -> dict:
    cells = []
    for cell in camp.cells():
        phases = {}
        for phase in camp.phases:
            data = camp.phase(cell["cell"], phase)
            phases[phase] = {"state": data["state"], "attempt": data["attempt"]}
        cells.append({"cell": cell["cell"], "group": cell["group"], "state": cell["state"],
                      "review": cell["review"], "origin": cell["origin"], "phases": phases})
    groups, stale_all = [], []
    for group in camp.decl["groups"]:
        name = group["group"]
        gate = camp.gate(name)
        stale = stale_checks(camp, gate) if gate["state"] == "passed" else []
        stale_all += [{"group": name, "check": check} for check in stale]
        groups.append({"group": name, "state": camp.group_state(name), "gate": gate["state"],
                       "stale": "STALE" if stale else None, "stale_checks": stale,
                       "uncommitted_checks": uncommitted_checks(camp, gate) if gate["state"] == "passed" else [],
                       "members": [c["cell"] for c in camp.members(name)],
                       "checks": [{"check": i.get("check"), "outcome": i.get("outcome"), "result": i.get("result"),
                                   "stale": "STALE" if i.get("check") in stale else None}
                                  for i in gate.get("checks") or []]})
    final = camp.gate(FINAL)
    final_stale = stale_checks(camp, final) if final["state"] == "passed" else []
    monitor = camp.monitor()
    return {
        "ok": True, "campaign": camp.decl.get("id"), "instance": str(camp.dir), "target": str(camp.root),
        "cells": cells, "groups": groups,
        "final": {"state": final["state"], "stale": "STALE" if final_stale else None, "stale_checks": final_stale},
        "stale": stale_all,
        "actionable": [{k: v for k, v in item.items() if k != "key"} for item in actionable(camp)],
        "waiting": [{k: v for k, v in h.items() if k not in ("answer", "event")} for h in camp.hitls()
                    if h["answer"] is None],
        "briefing_due": briefing_due(camp),
        "monitor": {"session": monitor["session"], "total_blocks": monitor["total_blocks"],
                    "released": monitor["released"]},
    }


def build_metrics(camp: Campaign) -> dict:
    counts = {name: 0 for name in ("ran", "forced", "error", "reused", "covered", "refused")}
    for event in camp.events():
        if event.get("event") == "check" and event.get("outcome") in counts:
            counts[event["outcome"]] += 1
    executed = counts["ran"] + counts["forced"] + counts["error"]
    skipped = counts["reused"] + counts["covered"]
    gates: dict[str, dict] = {}
    for event in camp.events():
        if event.get("event") in ("gate", "final"):
            name = event.get("group") or FINAL
            entry = gates.setdefault(name, {"runs": 0, "seconds": 0.0})
            entry["runs"] += 1
            entry["seconds"] = round(entry["seconds"] + float(event.get("seconds") or 0), 3)
    receipts = camp.all_receipts()
    return {
        "ok": True, "checks": counts, "executed": executed, "skipped": skipped,
        "receipt_files": len(receipts),
        "forced_receipts": sum(1 for r in receipts if r.get("forced")),
        "reused_ratio": round(counts["reused"] / (executed + skipped), 3) if executed + skipped else 0.0,
        "covered_ratio": round(counts["covered"] / (executed + skipped), 3) if executed + skipped else 0.0,
        "check_seconds": round(sum(float(r.get("seconds") or 0) for r in receipts), 3),
        "gates": gates,
    }


def cmd_status(camp: Campaign, args) -> dict:
    return build_status(camp)


def cmd_metrics(camp: Campaign, args) -> dict:
    return build_metrics(camp)


def cmd_brief(camp: Campaign, args) -> dict:
    if args.done:
        with camp.lock():
            event = camp.emit("brief", path=args.done)
            monitor = camp.monitor()
            monitor["last_brief_utc"] = event["utc"]
            camp.save_monitor(monitor)
            return {"ok": True, "brief": event}
    status = build_status(camp)
    return {"ok": True, "campaign": status["campaign"], "instance": status["instance"],
            "waiting": status["waiting"], "cells": status["cells"], "groups": status["groups"],
            "final": status["final"], "actionable": status["actionable"], "metrics": build_metrics(camp),
            "write_to": str(camp.dir / "briefing.md")}


def cmd_monitor_reset(camp: Campaign, args) -> dict:
    with camp.lock():
        try:
            total = camp.monitor()["total_blocks"]
        except Usage:
            total = 0
        camp.save_monitor({**json.loads(json.dumps(DEFAULT_MONITOR)), "total_blocks": total})
        return {"ok": True, "monitor": "reset"}


# --- stop hook ------------------------------------------------------------------

def state_digest(camp: Campaign) -> str:
    view = {
        "cells": {c["cell"]: c["state"] for c in camp.cells()},
        "phases": {c["cell"]: {p: [camp.phase(c["cell"], p)["state"], camp.phase(c["cell"], p)["attempt"]]
                               for p in camp.phases} for c in camp.cells()},
        "gates": {name: camp.gate(name)["state"] for name in [g["group"] for g in camp.decl["groups"]] + [FINAL]},
        "executed": sum(1 for r in camp.all_receipts() if r.get("result") in ("PASS", "FAIL")),
        "reviews": sum(1 for e in camp.events() if e.get("event") == "review"),
    }
    return hashlib.sha256(json.dumps(view, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def directive(pm: str, item: dict, count: int, retries: int) -> tuple[str, str]:
    kind = item["kind"]
    spent = kind == "phase-running" and item.get("retries", 0) >= retries   # phase retry would be refused
    if count <= retries and not spent:
        if kind == "phase-running":
            return "retry", (f"새 수행자로 다시 시작한다: {pm} phase retry {item['cell']} {item['phase']}")
        return "retry", f"그 일을 한 번 더 시도한다: {item['command']}"
    if kind in ("phase-running", "review", "phase-failed", "phase-start"):
        return "bypass", (f"이 cell을 멈추고 다음 일로 넘어간다: {pm} cell stop {item['cell']} --reason hang")
    if kind in ("gate-run", "gate-failed"):
        return "bypass", (f"사람에게 넘기고 다음 일로 넘어간다: {pm} hitl open --group {item['group']} "
                          f"--kind hang --question <물음>")
    return "bypass", f"사람에게 넘긴다: {pm} hitl open --final --kind hang --question <물음>"


def hook_decision(camp: Campaign, payload: dict) -> dict | None:
    """Return {'mode': 'guide'|'block', 'text': ...} to keep the agent going, or None to let it stop."""
    session = str(payload.get("session_id") or "unknown")
    monitor = camp.monitor()
    if monitor["session"] and monitor["session"] != session:
        return None                                                    # rule 2
    if not monitor["session"]:
        monitor["session"] = session
        camp.save_monitor(monitor)
    items = actionable(camp)
    if not items:
        return None                                                    # rule 3
    budget = timedelta(minutes=camp.budget("phase_minutes"))
    overdue = next((i for i in items if i["kind"] == "phase-running" and parse_time(i.get("started"))
                    and now() - parse_time(i["started"]) > budget), None)
    if payload.get("background_tasks") and overdue is None:
        return None                                                    # rule 4
    digest = state_digest(camp)
    changed = monitor["digest"] != digest
    if changed:
        monitor.update(digest=digest, unchanged=0, blocks=0, released=False)
    else:
        monitor["unchanged"] += 1
    refused = monitor["refused_since"] > 0 and not changed
    monitor["refused_since"] = 0
    if monitor["released"]:
        camp.save_monitor(monitor)
        return None                                                    # rule 5
    if monitor["blocks"] >= camp.budget("stop_block_cap"):
        camp.emit("monitor-release", blocks=monitor["blocks"])
        monitor["released"] = True
        camp.save_monitor(monitor)
        return None                                                    # rule 6
    monitor["blocks"] += 1
    monitor["total_blocks"] += 1
    monitor["last_block_utc"] = stamp()
    head = f"[{TOOL}] 캠페인 {camp.decl.get('id')}"
    if monitor["unchanged"] >= camp.budget("stall_stops") or overdue is not None or refused:
        item = overdue or items[0]                                     # rule 7
        count = monitor["stalls"].get(item["key"], 0) + 1
        monitor["stalls"][item["key"]] = count
        monitor["unchanged"] = 0
        name, text = directive(camp.pm, item, count, camp.budget("stall_retries"))
        camp.emit("hang", item=item["key"], count=count, directive=name)
        camp.save_monitor(monitor)
        why = "시간 예산을 넘겼다" if overdue is not None else \
            ("같은 입력으로 실패한 검사를 다시 돌리려 했다" if refused else "상태 변화 없이 종료 시도가 이어졌다")
        return {"mode": "block", "text": f"{head}: 정체다({why}, 이 항목 {count}회째). 항목: {item['text']}. "
                                         f"지시: {text}"}
    camp.save_monitor(monitor)
    item = items[0]                                                    # rule 8
    text = (f"{head}: 스스로 할 수 있는 일이 {len(items)}건 남아 있다. 다음 일: {item['text']}. "
            f"명령: {item['command']}. 전체 목록은 {camp.pm} status로 본다.")
    if briefing_due(camp)["hours"]:
        text += " 시간 단위 브리핑 때가 됐다. 브리핑 지시문으로 브리핑을 만든다."
    return {"mode": "guide", "text": text}


def stop_hook(base: Path, harness: str, executor: str) -> None:
    """Never exits non-zero: exit code 2 would be read by the harness as a block."""
    try:
        raw = sys.stdin.read()
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        try:
            camp = Campaign(base, executor)                            # rule 1
        except Usage:
            return
        with camp.lock(wait=5):
            decision = hook_decision(camp, payload)
        if decision is None:
            return
        if harness == "claude" and decision["mode"] == "guide":
            out = {"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": decision["text"]}}
        else:
            out = {"decision": "block", "reason": decision["text"]}
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except BaseException as exc:  # noqa: BLE001 - the hook must not block on its own failure
        sys.stderr.write(f"[{TOOL}] stop-hook error, not blocking: {type(exc).__name__}: {exc}\n")


# --- command line ---------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pm.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--campaigns", type=Path, default=None,
                        help="campaigns directory (default: PM_CAMPAIGNS_DIR or <repo>/campaigns)")
    parser.add_argument("--executor", default=None, help="who runs this (default: PM_EXECUTOR or unknown)")
    top = parser.add_subparsers(dest="command", required=True)

    def leaf(parent, name, handler, *arguments):
        sub = parent.add_parser(name)
        for flags, options in arguments:
            sub.add_argument(*flags, **options)
        sub.set_defaults(handler=handler)
        return sub

    def branch(name):
        return top.add_parser(name).add_subparsers(dest="action", required=True)

    def arg(*flags, **options):
        return flags, options

    leaf(top, "open", cmd_open, arg("--briefing-on-phase", choices=("true", "false"), default=None),
         arg("--briefing-every-hours", type=float, default=None), arg("--skills", default=None))
    leaf(top, "close", cmd_close, arg("--reason", default=None))
    leaf(top, "status", cmd_status)
    leaf(top, "metrics", cmd_metrics)
    leaf(top, "brief", cmd_brief, arg("--done", default=None))
    leaf(top, "event", cmd_event, arg("json"))
    phase = branch("phase")
    for name, handler in (("start", cmd_phase_start), ("close", cmd_phase_close), ("retry", cmd_phase_retry)):
        leaf(phase, name, handler, arg("cell"), arg("phase"))
    gate = branch("gate")
    leaf(gate, "run", cmd_gate_run, arg("group"), arg("--force", default=None), arg("--reason", default=None))
    leaf(gate, "reopen", cmd_gate_reopen, arg("group"), arg("--reason", required=True))
    leaf(branch("final"), "run", cmd_final_run, arg("--force", default=None), arg("--reason", default=None))
    check = branch("check")
    leaf(check, "invalidate", cmd_check_invalidate, arg("check"), arg("--reason", required=True))
    leaf(check, "run", cmd_check_run, arg("check"), arg("--explore", required=True), arg("--target", required=True))
    explore = branch("explore")
    leaf(explore, "open", cmd_explore_open, arg("--cell", required=True), arg("--reason", required=True))
    leaf(explore, "close", cmd_explore_close, arg("id"), arg("--selected", required=True),
         arg("--ref", required=True), arg("--note", default=None))
    cell = branch("cell")
    leaf(cell, "add", cmd_cell_add, arg("spec"))
    leaf(cell, "cancel", cmd_cell_cancel, arg("cell"), arg("--reason", required=True))
    leaf(cell, "move", cmd_cell_move, arg("cell"), arg("group"), arg("--reason", required=True),
         arg("--checks", default=None))
    leaf(cell, "reopen", cmd_cell_reopen, arg("cell"), arg("--reason", required=True))
    leaf(cell, "stop", cmd_cell_stop, arg("cell"), arg("--reason", required=True))
    leaf(cell, "resume", cmd_cell_resume, arg("cell"), arg("--ref", default=None))
    leaf(cell, "close", cmd_cell_close, arg("cell"))
    leaf(branch("review"), "record", cmd_review_record, arg("cell"), arg("review"))
    hitl = branch("hitl")
    opened = leaf(hitl, "open", cmd_hitl_open, arg("--kind", required=True), arg("--question", required=True))
    target = opened.add_mutually_exclusive_group(required=True)
    target.add_argument("--cell", default=None)
    target.add_argument("--group", default=None)
    target.add_argument("--final", action="store_true")
    leaf(hitl, "answer", cmd_hitl_answer, arg("id"), arg("--ref", default=None), arg("--allow", action="store_true"))
    leaf(branch("monitor"), "reset", cmd_monitor_reset)
    leaf(top, "stop-hook", None, arg("--harness", choices=("claude", "codex"), required=True))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        if "stop-hook" in (argv if argv is not None else sys.argv[1:]):
            return 0
        return 2 if exc.code not in (0, None) else 0
    executor = args.executor or os.environ.get("PM_EXECUTOR") or "unknown"
    try:
        base = (args.campaigns or Path(os.environ.get("PM_CAMPAIGNS_DIR") or REPO / "campaigns")).resolve()
    except (OSError, RuntimeError) as exc:
        if args.command != "stop-hook":
            print(json.dumps({"ok": False, "error": f"campaigns directory: {exc}"}, ensure_ascii=False, indent=2))
        return 0 if args.command == "stop-hook" else 2
    if args.command == "stop-hook":
        stop_hook(base, args.harness, executor)
        return 0
    try:
        result = args.handler(Campaign(base, executor), args)
        code = 0
    except Refused as exc:
        result, code = exc.payload, 1
    except Usage as exc:
        result, code = {"ok": False, "error": str(exc)}, 2
    except Exception as exc:  # noqa: BLE001 - malformed files end as unreadable input, not as a traceback
        result, code = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 2
    print(json.dumps({k: v for k, v in result.items() if v is not None or k in ("hitl", "note")},
                     ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    if "stop-hook" in sys.argv[1:]:
        # The hook must end with 0 whatever happens, even when its output cannot be written.
        try:
            main()
            sys.stdout.flush()
        except BaseException:  # noqa: BLE001
            pass
        os._exit(0)
    sys.exit(main())
