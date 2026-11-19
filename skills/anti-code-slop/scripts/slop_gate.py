#!/usr/bin/env python3
"""anti-code-slop gate: scan, snapshot, verify, restore.

Exit codes: 0 pass/COMPLETE, 1 INCOMPLETE or refusal, 2 input error.
Only `restore` writes into the target tree.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import slop_scan as sc

SKILL_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = Path(__file__).resolve().parents[3]
OUTPUT_TAIL = 64 * 1024
VERDICTS = {"fix", "keep", "propose", "blocked"}
ROLES = {"comment", "structure", "prose", "diff"}
OPEN_STATUSES = {"blocked", "accepted_unenforced", "out_of_scope"}
PROPOSAL_FIELDS = ("removes", "owner", "failure_signal", "applies_to")


class UsageError(Exception):
    pass


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def read_json(path: Path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def next_numbered(run_dir: Path, stem: str) -> Path:
    n = 1
    while (run_dir / f"{stem}-{n}.json").exists():
        n += 1
    return run_dir / f"{stem}-{n}.json"


def rel_paths(target: Path, items) -> list[str]:
    out = []
    for item in items or []:
        p = Path(item)
        if p.is_absolute():
            try:
                p = p.resolve().relative_to(target)
            except ValueError as exc:
                raise UsageError(f"path outside target: {item}") from exc
        rel = p.as_posix().strip("/")
        if rel.startswith("..") or "/../" in f"/{rel}/":
            raise UsageError(f"path escapes target: {item}")
        out.append(rel or ".")
    return sorted(set(out))


def inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


# ------------------------------------------------------------------ checks

def run_checks(commands: list[str], target: Path, run_dir: Path, phase: str, timeout: int) -> list[dict]:
    results = []
    bash = shutil.which("bash")
    for index, command in enumerate(commands, 1):
        started = now()
        record = {"command": command, "phase": phase, "started": started}
        if bash is None:
            record.update(status="unavailable", returncode=None, reason="bash not found")
            results.append(record)
            continue
        try:
            proc = subprocess.run([bash, "-c", command], cwd=target, capture_output=True, timeout=timeout)
            out, err, code = proc.stdout, proc.stderr, proc.returncode
            if code == 0:
                status = "passed"
            elif code in (126, 127):
                status = "unavailable"
            else:
                status = "failed"
        except subprocess.TimeoutExpired as exc:
            out, err, code, status = exc.stdout or b"", exc.stderr or b"", None, "timeout"
        except OSError as exc:
            out, err, code, status = b"", str(exc).encode(), None, "unavailable"
        log_dir = run_dir / "checks"
        log_dir.mkdir(parents=True, exist_ok=True)
        base = log_dir / f"{phase}-{index}"
        (base.with_suffix(".out")).write_bytes(out[-OUTPUT_TAIL:])
        (base.with_suffix(".err")).write_bytes(err[-OUTPUT_TAIL:])
        record.update(status=status, returncode=code, finished=now(),
                      stdout=str(base.with_suffix(".out")), stderr=str(base.with_suffix(".err")))
        results.append(record)
    return results


# --------------------------------------------------------------------- git

def git_held(path: Path) -> dict | None:
    """Return repo/commit/blob when Git holds exactly this file's bytes at HEAD."""
    git = shutil.which("git")
    if git is None:
        return None

    def run(*args, cwd):
        return subprocess.run([git, *args], cwd=cwd, capture_output=True, text=True)

    top = run("rev-parse", "--show-toplevel", cwd=path.parent)
    if top.returncode:
        return None
    repo = Path(top.stdout.strip())
    try:
        rel = path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return None
    head = run("rev-parse", "--verify", "-q", "HEAD", cwd=repo)
    if head.returncode:
        return None
    blob = run("rev-parse", "--verify", "-q", f"HEAD:{rel}", cwd=repo)
    if blob.returncode:
        return None
    current = run("hash-object", "--no-filters", "--", str(path), cwd=repo)
    if current.returncode or current.stdout.strip() != blob.stdout.strip():
        return None
    return {"repo": str(repo), "commit": head.stdout.strip(), "blob": blob.stdout.strip(), "path": rel}


