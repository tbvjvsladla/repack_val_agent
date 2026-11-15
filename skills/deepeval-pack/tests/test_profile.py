"""config.yaml provider + envs/ profile adapter; no SDK call."""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pydantic import ValidationError

from deepeval_pack.config import ConfigurationError, load_config

METRICS = "metrics:\n  - type: FaithfulnessMetric\n"
PROFILE = """model: deepseek-flash
settings:
  temperature: 0.0
  generation_kwargs:
    reasoning_effort: low
    max_tokens: 32768
    extra_body:
      thinking:
        type: enabled
"""


class ProfileAdapter(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="dp-prof-"))
        self.envs = self.root / "envs"
        self.envs.mkdir()
        (self.envs / "deepseek_flash_profile.yaml").write_text(PROFILE, encoding="utf-8")

    def config(self, body: str) -> Path:
        path = self.root / "config.yaml"
        path.write_text(body + METRICS, encoding="utf-8")
        return path

    def test_profile_becomes_judge(self) -> None:
        config = load_config(
            self.config("provider: deepseek\njudge_profile: deepseek_flash_profile.yaml\n"), self.envs
        )
        judge = config.judge_profile
        self.assertEqual((judge.provider, judge.model), ("deepseek", "deepseek-flash"))
        self.assertEqual(judge.settings.temperature, 0.0)
        self.assertEqual(judge.settings.generation_kwargs["extra_body"], {"thinking": {"type": "enabled"}})

    def test_inactive_state_is_valid(self) -> None:
        config = load_config(self.config("provider: null\njudge_profile: null\n"), self.envs)
        self.assertIsNone(config.judge_profile.provider)
        self.assertIsNone(config.judge_profile.model)

    def test_profile_must_be_a_plain_file_in_envs(self) -> None:
        (self.root / "outside.yaml").write_text(PROFILE, encoding="utf-8")
        for name in ("../outside.yaml", "/etc/passwd", "missing.yaml", ".hidden.yaml", "profile.txt"):
            with self.subTest(name=name), self.assertRaises(ConfigurationError):
                load_config(self.config(f"provider: deepseek\njudge_profile: '{name}'\n"), self.envs)

    def test_profile_provider_must_match(self) -> None:
        (self.envs / "opus_profile.yaml").write_text("provider: anthropic\nmodel: claude-opus-5-5\n", encoding="utf-8")
        with self.assertRaises(ConfigurationError):
            load_config(self.config("provider: openai\njudge_profile: opus_profile.yaml\n"), self.envs)
        ok = load_config(self.config("provider: anthropic\njudge_profile: opus_profile.yaml\n"), self.envs)
        self.assertEqual(ok.judge_profile.model, "claude-opus-5-5")

    def test_inline_profile_and_credentials_are_rejected(self) -> None:
        with self.assertRaises(ConfigurationError):
            load_config(self.config("judge_profile:\n  provider: deepseek\n  model: x\n"), self.envs)
        (self.envs / "bad_profile.yaml").write_text("model: x\napi_key: sentinel\n", encoding="utf-8")
        with self.assertRaises(ValidationError):
            load_config(self.config("provider: deepseek\njudge_profile: bad_profile.yaml\n"), self.envs)


if __name__ == "__main__":
    unittest.main()
