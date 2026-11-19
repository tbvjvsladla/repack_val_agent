import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import acceptance_spec as spec
import judge_acceptance
from support import build_variant, cargo_available, env, fixture_checks, gate, latest_verdict, sc


def node_test_count(run_dir: Path) -> int:
    node_index = next(i for i, c in enumerate(fixture_checks(), 1) if c.startswith("node "))
    text = (run_dir / "checks" / f"before-{node_index}.out").read_text()
    match = re.search(r"^ℹ tests (\d+)", text, re.M) or re.search(r"^# tests (\d+)", text, re.M)
    return int(match.group(1)) if match else 0


class Variants(unittest.TestCase):
    def test_variants(self):
        names = sorted(p.name for p in spec.VARIANTS.iterdir() if p.is_dir())
        self.assertEqual(len([n for n in names if n.startswith("bad-")]), 16)
        for name in names:
            with self.subTest(variant=name):
                meta = json.loads((spec.VARIANTS / name / "variant.json").read_text())
                if meta.get("requires") == "cargo" and not cargo_available():
                    self.skipTest("cargo not found on PATH or in ~/.cargo/bin")
                with tempfile.TemporaryDirectory() as tmp:
                    _, run_dir, meta = build_variant(name, Path(tmp))
                    self.assertGreaterEqual(node_test_count(run_dir), 2, "node --test must run the fixture tests")
                    proc = gate("verify", "--run-dir", run_dir)
                    verdict = latest_verdict(run_dir)
                    failed = sorted(k for k, v in verdict["items"].items() if v["status"] == "FAIL")
                    self.assertEqual(verdict["result"], meta["expect"], verdict["items"])
                    self.assertEqual(proc.returncode, 0 if meta["expect"] == "COMPLETE" else 1)
                    self.assertEqual(failed, sorted(meta["fail"]), verdict["items"])

    def test_fixture_checks_leave_hashes_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            shutil.copytree(spec.SEEDED, project)
            e = env()
            for check in fixture_checks():
                proc = subprocess.run(["bash", "-c", check], cwd=project, capture_output=True, text=True, env=e)
                self.assertEqual(proc.returncode, 0, check + proc.stdout + proc.stderr)
            self.assertEqual(sc.hash_tree(project), sc.hash_tree(spec.SEEDED))


class JudgeSelfTest(unittest.TestCase):
    def make_workspace(self, root: Path) -> tuple[Path, Path, str, str]:
        workspace = root / "round-1"
        _, run_dir, _ = build_variant("good", workspace)
        skill_copy = workspace / ".claude" / "skills" / "anti-code-slop"
        shutil.copytree(spec.SKILL_DIR, skill_copy, ignore=shutil.ignore_patterns("tests", "__pycache__"))
        transcript = root / "round-1.transcript.jsonl"
        events = [{"type": "system", "subtype": "init", "session_id": "s-1", "model": "claude-test"},
                  {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read",
                                                                 "input": {"file_path": str(workspace / "project/rust/src/lib.rs")}}]}}]
        transcript.write_text("\n".join(json.dumps(e) for e in events) + "\n")
        report = json.loads((run_dir / "report.json").read_text())
        report["findings"] = [{"file": "python/app/ledger.py", "line": 58, "role": "structure", "note": "_format has one caller"}]
        (run_dir / "report.json").write_text(json.dumps(report))
        gate("verify", "--run-dir", run_dir)
        return workspace, transcript, judge_acceptance.tree_hash(skill_copy), judge_acceptance.tree_hash(spec.SKILL_DIR)

    def judge(self, workspace, transcript, skill_hash, repo_hash):
        proc = subprocess.run([sys.executable, str(Path(judge_acceptance.__file__)), "judge", "--workspace", str(workspace),
                               "--transcript", str(transcript), "--skill-hash", skill_hash, "--repo-skill-hash", repo_hash],
                              capture_output=True, text=True, env=env())
        result = json.loads((workspace.parent / f"{workspace.name}.judge.json").read_text())
        return proc.returncode, result

    def test_judge_on_good_workspace(self):
        if not cargo_available():
            self.skipTest("the judge expects the four fixture checks, including cargo")
        with tempfile.TemporaryDirectory() as tmp:
            workspace, transcript, skill_hash, repo_hash = self.make_workspace(Path(tmp))
            code, result = self.judge(workspace, transcript, skill_hash, repo_hash)
            self.assertEqual((code, result["result"]), (0, "ACCEPTANCE PASS"), json.dumps(result["steps"], indent=1))
            self.assertEqual(result["observations"]["t7"]["caught"], 1)

            run_dir = workspace / spec.RUN_DIR
            run_json = run_dir / "run.json"
            original_run = run_json.read_text()
            run = json.loads(original_run)
            run["fix_types"] = ["lint-suppression"]
            run_json.write_text(json.dumps(run))
            code, result = self.judge(workspace, transcript, skill_hash, repo_hash)
            self.assertEqual((code, result["steps"]["1_inputs"]["status"]), (1, "FAIL"))
            run_json.write_text(original_run)

            protected = run_dir / "protected.json"
            original_protected = protected.read_text()
            data = json.loads(original_protected)
            data["python/app/ledger.py"] = [e for e in data["python/app/ledger.py"] if e["class"] != "issue_rfc"]
            protected.write_text(json.dumps(data))
            code, result = self.judge(workspace, transcript, skill_hash, repo_hash)
            self.assertEqual((code, result["steps"]["2_before_state"]["status"]), (1, "FAIL"))
            protected.write_text(original_protected)

            original_transcript = transcript.read_text()
            transcript.write_text(original_transcript + json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "cat ~/ws_docker/repack_val_agent/docs/plan/x.md"}}]}}) + "\n")
            code, result = self.judge(workspace, transcript, skill_hash, repo_hash)
            self.assertEqual((code, result["steps"]["3_isolation"]["status"]), (1, "FAIL"))
            transcript.write_text(original_transcript)

            code, result = self.judge(workspace, transcript, skill_hash, repo_hash)
            self.assertEqual(code, 0, json.dumps(result["steps"], indent=1))


if __name__ == "__main__":
    unittest.main()
