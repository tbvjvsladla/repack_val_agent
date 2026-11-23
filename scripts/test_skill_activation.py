"""Tests for scripts/skill_activation.py. Every test works in a throwaway root.

    python3 -m unittest scripts/test_skill_activation.py -v
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().with_name("skill_activation.py")
ADAPTERS = (".agents/skills", ".claude/skills")
SHARED = """schema_version: 1
skills:
  alpha: off
  beta: off
  gamma: on
mcp:
  beta:
    server: betasrv
    command: [skills/beta/run, serve]
setup:
  alpha: ["do the alpha setup"]
"""


class Root:
    def __init__(self, tmp: Path):
        self.path = tmp / "repo"
        self.home = tmp / "home"
        self.home.mkdir()
        for name in ("alpha", "beta", "gamma", "base"):
            (self.path / "skills" / name).mkdir(parents=True)
            (self.path / "skills" / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
        for base in ADAPTERS:
            (self.path / base).mkdir(parents=True)
            (self.path / base / "base").symlink_to("../../skills/base", target_is_directory=True)
        (self.path / "skill-activation.yaml").write_text(SHARED, encoding="utf-8")
        (self.path / "campaigns" / "c1").mkdir(parents=True)
        (self.path / "campaigns" / "c2").mkdir(parents=True)

    def run(self, *args: str, codex_home: Path | None = None) -> tuple[int, dict]:
        env = {**os.environ, "HOME": str(self.home)}
        env.pop("CODEX_HOME", None)
        if codex_home is not None:
            env["CODEX_HOME"] = str(codex_home)
        proc = subprocess.run([sys.executable, "-B", str(SCRIPT), "--root", str(self.path), *args],
                              capture_output=True, text=True, env=env)
        return proc.returncode, json.loads(proc.stdout)

    def local(self, text: str) -> None:
        (self.path / "skill-activation.local.yaml").write_text(text, encoding="utf-8")

    def linked(self, name: str) -> list[bool]:
        return [(self.path / base / name).is_symlink() for base in ADAPTERS]

    def record(self, instance: str) -> dict:
        return json.loads((self.path / "campaigns" / instance / "activation.json").read_text(encoding="utf-8"))

    def active(self, instance: str) -> None:
        (self.path / "campaigns" / "ACTIVE").write_text(instance + "\n", encoding="utf-8")


class SkillActivation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Root(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_local_overrides_shared(self):
        self.root.local("schema_version: 1\nskills:\n  alpha: on\n  gamma: off\n")
        code, out = self.root.run("candidates")
        self.assertEqual(code, 0)
        self.assertEqual({c["skill"]: c["on"] for c in out["candidates"]}, {"alpha": True, "beta": False, "gamma": False})

    def test_sync_lays_and_removes_only_optional_links(self):
        self.root.local("schema_version: 1\nskills:\n  alpha: on\n")
        code, out = self.root.run("sync", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.root.linked("alpha"), [True, True])
        self.assertEqual(self.root.linked("gamma"), [True, True])
        self.assertEqual(os.readlink(self.root.path / ".claude/skills/alpha"), "../../skills/alpha")
        self.root.local("schema_version: 1\nskills:\n  alpha: off\n  gamma: off\n")
        code, out = self.root.run("sync", "--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.root.linked("alpha") + self.root.linked("gamma"), [False] * 4)
        self.assertEqual(self.root.linked("base"), [True, True], "base tools are never touched")

    def test_sync_leaves_foreign_files_and_fails(self):
        (self.root.path / ".claude/skills/alpha").write_text("not a link", encoding="utf-8")
        self.root.local("schema_version: 1\nskills:\n  alpha: on\n")
        code, out = self.root.run("sync", "--apply")
        self.assertEqual(code, 1)
        self.assertTrue(any(".claude/skills/alpha" in e for e in out["errors"]))
        self.assertEqual((self.root.path / ".claude/skills/alpha").read_text(encoding="utf-8"), "not a link")

    def test_check_detects_link_mismatch(self):
        self.root.run("sync", "--apply")
        self.assertEqual(self.root.run("check")[0], 0)
        (self.root.path / ADAPTERS[0] / "gamma").unlink()
        code, out = self.root.run("check")
        self.assertEqual(code, 1)
        self.assertTrue(any("gamma" in p and "sync --apply" in p for p in out["problems"]))

    def test_check_detects_mcp_residue_in_claude_and_codex(self):
        self.root.run("sync", "--apply")
        claude = {"projects": {str(self.root.path.resolve()): {"mcpServers": {"betasrv": {}}}}}
        (self.root.home / ".claude.json").write_text(json.dumps(claude), encoding="utf-8")
        code, out = self.root.run("check")
        self.assertEqual(code, 1)
        self.assertTrue(any("claude mcp remove --scope local betasrv" in p for p in out["problems"]))
        (self.root.home / ".claude.json").unlink()
        (self.root.path / ".codex").mkdir()
        (self.root.path / ".codex/config.toml").write_text('[mcp_servers.betasrv]\ncommand = "x"\n', encoding="utf-8")
        code, out = self.root.run("check")
        self.assertEqual(code, 1)
        self.assertTrue(any("[mcp_servers.betasrv]" in p for p in out["problems"]))
        (self.root.path / ".codex/config.toml").unlink()
        self.assertEqual(self.root.run("check")[0], 0)

    def test_check_uses_codex_home_instead_of_default_user_config(self):
        self.root.run("sync", "--apply")
        config_text = '[mcp_servers.betasrv]\ncommand = "x"\n'
        default_dir = self.root.home / ".codex"
        default_dir.mkdir()
        (default_dir / "config.toml").write_text(config_text, encoding="utf-8")
        self.assertEqual(self.root.run("check")[0], 1)

        custom_dir = self.root.home / "custom codex"
        custom_dir.mkdir()
        self.assertEqual(self.root.run("check", codex_home=custom_dir)[0], 0)
        (custom_dir / "config.toml").write_text(config_text, encoding="utf-8")
        code, out = self.root.run("check", codex_home=custom_dir)
        self.assertEqual(code, 1)
        self.assertIn("delete the [mcp_servers.betasrv] table from $CODEX_HOME/config.toml", out["problems"][0])

    def test_enable_prints_mcp_register_and_setup(self):
        code, out = self.root.run("enable", "beta", "--apply")
        self.assertEqual(code, 0, out)
        self.assertIn("claude mcp add --scope local betasrv --", out["mcp_register"]["beta"]["claude"])
        self.assertIn("[mcp_servers.betasrv]", out["mcp_register"]["beta"]["codex"])
        self.assertEqual(self.root.linked("beta"), [True, True])
        code, out = self.root.run("enable", "alpha")
        self.assertEqual(out["mode"], "dry-run")
        self.assertEqual(out["next_steps"], ["do the alpha setup"])
        self.assertEqual(self.root.linked("alpha"), [False, False], "dry-run writes nothing")

    def test_bad_knob_and_unknown_skill_exit_2(self):
        cases = [
            "schema_version: 1\nskills:\n  delta: on\n",
            "schema_version: 1\nskills:\n  alpha: maybe\n",
            "schema_version: 2\nskills: {}\n",
            "skills: [alpha]\n",
            "schema_version: 1\nskills:\n  alpha: on\nextra: 1\n",
            ": : :\n",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.root.local(text)
                self.assertEqual(self.root.run("check")[0], 2)
        (self.root.path / "skill-activation.local.yaml").unlink()
        self.assertEqual(self.root.run("enable", "base")[0], 2)
        self.assertEqual(self.root.run("campaign-open", "c1", "--skills", "alpha,delta")[0], 2)
        (self.root.path / "skill-activation.yaml").write_text("schema_version: 1\nskills:\n  ghost: off\n", encoding="utf-8")
        self.assertEqual(self.root.run("status")[0], 2)

    def test_campaign_open_records_only_newly_enabled(self):
        code, out = self.root.run("campaign-open", "c1", "--skills", "alpha,gamma", "--hitl", "h1")
        self.assertEqual(code, 0, out)
        record = self.root.record("c1")
        self.assertEqual((record["enabled"], record["pre_existing"], record["hitl"]), (["alpha"], ["gamma"], "h1"))
        self.assertEqual(self.root.linked("alpha"), [True, True])
        self.assertEqual(self.root.linked("beta"), [False, False])

    def test_campaign_open_none_turns_nothing_on(self):
        code, out = self.root.run("campaign-open", "c1", "--skills", "none")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.root.record("c1")["requested"], [])
        self.assertEqual(self.root.linked("alpha") + self.root.linked("beta"), [False] * 4)

    def test_campaign_open_refuses_while_another_is_open(self):
        self.root.run("campaign-open", "c1", "--skills", "alpha")
        code, out = self.root.run("campaign-open", "c2", "--skills", "beta")
        self.assertEqual(code, 1)
        self.assertEqual((out["refused"], out["campaign"]), ("another-campaign-open", "c1"))
        self.assertFalse((self.root.path / "campaigns/c2/activation.json").exists())
        self.assertEqual(self.root.linked("beta"), [False, False])
        self.assertEqual(self.root.run("campaign-open", "c1", "--skills", "beta")[1]["refused"], "already-recorded")

    def test_campaign_open_rolls_back_when_sync_fails(self):
        self.root.local("schema_version: 1\nskills:\n  gamma: on\n")
        before = (self.root.path / "skill-activation.local.yaml").read_bytes()
        (self.root.path / ADAPTERS[0] / "beta").write_text("someone else's file", encoding="utf-8")
        code, out = self.root.run("campaign-open", "c1", "--skills", "alpha,beta")
        self.assertEqual(code, 1)
        self.assertEqual(out["refused"], "sync-failed")
        self.assertEqual((self.root.path / "skill-activation.local.yaml").read_bytes(), before)
        self.assertFalse((self.root.path / "campaigns/c1/activation.json").exists())
        self.assertEqual(self.root.linked("alpha"), [False, False], "links created before the failure are undone")

    def test_campaign_close_turns_off_only_what_it_enabled_and_is_on(self):
        self.root.local("schema_version: 1\nskills:\n  gamma: on\n")
        self.root.run("campaign-open", "c1", "--skills", "alpha,beta,gamma")
        self.root.run("disable", "beta", "--apply")  # a person turns beta off mid-campaign
        code, out = self.root.run("campaign-close", "c1")
        self.assertEqual(code, 0, out)
        record = self.root.record("c1")
        self.assertEqual((record["state"], record["turned_off"], record["left_as_changed"]),
                         ("closed", ["alpha"], ["beta"]))
        self.assertEqual(self.root.linked("alpha"), [False, False])
        self.assertEqual(self.root.linked("gamma"), [True, True], "pre-existing stays on")
        self.assertEqual(self.root.run("campaign-close", "c1")[1]["refused"], "already-closed")
        self.assertEqual(self.root.run("campaign-open", "c2", "--skills", "beta")[0], 0, "a closed record does not block")

    def test_flow_form_shared_knob_is_refused(self):
        (self.root.path / "skill-activation.yaml").write_text(
            "schema_version: 1\nskills: {alpha: off, beta: off, gamma: on}\n", encoding="utf-8")
        code, out = self.root.run("check")
        self.assertEqual(code, 2)
        self.assertIn("block", out["error"])

    def test_already_recorded_points_at_abort_and_candidates_list_open_records(self):
        self.root.run("campaign-open", "c1", "--skills", "alpha")
        out = self.root.run("campaign-open", "c1", "--skills", "beta")[1]
        self.assertIn("campaign-abort c1", out["hint"])
        self.assertEqual(self.root.run("candidates")[1]["open_records"], ["c1"])

    def test_campaign_abort_removes_record_and_links(self):
        self.root.run("campaign-open", "c1", "--skills", "alpha")
        code, out = self.root.run("campaign-abort", "c1")
        self.assertEqual(code, 0, out)
        self.assertFalse((self.root.path / "campaigns/c1/activation.json").exists())
        self.assertEqual(self.root.linked("alpha"), [False, False])

    def test_retroactive_record_keeps_links_and_requires_on(self):
        self.root.run("sync", "--apply")
        self.assertEqual(self.root.run("campaign-record", "c1", "--skills", "alpha", "--retroactive")[1]["refused"],
                         "not-on")
        before = os.readlink(self.root.path / ".claude/skills/gamma")
        code, out = self.root.run("campaign-record", "c1", "--skills", "gamma", "--retroactive")
        self.assertEqual(code, 0, out)
        record = self.root.record("c1")
        self.assertEqual((record["enabled"], record["retroactive"]), (["gamma"], True))
        self.assertEqual(os.readlink(self.root.path / ".claude/skills/gamma"), before)
        self.assertFalse((self.root.path / "skill-activation.local.yaml").exists(), "knob untouched")

    def test_check_flags_open_record_without_active(self):
        self.root.run("campaign-open", "c1", "--skills", "alpha")
        self.root.active("c1")
        self.assertEqual(self.root.run("check")[0], 0)
        (self.root.path / "campaigns/ACTIVE").unlink()
        code, out = self.root.run("check")
        self.assertEqual(code, 1)
        self.assertTrue(any("campaign-close c1" in p for p in out["problems"]))


if __name__ == "__main__":
    unittest.main()