def git_scope(target: Path, ref: str) -> list[str]:
    git = shutil.which("git")
    if git is None:
        raise UsageError("--base-ref needs git")
    top = subprocess.run([git, "rev-parse", "--show-toplevel"], cwd=target, capture_output=True, text=True)
    if top.returncode:
        raise UsageError("--base-ref given but the target is not in a Git repository")
    repo = Path(top.stdout.strip()).resolve()
    diff = subprocess.run([git, "--no-optional-locks", "diff", "--name-only", ref, "--", "."], cwd=target,
                          capture_output=True, text=True)
    if diff.returncode:
        raise UsageError(f"git diff failed for ref {ref}: {diff.stderr.strip()}")
    untracked = subprocess.run([git, "--no-optional-locks", "ls-files", "--others", "--exclude-standard", "--full-name", "--", "."],
                               cwd=target, capture_output=True, text=True)
    names = set(diff.stdout.split("\n")) | set(untracked.stdout.split("\n"))
    out = []
    for name in sorted(n for n in names if n):
        full = repo / name
        if inside(full, target):
            out.append(full.resolve().relative_to(target.resolve()).as_posix())
    return out


# ---------------------------------------------------------------- commands

def cmd_scan(args) -> int:
    target = Path(args.target).resolve()
    if not target.is_dir():
        raise UsageError(f"target is not a directory: {args.target}")
    rules = sc.load_rules()
    scope = rel_paths(target, args.paths) if args.paths else None
    if args.out and inside(Path(args.out).resolve().parent, target):
        raise UsageError("--out must be outside the target tree")
    result = sc.scan_tree(target, rules, scope)
    if args.out:
        write_json(Path(args.out), result)
    counts = {}
    for s in result["signals"]:
        counts[s["grade"]] = counts.get(s["grade"], 0) + 1
    print(json.dumps({"signals": len(result["signals"]), "by_grade": counts,
                      "checked": len(result["files"]["checked"]),
                      "unchecked": len(result["files"]["unchecked"]),
                      "excluded": len(result["files"]["excluded"])}, sort_keys=True))
    return 0


def default_run_dir(target: Path) -> Path:
    stamp = dt.datetime.now().strftime("%y%m%d%H%M%S")
    return REPO_DIR / "temp" / "anti-code-slop" / f"{target.name}-{stamp}"


def cmd_snapshot(args) -> int:
    target = Path(args.target).resolve()
    if not target.is_dir():
        raise UsageError(f"target is not a directory: {args.target}")
    run_dir = Path(args.run_dir).resolve() if args.run_dir else default_run_dir(target)
    if run_dir.exists():
        raise UsageError(f"run dir already exists: {run_dir}")
    if inside(run_dir, target):
        raise UsageError("run dir must be outside the target tree")
    if args.check and args.behavior_delegated:
        raise UsageError("give either --check or --behavior-delegated, not both")
    rules = sc.load_rules()
    known_types = {r["fix_type"] for r in rules}
    unknown = sorted(set(args.fix_types or []) - known_types)
    if unknown:
        raise UsageError(f"unknown fix types: {unknown}")
    scope = rel_paths(target, args.scope) if args.scope else ["."]
    allowed = rel_paths(target, args.allowed)
    protect = rel_paths(target, args.protect)
    for path in allowed:
        if not sc.under(path, scope):
            raise UsageError(f"--allowed path outside --scope: {path}")
    if args.base_ref:
        changed = git_scope(target, args.base_ref)
        scope = sorted(p for p in changed if sc.under(p, scope))
    run_dir.mkdir(parents=True)
    run = {
        "target": str(target),
        "scope": scope,
        "scope_default": not args.scope and not args.base_ref,
        "allowed": allowed,
        "fix_types": sorted(set(args.fix_types or [])),
        "protect": protect,
        "checks": list(args.check or []),
        "behavior_delegated": args.behavior_delegated,
        "check_timeout": args.check_timeout,
        "allow_constraint_deletion": bool(args.allow_constraint_deletion),
        "base_ref": args.base_ref,
        "executor": args.executor,
        "python": sys.version.split()[0],
        "snapshot_started": now(),
    }
    write_json(run_dir / "run.json", run)
    baseline = run_checks(run["checks"], target, run_dir, "before", args.check_timeout) if run["checks"] else []
    hashes = sc.hash_tree(target)
    scan = sc.scan_tree(target, rules, scope)
    protected = sc.protected_tree(target, rules, scope)
    backups, held = [], {}
    for rel in sorted(hashes):
        if not allowed or not sc.under(rel, allowed):
            continue
        info = git_held(target / rel)
        if info:
            held[rel] = info
            continue
        dest = run_dir / "backup" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target / rel, dest)
        backups.append(rel)
    write_json(run_dir / "snapshot.json", {
        "files": hashes, "baseline_checks": baseline, "git_held": held, "backups": backups,
        "excluded_dirs": sorted(sc.HASH_EXCLUDE_DIRS), "taken": now(),
    })
    write_json(run_dir / "scan_before.json", scan)
    write_json(run_dir / "protected.json", protected)
    failed = [c for c in baseline if c["status"] != "passed"]
    summary = {"run_dir": str(run_dir), "files": len(hashes), "signals": len(scan["signals"]),
               "backups": len(backups), "git_held": len(held),
               "baseline": [c["status"] for c in baseline]}
    print(json.dumps(summary, sort_keys=True))
    if failed:
        print("baseline check failed: do not edit the target; fix the check or declare delegation", file=sys.stderr)
        return 1
    return 0


