import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import acceptance_spec as spec
from support import basic_report, env, gate, latest_verdict, sc, small_target

GIT = shutil.which("git")
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_CONFIG_NOSYSTEM": "1", "HOME": tempfile.gettempdir()}


def git(target, *args):
    subprocess.run([GIT, *args], cwd=target, check=True, capture_output=True, env={**os.environ, **GIT_ENV})


class Base(unittest.TestCase):
    files = {"app/mod.py": "value = 1\n"}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.target = small_target(self.tmp, self.files)
        self.run_dir = self.tmp / "run"

    def snapshot(self, *extra, allowed=("app",), fix_types=("lint-suppression",), expect=0):
        args = ["snapshot", "--target", self.target, "--run-dir", self.run_dir]
        if allowed is not None:
            args += ["--allowed", *allowed]
        args += ["--fix-types", *fix_types, *extra]
        proc = gate(*args)
        self.assertEqual(proc.returncode, expect, proc.stdout + proc.stderr)
        return proc

    def verify(self):
        proc = gate("verify", "--run-dir", self.run_dir)
        self.assertIn(proc.returncode, (0, 1), proc.stderr)
        return proc.returncode, latest_verdict(self.run_dir)

    def edit(self, rel="app/mod.py", text="value = 2\n"):
        p = self.target / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


class Behavior(Base):
    def test_behavior_before_fail_incomplete(self):
        self.snapshot("--check", "false", expect=1)
        self.edit()
        basic_report(self.run_dir)
        code, v = self.verify()
        self.assertEqual((code, v["items"]["V7"]["status"]), (1, "FAIL"))
        self.assertIn("baseline check already failing", v["items"]["V7"]["reasons"][0])

    def test_behavior_before_fail_zero_change_incomplete(self):
        self.snapshot("--check", "false", expect=1)
        basic_report(self.run_dir)
        code, v = self.verify()
        self.assertEqual((code, v["result"], v["items"]["V7"]["status"]), (1, "INCOMPLETE", "FAIL"))

    def test_behavior_after_fail_incomplete(self):
        self.snapshot("--check", "test ! -f app/broken")
        self.edit("app/broken", "x = 1\n")
        basic_report(self.run_dir)
        code, v = self.verify()
        self.assertEqual((code, v["items"]["V7"]["status"]), (1, "FAIL"))
        self.assertEqual(v["checks"]["after"][0]["status"], "failed")

    def test_behavior_unavailable_incomplete(self):
        self.snapshot("--check", "if [ -f app/gone ]; then exit 127; fi")
        self.edit("app/gone", "x = 1\n")
        basic_report(self.run_dir)
        code, v = self.verify()
        self.assertEqual((code, v["checks"]["after"][0]["status"]), (1, "unavailable"))

    def test_behavior_timeout_incomplete(self):
        self.snapshot("--check", "if [ -f app/slow ]; then sleep 5; fi", "--check-timeout", "1")
        self.edit("app/slow", "x = 1\n")
        basic_report(self.run_dir)
        code, v = self.verify()
        self.assertEqual((code, v["checks"]["after"][0]["status"]), (1, "timeout"))

    def test_behavior_delegated_pass(self):
        self.snapshot("--behavior-delegated", "campaign gate runs the build")
        self.edit()
        basic_report(self.run_dir)
        code, v = self.verify()
        self.assertEqual((code, v["items"]["V7"]["status"]), (0, "PASS"))
        self.assertEqual(v["checks"]["delegated"], "campaign gate runs the build")

    def test_behavior_zero_change_not_applicable(self):
        self.snapshot("--check", "true")
        basic_report(self.run_dir, roles=("comment", "structure"))
        code, v = self.verify()
        self.assertEqual((code, v["items"]["V7"]["status"]), (0, "NOT_APPLICABLE"))

    def test_behavior_missing_with_edits_incomplete(self):
        self.snapshot()
        self.edit()
        basic_report(self.run_dir)
        code, v = self.verify()
        self.assertEqual((code, v["items"]["V7"]["status"]), (1, "FAIL"))

    def test_check_and_delegation_together_usage_error(self):
        self.snapshot("--check", "true", "--behavior-delegated", "x", expect=2)


