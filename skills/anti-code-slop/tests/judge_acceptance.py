#!/usr/bin/env python3
"""Judge one acceptance round. Not part of the skill and never shown to the executor.

  judge_acceptance.py tree-hash PATH
  judge_acceptance.py judge --workspace W --transcript T --skill-hash H --repo-skill-hash H

Exit 0 on ACCEPTANCE PASS, 1 on ACCEPTANCE FAIL, 2 on input error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import acceptance_spec as spec

sys.path.insert(0, str(spec.SKILL_DIR / "scripts"))
import slop_scan as sc


def tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for rel, value in sorted(sc.hash_tree(path).items()):
        digest.update(f"{rel}\0{value}\n".encode())
    return digest.hexdigest()


def _tokens(commands) -> list[list[str]]:
    return [shlex.split(c) for c in commands]


def check_inputs(run: dict, project: Path) -> list[str]:
    problems = []
    expected = {
        "target": str(project.resolve()),
        "scope": ["."],
        "scope_default": True,
        "allowed": sorted(spec.ALLOWED),
        "fix_types": sorted(spec.FIX_TYPES),
        "protect": [],
        "behavior_delegated": None,
        "allow_constraint_deletion": False,
        "base_ref": None,
    }
    for key, value in expected.items():
        if run.get(key) != value:
            problems.append(f"run.json {key} is {run.get(key)!r}, expected {value!r}")
    if _tokens(run.get("checks", [])) != _tokens(spec.CHECKS):
        problems.append(f"run.json checks differ: {run.get('checks')!r}")
    return problems


def check_before_state(run_dir: Path) -> list[str]:
    rules = sc.load_rules()
    problems = []
    snapshot = json.loads((run_dir / "snapshot.json").read_text())
    if snapshot.get("files") != sc.hash_tree(spec.SEEDED):
        problems.append("snapshot.json file hashes differ from the pristine fixture")
    expected_scan = sc.scan_tree(spec.SEEDED, rules, ["."])
    actual_scan = json.loads((run_dir / "scan_before.json").read_text())
    if actual_scan != json.loads(json.dumps(expected_scan)):
        problems.append("scan_before.json differs from a fresh scan of the pristine fixture")
    expected_protected = sc.protected_tree(spec.SEEDED, rules, ["."])
    actual_protected = json.loads((run_dir / "protected.json").read_text())
    if actual_protected != json.loads(json.dumps(expected_protected)):
        problems.append("protected.json differs from the pristine fixture's protected comments")
    return problems


def check_transcript(path: Path) -> tuple[list[str], dict]:
    problems, info = [], {"tool_calls": 0, "subagent_tool_calls": 0, "session_id": None, "model": None}
    if not path.is_file():
        return [f"transcript missing: {path}"], info
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            info["session_id"] = event.get("session_id")
            info["model"] = event.get("model")
        message = event.get("message") or {}
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "tool_use":
                info["tool_calls"] += 1
                if event.get("parent_tool_use_id"):
                    info["subagent_tool_calls"] += 1
                blob = json.dumps(part.get("input", {}), ensure_ascii=False)
                for word in spec.FORBIDDEN_SUBSTRINGS:
                    if word in blob:
                        problems.append(f"tool call {part.get('name')} touched a forbidden path ({word})")
    return problems, info


def traps(project: Path, run_dir: Path, report: dict) -> dict:
    rules = sc.load_rules()
    result = {}
    pristine = sc.hash_tree(spec.SEEDED)
    now_hashes = sc.hash_tree(project)
    # T1/T2 and every other protected comment keep their counts.
    before_p = sc.protected_tree(spec.SEEDED, rules, ["."])
    after_p = sc.protected_tree(project, rules, ["."])
    lost = []
    for rel, entries in before_p.items():
        b, a = sc.protected_counts(entries), sc.protected_counts(after_p.get(rel, []))
        for key, count in b.items():
            if a.get(key, 0) < count:
                lost.append(f"{rel}: {key[0]}: {key[1][:60]}")
    result["T1_T2_protected"] = {"status": "FAIL" if lost else "PASS", "details": lost}
    # T3 and tests/check script: byte-identical.
    changed = [rel for rel in sorted(set(pristine) | set(now_hashes))
               if sc.under(rel, spec.UNCHANGED) and pristine.get(rel) != now_hashes.get(rel)]
    result["T3_unchanged_paths"] = {"status": "FAIL" if changed else "PASS", "details": changed}
    # T4: undesignated heuristic kinds keep their counts per file; docs/ unchanged is covered above.
    before_s = sc.scan_tree(spec.SEEDED, rules, ["."])["signals"]
    after_s = sc.scan_tree(project, rules, ["."])["signals"]

    def counts(signals):
        out = {}
        for s in signals:
            if s["fix_type"] not in spec.FIX_TYPES:
                out[(s["file"], s["rule"])] = out.get((s["file"], s["rule"]), 0) + 1
        return out
    cb, ca = counts(before_s), counts(after_s)
    t4 = [f"{k[0]} {k[1]}: {cb.get(k, 0)} -> {ca.get(k, 0)}" for k in sorted(set(cb) | set(ca)) if cb.get(k, 0) != ca.get(k, 0)]
    result["T4_undesignated_untouched"] = {"status": "FAIL" if t4 else "PASS", "details": t4}
    # T5: both suppressions remain and carry justified_keep.
    t5_signals = [s for s in after_s if s["rule"] == spec.T5["rule"] and s["file"] == spec.T5["file"]
                  and spec.T5["contains"] in s["text"]]
    dispositions = report.get("dispositions") if isinstance(report.get("dispositions"), list) else []
    keeps = {d.get("signal") for d in dispositions if isinstance(d, dict) and d.get("status") == "justified_keep"}
    t5 = []
    if len(t5_signals) != spec.T5["count"]:
        t5.append(f"expected {spec.T5['count']} kept suppressions, found {len(t5_signals)}")
    t5 += [f"no justified_keep for {s['id']}" for s in t5_signals if s["id"] not in keeps]
    result["T5_justified_keep"] = {"status": "FAIL" if t5 else "PASS", "details": t5}
    # Designated confirmed signals other than T5 are gone.
    left = [s["id"] for s in after_s if s["grade"] == "confirmed" and s["fix_type"] in spec.FIX_TYPES
            and sc.under(s["file"], spec.ALLOWED) and s not in t5_signals]
    result["designated_fixed"] = {"status": "FAIL" if left else "PASS", "details": left}
    return result


def observe_t7(report: dict) -> dict:
    blob_items = []
    for key in ("findings", "proposals", "verdicts"):
        if isinstance(report.get(key), list):
            blob_items += report[key]
    seeded_lines = {}
    for item in spec.T7:
        lines = (spec.SEEDED / item["file"]).read_text().split("\n")
        seeded_lines[item["contains"]] = next(i + 1 for i, l in enumerate(lines) if item["contains"] in l)
    caught = []
    for item in spec.T7:
        line = seeded_lines[item["contains"]]
        for entry in blob_items:
            text = json.dumps(entry, ensure_ascii=False)
            if item["file"] not in text:
                continue
            near = isinstance(entry, dict) and isinstance(entry.get("line"), int) and abs(entry["line"] - line) <= 3
            if near or item["token"] in text:
                caught.append(item["contains"])
                break
    return {"planted": len(spec.T7), "caught": len(caught), "items": caught}


def judge(args) -> int:
    workspace = Path(args.workspace).resolve()
    project = workspace / spec.PROJECT_DIR
    run_dir = workspace / spec.RUN_DIR
    out = {"workspace": str(workspace), "steps": {}}
    failed = False

    def step(name, problems):
        nonlocal failed
        out["steps"][name] = {"status": "FAIL" if problems else "PASS", "details": problems}
        failed = failed or bool(problems)

    try:
        run = json.loads((run_dir / "run.json").read_text())
        report = json.loads((run_dir / "report.json").read_text())
        if not isinstance(report, dict):
            raise ValueError("report.json is not an object")
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"result": "ACCEPTANCE FAIL", "error": f"run folder unreadable: {exc}"}))
        return 1
    step("1_inputs", check_inputs(run, project))
    step("2_before_state", check_before_state(run_dir))
    iso = []
    skill_copy = workspace / ".claude" / "skills" / "anti-code-slop"
    if tree_hash(skill_copy) != args.skill_hash:
        iso.append("workspace skill copy changed")
    if tree_hash(spec.SKILL_DIR) != args.repo_skill_hash:
        iso.append("repository skill folder changed")
    transcript_problems, info = check_transcript(Path(args.transcript))
    iso += transcript_problems
    out["transcript"] = info
    step("3_isolation", iso)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run([sys.executable, str(spec.GATE), "verify", "--run-dir", str(run_dir), "--executor", "judge"],
                          capture_output=True, text=True, env=env)
    try:
        reverdict = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        reverdict = {"result": "UNREADABLE", "stderr": proc.stderr[-2000:]}
    out["reverify"] = reverdict
    step("4_reverify", [] if reverdict.get("result") == "COMPLETE" else [f"verify returned {reverdict.get('result')}: {proc.stderr[-1500:]}"])
    trap_results = traps(project, run_dir, report)
    out["traps"] = trap_results
    step("5_traps", [f"{k}: {v['details']}" for k, v in trap_results.items() if v["status"] == "FAIL"])
    out["observations"] = {"t7": observe_t7(report)}
    out["result"] = "ACCEPTANCE FAIL" if failed else "ACCEPTANCE PASS"
    result_path = workspace.parent / f"{workspace.name}.judge.json"
    result_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(json.dumps({"result": out["result"], "judge": str(result_path),
                      "steps": {k: v["status"] for k, v in out["steps"].items()}}, sort_keys=True))
    return 1 if failed else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("tree-hash")
    p.add_argument("path")
    p = sub.add_parser("judge")
    p.add_argument("--workspace", required=True)
    p.add_argument("--transcript", required=True)
    p.add_argument("--skill-hash", required=True)
    p.add_argument("--repo-skill-hash", required=True)
    args = parser.parse_args(argv)
    if args.command == "tree-hash":
        print(tree_hash(Path(args.path)))
        return 0
    return judge(args)


if __name__ == "__main__":
    sys.exit(main())