def _changes(before: dict, after: dict) -> dict:
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    modified = sorted(p for p in set(before) & set(after) if before[p] != after[p])
    return {"added": added, "removed": removed, "modified": modified,
            "before": {p: before[p] for p in removed + modified},
            "after": {p: after[p] for p in added + modified}}


def _validate_report(report, signals_by_id, exceptions_map) -> list[str]:
    problems = []
    if not isinstance(report, dict):
        return ["report.json is not an object"]
    if not isinstance(report.get("executor"), str) or not report["executor"].strip():
        problems.append("executor missing")
    for key in ("reviewers", "verdicts", "dispositions", "proposals", "open_constraints", "findings"):
        if not isinstance(report.get(key), list):
            problems.append(f"{key} must be present as a list")
    for r in report.get("reviewers", []):
        if not isinstance(r, dict) or r.get("role") not in ROLES or not isinstance(r.get("independent"), bool):
            problems.append(f"bad reviewer entry: {r}")
    for v in report.get("verdicts", []):
        if not isinstance(v, dict):
            problems.append(f"bad verdict: {v}")
            continue
        if not (isinstance(v.get("signal"), str) or (isinstance(v.get("rule_id"), str) and isinstance(v.get("file"), str))):
            problems.append(f"verdict needs signal or rule_id+file: {v}")
        if v.get("verdict") not in VERDICTS:
            problems.append(f"verdict value must be one of {sorted(VERDICTS)}: {v}")
        if not isinstance(v.get("reason"), str) or not v["reason"].strip():
            problems.append(f"verdict reason missing: {v}")
    for d in report.get("dispositions", []):
        if not isinstance(d, dict) or not isinstance(d.get("signal"), str):
            problems.append(f"bad disposition: {d}")
            continue
        status = d.get("status")
        if status not in ("justified_keep", "blocked"):
            problems.append(f"disposition status must be justified_keep or blocked: {d}")
            continue
        if status == "justified_keep":
            for field in ("exception", "evidence", "proposed_by", "accepted_by"):
                if not isinstance(d.get(field), str) or not d[field].strip():
                    problems.append(f"justified_keep needs {field}: {d['signal']}")
            if d.get("accepted_by") not in (None, "main") and isinstance(d.get("accepted_by"), str):
                problems.append(f"accepted_by must be main: {d['signal']}")
            if isinstance(d.get("proposed_by"), str) and d["proposed_by"] not in ROLES:
                problems.append(f"proposed_by must be a reviewer role: {d['signal']}")
            sig = signals_by_id.get(d["signal"])
            if sig is not None:
                allowed = exceptions_map.get(sig["fix_type"], [])
                if d.get("exception") not in allowed:
                    problems.append(f"exception {d.get('exception')!r} not allowed for {sig['fix_type']}: {d['signal']}")
        elif not isinstance(d.get("reason"), str) or not d["reason"].strip():
            problems.append(f"blocked disposition needs reason: {d['signal']}")
    for p in report.get("proposals", []):
        if not isinstance(p, dict) or any(not isinstance(p.get(f), str) or not p[f].strip() for f in PROPOSAL_FIELDS):
            problems.append(f"proposal needs {', '.join(PROPOSAL_FIELDS)}: {p}")
    for f in report.get("deleted_files", []) if isinstance(report.get("deleted_files", []), list) else ["not a list"]:
        if not isinstance(f, dict) or not isinstance(f.get("file"), str) or not isinstance(f.get("reason"), str) \
                or not f["reason"].strip():
            problems.append(f"bad deleted_files entry: {f}")
    for o in report.get("open_constraints", []):
        if not isinstance(o, dict) or not isinstance(o.get("file"), str) or not isinstance(o.get("text"), str) \
                or o.get("status") not in OPEN_STATUSES:
            problems.append(f"bad open_constraint: {o}")
    return problems