class Judgment(Base):
    files = {"app/mod.py": "x = f()  # noqa: E501\n# TODO tidy\nvalue = 1\n"}

    def test_heuristic_verdict_content_does_not_block(self):
        self.snapshot("--check", "true")
        before = json.loads((self.run_dir / "scan_before.json").read_text())["signals"]
        sig = next(s for s in before if s["rule"] == "PY-LINT-SUPPRESSION")
        basic_report(self.run_dir, roles=("comment", "structure"),
                     verdicts=[{"rule_id": "ANY-EXCUSE-MARKER", "file": "app/mod.py", "verdict": "blocked", "reason": "owner"}],
                     dispositions=[{"signal": sig["id"], "status": "justified_keep", "exception": "allowed_suppression",
                                    "evidence": "line length is style only", "proposed_by": "comment", "accepted_by": "main"}])
        code, v = self.verify()
        self.assertEqual((code, v["result"]), (0, "COMPLETE"), v["items"])

    def test_disposition_requires_exception_and_evidence(self):
        self.snapshot("--check", "true")
        sig = json.loads((self.run_dir / "scan_before.json").read_text())["signals"][0]
        basic_report(self.run_dir, roles=("comment", "structure"),
                     verdicts=[{"rule_id": "ANY-EXCUSE-MARKER", "file": "app/mod.py", "verdict": "keep", "reason": "r"}],
                     dispositions=[{"signal": sig["id"], "status": "justified_keep", "exception": "allowed_suppression",
                                    "proposed_by": "comment", "accepted_by": "main"}])
        code, v = self.verify()
        self.assertEqual((code, v["items"]["V8"]["status"]), (1, "FAIL"))

    def test_disposition_only_for_unchanged_before_signal(self):
        self.snapshot("--check", "true")
        self.edit(text="x = g()  # noqa: E501\n# TODO tidy\nvalue = 1\n")
        rules = sc.load_rules()
        after = sc.scan_tree(self.target, rules)["signals"]
        sig = next(s for s in after if s["rule"] == "PY-LINT-SUPPRESSION")
        basic_report(self.run_dir,
                     verdicts=[{"rule_id": "ANY-EXCUSE-MARKER", "file": "app/mod.py", "verdict": "keep", "reason": "r"}],
                     dispositions=[{"signal": sig["id"], "status": "justified_keep", "exception": "allowed_suppression",
                                    "evidence": "e", "proposed_by": "comment", "accepted_by": "main"}])
        code, v = self.verify()
        self.assertEqual((code, v["items"]["V3"]["status"]), (1, "FAIL"))
        self.assertIn("unchanged pre-edit signal", v["items"]["V3"]["reasons"][0])

    def test_undesignated_change_reported_not_failed(self):
        self.snapshot("--check", "true")
        self.edit(text="x = f()  # noqa: E501\nvalue = 1\n")
        sig = json.loads((self.run_dir / "scan_before.json").read_text())["signals"]
        keep = next(s for s in sig if s["rule"] == "PY-LINT-SUPPRESSION")
        basic_report(self.run_dir,
                     verdicts=[{"rule_id": "ANY-EXCUSE-MARKER", "file": "app/mod.py", "verdict": "fix", "reason": "r"}],
                     dispositions=[{"signal": keep["id"], "status": "justified_keep", "exception": "allowed_suppression",
                                    "evidence": "e", "proposed_by": "comment", "accepted_by": "main"}])
        code, v = self.verify()
        self.assertEqual(code, 0, v["items"])
        self.assertEqual(v["undesignated_changed"], [{"file": "app/mod.py", "rule": "ANY-EXCUSE-MARKER", "before": 1, "after": 0}])

    def test_audit_only_any_change_fails(self):
        self.snapshot("--check", "true", allowed=None)
        self.edit()
        basic_report(self.run_dir, verdicts=[{"rule_id": "ANY-EXCUSE-MARKER", "file": "app/mod.py", "verdict": "keep", "reason": "r"}])
        code, v = self.verify()
        self.assertEqual((code, v["items"]["V1"]["status"]), (1, "FAIL"))

    def test_allowed_outside_scope_usage_error(self):
        self.snapshot("--scope", "app", allowed=("other",), expect=2)


