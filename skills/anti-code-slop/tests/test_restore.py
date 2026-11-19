import json
import unittest

from support import basic_report, gate
from test_verify import GIT, Base, git


class Restore(Base):
    files = {"app/mod.py": "value = 1\n", "app/other.py": "other = 1\n"}

    def prepare(self):
        self.snapshot("--behavior-delegated", "unit test")
        basic_report(self.run_dir)

    def restore(self, *files):
        args = ["restore", "--run-dir", self.run_dir]
        if files:
            args += ["--files", *files]
        return gate(*args)

    def test_restore_from_backup(self):
        self.prepare()
        self.edit()
        self.verify()
        proc = self.restore()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((self.target / "app/mod.py").read_text(), "value = 1\n")

    @unittest.skipIf(GIT is None, "git not installed")
    def test_restore_from_git_blob(self):
        git(self.target, "init", "-q")
        git(self.target, "add", ".")
        git(self.target, "commit", "-q", "-m", "init")
        self.prepare()
        snap = json.loads((self.run_dir / "snapshot.json").read_text())
        self.assertIn("app/mod.py", snap["git_held"])
        self.assertFalse((self.run_dir / "backup" / "app" / "mod.py").exists())
        self.edit()
        self.verify()
        proc = self.restore()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((self.target / "app/mod.py").read_text(), "value = 1\n")

    def test_restore_refuses_without_changes(self):
        self.prepare()
        self.edit()
        proc = self.restore()
        self.assertEqual(proc.returncode, 1)
        self.assertEqual((self.target / "app/mod.py").read_text(), "value = 2\n")

    def test_restore_refuses_hash_mismatch(self):
        self.prepare()
        self.edit()
        self.verify()
        self.edit(text="value = 3\n")
        proc = self.restore()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("hash mismatch", proc.stderr)
        self.assertEqual((self.target / "app/mod.py").read_text(), "value = 3\n")

    def test_restore_refuses_unlisted_file(self):
        self.prepare()
        self.edit()
        self.verify()
        proc = self.restore("app/other.py")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("not in the recorded change list", proc.stderr)

    def test_restore_deletes_created_file_only_if_unchanged(self):
        self.prepare()
        self.edit("app/new.py", "n = 1\n")
        self.verify()
        self.assertEqual(self.restore().returncode, 0)
        self.assertFalse((self.target / "app/new.py").exists())
        self.edit("app/new2.py", "n = 1\n")
        self.verify()
        self.edit("app/new2.py", "n = 2\n")
        self.assertEqual(self.restore("app/new2.py").returncode, 1)
        self.assertTrue((self.target / "app/new2.py").exists())


if __name__ == "__main__":
    unittest.main()
