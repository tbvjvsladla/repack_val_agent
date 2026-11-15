"""Project root and envs/ location for each install place; no SDK import."""

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

RUN = Path(__file__).resolve().parents[1] / "scripts" / "run.py"
spec = importlib.util.spec_from_file_location("deepeval_pack_run", RUN)
assert spec is not None and spec.loader is not None
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)


class ProjectRoot(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="dp-root-")).resolve()

    def test_install_places(self) -> None:
        for place in ("skills", ".agents/skills", ".claude/skills"):
            with self.subTest(place=place), patch.dict(os.environ, {}, clear=False):
                os.environ.pop("DEEPEVAL_PACK_PROJECT_ROOT", None)
                skill = self.tmp / place / "deepeval-pack"
                self.assertEqual(run.find_project_root(skill), self.tmp)

    def test_adapter_symlink_resolves_to_canonical_root(self) -> None:
        canonical = self.tmp / "skills" / "deepeval-pack"
        canonical.mkdir(parents=True)
        (self.tmp / ".claude" / "skills").mkdir(parents=True)
        link = self.tmp / ".claude" / "skills" / "deepeval-pack"
        link.symlink_to(Path("..") / ".." / "skills" / "deepeval-pack")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DEEPEVAL_PACK_PROJECT_ROOT", None)
            self.assertEqual(run.find_project_root(link.resolve()), self.tmp)

    def test_env_override(self) -> None:
        other = self.tmp / "other"
        with patch.dict(os.environ, {"DEEPEVAL_PACK_PROJECT_ROOT": str(other)}):
            self.assertEqual(run.find_project_root(self.tmp / "skills" / "x"), other)


if __name__ == "__main__":
    unittest.main()