class HashIntegrity(Base):
    files = {"app/mod.py": "import hashlib\n\n\ndef same(a, b):\n    return hashlib.sha256(a).digest() == hashlib.sha256(b).digest()\n"}

    roles = ("comment", "structure", "integrity", "diff")

    def hash_signal(self):
        return next(s for s in json.loads((self.run_dir / "scan_before.json").read_text())["signals"]
                    if s["rule"] == "ANY-HASH-INTEGRITY")

    def test_hash_integrity_kept_only_with_exception(self):
        self.snapshot("--check", "true", fix_types=("hash-integrity",))
        sig = self.hash_signal()
        basic_report(self.run_dir, roles=self.roles)
        code, v = self.verify()
        self.assertEqual((code, v["items"]["V3"]["status"]), (1, "FAIL"))
        keep = {"signal": sig["id"], "status": "justified_keep", "evidence": "e", "proposed_by": "integrity",
                "accepted_by": "main"}
        basic_report(self.run_dir, roles=self.roles, dispositions=[{**keep, "exception": "allowed_suppression"}])
        code, v = self.verify()
        self.assertEqual((code, v["items"]["V8"]["status"]), (1, "FAIL"))
        basic_report(self.run_dir, roles=self.roles, dispositions=[{**keep, "exception": "last_resort",
                                                  "evidence": "no git and no original copy beside the target"}])
        code, v = self.verify()
        self.assertEqual(code, 0, v["items"])

    def test_hash_integrity_fix_by_direct_comparison(self):
        self.snapshot("--check", "true", fix_types=("hash-integrity",))
        self.edit(text="import filecmp\n\n\ndef same(a, b):\n    return filecmp.cmp(a, b, shallow=False)\n")
        basic_report(self.run_dir, roles=self.roles)
        code, v = self.verify()
        self.assertEqual(code, 0, v["items"])

    def test_new_hash_in_a_fix_fails(self):
        self.files = {"app/mod.py": "value = 1\n"}
        self.target = small_target(self.tmp / "plain", self.files)
        self.snapshot("--check", "true", fix_types=("hash-integrity",))
        self.edit(text="import hashlib\n\nvalue = hashlib.sha256(b\"1\").hexdigest()\n")
        basic_report(self.run_dir)
        code, v = self.verify()
        self.assertEqual((code, v["items"]["V4"]["status"]), (1, "FAIL"))

    def test_integrity_review_required_for_hash_signal(self):
        self.snapshot("--check", "true", fix_types=("hash-integrity",))
        self.edit(text="import filecmp\n\n\ndef same(a, b):\n    return filecmp.cmp(a, b, shallow=False)\n")
        basic_report(self.run_dir)
        code, v = self.verify()
        self.assertEqual((code, v["items"]["V6"]["status"]), (1, "FAIL"))
        self.assertIn("reviews/integrity.json", " ".join(v["items"]["V6"]["reasons"]))
        basic_report(self.run_dir, roles=self.roles)
        code, v = self.verify()
        self.assertEqual((code, v["items"]["V6"]["status"]), (0, "PASS"), v["items"])

    def test_integrity_review_required_for_new_hash_signal(self):
        self.files = {"app/mod.py": "value = 1\n", "docs/note.md": "Use the ledger.\n"}
        self.target = small_target(self.tmp / "plain", self.files)
        self.snapshot("--check", "true", allowed=("app", "docs"), fix_types=("prose",))
        self.edit("docs/note.md", "The archive is intact: sha256 matched.\n")
        report = {"verdicts": [{"rule_id": "PROSE-HASH-RECORD", "file": "docs/note.md", "verdict": "fix", "reason": "r"}]}
        basic_report(self.run_dir, roles=("comment", "structure", "prose", "diff"), **report)
        code, v = self.verify()
        self.assertEqual((code, v["items"]["V6"]["status"]), (1, "FAIL"))
        self.assertIn("reviews/integrity.json", " ".join(v["items"]["V6"]["reasons"]))

    def test_integrity_review_not_required_without_hash_signal(self):
        self.files = {"app/mod.py": "import filecmp\n\n\ndef same(a, b):\n    return filecmp.cmp(a, b, shallow=False)\n"}
        self.target = small_target(self.tmp / "plain", self.files)
        self.snapshot("--check", "true", fix_types=("hash-integrity",))
        basic_report(self.run_dir, roles=("comment", "structure"))
        code, v = self.verify()
        self.assertEqual((code, v["items"]["V6"]["status"]), (0, "PASS"), v["items"])