def _verdict_covers(verdicts: list[dict], signal: dict) -> bool:
    for v in verdicts:
        if not isinstance(v, dict):
            continue
        if v.get("signal") == signal["id"]:
            return True
        if v.get("rule_id") == signal["rule"] and v.get("file") == signal["file"]:
            return True
    return False


def cmd_verify(args) -> int:
    run_dir = Path(args.run_dir).resolve()
    try:
        run = read_json(run_dir / "run.json")
        snapshot = read_json(run_dir / "snapshot.json")
        before_scan = read_json(run_dir / "scan_before.json")
        before_protected = read_json(run_dir / "protected.json")
    except FileNotFoundError as exc:
        raise UsageError(f"run dir incomplete, run snapshot first: {exc.filename}") from exc
    target = Path(run["target"])
    rules = sc.load_rules()
    rules_data = read_json(sc.RULES_PATH)
    exceptions_map = rules_data["disposition_exceptions"]
    allowed, protect, fix_types = run["allowed"], run["protect"], set(run["fix_types"])
    scope = run["scope"]

    after_hashes = sc.hash_tree(target)
    current_hashes = dict(after_hashes)
    outputs_path = run_dir / "check_outputs.json"
    check_outputs = read_json(outputs_path) if outputs_path.exists() else {}
    for rel, record in check_outputs.items():
        # A file rewritten by an earlier after-edit check and untouched since is judged by
        # its state just before that check ran, so the check's own writes are not edits.
        if after_hashes.get(rel) == record["after"]:
            if record["before"] is None:
                del after_hashes[rel]
            else:
                after_hashes[rel] = record["before"]
    changes = _changes(snapshot["files"], after_hashes)
    changes["verified"] = now()
    write_json(run_dir / "changes.json", changes)
    changed = changes["added"] + changes["removed"] + changes["modified"]
    before_checked = before_scan["files"]["checked"]
    after_scan = sc.scan_tree(target, rules, scope, force=before_checked)
    write_json(run_dir / "scan_after.json", after_scan)
    after_protected = sc.protected_tree(target, rules, scope, force=before_checked)

    out = next_numbered(run_dir, "verdict")
    report_path = run_dir / "report.json"
    report = None
    report_error = None
    if report_path.exists():
        try:
            report = read_json(report_path)
        except json.JSONDecodeError as exc:
            report_error = f"report.json is not valid JSON: {exc}"
    else:
        report_error = "report.json missing"
    report_obj = report if isinstance(report, dict) else {}

    items = {}

    def item(key, reasons, na=False):
        items[key] = {"status": "NOT_APPLICABLE" if na else ("FAIL" if reasons else "PASS"), "reasons": reasons}

    # V1 scope
    v1 = []
    for rel in changed:
        if not allowed:
            v1.append(f"audit-only run changed {rel}")
        elif not sc.under(rel, allowed):
            v1.append(f"changed outside allowed paths: {rel}")
        elif not sc.under(rel, scope):
            v1.append(f"changed outside the audit scope: {rel}")
        elif protect and sc.under(rel, protect):
            v1.append(f"changed protected path: {rel}")
    item("V1", v1)

    # V2 protected comments
    v2 = []
    open_texts = [sc.normalize(o.get("text", "")) for o in report_obj.get("open_constraints", [])
                  if isinstance(o, dict) and o.get("status") == "accepted_unenforced"]
    for rel, entries in sorted(before_protected.items()):
        before_counts = sc.protected_counts(entries)
        after_counts = sc.protected_counts(after_protected.get(rel, []))
        for (cls, text), count in sorted(before_counts.items()):
            missing = count - after_counts.get((cls, text), 0)
            if missing <= 0:
                continue
            if cls == "constraint" and run["allow_constraint_deletion"]:
                if text and any(text in o for o in open_texts):
                    continue
                v2.append(f"constraint comment deleted but not reported as accepted_unenforced: {rel}: {text[:80]}")
                continue
            v2.append(f"protected {cls} comment removed ({missing}x): {rel}: {text[:80]}")
    item("V2", v2)

    # V3 designated confirmed signals
    v3 = []
    dispositions = {d["signal"]: d for d in report_obj.get("dispositions", []) if isinstance(d, dict) and isinstance(d.get("signal"), str)}
    before_ids = {s["id"]: s for s in before_scan["signals"]}
    after_ids = {s["id"] for s in after_scan["signals"]}
    groups: dict[tuple, list[str]] = {}
    for s in before_scan["signals"]:
        groups.setdefault((s["rule"], s["file"], s["context"]), []).append(s["id"])
    kept = 0
    designated_after = [s for s in after_scan["signals"]
                        if s["grade"] == "confirmed" and s["fix_type"] in fix_types and allowed
                        and sc.under(s["file"], allowed) and not (protect and sc.under(s["file"], protect))]
    for s in designated_after:
        d = dispositions.get(s["id"])
        if d is None:
            v3.append(f"designated confirmed signal left without disposition: {s['id']} ({s['file']}:{s['line']})")
            continue
        if d.get("status") == "blocked":
            v3.append(f"designated confirmed signal blocked: {s['id']}")
            continue
        if s["id"] not in before_ids:
            v3.append(f"justified_keep only applies to an unchanged pre-edit signal: {s['id']}")
            continue
        group = groups.get((s["rule"], s["file"], s["context"]), [s["id"]])
        if len(group) > 1:
            if any(g not in after_ids for g in group):
                v3.append(f"duplicate group partly removed, dispositions void: {s['id']}")
                continue
            if any(dispositions.get(g, {}).get("status") != "justified_keep" for g in group):
                v3.append(f"duplicate group needs justified_keep on every member: {s['id']}")
                continue
        kept += 1
    item("V3", v3)

    # V4 no increase of confirmed signals
    v4 = []

    def confirmed_counts(scan):
        counts = {}
        for s in scan["signals"]:
            if s["grade"] == "confirmed":
                counts[(s["file"], s["rule"])] = counts.get((s["file"], s["rule"]), 0) + 1
        return counts
    cb, ca = confirmed_counts(before_scan), confirmed_counts(after_scan)
    for key, count in sorted(ca.items()):
        if count > cb.get(key, 0):
            v4.append(f"confirmed signals increased: {key[0]} {key[1]} {cb.get(key, 0)} -> {count}")
    after_files = after_scan["files"]
    before_errors = {e["file"] for e in before_scan["parse_errors"]}
    declared = {d.get("file") for d in report_obj.get("deleted_files", []) if isinstance(d, dict)
                and isinstance(d.get("reason"), str) and d["reason"].strip()}
    with_signals = {s["file"] for s in before_scan["signals"]}
    for rel in sorted(before_checked):
        if rel not in after_files["checked"] and rel in with_signals and rel not in declared:
            v4.append(f"scanned file with signals removed or moved without a deleted_files entry: {rel}")
    for rel in changes["added"]:
        if allowed and sc.under(rel, allowed) and rel in after_files["excluded"]:
            v4.append(f"new file is excluded from the scan ({after_files['excluded'][rel]}): {rel}")
    for err in after_scan["parse_errors"]:
        if err["file"] not in before_errors:
            v4.append(f"file no longer parses ({err['error']}), its signals cannot be counted: {err['file']}")
    item("V4", v4)

    # V5 heuristic verdicts
    v5 = []
    verdicts = [v for v in report_obj.get("verdicts", []) if isinstance(v, dict)]
    heuristic = [s for s in before_scan["signals"] if s["grade"] == "heuristic"]
    heuristic += [s for s in after_scan["signals"] if s["grade"] == "heuristic" and s["id"] not in before_ids]
    for s in heuristic:
        if not _verdict_covers(verdicts, s):
            v5.append(f"heuristic signal without verdict: {s['id']} ({s['file']}:{s['line']})")
    item("V5", v5)

    # V6 reviews ran
    v6 = []
    reviews = run_dir / "reviews"
    needed = ["comment", "structure"]
    if any(lang == "prose" for lang in before_scan["files"]["checked"].values()):
        needed.append("prose")
    if changed:
        needed.append("diff")
    for role in needed:
        path = reviews / f"{role}.json"
        if not path.exists():
            v6.append(f"review record missing: reviews/{role}.json")
            continue
        try:
            read_json(path)
        except json.JSONDecodeError:
            v6.append(f"review record is not JSON: reviews/{role}.json")
    item("V6", v6)

    # V7 behavior preservation
    v7, v7_na = [], False
    check_rewrote = []
    baseline = snapshot.get("baseline_checks", [])
    after_checks = []
    if run["checks"] and any(c["status"] != "passed" for c in baseline):
        v7.append("baseline check already failing; fix the check or declare delegation")
    elif not changed:
        v7_na = True
    elif run["behavior_delegated"]:
        pass
    elif not run["checks"]:
        v7.append("target files changed without behavior checks or delegation")
    else:
        after_checks = run_checks(run["checks"], target, run_dir, f"after-{out.stem.split('-')[-1]}",
                                  run["check_timeout"])
        for c in after_checks:
            if c["status"] != "passed":
                v7.append(f"after-edit check {c['status']}: {c['command']}")
        post_check = sc.hash_tree(target)
        for rel in sorted(post_check):
            if post_check[rel] != current_hashes.get(rel):
                # carry the judged (mapped) state forward so repeated verifies stay anchored
                check_outputs[rel] = {"before": after_hashes.get(rel), "after": post_check[rel]}
                if rel in changed:
                    check_rewrote.append(rel)
        write_json(outputs_path, check_outputs)
    item("V7", v7, na=v7_na)

    # V8 report form
    v8 = []
    if report_error:
        v8.append(report_error)
    else:
        all_signals = {s["id"]: s for s in before_scan["signals"] + after_scan["signals"]}
        v8.extend(_validate_report(report, all_signals, exceptions_map))
    item("V8", v8)

    complete = all(i["status"] != "FAIL" for i in items.values())
    undesignated = []
    def undesignated_counts(scan):
        counts = {}
        for s in scan["signals"]:
            if s["fix_type"] not in fix_types and allowed and sc.under(s["file"], allowed):
                counts[(s["file"], s["rule"])] = counts.get((s["file"], s["rule"]), 0) + 1
        return counts
    ub, ua = undesignated_counts(before_scan), undesignated_counts(after_scan)
    for key in sorted(set(ub) | set(ua)):
        if ub.get(key, 0) != ua.get(key, 0):
            undesignated.append({"file": key[0], "rule": key[1], "before": ub.get(key, 0), "after": ua.get(key, 0)})
    not_designated = {}
    for s in after_scan["signals"]:
        if s["fix_type"] not in fix_types:
            not_designated.setdefault(s["rule"], []).append(f"{s['file']}:{s['line']}")
    verdict = {
        "result": "COMPLETE" if complete else "INCOMPLETE",
        "items": items,
        "changed_files": len(changed),
        "justified_keep": kept,
        "checks": {"baseline": baseline, "after": after_checks, "delegated": run["behavior_delegated"]},
        "undesignated_changed": undesignated,
        "not_designated_signals": {k: {"count": len(v), "locations": v[:50]} for k, v in sorted(not_designated.items())},
        "unchecked_files": after_scan["files"]["unchecked"],
        "check_outputs": sorted(check_outputs),
        "check_rewrote_edits": check_rewrote,
        "new_unscanned_files": [rel for rel in changes["added"] if allowed and sc.under(rel, allowed)
                                and rel in after_scan["files"]["unchecked"]],
        "excluded_dirs": sorted(sc.HASH_EXCLUDE_DIRS),
        "git_used": bool(snapshot.get("git_held")),
        "executor": args.executor,
        "verified": now(),
    }
    write_json(out, verdict)
    print(json.dumps({"result": verdict["result"], "verdict": str(out),
                      "items": {k: v["status"] for k, v in items.items()}}, sort_keys=True))
    for key, value in items.items():
        for reason in value["reasons"][:20]:
            print(f"{key}: {reason}", file=sys.stderr)
    return 0 if complete else 1


