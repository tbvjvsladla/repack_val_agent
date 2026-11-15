"""Offline contract and native DeepEval metric tests; never call a paid Judge."""

import asyncio
import os
from pathlib import Path
import sys
import tempfile
from typing import Any
import unittest
from unittest.mock import patch

os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] = "1"
os.environ["DEEPEVAL_DISABLE_DOTENV"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from deepeval.models import DeepEvalBaseLLM
from pydantic import BaseModel, ValidationError

from deepeval_pack import engine
from deepeval_pack.config import Configuration, ConfigurationError, load_config
from deepeval_pack.server import _failure


class FixtureJudge(DeepEvalBaseLLM):
    """A test-only schema responder, never installed as a runtime Judge."""

    def load_model(self) -> None:
        """Use no provider model."""

    def get_model_name(self) -> str:
        """Return the test fixture identity."""
        return "test-only-schema-fixture"

    def generate(self, prompt: str, schema: type[BaseModel] | None = None) -> Any:
        """Generate minimal consistent schema instances for two actual native metrics."""
        if schema is None:
            raise ValueError
        fields = schema.model_fields
        if "truths" in fields:
            return schema(truths=["Paris is the capital of France."])
        if "claims" in fields:
            return schema(claims=["Paris is the capital of France."])
        if "verdicts" in fields:
            return schema(
                verdicts=[{"verdict": "yes", "reason": "Supported by supplied text."}]
            )
        return schema(reason="All supplied claims are supported.")

    async def a_generate(
        self, prompt: str, schema: type[BaseModel] | None = None
    ) -> Any:
        """Match the asynchronous DeepEval Judge contract."""
        return self.generate(prompt, schema)


def fixture_config(configured: bool = False) -> Configuration:
    """Construct test-only settings, independent of active user config."""
    profile = {"provider": "openai", "model": "fixture-model"} if configured else {}
    return Configuration.model_validate(
        {
            "judge_profile": profile,
            "metrics": [
                {"type": "ContextualRecallMetric"},
                {"type": "FaithfulnessMetric"},
            ],
        }
    )


def fixture_case() -> dict[str, Any]:
    """Supply all fields consumed by both native metrics."""
    return {
        "id": "fixture",
        "input": "What is the capital of France?",
        "actual_output": "Paris is the capital of France.",
        "expected_output": "Paris is the capital of France.",
        "retrieval_context": ["Paris is the capital of France."],
    }


class PackTest(unittest.TestCase):
    """Exercise dynamic settings, validation, native bindings and partial failures."""

    def test_example_is_valid_without_judge(self):
        config = load_config(
            Path(__file__).resolve().parents[1] / "config.example.yaml"
        )
        status = engine.validate_config(config)
        self.assertFalse(status["judge_configured"])  # noqa: PT009
        self.assertEqual(len(status["metrics"]), 2)  # noqa: PT009

    def test_unknown_fields_provider_and_metric_are_rejected(self):
        for profile in ({"provider": "unsupported"}, {"api_key": "fixture"}):
            with self.subTest(profile=profile), self.assertRaises(ValidationError):  # noqa: PT027
                Configuration.model_validate(
                    {
                        "judge_profile": profile,
                        "metrics": [{"type": "FaithfulnessMetric"}],
                    }
                )
        config = fixture_config()
        config.metrics[0].type = "MCPUseMetric"
        with self.assertRaises(ConfigurationError):  # noqa: PT027
            engine.validate_config(config)

    def test_reserved_and_invalid_options_are_rejected(self):
        for params in (
            {"model": "hidden-default"},
            {"threshold": -1},
            {"threshold": True},
            {"include_reason": "yes"},
            {"unknown": 1},
            {"truths_extraction_limit": 0},
        ):
            config = fixture_config()
            config.metrics[1].params = params
            with self.subTest(params=params), self.assertRaises(ConfigurationError):  # noqa: PT027
                engine.validate_config(config)
        with self.assertRaises(ValidationError):  # noqa: PT027
            Configuration.model_validate(
                {
                    "judge_profile": {
                        "settings": {"generation_kwargs": {"api_key": "fixture"}}
                    },
                    "metrics": [{"type": "FaithfulnessMetric"}],
                }
            )

    def test_input_required_fields_and_empty_context(self):
        case = fixture_case()
        del case["expected_output"]
        errors = engine.validate_cases(fixture_config(), [case])
        self.assertEqual(  # noqa: PT009
            [item["metric"] for item in errors], ["ContextualRecallMetric"]
        )
        case["retrieval_context"] = []
        self.assertEqual(len(engine.validate_cases(fixture_config(), [case])), 2)  # noqa: PT009

    def test_unconfigured_evaluation_does_not_create_judge(self):
        with (
            patch.object(engine, "create_judge") as create,
            self.assertRaises(ConfigurationError),  # noqa: PT027
        ):
            asyncio.run(
                engine.evaluate(fixture_config(), [fixture_case()], Path("unused"))
            )
        create.assert_not_called()

    def test_invalid_cases_do_not_create_judge(self):
        with patch.object(engine, "create_judge") as create:
            result = asyncio.run(
                engine.evaluate(fixture_config(True), [{}], Path("unused"))
            )
        create.assert_not_called()
        self.assertEqual(result["status"], "invalid_input")  # noqa: PT009

    def test_four_provider_mappings_and_generation_knobs(self):
        for provider, (class_name, key_name) in engine.PROVIDERS.items():
            config = fixture_config(True)
            config.judge_profile.provider = provider
            config.judge_profile.settings.generation_kwargs = {"max_tokens": 99}
            with (
                self.subTest(provider=provider),
                patch.dict(os.environ, {key_name: "fixture"}, clear=True),
                patch.object(
                    engine.native_models,
                    class_name,
                ) as adapter,
            ):
                engine.create_judge(config.judge_profile, Path("unused"))
                self.assertEqual(adapter.call_args.kwargs["model"], "fixture-model")  # noqa: PT009
                self.assertEqual(  # noqa: PT009
                    adapter.call_args.kwargs["generation_kwargs"], {"max_tokens": 99}
                )
                self.assertEqual(adapter.call_args.kwargs["api_key"], "fixture")  # noqa: PT009

    def test_native_provider_adapters_construct_without_network(self):
        for provider, (class_name, key_name) in engine.PROVIDERS.items():
            config = fixture_config(True)
            config.judge_profile.provider = provider
            config.judge_profile.settings.cost_per_input_token = 0.0
            config.judge_profile.settings.cost_per_output_token = 0.0
            with self.subTest(provider=provider), patch.dict(os.environ, {key_name: "fixture"}, clear=True), patch(
                "socket.socket.connect", side_effect=AssertionError("Unexpected network request"),
            ):
                judge = engine.create_judge(config.judge_profile, Path("unused"))
                self.assertIsInstance(judge, getattr(engine.native_models, class_name))  # noqa: PT009 - stdlib unittest.

    def test_env_file_fallback_and_environment_precedence(self):
        config = fixture_config(True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("OPENAI_API_KEY=file-fixture\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {}, clear=True),
                patch.object(engine.native_models, "OpenAIModel") as adapter,
            ):
                engine.create_judge(config.judge_profile, path)
                self.assertEqual(adapter.call_args.kwargs["api_key"], "file-fixture")  # noqa: PT009
            with (
                patch.dict(os.environ, {"OPENAI_API_KEY": "env-fixture"}, clear=True),
                patch.object(
                    engine.native_models,
                    "OpenAIModel",
                ) as adapter,
            ):
                engine.create_judge(config.judge_profile, path)
                self.assertEqual(adapter.call_args.kwargs["api_key"], "env-fixture")  # noqa: PT009

    def test_real_native_metrics_with_test_only_judge(self):
        with patch.object(engine, "create_judge", return_value=FixtureJudge()):
            result = asyncio.run(
                engine.evaluate(fixture_config(True), [fixture_case()], Path("unused"))
            )
        self.assertEqual(result["status"], "ok", result)  # noqa: PT009
        self.assertEqual([item["score"] for item in result["results"]], [1, 1])  # noqa: PT009
        self.assertTrue(all(item["reason"] for item in result["results"]))  # noqa: PT009

    def test_partial_failure_preserves_other_metric_results(self):
        real_factory = engine.create_metric

        def fail_recall(spec, judge):
            if spec.type == "ContextualRecallMetric":
                raise RuntimeError
            return real_factory(spec, judge)

        with (
            patch.object(engine, "create_judge", return_value=FixtureJudge()),
            patch.object(
                engine,
                "create_metric",
                side_effect=fail_recall,
            ),
        ):
            result = asyncio.run(
                engine.evaluate(fixture_config(True), [fixture_case()], Path("unused"))
            )
        self.assertEqual(result["status"], "partial_error")  # noqa: PT009
        self.assertEqual(  # noqa: PT009
            [item["status"] for item in result["results"]], ["error", "ok"]
        )

    def test_config_knob_selects_other_metrics_and_geval(self):
        config = fixture_config()
        config.metrics = [
            config.metrics[0].model_copy(
                update={
                    "type": "AnswerRelevancyMetric",
                    "params": {"threshold": 0.8},
                }
            )
        ]
        self.assertEqual(  # noqa: PT009
            engine.validate_config(config)["metrics"][0]["required_fields"],
            ["input", "actual_output"],
        )
        config.metrics[0].type = "GEval"
        config.metrics[0].params = {
            "name": "test-rule",
            "criteria": "Answer is concise.",
            "evaluation_params": ["actual_output"],
        }
        self.assertEqual(  # noqa: PT009
            engine.validate_config(config)["metrics"][0]["required_fields"],
            ["actual_output"],
        )
        self.assertIsInstance(  # noqa: PT009
            engine.create_metric(config.metrics[0], FixtureJudge()),
            engine.native_metrics.GEval,
        )

    def test_validation_error_omits_input_values(self):
        try:
            Configuration.model_validate(
                {"judge_profile": {"api_key": "sentinel"}, "metrics": []}
            )
        except ValidationError as error:
            self.assertNotIn("sentinel", str(_failure(error)))  # noqa: PT009


if __name__ == "__main__":
    unittest.main()