class Groups(Base):
    files = {"app/pad.py": 'def pad(rows):\n    mark()\n    rows.append(1)  # noqa: E501\n    mark()\n'
                           '    rows.append(1)  # noqa: E501\n    mark()\n'}

    def run_with(self, keep_both: bool, edit: bool):
        self.snapshot("--check", "true")
        pair = [s for s in json.loads((self.run_dir / "scan_before.json").read_text())["signals"]
                if s["rule"] == "PY-LINT-SUPPRESSION"]
        self.assertEqual(len({s["context"] for s in pair}), 1)
        if edit:
            self.edit("app/pad.py", 'def pad(rows):\n    mark()\n    rows.append(1)\n    mark()\n'
                                    '    rows.append(1)  # noqa: E501\n    mark()\n')
        chosen = pair if keep_both else pair[:1]
        basic_report(self.run_dir, roles=("comment", "structure", "diff"), dispositions=[
            {"signal": s["id"], "status": "justified_keep", "exception": "allowed_suppression", "evidence": "style",
             "proposed_by": "comment", "accepted_by": "main"} for s in chosen])
        return self.verify()

    def test_duplicate_group_disposition_all_or_nothing(self):
        code, v = self.run_with(keep_both=True, edit=False)
        self.assertEqual((code, v["items"]["V3"]["status"]), (0, "PASS"), v["items"])
        shutil.rmtree(self.run_dir)
        code, v = self.run_with(keep_both=False, edit=False)
        self.assertEqual((code, v["items"]["V3"]["status"]), (1, "FAIL"))
        shutil.rmtree(self.run_dir)
        code, v = self.run_with(keep_both=True, edit=True)
        self.assertEqual((code, v["items"]["V3"]["status"]), (1, "FAIL"))
        self.assertIn("duplicate group partly removed", " ".join(v["items"]["V3"]["reasons"]))


class Protected(Base):
    files = {"app/mod.py": "# Copyright 2026 Example\n# Copyright 2026 Example\nvalue = 1\n", "app/test_mod.py": "x = 1\n"}

    def test_protected_comment_count_decrease_fails(self):
        self.snapshot("--check", "true")
        self.edit(text="# Copyright 2026 Example\nvalue = 1\n")
        basic_report(self.run_dir)
        code, v = self.verify()
        self.assertEqual((code, v["items"]["V2"]["status"]), (1, "FAIL"))

    def test_protect_path_inside_allowed_fails(self):
        self.snapshot("--check", "true", "--protect", "app/test_mod.py")
        self.edit("app/test_mod.py", "x = 2\n")
        basic_report(self.run_dir)
        code, v = self.verify()
        self.assertEqual((code, v["items"]["V1"]["status"]), (1, "FAIL"))


