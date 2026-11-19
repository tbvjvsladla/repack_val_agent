"""Test helpers: run the gate, build variant workspaces, resolve report templates."""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import acceptance_spec as spec

sys.dont_write_bytecode = True
sys.path.insert(0, str(spec.SKILL_DIR / "scripts"))
import slop_scan as sc

CARGO_HOME_BIN = Path.home() / ".cargo" / "bin"


def cargo_available() -> bool:
    path = os.environ.get("PATH", "") + os.pathsep + str(CARGO_HOME_BIN)
    return shutil.which("cargo", path=path) is not None


def fixture_checks() -> list[str]:
    if cargo_available():
        return list(spec.CHECKS)
    return [c for c in spec.CHECKS if c != spec.CARGO_CHECK]


def env() -> dict:
    e = dict(os.environ)
    e["PYTHONDONTWRITEBYTECODE"] = "1"
    e.pop("CARGO_TARGET_DIR", None)
    return e


def gate(*args, extra_env=None) -> subprocess.CompletedProcess:
    e = env()
    if extra_env:
        e.update(extra_env)
    return subprocess.run([sys.executable, str(spec.GATE), *map(str, args)], capture_output=True, text=True, env=e)


def latest_verdict(run_dir: Path) -> dict:
    files = sorted(run_dir.glob("verdict-*.json"), key=lambda p: int(p.stem.split("-")[1]))
    return json.loads(files[-1].read_text())


def copy_tree(src: Path, dst: Path) -> None:
    """Copy with fresh modification times so build tools see every overlay as new."""
    shutil.copytree(src, dst, dirs_exist_ok=True, copy_function=shutil.copy)


def write_reviews(run_dir: Path, roles) -> None:
    for role in roles:
        path = run_dir / "reviews" / f"{role}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"role": role, "independent": True, "items": []}))


def _signal_token(token: str, signals: list[dict]) -> str:
    rule, file, contains, index = token[1:].split("|")
    matches = [s for s in signals if s["rule"] == rule and s["file"] == file and contains in s["text"]]
    return matches[int(index)]["id"]


def resolve_report(template: dict, before: list[dict], after: list[dict]) -> dict:
    report = copy.deepcopy(template)
    if report.get("verdicts") == "@ALL_HEURISTIC_GROUPS":
        groups = sorted({(s["rule"], s["file"]) for s in before + after if s["grade"] == "heuristic"})
        report["verdicts"] = [{"rule_id": r, "file": f, "verdict": "keep", "role": "structure",
                               "reason": "fixture verdict"} for r, f in groups]
    for d in report.get("dispositions", []):
        if d["signal"].startswith("@"):
            d["signal"] = _signal_token(d["signal"], before)
    return report


def apply_report_ops(report: dict, ops: list[dict], dropped_roles: set) -> None:
    for op in ops:
        kind = op["op"]
        if kind == "set_open_constraints":
            report["open_constraints"] = op["value"]
        elif kind == "keep_dispositions":
            report["dispositions"] = [report["dispositions"][i] for i in op["index"]]
        elif kind == "add_dispositions":
            report["dispositions"] = report["dispositions"] + copy.deepcopy(op["value"])
        elif kind == "patch_disposition":
            report["dispositions"][op["index"]].update(op["value"])
        elif kind == "drop_verdict":
            report["verdicts"] = [v for v in report["verdicts"]
                                  if not (v.get("rule_id") == op["rule"] and v.get("file") == op["file"])]
        elif kind == "drop_reviews":
            dropped_roles.update(op["roles"])
        else:
            raise ValueError(f"unknown report op {kind}")


def variant_chain(name: str) -> list[tuple[Path, dict]]:
    chain = []
    while name:
        d = spec.VARIANTS / name
        meta = json.loads((d / "variant.json").read_text())
        chain.insert(0, (d, meta))
        name = meta.get("base")
    return chain


def build_variant(name: str, workdir: Path, project_name: str = spec.PROJECT_DIR, run_rel: str = spec.RUN_DIR):
    """Copy the seeded fixture, snapshot, apply the variant, write the report. Returns (project, run_dir, meta)."""
    chain = variant_chain(name)
    meta = chain[-1][1]
    project = workdir / project_name
    run_dir = workdir / run_rel
    copy_tree(spec.SEEDED, project)
    for d, _ in chain:
        if (d / "pre").is_dir():
            copy_tree(d / "pre", project)
    args = ["snapshot", "--target", project, "--run-dir", run_dir,
            "--allowed", *spec.ALLOWED, "--fix-types", *meta.get("fix_types", spec.FIX_TYPES)]
    for check in fixture_checks():
        args += ["--check", check]
    args += meta.get("snapshot_extra", [])
    args += ["--executor", f"fixture {name}"]
    proc = gate(*args)
    if proc.returncode != 0:
        raise AssertionError(f"snapshot failed for {name}: {proc.stdout}\n{proc.stderr}")
    for d, _ in chain:
        if (d / "files").is_dir():
            copy_tree(d / "files", project)
    template = None
    for d, _ in chain:
        if (d / "report.json").exists():
            template = json.loads((d / "report.json").read_text())
    rules = sc.load_rules()
    before = json.loads((run_dir / "scan_before.json").read_text())["signals"]
    after = sc.scan_tree(project, rules, ["."])["signals"]
    report = resolve_report(template, before, after)
    dropped: set = set()
    for _, m in chain:
        apply_report_ops(report, m.get("report_ops", []), dropped)
    for d in report.get("dispositions", []):
        if d["signal"].startswith("@"):
            d["signal"] = _signal_token(d["signal"], before)
    (run_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    write_reviews(run_dir, [r["role"] for r in report["reviewers"] if r["role"] not in dropped])
    return project, run_dir, meta


# ------------------------------------------------------------- small targets

def small_target(root: Path, files: dict[str, str]) -> Path:
    target = root / "target-under-test"
    for rel, text in files.items():
        p = target / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return target


def basic_report(run_dir: Path, verdicts=None, dispositions=None, roles=("comment", "structure", "diff"), **extra) -> None:
    report = {"executor": "unit test", "reviewers": [{"role": r, "independent": True} for r in roles],
              "verdicts": verdicts or [], "dispositions": dispositions or [], "proposals": [],
              "open_constraints": [], "findings": []}
    report.update(extra)
    (run_dir / "report.json").write_text(json.dumps(report))
    write_reviews(run_dir, roles)
