#!/usr/bin/env python3
"""Regression tests for the progress-monitor executor.

Run from the repository root:
    python3 -B -m unittest discover -s skills/progress-monitor/tests -v

Each test builds a small Git target and a campaign instance in a temporary
directory. "Rule N" is the order in references/gate.md ("검사 하나를 돌리는 규칙").
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
SCRIPT = HERE.parents[1] / "scripts" / "pm.py"
sys.path.insert(0, str(SCRIPT.parent))
import pm  # noqa: E402

LINT = HERE.parents[3] / "scripts" / "check_campaign.py"
SECTIONS = ("목표", "확인된 사실", "적용한 변경", "검증", "막다른 길", "남은 계획")


class Sandbox:
    """A Git target with alpha/ and beta/, and an instance with groups g-a (a1, a2) and g-b (b1)."""

    def __init__(self, case: unittest.TestCase, budgets: dict | None = None, opened: bool = True,
                 open_args: tuple = ()):
        self.case = case
        self.tmp = Path(tempfile.mkdtemp(prefix="pm-test-"))
        case.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.target = self.tmp / "target"
        self.base = self.tmp / "campaigns"
        self.inst = self.base / "camp"
        self.target.mkdir()
        self.git("init", "-q", ".")
        (self.target / ".gitignore").write_text("scratch/\n", encoding="utf-8")
        self.commit("alpha/state.txt", "alpha-ok\n", "init alpha")
        self.commit("beta/state.txt", "beta-ok\n", "init beta")
        self.flag("flag-ok")
        self.make_instance(budgets or {})
        if opened:
            self.ok("open", *open_args)

    # -- target
    def git(self, *args: str, cwd: Path | None = None) -> str:
        done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                              cwd=cwd or self.target, check=True, capture_output=True, text=True)
        return done.stdout.strip()

    def commit(self, rel: str, text: str, message: str = "change", cwd: Path | None = None) -> None:
        path = (cwd or self.target) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self.git("add", "-A", cwd=cwd)
        self.git("commit", "-q", "-m", message, cwd=cwd)

    def flag(self, text: str) -> None:
        """An ignored file read by the check `flaky`: it changes the result without changing the key."""
        path = self.target / "scratch" / "flag.txt"
        path.parent.mkdir(exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")

    # -- instance
    def make_instance(self, budgets: dict) -> None:
        def check(ident, cmd, expect, inputs, covers=()):
            return {"id": ident, "cmd": cmd, "cwd": ".", "expect": expect, "inputs": list(inputs), "env": [],
                    "covers": list(covers)}

        def cell(name, group):
            return {"cell": name, "goal": f"work on {name}", "group": group, "needs": [], "review": "none",
                    "origin": "plan", "parent": None, "state": "open"}

        decl = {
            "schema_version": 2, "id": "camp", "goal": "regression", "plan_ref": "plan.md",
            "checklist_ref": "plan.md", "declared": "2026-01-01T00:00:00+00:00",
            "target": {"root": "target", "env_probe": []}, "phases": ["prepare", "change"],
            "review": {"round_cap": 3}, "budgets": budgets, "briefing": {"on_phase": True, "every_hours": None},
            "checks": [
                check("alpha-suite", "cat alpha/state.txt", "alpha-ok", ["alpha"], ["alpha-unit"]),
                check("alpha-unit", "cat alpha/state.txt; echo unit", "alpha-ok", ["alpha"]),
                check("beta-unit", "cat beta/state.txt", "beta-ok", ["beta"]),
                check("flaky", "cat scratch/flag.txt", "flag-ok", ["beta"]),
                check("full", "cat alpha/state.txt beta/state.txt | tr -d '\\n'", "alpha-okbeta-ok", ["."]),
            ],
            "groups": [{"group": "g-a", "checks": ["alpha-suite", "alpha-unit"]},
                       {"group": "g-b", "checks": ["beta-unit"]}],
            "final": {"checks": ["full"]},
            "cells": [cell("a1", "g-a"), cell("a2", "g-a"), cell("b1", "g-b")],
        }
        self.inst.mkdir(parents=True)
        (self.base / "ACTIVE").write_text("camp\n", encoding="utf-8")
        self.write(self.inst / "campaign.json", decl)
        self.write(self.inst / "evidence_pointers.json", {"schema_version": 1, "pointers": []})
        (self.inst / "journey.jsonl").write_text("", encoding="utf-8")
        for entry in decl["cells"]:
            folder = self.inst / "cells" / entry["cell"] / "phases"
            folder.mkdir(parents=True)
            body = [f"# {entry['cell']}", ""]
            for section in SECTIONS:
                body += [f"## {section}", "", "없음", ""]
            (folder.parent / "cell.md").write_text("\n".join(body), encoding="utf-8")
            for phase in decl["phases"]:
                self.write(folder / f"{phase}.status.json", {
                    "schema_version": 2, "cell": entry["cell"], "phase": phase, "state": "pending", "attempt": 1,
                    "started": None, "ended": None,
                    "proof": {"check": "echo ready", "expect": "ready", "ok": False, "source": None}, "by": None})

    @staticmethod
    def write(path: Path, data: object) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def read(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def edit_decl(self, change) -> None:
        decl = self.read(self.inst / "campaign.json")
        change(decl)
        self.write(self.inst / "campaign.json", decl)

    def set_check(self, ident: str, **fields) -> None:
        self.edit_decl(lambda d: next(c for c in d["checks"] if c["id"] == ident).update(fields))

    def set_cell(self, name: str, **fields) -> None:
        self.edit_decl(lambda d: next(c for c in d["cells"] if c["cell"] == name).update(fields))

    def set_group(self, name: str, checks: list[str]) -> None:
        self.edit_decl(lambda d: next(g for g in d["groups"] if g["group"] == name).update(checks=checks))

    # -- executor
    def pm(self, *args: str, stdin: str = "") -> tuple[int, object]:
        out = io.StringIO()
        old = sys.stdin
        sys.stdin = io.StringIO(stdin)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = pm.main(["--campaigns", str(self.base), "--executor", "test", *args])
        finally:
            sys.stdin = old
        text = out.getvalue()
        return code, (json.loads(text) if text.strip() else None)

    def ok(self, *args: str) -> dict:
        code, data = self.pm(*args)
        self.case.assertEqual(code, 0, f"{args}: {data}")
        return data

    def no(self, *args: str, code: int = 1) -> dict:
        got, data = self.pm(*args)
        self.case.assertEqual(got, code, f"{args}: {data}")
        return data

    def hook(self, session: str = "s1", harness: str = "claude", **payload) -> dict | None:
        code, data = self.pm("stop-hook", "--harness", harness,
                             stdin=json.dumps({"session_id": session, "hook_event_name": "Stop", **payload}))
        self.case.assertEqual(code, 0)
        return data

    def finish(self, *cells: str) -> None:
        for name in cells:
            for phase in ("prepare", "change"):
                self.ok("phase", "start", name, phase)
                self.ok("phase", "close", name, phase)

    # -- reading state
    def cell(self, name: str) -> dict:
        return next(c for c in self.read(self.inst / "campaign.json")["cells"] if c["cell"] == name)

    def phase(self, name: str, phase: str) -> dict:
        return self.read(self.inst / "cells" / name / "phases" / f"{phase}.status.json")

    def gate(self, group: str) -> dict:
        return self.read(self.inst / "gates" / f"{group}.status.json")

    def receipts(self, check: str | None = None) -> list[dict]:
        pattern = f"receipts/{check or '*'}/*.json"
        return sorted((self.read(p) for p in self.inst.glob(pattern) if p.stem.isdigit()),
                      key=lambda r: (r["check"], r["n"]))

    def events(self, name: str | None = None) -> list[dict]:
        lines = (self.inst / "journey.jsonl").read_text(encoding="utf-8").splitlines()
        return [e for e in map(json.loads, lines) if name is None or e["event"] == name]

    def monitor(self) -> dict:
        return self.read(self.inst / "monitor.json")

    def outcome(self, data: dict, check: str) -> dict:
        return next(item for item in data["checks"] if item["check"] == check)

    def group_state(self, group: str) -> str:
        return next(g for g in self.ok("status")["groups"] if g["group"] == group)["state"]

    def kinds(self) -> list[str]:
        return [item["kind"] for item in self.ok("status")["actionable"]]

    def assert_lint(self) -> None:
        if not LINT.is_file():
            return
        done = subprocess.run([sys.executable, "-B", str(LINT), str(self.inst), "--root", str(self.tmp)],
                              capture_output=True, text=True)
        self.case.assertEqual(done.returncode, 0, done.stdout)

    def spec(self, name: str, group: str, **extra) -> str:
        path = self.tmp / f"spec-{name}.json"
        self.write(path, {"cell": name, "goal": f"work on {name}", "group": group, "parent": None, "needs": [],
                          "reason": "found on the way", "prepare": {"check": "echo ready", "expect": "ready"},
                          "change": {"check": "echo ready", "expect": "ready"}, **extra})
        return str(path)

    def review(self, round_number: int, clean: bool) -> str:
        path = self.tmp / f"review-{round_number}.json"
        blocking = [] if clean else [{"id": f"r{round_number}-1", "where": "alpha/state.txt:1", "settled": False}]
        self.write(path, {"round": round_number, "ran": True, "blocking": blocking, "non_blocking": [],
                          "clean": clean})
        return str(path)


class RuleTests(unittest.TestCase):
    """One check: when it runs, when it is reused, covered or refused."""

    def test_uncommitted_input_always_runs(self):
        sb = Sandbox(self)
        sb.finish("b1")
        (sb.target / "beta" / "state.txt").write_text("beta-ok\nedited, not committed\n", encoding="utf-8")
        self.assertEqual(sb.outcome(sb.ok("gate", "run", "g-b"), "beta-unit")["outcome"], "ran")
        sb.ok("gate", "reopen", "g-b", "--reason", "again")
        self.assertEqual(sb.outcome(sb.ok("gate", "run", "g-b"), "beta-unit")["outcome"], "ran")
        self.assertEqual([r["reusable"] for r in sb.receipts("beta-unit")], [False, False])

    def test_empty_inputs_always_runs(self):
        sb = Sandbox(self)
        sb.set_check("beta-unit", inputs=[])
        sb.finish("b1")
        sb.ok("gate", "run", "g-b")
        sb.ok("gate", "reopen", "g-b", "--reason", "again")
        self.assertEqual(sb.outcome(sb.ok("gate", "run", "g-b"), "beta-unit")["outcome"], "ran")
        self.assertEqual([r["reusable"] for r in sb.receipts("beta-unit")], [False, False])

    def test_ignored_files_do_not_dirty(self):
        sb = Sandbox(self)
        sb.finish("b1")
        (sb.target / "beta" / "scratch").mkdir()
        (sb.target / "beta" / "scratch" / "cache.bin").write_text("ignored\n", encoding="utf-8")
        sb.ok("gate", "run", "g-b")
        self.assertTrue(sb.receipts("beta-unit")[0]["reusable"])
        (sb.target / "beta" / "new.txt").write_text("untracked, not ignored\n", encoding="utf-8")
        sb.ok("gate", "reopen", "g-b", "--reason", "again")
        sb.ok("gate", "run", "g-b")
        self.assertFalse(sb.receipts("beta-unit")[1]["reusable"])

    def test_unresolved_input_always_runs(self):
        for inputs in (["beta/*.txt"], ["beta/typo.txt"], ["scratch"]):   # a glob, a missing path, an ignored path
            sb = Sandbox(self)
            sb.set_check("beta-unit", inputs=inputs)
            sb.finish("b1")
            sb.ok("gate", "run", "g-b")
            sb.commit("beta/state.txt", "broken\n")
            sb.ok("gate", "reopen", "g-b", "--reason", "again")
            data = sb.no("gate", "run", "g-b")                          # must run and fail, not be reused
            self.assertEqual((sb.outcome(data, "beta-unit")["outcome"], sb.outcome(data, "beta-unit")["result"]),
                             ("ran", "FAIL"), inputs)
            self.assertEqual([r["reusable"] for r in sb.receipts("beta-unit")], [False, False], inputs)
        nested = Sandbox(self)                                          # a plain folder inside another repository
        inner = nested.target / "scratch" / "inner"
        (inner / "beta").mkdir(parents=True)
        (inner / "beta" / "state.txt").write_text("beta-ok\n", encoding="utf-8")
        nested.edit_decl(lambda d: d["target"].update(root="target/scratch/inner"))
        nested.finish("b1")
        nested.ok("gate", "run", "g-b")
        (inner / "beta" / "state.txt").write_text("broken\n", encoding="utf-8")
        nested.ok("gate", "reopen", "g-b", "--reason", "again")
        self.assertEqual(nested.outcome(nested.no("gate", "run", "g-b"), "beta-unit")["result"], "FAIL")

    def test_input_changed_during_run_is_not_reusable(self):
        sb = Sandbox(self)
        sb.set_check("beta-unit", cmd="echo more >> beta/state.txt; git add -A; "
                                      "git -c user.name=t -c user.email=t@example.invalid commit -q -m during; "
                                      "cat beta/state.txt")
        sb.finish("b1")
        sb.ok("gate", "run", "g-b")                                     # the check's own input moved while it ran
        receipt = sb.receipts("beta-unit")[0]
        self.assertEqual((receipt["reusable"], receipt["reason"]), (False, "input-changed-during-run"))
        covering = Sandbox(self)                                        # a narrow check with uncommitted input
        covering.set_check("alpha-unit", inputs=["beta"])
        covering.finish("a1", "a2")
        (covering.target / "beta" / "state.txt").write_text("not committed\n", encoding="utf-8")
        data = covering.ok("gate", "run", "g-a")
        self.assertEqual(covering.outcome(data, "alpha-unit")["outcome"], "ran")
        self.assertEqual(covering.receipts("alpha-suite")[0]["covers"], {})

    def test_same_key_pass_is_reused(self):
        sb = Sandbox(self)
        sb.finish("b1")
        sb.ok("gate", "run", "g-b")
        sb.ok("gate", "reopen", "g-b", "--reason", "again")
        entry = sb.outcome(sb.ok("gate", "run", "g-b"), "beta-unit")
        self.assertEqual((entry["outcome"], entry["receipt"]), ("reused", "receipts/beta-unit/1.json"))
        self.assertEqual(len(sb.receipts("beta-unit")), 1)
        self.assertEqual(sb.gate("g-b")["state"], "passed")

    def test_same_key_fail_is_refused(self):
        sb = Sandbox(self)
        sb.commit("beta/state.txt", "broken\n")
        sb.finish("b1")
        first = sb.no("gate", "run", "g-b")
        self.assertEqual((sb.outcome(first, "beta-unit")["outcome"], sb.outcome(first, "beta-unit")["result"]),
                         ("ran", "FAIL"))
        self.assertEqual(sb.gate("g-b")["state"], "failed")
        second = sb.no("gate", "run", "g-b")
        self.assertEqual(sb.outcome(second, "beta-unit")["outcome"], "refused")
        self.assertEqual(len(sb.receipts()), 1)

    def test_latest_verdict_wins(self):
        sb = Sandbox(self, budgets={"forced_reruns_per_check": 5, "forced_reruns_total": 5})
        sb.set_group("g-b", ["flaky"])
        sb.finish("b1")
        sb.ok("gate", "run", "g-b")                                     # PASS
        sb.ok("gate", "reopen", "g-b", "--reason", "doubt")
        sb.flag("bad")
        forced = sb.no("gate", "run", "g-b", "--force", "flaky", "--reason", "doubt")
        self.assertEqual((sb.outcome(forced, "flaky")["outcome"], sb.outcome(forced, "flaky")["result"]),
                         ("forced", "FAIL"))
        again = sb.no("gate", "run", "g-b")                             # the old PASS must not be reused
        self.assertEqual(sb.outcome(again, "flaky")["outcome"], "refused")
        sb.flag("flag-ok")
        fixed = sb.ok("gate", "run", "g-b", "--force", "flaky", "--reason", "environment fixed")
        self.assertEqual(sb.outcome(fixed, "flaky")["result"], "PASS")
        sb.ok("gate", "reopen", "g-b", "--reason", "again")
        self.assertEqual(sb.outcome(sb.ok("gate", "run", "g-b"), "flaky")["outcome"], "reused")
        self.assertEqual(len(sb.receipts("flaky")), 3)

    def test_own_fail_beats_cover(self):
        sb = Sandbox(self)
        sb.set_check("alpha-unit", cmd="cat scratch/flag.txt")
        sb.set_check("alpha-unit", expect="flag-ok")
        sb.set_group("g-a", ["alpha-unit"])
        sb.flag("bad")
        sb.finish("a1", "a2")
        sb.no("gate", "run", "g-a")                                     # alpha-unit fails on its own key
        sb.set_group("g-a", ["alpha-suite", "alpha-unit"])
        data = sb.no("gate", "run", "g-a")
        self.assertEqual((sb.outcome(data, "alpha-suite")["outcome"], sb.outcome(data, "alpha-suite")["result"]),
                         ("ran", "PASS"))
        self.assertEqual(sb.outcome(data, "alpha-unit")["outcome"], "refused")
        self.assertEqual(sb.gate("g-a")["state"], "failed")

    def test_cover_requires_same_inputs(self):
        sb = Sandbox(self)
        sb.set_check("alpha-unit", inputs=["alpha", "beta"])
        sb.finish("a1", "a2")
        first = sb.ok("gate", "run", "g-a")
        self.assertEqual(sb.outcome(first, "alpha-unit")["outcome"], "covered")
        self.assertEqual(sb.outcome(first, "alpha-unit")["via"], "receipts/alpha-suite/1.json")
        sb.commit("beta/state.txt", "beta-ok\nmore\n")
        sb.ok("gate", "reopen", "g-a", "--reason", "input changed")
        second = sb.ok("gate", "run", "g-a")
        self.assertEqual(sb.outcome(second, "alpha-suite")["outcome"], "reused")
        self.assertEqual(sb.outcome(second, "alpha-unit")["outcome"], "ran")

    def test_cover_ignores_command_difference(self):
        sb = Sandbox(self)
        sb.finish("a1", "a2")
        sb.ok("gate", "run", "g-a")
        sb.ok("gate", "reopen", "g-a", "--reason", "again")
        sb.set_check("alpha-unit", cmd="cat alpha/state.txt; echo another build")
        data = sb.ok("gate", "run", "g-a")
        self.assertEqual(sb.outcome(data, "alpha-unit")["outcome"], "covered")
        self.assertEqual(len(sb.receipts("alpha-unit")), 0)

    def test_error_is_not_fail(self):
        sb = Sandbox(self)
        sb.set_check("beta-unit", cmd="sleep 5", timeout_s=1)
        sb.finish("b1")
        data = sb.no("gate", "run", "g-b")
        self.assertEqual(sb.outcome(data, "beta-unit")["outcome"], "error")
        sb.set_check("beta-unit", cmd="exit 127")
        sb.no("gate", "run", "g-b")
        sb.set_check("beta-unit", cmd="python3 -c 'import os, signal; os.kill(os.getpid(), signal.SIGKILL)'")
        sb.no("gate", "run", "g-b")
        got = [(r["result"], r["error"]) for r in sb.receipts("beta-unit")]
        self.assertEqual(got, [("ERROR", "timeout"), ("ERROR", "not-found"), ("ERROR", "signal")])
        self.assertEqual(sb.gate("g-b")["state"], "pending")

    def test_expect_marker_required(self):
        sb = Sandbox(self)
        sb.set_check("beta-unit", cmd="echo hello")
        sb.finish("b1")
        data = sb.no("gate", "run", "g-b")
        self.assertEqual(sb.outcome(data, "beta-unit")["result"], "FAIL")
        self.assertEqual(sb.receipts("beta-unit")[0]["exit_code"], 0)

    def test_exec_retry_cap(self):
        sb = Sandbox(self)
        sb.set_check("beta-unit", cmd="exit 127")
        sb.finish("b1")
        for _ in range(3):
            data = sb.no("gate", "run", "g-b")
            self.assertEqual(sb.outcome(data, "beta-unit")["outcome"], "error")
            self.assertEqual(sb.gate("g-b")["state"], "pending")
        data = sb.no("gate", "run", "g-b")
        self.assertEqual(sb.outcome(data, "beta-unit")["outcome"], "refused")
        self.assertEqual(len(sb.receipts("beta-unit")), 3)
        self.assertEqual([e["kind"] for e in sb.events("hitl")], ["exec-retry-cap"])
        self.assertEqual(sb.gate("g-b")["state"], "waiting")
        self.assertEqual([r for r in sb.receipts() if r["result"] == "FAIL"], [])

    def test_forced_rerun_caps(self):
        sb = Sandbox(self)                                              # per check 1, total 3
        sb.set_group("g-b", ["flaky"])
        sb.flag("bad")
        sb.finish("b1")
        sb.no("gate", "run", "g-b")
        self.assertEqual(sb.outcome(sb.no("gate", "run", "g-b", "--force", "flaky", "--reason", "r1"),
                                    "flaky")["outcome"], "forced")
        refused = sb.no("gate", "run", "g-b", "--force", "flaky", "--reason", "r2")
        self.assertEqual((refused["refused"], refused["cap"]), ("forced-rerun-cap", "per-check"))
        self.assertEqual(sb.gate("g-b")["state"], "failed")             # a refused run leaves the gate alone
        self.assertEqual(len(sb.receipts("flaky")), 2)
        self.assertEqual([e["kind"] for e in sb.events("hitl")], ["forced-rerun-cap"])
        sb.no("gate", "run", "g-b", "--force", "flaky", "--reason", "r2")
        self.assertEqual(len(sb.events("hitl")), 1)                     # the open question is not repeated
        sb.ok("hitl", "answer", refused["hitl"], "--ref", "docs/answer.md", "--allow")
        sb.no("gate", "run", "g-b", "--force", "flaky", "--reason", "r3")
        self.assertEqual(len(sb.receipts("flaky")), 3)
        self.assertEqual(sb.no("gate", "run", "g-b", "--force", "flaky", "--reason", "r4")["cap"], "per-check")

        total = Sandbox(self, budgets={"forced_reruns_per_check": 9, "forced_reruns_total": 2})
        total.set_group("g-b", ["flaky"])
        total.flag("bad")
        total.finish("b1")
        total.no("gate", "run", "g-b")
        for _ in range(2):
            total.no("gate", "run", "g-b", "--force", "flaky", "--reason", "r")
        refused = total.no("gate", "run", "g-b", "--force", "flaky", "--reason", "r")
        self.assertEqual(refused["cap"], "total")
        total.ok("hitl", "answer", refused["hitl"], "--ref", "docs/answer.md", "--allow")
        total.no("gate", "run", "g-b", "--force", "flaky", "--reason", "r")
        self.assertEqual(len(total.receipts("flaky")), 4)
        self.assertEqual(total.no("gate", "run", "g-b", "--force", "flaky", "--reason", "r")["cap"], "total")

    def test_invalidate_blocks_reuse_and_cover(self):
        sb = Sandbox(self)
        sb.finish("a1", "a2")
        sb.ok("gate", "run", "g-a")
        sb.ok("check", "invalidate", "alpha-unit", "--reason", "an input was missing")
        sb.ok("gate", "reopen", "g-a", "--reason", "invalidated")
        data = sb.ok("gate", "run", "g-a")
        self.assertEqual(sb.outcome(data, "alpha-suite")["outcome"], "reused")
        self.assertEqual(sb.outcome(data, "alpha-unit")["outcome"], "ran")   # the earlier cover is not used
        sb.ok("check", "invalidate", "alpha-suite", "--reason", "an input was missing")
        sb.ok("gate", "reopen", "g-a", "--reason", "invalidated")
        data = sb.ok("gate", "run", "g-a")
        self.assertEqual(sb.outcome(data, "alpha-suite")["outcome"], "ran")  # the earlier PASS is not reused
        self.assertEqual(sb.outcome(data, "alpha-unit")["outcome"], "reused")

    def test_env_value_changes_key(self):
        sb = Sandbox(self)
        sb.set_check("beta-unit", env=["PM_TEST_FLAVOR"])
        self.addCleanup(os.environ.pop, "PM_TEST_FLAVOR", None)
        os.environ["PM_TEST_FLAVOR"] = "one"
        sb.finish("b1")
        sb.ok("gate", "run", "g-b")
        sb.ok("gate", "reopen", "g-b", "--reason", "again")
        self.assertEqual(sb.outcome(sb.ok("gate", "run", "g-b"), "beta-unit")["outcome"], "reused")
        os.environ["PM_TEST_FLAVOR"] = "two"
        sb.ok("gate", "reopen", "g-b", "--reason", "again")
        self.assertEqual(sb.outcome(sb.ok("gate", "run", "g-b"), "beta-unit")["outcome"], "ran")
        self.assertEqual([r["key"]["env"]["PM_TEST_FLAVOR"] for r in sb.receipts("beta-unit")], ["one", "two"])

    def test_expect_changes_key(self):
        sb = Sandbox(self)
        sb.finish("b1")
        sb.ok("gate", "run", "g-b")
        sb.ok("gate", "reopen", "g-b", "--reason", "criterion changed")
        sb.set_check("beta-unit", expect="beta")
        self.assertEqual(sb.outcome(sb.ok("gate", "run", "g-b"), "beta-unit")["outcome"], "ran")
        self.assertEqual(len({r["key_sha256"] for r in sb.receipts("beta-unit")}), 2)

    def test_stale_display(self):
        sb = Sandbox(self)
        sb.finish("b1")
        sb.ok("gate", "run", "g-b")
        group = next(g for g in sb.ok("status")["groups"] if g["group"] == "g-b")
        self.assertIsNone(group["stale"])
        sb.commit("beta/state.txt", "beta-ok\nchanged elsewhere\n")
        group = next(g for g in sb.ok("status")["groups"] if g["group"] == "g-b")
        self.assertEqual((group["stale"], group["gate"], group["stale_checks"]), ("STALE", "passed", ["beta-unit"]))
        self.assertEqual(sb.gate("g-b")["state"], "passed")


class FinalTests(unittest.TestCase):
    def settle(self, sb: Sandbox) -> None:
        sb.ok("cell", "cancel", "a1", "--reason", "out of scope")
        sb.ok("cell", "cancel", "a2", "--reason", "out of scope")

    def test_final_ignores_gate_receipts(self):
        sb = Sandbox(self)
        self.settle(sb)
        sb.edit_decl(lambda d: d["final"].update(checks=["beta-unit"]))
        sb.finish("b1")
        sb.ok("gate", "run", "g-b")
        data = sb.ok("final", "run")
        self.assertEqual(sb.outcome(data, "beta-unit")["outcome"], "ran")       # the gate PASS is not reused
        self.assertEqual([r["scope"] for r in sb.receipts("beta-unit")], ["gate", "final"])

        other = Sandbox(self)
        self.settle(other)
        other.set_group("g-b", ["flaky"])
        other.edit_decl(lambda d: d["final"].update(checks=["flaky"]))
        other.flag("bad")
        other.finish("b1")
        other.no("gate", "run", "g-b")                                          # FAIL in the gate
        other.ok("cell", "stop", "b1", "--reason", "handed over")
        other.flag("flag-ok")
        data = other.ok("final", "run")                                         # not refused by the gate FAIL
        self.assertEqual((other.outcome(data, "flaky")["outcome"], other.outcome(data, "flaky")["result"]),
                         ("ran", "PASS"))

    def test_final_already_passed(self):
        sb = Sandbox(self)
        self.settle(sb)
        sb.finish("b1")
        sb.ok("gate", "run", "g-b")
        first = sb.ok("final", "run")
        self.assertEqual(sb.outcome(first, "full")["outcome"], "ran")
        second = sb.ok("final", "run")
        self.assertEqual(second["note"], "already-passed")
        self.assertEqual(len(sb.receipts("full")), 1)
        sb.commit("beta/state.txt", "beta-ok\nlater change\n")
        third = sb.ok("final", "run")
        self.assertEqual(sb.outcome(third, "full")["outcome"], "ran")
        self.assertEqual(len(sb.receipts("full")), 2)

    def test_final_requires_groups_settled(self):
        sb = Sandbox(self)
        self.assertEqual(sb.no("final", "run")["refused"], "groups-not-settled")   # groups in progress
        self.settle(sb)
        sb.set_group("g-b", ["flaky"])
        sb.flag("bad")
        sb.finish("b1")
        sb.no("gate", "run", "g-b")
        self.assertEqual(sb.group_state("g-b"), "실패")
        self.assertEqual(sb.no("final", "run")["refused"], "groups-not-settled")
        sb.ok("cell", "stop", "b1", "--reason", "handed over")
        self.assertEqual((sb.group_state("g-a"), sb.group_state("g-b")), ("취소", "멈춤"))
        self.assertEqual(sb.ok("final", "run")["state"], "passed")

    def test_final_reset_on_gate_reopen(self):
        sb = Sandbox(self)
        self.settle(sb)
        sb.finish("b1")
        sb.ok("gate", "run", "g-b")
        sb.ok("final", "run")
        self.assertEqual(sb.gate("_final")["state"], "passed")
        sb.ok("gate", "reopen", "g-b", "--reason", "look again")
        self.assertEqual(sb.gate("_final")["state"], "pending")
        sb.ok("gate", "run", "g-b")
        self.assertIn("final", sb.kinds())
        asked = sb.ok("hitl", "open", "--final", "--kind", "criteria", "--question", "is this enough?")
        self.assertEqual(sb.gate("_final")["state"], "waiting")
        self.assertNotIn("final", sb.kinds())
        sb.ok("hitl", "answer", asked["id"], "--ref", "docs/answer.md")
        sb.ok("final", "run")
        sb.commit("beta/state.txt", "beta-ok\nlater change\n")           # passed, but the input moved on
        self.assertIn("final", sb.kinds())
        sb.ok("hitl", "open", "--final", "--kind", "hang", "--question", "?")   # the bypass the hook asks for
        self.assertEqual(sb.gate("_final")["state"], "waiting")


class TransitionTests(unittest.TestCase):
    """Cell, gate and group states."""

    def test_needs_cross_group_requires_gate(self):
        sb = Sandbox(self)
        sb.set_cell("a2", needs=["a1"])
        sb.set_cell("b1", needs=["a1"])
        for name in ("a2", "b1"):
            sb.ok("phase", "start", name, "prepare")
            sb.ok("phase", "close", name, "prepare")
        self.assertEqual(sb.no("phase", "start", "a2", "change")["refused"], "conditions-unmet")
        sb.finish("a1")
        sb.ok("phase", "start", "a2", "change")                         # same group: a1's change is done
        sb.ok("phase", "close", "a2", "change")
        self.assertEqual(sb.no("phase", "start", "b1", "change")["refused"], "conditions-unmet")
        sb.ok("gate", "run", "g-a")
        sb.ok("phase", "start", "b1", "change")                         # other group: its gate has passed

    def test_gate_requires_members_done(self):
        sb = Sandbox(self)
        sb.finish("a1")
        self.assertEqual(sb.no("gate", "run", "g-a")["refused"], "members-not-done")
        self.assertEqual(sb.receipts(), [])
        self.assertEqual(sb.ok("status")["groups"][0]["gate"], "pending")

    def test_gate_pass_closes_unmarked_only(self):
        sb = Sandbox(self)
        sb.set_cell("a2", review="judgment")
        sb.finish("a1", "a2")
        sb.ok("gate", "run", "g-a")
        self.assertEqual((sb.cell("a1")["state"], sb.cell("a2")["state"]), ("closed", "open"))
        self.assertEqual(sb.gate("g-a")["members"], ["a1", "a2"])

    def test_gate_reopen_keeps_invariant(self):
        sb = Sandbox(self)
        sb.finish("a1", "a2")
        sb.ok("gate", "run", "g-a")
        sb.assert_lint()
        sb.ok("gate", "reopen", "g-a", "--reason", "another group touched alpha")
        self.assertEqual((sb.cell("a1")["state"], sb.cell("a2")["state"], sb.gate("g-a")["state"]),
                         ("open", "open", "pending"))
        sb.assert_lint()
        sb.ok("gate", "run", "g-a")
        self.assertEqual((sb.cell("a1")["state"], sb.cell("a2")["state"]), ("closed", "closed"))
        sb.assert_lint()

    def test_cell_reopen_after_gate_fail(self):
        sb = Sandbox(self)
        sb.commit("alpha/state.txt", "broken\n")
        sb.finish("a1", "a2")
        sb.no("gate", "run", "g-a")
        self.assertEqual(sb.gate("g-a")["state"], "failed")
        sb.ok("cell", "reopen", "a1", "--reason", "a1 broke alpha")
        change = sb.phase("a1", "change")
        self.assertEqual((change["state"], change["attempt"], change["by"]), ("pending", 2, None))
        self.assertEqual(sb.gate("g-a")["state"], "pending")
        sb.assert_lint()

    def test_member_added_to_passed_group_reopens(self):
        sb = Sandbox(self)
        sb.finish("a1", "a2")
        sb.ok("gate", "run", "g-a")
        self.assertEqual(sb.no("cell", "move", "a1", "g-b", "--reason", "x")["refused"], "cell-not-open")
        sb.ok("cell", "add", sb.spec("a3", "g-a"))
        self.assertEqual((sb.gate("g-a")["state"], sb.cell("a1")["state"]), ("pending", "open"))
        sb.finish("a3")
        sb.ok("gate", "run", "g-a")
        self.assertEqual(sb.cell("a3")["state"], "closed")
        sb.ok("cell", "move", "b1", "g-a", "--reason", "it changes alpha too")
        self.assertEqual((sb.gate("g-a")["state"], sb.cell("a1")["state"], sb.cell("b1")["group"]),
                         ("pending", "open", "g-a"))
        sb.assert_lint()

    def test_marked_cell_closes_after_clean_review(self):
        sb = Sandbox(self)
        sb.set_cell("a1", review="judgment")
        sb.finish("a1", "a2")
        sb.ok("review", "record", "a1", sb.review(1, clean=True))       # before the gate passes
        sb.ok("gate", "run", "g-a")
        self.assertEqual(sb.no("cell", "close", "a1")["refused"], "no-clean-review-after-gate")
        sb.ok("review", "record", "a1", sb.review(2, clean=False))
        self.assertEqual(sb.no("cell", "close", "a1")["refused"], "no-clean-review-after-gate")
        sb.ok("review", "record", "a1", sb.review(3, clean=True))
        sb.ok("cell", "close", "a1")
        self.assertEqual(sb.cell("a1")["state"], "closed")
        sb.assert_lint()

    def test_review_cap_stops_only_if_blocking(self):
        sb = Sandbox(self)
        sb.set_cell("a1", review="judgment")
        for number in (1, 2):
            self.assertFalse(sb.ok("review", "record", "a1", sb.review(number, clean=False))["stopped"])
        self.assertTrue(sb.ok("review", "record", "a1", sb.review(3, clean=False))["stopped"])
        self.assertEqual(sb.cell("a1")["state"], "stopped")
        sb.ok("cell", "resume", "a1", "--ref", "docs/approval.md")
        self.assertTrue(sb.ok("review", "record", "a1", sb.review(4, clean=False))["stopped"])

        clean = Sandbox(self)
        clean.set_cell("a1", review="judgment")
        for number in (1, 2):
            clean.ok("review", "record", "a1", clean.review(number, clean=False))
        self.assertFalse(clean.ok("review", "record", "a1", clean.review(3, clean=True))["stopped"])
        self.assertEqual(clean.cell("a1")["state"], "open")

    def test_auto_cell_cap_hands_over(self):
        sb = Sandbox(self, budgets={"auto_cells": 1})
        sb.ok("cell", "add", sb.spec("x1", "g-a"))
        refused = sb.no("cell", "add", sb.spec("x2", "g-a"))
        self.assertEqual(refused["refused"], "auto-cell-cap")
        self.assertEqual([e["kind"] for e in sb.events("hitl")], ["auto-cell-cap"])
        self.assertFalse((sb.inst / "cells" / "x2").exists())

    def test_parentless_cell_review_is_none(self):
        sb = Sandbox(self)
        sb.set_cell("a1", review="judgment")
        sb.ok("review", "record", "a1", sb.review(1, clean=False))
        self.assertEqual(sb.ok("cell", "add", sb.spec("orphan", "g-a"))["review"], "none")
        self.assertEqual(sb.ok("cell", "add", sb.spec("child", "g-a", parent="a1"))["review"], "judgment")
        self.assertEqual(sb.no("review", "record", "child", sb.review(1, clean=True))["expected"], 2)
        sb.ok("review", "record", "child", sb.review(2, clean=True))
        self.assertEqual(sb.cell("child")["origin"], "auto")

    def test_cancel_rules(self):
        sb = Sandbox(self)
        sb.ok("cell", "stop", "a1", "--reason", "handed over")
        sb.ok("hitl", "open", "--cell", "a2", "--kind", "criteria", "--question", "which way?")
        self.assertEqual(sb.no("cell", "cancel", "a1", "--reason", "x")["state"], "stopped")
        self.assertEqual(sb.no("cell", "cancel", "a2", "--reason", "x")["state"], "waiting")
        sb.ok("cell", "cancel", "b1", "--reason", "not needed")
        self.assertEqual(sb.cell("b1")["state"], "cancelled")
        self.assertTrue((sb.inst / "cells" / "b1" / "cell.md").is_file())
        self.assertEqual(sb.group_state("g-b"), "취소")

    def test_regroup_keeps_counts(self):
        sb = Sandbox(self)
        sb.set_group("g-b", ["flaky"])
        sb.flag("bad")
        sb.finish("b1")
        sb.no("gate", "run", "g-b")
        sb.no("gate", "run", "g-b", "--force", "flaky", "--reason", "once")
        sb.ok("cell", "move", "b1", "g-c", "--reason", "verified separately", "--checks", "flaky")
        self.assertEqual(sb.cell("b1")["group"], "g-c")
        refused = sb.no("gate", "run", "g-c", "--force", "flaky", "--reason", "twice")
        self.assertEqual(refused["refused"], "forced-rerun-cap")        # the count followed the check
        sb.assert_lint()

    def test_waiting_cell_does_not_block_others(self):
        sb = Sandbox(self)
        asked = sb.ok("hitl", "open", "--cell", "a1", "--kind", "criteria", "--question", "which way?")
        self.assertEqual(sb.cell("a1")["state"], "waiting")
        sb.finish("b1")
        sb.ok("gate", "run", "g-b")
        self.assertEqual((sb.group_state("g-a"), sb.group_state("g-b")), ("기다림", "통과"))
        sb.ok("hitl", "answer", asked["id"], "--ref", "docs/answer.md")
        self.assertEqual(sb.cell("a1")["state"], "open")

    def test_cell_resume_requires_ref(self):
        sb = Sandbox(self)
        sb.ok("cell", "stop", "a1", "--reason", "handed over")
        sb.no("cell", "resume", "a1", code=2)
        self.assertEqual(sb.cell("a1")["state"], "stopped")
        sb.ok("cell", "resume", "a1", "--ref", "docs/approval.md")
        self.assertEqual(sb.cell("a1")["state"], "open")

    def test_explore_caps(self):
        sb = Sandbox(self, budgets={"explore_candidates": 1, "explore_rounds": 1})
        self.assertEqual(sb.no("check", "run", "beta-unit", "--explore", "x1", "--target", str(sb.target))["refused"],
                         "no-open-exploration")
        opened = sb.ok("explore", "open", "--cell", "b1", "--reason", "two ways to do it")
        trees = []
        for name in ("one", "two"):
            tree = sb.tmp / "worktrees" / name
            sb.git("worktree", "add", "-q", "--detach", str(tree))
            sb.commit("beta/state.txt", f"beta-ok {name}\n", f"candidate {name}", cwd=tree)
            trees.append(tree)
        sb.ok("check", "run", "beta-unit", "--explore", opened["id"], "--target", str(sb.target))   # baseline
        sb.ok("check", "run", "beta-unit", "--explore", opened["id"], "--target", str(trees[0]))
        refused = sb.no("check", "run", "beta-unit", "--explore", opened["id"], "--target", str(trees[1]))
        self.assertEqual(refused["refused"], "explore-candidates-spent")
        self.assertEqual([r["scope"] for r in sb.receipts("beta-unit")], ["explore", "explore"])
        sb.commit("beta/state.txt", "broken\n", "candidate two, broken", cwd=trees[0])
        failed = sb.no("check", "run", "beta-unit", "--explore", opened["id"], "--target", str(trees[0]))
        self.assertEqual((failed["refused"], failed["result"]), ("check-ran", "FAIL"))      # exit 1, not a crash
        again = sb.no("check", "run", "beta-unit", "--explore", opened["id"], "--target", str(trees[0]))
        self.assertEqual(again["refused"], "check-refused")
        sb.ok("explore", "close", opened["id"], "--selected", "one", "--ref", "notes/explore.md")
        self.assertEqual(sb.no("explore", "open", "--cell", "b1", "--reason", "again")["refused"],
                         "explore-rounds-spent")
        self.assertEqual(sb.events("direction")[0]["selected"], "one")

    def test_hitl_group_refused_on_passed_gate(self):
        sb = Sandbox(self)
        sb.set_cell("a2", review="judgment")
        sb.finish("a1", "a2")
        sb.ok("gate", "run", "g-a")
        refused = sb.no("hitl", "open", "--group", "g-a", "--kind", "hang", "--question", "?")
        self.assertEqual(refused["refused"], "gate-passed")
        sb.ok("cell", "stop", "a2", "--reason", "hang")                 # the stalled review stops its cell
        self.assertEqual((sb.cell("a1")["state"], sb.gate("g-a")["state"], sb.group_state("g-a")),
                         ("closed", "passed", "멈춤"))
        sb.assert_lint()
        asked = sb.ok("hitl", "open", "--group", "g-b", "--kind", "hang", "--question", "?")
        self.assertEqual(sb.gate("g-b")["state"], "waiting")
        sb.finish("b1")
        self.assertEqual(sb.no("gate", "run", "g-b")["refused"], "gate-waiting")
        sb.ok("hitl", "answer", asked["id"], "--ref", "docs/answer.md")
        self.assertEqual(sb.gate("g-b")["state"], "pending")
        sb.ok("gate", "run", "g-b")
        sb.assert_lint()

    def test_gate_error_keeps_pending(self):
        sb = Sandbox(self)
        sb.set_check("beta-unit", cmd="exit 127")
        sb.finish("b1")
        self.assertEqual(sb.no("gate", "run", "g-b")["state"], "pending")
        sb.commit("alpha/state.txt", "broken\n")
        sb.finish("a1", "a2")
        data = sb.no("gate", "run", "g-a")
        got = [(item["check"], item["outcome"], item["result"]) for item in data["checks"]]
        self.assertEqual(got, [("alpha-suite", "ran", "FAIL"), ("alpha-unit", "ran", "FAIL")])
        self.assertEqual(data["state"], "failed")

    def test_dead_gate_run_recovers(self):
        sb = Sandbox(self)
        sb.finish("b1")
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        record = {"schema_version": 2, "group": "g-b", "state": "pending", "runs": 1,
                  "started": "2026-01-01T00:00:00+00:00", "ended": None, "members": [], "hitl": None,
                  "checks": [], "pid": 1}
        (sb.inst / "gates").mkdir(exist_ok=True)
        sb.write(sb.inst / "gates" / "g-b.status.json", record)
        self.assertEqual(sb.no("gate", "run", "g-b")["refused"], "gate-running")    # pid 1 is alive
        sb.write(sb.inst / "gates" / "g-b.status.json", {**record, "pid": dead.pid})
        holder = subprocess.Popen([sys.executable, "-c",
                                   "import fcntl, sys, time; handle = open(sys.argv[1], 'a+'); "
                                   "fcntl.flock(handle, fcntl.LOCK_EX); print('held', flush=True); time.sleep(60)",
                                   str(sb.inst / ".lock")], stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.kill)
        self.assertEqual(holder.stdout.readline().strip(), "held")
        holder.kill()                                                   # the process dies holding the lock
        holder.wait()
        holder.stdout.close()
        self.assertEqual(sb.ok("gate", "run", "g-b")["state"], "passed")
        self.assertEqual(sb.gate("g-b").get("pid"), None)

    def test_allow_raises_cap(self):
        sb = Sandbox(self, budgets={"auto_cells": 1})
        sb.ok("cell", "add", sb.spec("x1", "g-a"))
        refused = sb.no("cell", "add", sb.spec("x2", "g-a"))
        sb.ok("hitl", "answer", refused["hitl"], "--ref", "docs/answer.md", "--allow")
        sb.ok("cell", "add", sb.spec("x2", "g-a"))
        self.assertEqual(sb.no("cell", "add", sb.spec("x3", "g-a"))["refused"], "auto-cell-cap")
        sb.assert_lint()                                                # auto-cap-exceeded counts the answer


class HookTests(unittest.TestCase):
    """The Stop hook: when it keeps the agent going and when it lets go."""

    def test_hook_no_action_when_nothing_actionable(self):
        sb = Sandbox(self)
        sb.set_cell("b1", needs=["a1"])
        sb.ok("cell", "cancel", "a2", "--reason", "out of scope")
        sb.ok("hitl", "open", "--cell", "a1", "--kind", "criteria", "--question", "which way?")
        sb.ok("phase", "start", "b1", "prepare")
        sb.ok("phase", "close", "b1", "prepare")
        self.assertEqual(sb.ok("status")["actionable"], [])
        self.assertIsNone(sb.hook())
        self.assertEqual(sb.events("hang"), [])

    def test_hook_never_exits_nonzero(self):
        sb = Sandbox(self)
        sb.ok("phase", "start", "a1", "prepare")
        (sb.inst / "monitor.json").write_text("{not json", encoding="utf-8")
        self.assertIsNone(sb.hook())
        done = subprocess.run([sys.executable, "-B", str(SCRIPT), "--campaigns", str(sb.base), "stop-hook",
                               "--harness", "codex"], input="not json either", capture_output=True, text=True)
        self.assertEqual((done.returncode, done.stdout), (0, ""))
        done = subprocess.run([sys.executable, "-B", str(SCRIPT), "--campaigns", str(sb.base), "stop-hook"],
                              input="{}", capture_output=True, text=True)
        self.assertEqual((done.returncode, done.stdout), (0, ""))
        loop = sb.tmp / "loop"
        loop.symlink_to(loop)                                           # a campaigns path that cannot be resolved
        done = subprocess.run([sys.executable, "-B", str(SCRIPT), "--campaigns", str(loop), "stop-hook",
                               "--harness", "claude"], input="{}", capture_output=True, text=True)
        self.assertEqual((done.returncode, done.stdout), (0, ""))
        (sb.inst / "monitor.json").unlink()
        with open("/dev/full", "w") as full:                            # the output cannot be written
            done = subprocess.run([sys.executable, "-B", str(SCRIPT), "--campaigns", str(sb.base), "stop-hook",
                                   "--harness", "claude"], input=json.dumps({"session_id": "s1"}), stdout=full,
                                  stderr=subprocess.PIPE, text=True)
        self.assertEqual(done.returncode, 0)

    def test_hook_ignores_other_session(self):
        sb = Sandbox(self)
        sb.ok("phase", "start", "a1", "prepare")
        self.assertIn("additionalContext", sb.hook("s1")["hookSpecificOutput"])
        before = sb.monitor()
        self.assertEqual((before["session"], before["blocks"]), ("s1", 1))
        self.assertIsNone(sb.hook("s2"))
        self.assertEqual(sb.monitor(), before)
        sb.ok("monitor", "reset")
        self.assertIsNotNone(sb.hook("s2"))
        self.assertEqual((sb.monitor()["session"], sb.monitor()["total_blocks"]), ("s2", 2))

    def test_hook_background_tasks_not_counted(self):
        sb = Sandbox(self)
        sb.ok("phase", "start", "a1", "prepare")
        tasks = [{"id": "t1", "type": "shell", "status": "running"}]
        self.assertIsNone(sb.hook(background_tasks=tasks))
        self.assertEqual(sb.monitor()["blocks"], 0)
        path = sb.inst / "cells" / "a1" / "phases" / "prepare.status.json"
        data = sb.read(path)
        data["started"] = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        sb.write(path, data)
        out = sb.hook(background_tasks=tasks)                           # over the phase budget
        self.assertEqual(out["decision"], "block")
        self.assertIn("phase retry a1 prepare", out["reason"])
        self.assertEqual([e["directive"] for e in sb.events("hang")], ["retry"])

    def test_hook_refused_fail_is_stall(self):
        sb = Sandbox(self)
        sb.ok("cell", "cancel", "a1", "--reason", "out of scope")
        sb.ok("cell", "cancel", "a2", "--reason", "out of scope")
        sb.commit("beta/state.txt", "broken\n")
        sb.finish("b1")
        sb.no("gate", "run", "g-b")
        self.assertIn("hookSpecificOutput", sb.hook())
        sb.no("gate", "run", "g-b")                                     # refused: same input, already FAIL
        out = sb.hook()
        self.assertEqual(out["decision"], "block")
        self.assertEqual([(e["count"], e["directive"]) for e in sb.events("hang")], [(1, "retry")])

    def test_hook_release_stays_until_change(self):
        sb = Sandbox(self, budgets={"stop_block_cap": 2})
        sb.ok("phase", "start", "a1", "prepare")
        self.assertIsNotNone(sb.hook())
        self.assertIsNotNone(sb.hook())
        self.assertIsNone(sb.hook())
        self.assertEqual([e["blocks"] for e in sb.events("monitor-release")], [2])
        self.assertIsNone(sb.hook())
        self.assertEqual(len(sb.events("monitor-release")), 1)
        sb.ok("phase", "close", "a1", "prepare")
        self.assertIsNotNone(sb.hook())

    def test_hook_bypasses_when_retries_spent(self):
        sb = Sandbox(self)
        sb.ok("phase", "start", "a1", "prepare")
        for _ in range(2):
            sb.hook("s1")
        self.assertIn("phase retry a1 prepare", sb.hook("s1")["reason"])
        self.assertIn(f"--campaigns {sb.base}", sb.hook("s1")["hookSpecificOutput"]["additionalContext"])
        sb.ok("phase", "retry", "a1", "prepare")
        sb.ok("monitor", "reset")                                       # a new session takes over
        for _ in range(2):
            sb.hook("s2")
        out = sb.hook("s2")                                             # the retry is spent: do not ask for it again
        self.assertIn("cell stop a1", out["reason"])
        self.assertEqual([e["directive"] for e in sb.events("hang")], ["retry", "bypass"])

    def test_briefing_due_by_hours(self):
        sb = Sandbox(self, open_args=("--briefing-every-hours", "1"))
        self.assertEqual(sb.events("campaign-open")[0]["briefing_source"], "user")
        self.assertFalse(sb.ok("status")["briefing_due"]["hours"])
        opened = sb.events()[0]
        opened["utc"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        (sb.inst / "journey.jsonl").write_text(json.dumps(opened, ensure_ascii=False) + "\n", encoding="utf-8")
        self.assertTrue(sb.ok("status")["briefing_due"]["hours"])
        sb.ok("phase", "start", "a1", "prepare")
        self.assertIn("브리핑", sb.hook()["hookSpecificOutput"]["additionalContext"])
        sb.ok("brief", "--done", "briefing.md")
        self.assertFalse(sb.ok("status")["briefing_due"]["hours"])


if __name__ == "__main__":
    unittest.main()