class GitAndRunDir(Base):
    files = {"app/held.py": "a = 1\n", "app/dirty.py": "b = 1\n", "app/ignored.py": "c = 1\n", ".gitignore": "app/ignored.py\n"}

    def git_init(self):
        git(self.target, "init", "-q")
        git(self.target, "add", "app/held.py", "app/dirty.py", ".gitignore")
        git(self.target, "commit", "-q", "-m", "init")
        (self.target / "app" / "dirty.py").write_text("b = 2\n")
        (self.target / "app" / "untracked.py").write_text("d = 1\n")

    @unittest.skipIf(GIT is None, "git not installed")
    def test_backup_skipped_only_for_git_held_clean(self):
        self.git_init()
        self.snapshot()
        snap = json.loads((self.run_dir / "snapshot.json").read_text())
        self.assertEqual(sorted(snap["git_held"]), ["app/held.py"])

    @unittest.skipIf(GIT is None, "git not installed")
    def test_backup_for_dirty_untracked_ignored(self):
        self.git_init()
        self.snapshot()
        snap = json.loads((self.run_dir / "snapshot.json").read_text())
        self.assertEqual(sorted(snap["backups"]), ["app/dirty.py", "app/ignored.py", "app/untracked.py"])
        self.assertEqual((self.run_dir / "backup" / "app" / "dirty.py").read_text(), "b = 2\n")

    def test_backup_when_git_binary_missing(self):
        proc = subprocess.run([sys.executable, str(Path(sc.__file__).with_name("slop_gate.py")), "snapshot",
                               "--target", str(self.target), "--run-dir", str(self.run_dir), "--allowed", "app"],
                              capture_output=True, text=True, env={"PATH": "/nonexistent", "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        snap = json.loads((self.run_dir / "snapshot.json").read_text())
        self.assertEqual(snap["git_held"], {})
        self.assertEqual(len(snap["backups"]), 3)

    def test_gitless_target_never_not_run(self):
        self.snapshot("--check", "true")
        self.edit("app/held.py", "a = 2\n")
        basic_report(self.run_dir)
        code, v = self.verify()
        self.assertEqual((code, v["result"]), (0, "COMPLETE"), v["items"])
        self.assertFalse(v["git_used"])
        self.assertTrue(all(i["status"] in ("PASS", "NOT_APPLICABLE") for i in v["items"].values()))

    @unittest.skipIf(GIT is None, "git not installed")
    def test_base_ref_narrows_scope(self):
        self.git_init()
        self.snapshot("--base-ref", "HEAD")
        run = json.loads((self.run_dir / "run.json").read_text())
        self.assertEqual(run["scope"], ["app/dirty.py", "app/untracked.py"])

    def test_base_ref_on_gitless_usage_error(self):
        if GIT is not None:
            probe = subprocess.run([GIT, "rev-parse", "--show-toplevel"], cwd=self.target, capture_output=True)
            if probe.returncode == 0:
                self.skipTest("temporary directory is inside a Git work tree")
        self.snapshot("--base-ref", "HEAD", expect=2)

    def test_run_dir_never_overwritten(self):
        self.snapshot()
        self.snapshot(expect=2)

    def test_run_dir_inside_target_refused(self):
        self.run_dir = self.target / "runs"
        self.snapshot(expect=2)
        self.assertFalse((self.target / "runs").exists())

    def test_target_tree_not_written_by_snapshot_verify(self):
        before = sc.hash_tree(self.target)
        self.snapshot()
        basic_report(self.run_dir, roles=("comment", "structure"))
        self.verify()
        self.assertEqual(sc.hash_tree(self.target), before)
        self.assertEqual(sorted(p.name for p in (self.target / "app").iterdir()), ["dirty.py", "held.py", "ignored.py"])


class Regressions(Base):
    files = {"app/mod.py": "import os  # noqa: F401\nvalue = 1\n", "app/keep.py": "# Copyright 2026 Example\nkeep = 1\n",
             "app/run.sh": "#!/usr/bin/env bash\necho hi\n"}

    def one_fix_report(self, **extra):
        basic_report(self.run_dir, verdicts=[], **extra)

    @unittest.skipIf(GIT is None, "git not installed")
    def test_base_ref_edit_outside_narrowed_scope_fails(self):
        git(self.target, "init", "-q")
        git(self.target, "add", ".")
        git(self.target, "commit", "-q", "-m", "init")
        self.edit("app/mod.py", "import os  # noqa: F401\nvalue = 2\n")
        self.snapshot("--base-ref", "HEAD", "--behavior-delegated", "unit test")
        self.edit("app/keep.py", "keep = 1\n")
        self.one_fix_report()
        code, v = self.verify()
        self.assertEqual((code, v["items"]["V1"]["status"]), (1, "FAIL"))
        self.assertIn("outside the audit scope", " ".join(v["items"]["V1"]["reasons"]))

    def test_generated_marker_escape_fails(self):
        self.snapshot("--behavior-delegated", "unit test")
        self.edit("app/mod.py", "# DO NOT EDIT\nimport os  # noqa: F401\nvalue = 1\n")
        self.one_fix_report()
        code, v = self.verify()
        self.assertEqual((code, v["items"]["V3"]["status"]), (1, "FAIL"), "the file is still scanned")

    def test_removed_or_moved_file_with_signals_fails(self):
        self.snapshot("--behavior-delegated", "unit test")
        (self.target / "app/mod.py").rename(self.target / "app/mod.py.orig")
        self.one_fix_report()
        code, v = self.verify()
        self.assertEqual((code, v["items"]["V4"]["status"]), (1, "FAIL"))
        (self.target / "app/mod.py.orig").unlink()
        self.one_fix_report(deleted_files=[{"file": "app/mod.py", "reason": "dead module, no importers"}])
        code, v = self.verify()
        self.assertEqual(v["items"]["V4"]["status"], "PASS", v["items"])
        (self.target / "app/vendor").mkdir()
        (self.target / "app/vendor/mod.py").write_text("import os  # noqa: F401\n")
        self.one_fix_report()
        code, v = self.verify()
        self.assertEqual((code, v["items"]["V4"]["status"]), (1, "FAIL"))

    def test_check_rewrite_does_not_hide_failed_edit(self):
        self.snapshot("--check", "sed -i 's/value/value/' app/mod.py && grep -q 'value = 1' app/mod.py")
        self.edit("app/mod.py", "import os  # noqa: F401\nvalue = 2\n")
        self.one_fix_report()
        code, v = self.verify()
        self.assertEqual((code, v["items"]["V7"]["status"]), (1, "FAIL"))
        code, v = self.verify()
        self.assertEqual((code, v["items"]["V7"]["status"]), (1, "FAIL"), "a second verify must not turn the failure into COMPLETE")
        self.assertEqual(v["changed_files"], 1)

    def test_repeated_verify_with_changing_check_output(self):
        self.snapshot("--check", "mkdir -p out && date +%s%N > out/run.log")
        self.edit("app/mod.py", "value = 1\n")
        self.one_fix_report()
        for attempt in range(4):
            code, v = self.verify()
            self.assertEqual((attempt, code, v["result"]), (attempt, 0, "COMPLETE"), v["items"])

    def test_restore_after_check_rewrite(self):
        self.snapshot("--check", "sed -i 's/value = 2/value = 2 /' app/mod.py && grep -q 'value = 1' app/mod.py")
        self.edit("app/mod.py", "import os  # noqa: F401\nvalue = 2\n")
        self.one_fix_report()
        code, v = self.verify()
        self.assertEqual(code, 1)
        self.assertEqual(v["check_rewrote_edits"], ["app/mod.py"])
        proc = gate("restore", "--run-dir", self.run_dir, "--files", "app/mod.py")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((self.target / "app/mod.py").read_text(), "import os  # noqa: F401\nvalue = 1\n")

    def test_restore_refuses_final_symlink(self):
        self.snapshot("--behavior-delegated", "unit test")
        self.edit("app/run.sh", "#!/usr/bin/env bash\necho bye\n")
        self.one_fix_report()
        self.verify()
        outside = self.tmp / "outside.sh"
        outside.write_text("#!/usr/bin/env bash\necho bye\n")
        (self.target / "app/run.sh").unlink()
        (self.target / "app/run.sh").symlink_to(outside)
        proc = gate("restore", "--run-dir", self.run_dir, "--files", "app/run.sh")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(outside.read_text(), "#!/usr/bin/env bash\necho bye\n")

    def test_syntax_error_escape_fails(self):
        self.snapshot("--behavior-delegated", "unit test")
        self.edit("app/mod.py", "import os  # noqa: F401\nX = (1\n")
        self.one_fix_report()
        code, v = self.verify()
        self.assertEqual((code, v["items"]["V4"]["status"]), (1, "FAIL"))
        self.assertIn("no longer parses", " ".join(v["items"]["V4"]["reasons"]))

    def test_check_side_effect_does_not_fail_reverify(self):
        self.snapshot("--check", "date +%s%N > app/check.log")
        self.edit("app/mod.py", "value = 1\n")
        self.one_fix_report()
        code, v = self.verify()
        self.assertEqual(code, 0, v["items"])
        code, v = self.verify()
        self.assertEqual((code, v["items"]["V1"]["status"]), (0, "PASS"), v["items"])
        self.assertIn("app/check.log", v["check_outputs"])

    def test_approved_deletion_needs_accepted_unenforced(self):
        self.files = None
        self.edit("app/c.py", "# IMPORTANT: keep order\nvalue = 1\n")
        self.snapshot("--behavior-delegated", "unit test", "--allow-constraint-deletion")
        self.edit("app/c.py", "value = 1\n")
        groups = [{"rule_id": "ANY-CONSTRAINT-MARKER", "file": "app/c.py", "verdict": "fix", "reason": "approved"}]
        basic_report(self.run_dir, verdicts=groups,
                     dispositions=[], open_constraints=[{"file": "app/c.py", "text": "IMPORTANT: keep order", "status": "blocked"}])
        code, v = self.verify()
        self.assertEqual((code, v["items"]["V2"]["status"]), (1, "FAIL"))

    def test_restore_refuses_symlink_escape(self):
        self.snapshot("--behavior-delegated", "unit test")
        self.edit("app/run.sh", "#!/usr/bin/env bash\necho bye\n")
        self.one_fix_report()
        self.verify()
        outside = self.tmp / "outside"
        outside.mkdir()
        app = self.target / "app"
        moved = self.tmp / "app-moved"
        app.rename(moved)
        app.symlink_to(outside, target_is_directory=True)
        proc = gate("restore", "--run-dir", self.run_dir)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(list(outside.iterdir()), [])

    def test_scan_out_inside_target_refused(self):
        proc = gate("scan", "--target", self.target, "--out", self.target / "scan.json")
        self.assertEqual(proc.returncode, 2)
        self.assertFalse((self.target / "scan.json").exists())

    def test_report_missing_list_key_fails(self):
        self.snapshot("--behavior-delegated", "unit test")
        basic_report(self.run_dir, roles=("comment", "structure"))
        report = json.loads((self.run_dir / "report.json").read_text())
        del report["findings"]
        (self.run_dir / "report.json").write_text(json.dumps(report))
        code, v = self.verify()
        self.assertEqual((code, v["items"]["V8"]["status"]), (1, "FAIL"))


class InstalledSkill(unittest.TestCase):
    # A copy installed inside the target, run from there, so its own folder lies inside the target.
    skill_rel = ".claude/skills/anti-code-slop"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.target = small_target(self.tmp, {"src/a.py": "x = 1  # noqa\n"})
        self.skill = self.target / self.skill_rel
        shutil.copytree(spec.SKILL_DIR, self.skill, ignore=shutil.ignore_patterns("tests", "__pycache__"))

    def copy_gate(self, *args) -> subprocess.CompletedProcess:
        script = self.skill / "scripts" / "slop_gate.py"
        return subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True, text=True, env=env())

    def test_allowed_covering_installed_skill_refused(self):
        for n, allowed in enumerate([".", ".claude", self.skill_rel, self.skill_rel + "/scripts"]):
            with self.subTest(allowed=allowed):
                run_dir = self.tmp / f"refused-{n}"
                proc = self.copy_gate("snapshot", "--target", self.target, "--run-dir", run_dir, "--allowed", allowed)
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertIn("input error", proc.stderr)
                self.assertIn(self.skill_rel, proc.stderr)
                self.assertFalse(run_dir.exists())
        for n, allowed in enumerate(["src", self.skill_rel + "-notes"]):
            with self.subTest(allowed=allowed):
                run_dir = self.tmp / f"accepted-{n}"
                proc = self.copy_gate("snapshot", "--target", self.target, "--run-dir", run_dir, "--allowed", allowed)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_installed_skill_edit_fails_v1(self):
        run_dir = self.tmp / "run"
        proc = self.copy_gate("snapshot", "--target", self.target, "--run-dir", run_dir, "--allowed", "src",
                              "--fix-types", "lint-suppression", "--behavior-delegated", "unit test")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        excluded = json.loads((run_dir / "scan_before.json").read_text())["files"]["excluded"]
        self.assertEqual(excluded.get(self.skill_rel + "/SKILL.md"), "anti_code_slop_skill")
        with open(self.skill / "SKILL.md", "a", encoding="utf-8") as handle:
            handle.write("edited during the run\n")
        (self.target / "src" / "a.py").write_text("x = 1\n")
        basic_report(run_dir)
        proc = self.copy_gate("verify", "--run-dir", run_dir)
        v = latest_verdict(run_dir)
        self.assertEqual((proc.returncode, v["result"], v["items"]["V1"]["status"]), (1, "INCOMPLETE", "FAIL"))
        self.assertIn(f"changed outside allowed paths: {self.skill_rel}/SKILL.md", v["items"]["V1"]["reasons"])

    def test_skill_folder_as_target_allowed(self):
        run_dir = self.tmp / "self-run"
        proc = self.copy_gate("snapshot", "--target", self.skill, "--run-dir", run_dir, "--allowed", ".")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        checked = json.loads((run_dir / "scan_before.json").read_text())["files"]["checked"]
        self.assertIn("scripts/slop_gate.py", checked)


if __name__ == "__main__":
    unittest.main()
