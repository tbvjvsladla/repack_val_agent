"""Adapter contract with a stand-in CLI; never calls a provider."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RUN = Path(__file__).resolve().parents[1] / "scripts" / "run.py"
FAKE_CLI = """#!/usr/bin/env python3
import json, os, sys
names = sorted(k for k in os.environ if k.startswith(("SKILL_EVAL", "LLM_JUDGE")) or k.endswith("_API_KEY"))
print(json.dumps({"argv": sys.argv[1:], "names": names,
                  "provider": os.environ.get("SKILL_EVAL_LLM_PROVIDER"),
                  "model": os.environ.get("SKILL_EVAL_LLM_MODEL"),
                  "judge": os.environ.get("LLM_JUDGE_MODEL"),
                  "base_url": os.environ.get("SKILL_EVAL_LLM_BASE_URL"),
                  "key_is_fixture": os.environ.get("ANTHROPIC_API_KEY") == "fixture-key"
                                    or os.environ.get("SKILL_EVAL_LLM_API_KEY") == "fixture-key"}))
"""


class Adapter(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="se-adapter-"))
        self.envs = self.root / "envs"
        self.envs.mkdir()
        (self.envs / "opus_profile.yaml").write_text(
            "provider: anthropic\nmodel: claude-opus-5-5\nsettings:\n  temperature: 0.0\n"
            "  generation_kwargs:\n    max_tokens: 4096\n", encoding="utf-8")
        (self.envs / "deepseek_flash_profile.yaml").write_text("model: deepseek-flash\n", encoding="utf-8")
        self.cli = self.root / "fake-cli"
        self.cli.write_text(FAKE_CLI, encoding="utf-8")
        self.cli.chmod(0o755)
        self.config = self.root / "config.yaml"

    def run_adapter(self, config: str, *args: str, env_file: str = "ANTHROPIC_API_KEY=fixture-key\n",
                    extra_env: dict | None = None) -> tuple[int, str]:
        self.config.write_text(config, encoding="utf-8")
        (self.envs / ".env").write_text(env_file, encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY") and not k.startswith("SKILL_EVAL")}
        env.update({"NVIDIA_API_KEY": "stale", "SKILL_EVAL_LLM_PROVIDER": "nv_build", **(extra_env or {})})
        done = subprocess.run([sys.executable, "-B", str(RUN), *args[:1], "--config", str(self.config),
                               "--env-file", str(self.envs / ".env"), "--cli", str(self.cli), *args[1:]],
                              capture_output=True, text=True, env=env, timeout=60)
        return done.returncode, done.stdout

    def test_profile_drives_every_llm_and_hides_the_key(self) -> None:
        code, out = self.run_adapter("provider: anthropic\njudge_profile: opus_profile.yaml\n",
                                     "exec", "--", "tier3", "skills/x")
        self.assertEqual(code, 0, out)
        seen = json.loads(out)
        self.assertEqual((seen["provider"], seen["model"], seen["judge"]), ("anthropic", "claude-opus-5-5", "claude-opus-5-5"))
        self.assertTrue(seen["key_is_fixture"])
        self.assertNotIn("NVIDIA_API_KEY", seen["names"])
        self.assertEqual(seen["argv"][-2:], ["--agent-model", "claude-code=claude-opus-5-5"])
        code, out = self.run_adapter("provider: anthropic\njudge_profile: opus_profile.yaml\n", "check")
        report = json.loads(out)
        self.assertNotIn("fixture-key", out)
        self.assertEqual(report["not_applied"], ["settings.temperature", "settings.generation_kwargs.max_tokens"])

    def test_explicit_agent_and_non_tier3_are_left_alone(self) -> None:
        cfg = "provider: anthropic\njudge_profile: opus_profile.yaml\n"
        _, out = self.run_adapter(cfg, "exec", "--", "tier3", "evaluate", "x", "-a", "opencode")
        self.assertNotIn("--agent-model", json.loads(out)["argv"])
        _, out = self.run_adapter(cfg, "exec", "--", "validate", "x", "--tiers", "1,2")
        self.assertNotIn("--agent-model", json.loads(out)["argv"])
        _, out = self.run_adapter(cfg, "exec", "--", "validate", "x", "--tiers", "1,3")
        self.assertIn("--agent-model", json.loads(out)["argv"])

    def test_deepseek_goes_through_the_compatible_gateway(self) -> None:
        code, out = self.run_adapter("provider: deepseek\njudge_profile: deepseek_flash_profile.yaml\n",
                                     "exec", "--", "tier1", "x", env_file="DEEPSEEK_API_KEY=fixture-key\n")
        seen = json.loads(out)
        self.assertEqual((code, seen["provider"], seen["base_url"]), (0, "openai-compatible", "https://api.deepseek.com"))
        self.assertTrue(seen["key_is_fixture"])
        for command in (("tier3", "evaluate", "x"), ("tier3", "x"), ("doctor", "--verify-models")):
            code, out = self.run_adapter("provider: deepseek\njudge_profile: deepseek_flash_profile.yaml\n",
                                         "exec", "--", *command, env_file="DEEPSEEK_API_KEY=fixture-key\n")
            self.assertEqual(json.loads(out)["argv"][-4:],
                             ["--agents", "opencode", "--agent-model", "opencode=openai/deepseek-flash"], command)
        for command in (("doctor", "--tier", "1"), ("tier3", "validate", "x", "--strict"), ("tier3", "view", "x")):
            code, out = self.run_adapter("provider: deepseek\njudge_profile: deepseek_flash_profile.yaml\n",
                                         "exec", "--", *command, env_file="DEEPSEEK_API_KEY=fixture-key\n")
            self.assertEqual(json.loads(out)["argv"], list(command), "commands without agent options stay as given")

    def test_process_environment_wins_over_env_file(self) -> None:
        _, out = self.run_adapter("provider: anthropic\njudge_profile: opus_profile.yaml\n", "exec", "--", "tier1", "x",
                                  env_file="ANTHROPIC_API_KEY=other\n", extra_env={"ANTHROPIC_API_KEY": "fixture-key"})
        self.assertTrue(json.loads(out)["key_is_fixture"])

    def test_inactive_config_scrubs_providers_for_keyless_runs(self) -> None:
        code, out = self.run_adapter("provider: null\njudge_profile: null\n", "exec", "--", "quality-check", "x")
        seen = json.loads(out)
        self.assertEqual((code, seen["provider"], seen["names"]), (0, None, []))

    def test_cli_prefers_the_skill_venv_over_path(self) -> None:
        sys.path.insert(0, str(RUN.parent))
        import run  # noqa: E402 - the adapter module, imported only for this lookup
        skill = self.root / "skill"
        self.assertEqual(run.default_cli(skill, {}), "skillevaluator")
        local = skill / ".venv" / "bin" / "skillevaluator"
        local.parent.mkdir(parents=True)
        local.write_text("#!/bin/sh\n", encoding="utf-8")
        local.chmod(0o755)
        self.assertEqual(run.default_cli(skill, {}), str(local))
        self.assertEqual(run.default_cli(skill, {"SKILLEVALUATOR_BIN": "/opt/se"}), "/opt/se")

    def test_rejections(self) -> None:
        cases = {
            "provider: google\njudge_profile: opus_profile.yaml\n": "Google",
            "provider: openai\njudge_profile: opus_profile.yaml\n": "is for anthropic",
            "provider: anthropic\njudge_profile: ../x.yaml\n": "file name",
            "provider: anthropic\njudge_profile: missing.yaml\n": "not found",
            "provider: anthropic\njudge_profile: opus_profile.yaml\nmodel: x\n": "Unknown config",
        }
        for config, needle in cases.items():
            with self.subTest(config=config):
                code, out = self.run_adapter(config, "check")
                self.assertEqual(code, 1, out)
                self.assertIn(needle, json.loads(out)["error"])
        code, out = self.run_adapter("provider: anthropic\njudge_profile: opus_profile.yaml\n", "check", env_file="")
        self.assertEqual(code, 1)
        self.assertIn("ANTHROPIC_API_KEY is not set", out)


if __name__ == "__main__":
    unittest.main()