def cmd_restore(args) -> int:
    run_dir = Path(args.run_dir).resolve()
    try:
        run = read_json(run_dir / "run.json")
        snapshot = read_json(run_dir / "snapshot.json")
    except FileNotFoundError as exc:
        raise UsageError(f"run dir incomplete: {exc.filename}") from exc
    changes_path = run_dir / "changes.json"
    if not changes_path.exists():
        print("refused: no changes.json; run verify first so edits are recorded", file=sys.stderr)
        return 1
    changes = read_json(changes_path)
    outputs_path = run_dir / "check_outputs.json"
    check_outputs = read_json(outputs_path) if outputs_path.exists() else {}
    target = Path(run["target"])
    listed = set(changes["added"]) | set(changes["removed"]) | set(changes["modified"])
    wanted = sorted(set(rel_paths(target, args.files)) if args.files else listed)
    restored, refused = [], []
    for rel in wanted:
        path = target / rel
        components, cursor = [], target
        for part in Path(rel).parts:
            cursor = cursor / part
            components.append(cursor)
        if any(c.is_symlink() for c in components) or not inside(path.parent, target):
            refused.append({"file": rel, "reason": "path resolves outside the target or through a symlink"})
            continue
        if rel not in listed:
            refused.append({"file": rel, "reason": "not in the recorded change list"})
            continue
        current = sc.sha256_bytes(path.read_bytes()) if path.is_file() else None
        expected_after = changes["after"].get(rel)
        record = check_outputs.get(rel)
        rewritten_by_check = record is not None and current == record["after"] and record["before"] == expected_after
        if current != expected_after and not rewritten_by_check:
            refused.append({"file": rel, "reason": "file changed after the recorded edit (hash mismatch)"})
            continue
        if rel in changes["added"]:
            path.unlink()
            restored.append({"file": rel, "action": "deleted created file"})
            continue
        original_hash = snapshot["files"][rel]
        data = None
        backup = run_dir / "backup" / rel
        if backup.is_file():
            data = backup.read_bytes()
        elif rel in snapshot.get("git_held", {}):
            info = snapshot["git_held"][rel]
            git = shutil.which("git")
            if git:
                proc = subprocess.run([git, "cat-file", "blob", info["blob"]], cwd=info["repo"], capture_output=True)
                if proc.returncode == 0:
                    data = proc.stdout
        if data is None:
            refused.append({"file": rel, "reason": "original content unavailable (no backup, no Git blob)"})
            continue
        if sc.sha256_bytes(data) != original_hash:
            refused.append({"file": rel, "reason": "recovered content does not match the snapshot hash"})
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        restored.append({"file": rel, "action": "restored"})
    out = next_numbered(run_dir, "restore")
    write_json(out, {"restored": restored, "refused": refused, "at": now()})
    print(json.dumps({"restored": len(restored), "refused": len(refused), "log": str(out)}, sort_keys=True))
    for r in refused:
        print(f"refused: {r['file']}: {r['reason']}", file=sys.stderr)
    return 1 if refused else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="slop_gate.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan", help="read-only slop scan")
    p.add_argument("--target", required=True)
    p.add_argument("--paths", nargs="*")
    p.add_argument("--out")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("snapshot", help="record the pre-edit state")
    p.add_argument("--target", required=True)
    p.add_argument("--run-dir")
    p.add_argument("--scope", nargs="*")
    p.add_argument("--allowed", nargs="*")
    p.add_argument("--fix-types", nargs="*")
    p.add_argument("--protect", nargs="*")
    p.add_argument("--check", action="append")
    p.add_argument("--behavior-delegated")
    p.add_argument("--check-timeout", type=int, default=600)
    p.add_argument("--allow-constraint-deletion", action="store_true")
    p.add_argument("--base-ref")
    p.add_argument("--executor")
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("verify", help="judge COMPLETE or INCOMPLETE")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--executor")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("restore", help="put recorded edits back, file by file")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--files", nargs="*")
    p.set_defaults(func=cmd_restore)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except UsageError as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
