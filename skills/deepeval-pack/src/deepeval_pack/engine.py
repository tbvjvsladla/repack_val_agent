"""Bind native DeepEval models and metrics without choosing a default Judge."""

from collections.abc import Callable
import inspect
import math
import os
from pathlib import Path
from typing import Any, NoReturn

from deepeval import metrics as native_metrics
from deepeval import models as native_models
from deepeval.metrics.base_metric import BaseMetric
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase, LLMTestCaseParams
from dotenv import dotenv_values

from deepeval_pack.config import (
    Configuration,
    ConfigurationError,
    JudgeProfile,
    MetricSpec,
)

PROVIDERS = {
    "anthropic": ("AnthropicModel", "ANTHROPIC_API_KEY"),
    "openai": ("OpenAIModel", "OPENAI_API_KEY"),
    "google": ("GeminiModel", "GOOGLE_API_KEY"),
    "gemini": ("GeminiModel", "GOOGLE_API_KEY"),
    "deepseek": ("DeepSeekModel", "DEEPSEEK_API_KEY"),
}
METRICS = {
    "ContextualRecallMetric": (
        "input",
        "actual_output",
        "expected_output",
        "retrieval_context",
    ),
    "FaithfulnessMetric": ("input", "actual_output", "retrieval_context"),
    "AnswerRelevancyMetric": ("input", "actual_output"),
    "ContextualPrecisionMetric": (
        "input",
        "actual_output",
        "expected_output",
        "retrieval_context",
    ),
    "ContextualRelevancyMetric": ("input", "actual_output", "retrieval_context"),
    "GEval": (),
}
CASE_FIELDS = {
    "input",
    "actual_output",
    "expected_output",
    "retrieval_context",
    "context",
}
_RESERVED_PARAMS = {
    "model",
    "system_one_model",
    "eval_mode",
    "evaluation_template",
    "async_mode",
    "verbose_mode",
    "flaky",
    "rubric",
}


def _invalid(message: str) -> NoReturn:
    raise ConfigurationError(message)


def metric_class(spec: MetricSpec) -> Callable[..., BaseMetric]:
    """Resolve only the pack's supported native text metrics and validate options."""
    if spec.type not in METRICS:
        _invalid(f"Unsupported metric: {spec.type}; use the status catalog")
    metric_type = getattr(native_metrics, spec.type)
    parameters = inspect.signature(metric_type).parameters
    if _RESERVED_PARAMS.intersection(spec.params) or set(spec.params).difference(
        parameters
    ):
        _invalid(f"Unsupported or reserved parameter for {spec.type}")
    for name in ("include_reason", "strict_mode", "penalize_ambiguous_claims"):
        if name in spec.params and not isinstance(spec.params[name], bool):
            _invalid(f"{spec.type}.{name} must be a boolean")
    threshold = spec.params.get("threshold", 0.5)
    if isinstance(threshold, bool) or not isinstance(threshold, (float, int)):
        _invalid("Metric threshold must be a number in [0, 1]")
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        _invalid("Metric threshold must be a number in [0, 1]")
    limit = spec.params.get("truths_extraction_limit")
    if limit is not None and (type(limit) is not int or limit < 1):
        _invalid("truths_extraction_limit must be a positive integer")
    if spec.type == "GEval":
        _validate_geval(spec)
    return metric_type


def _validate_geval(spec: MetricSpec) -> None:
    name = spec.params.get("name")
    criteria = spec.params.get("criteria")
    steps = spec.params.get("evaluation_steps")
    if not isinstance(name, str) or not name.strip():
        _invalid("GEval requires a nonempty name")
    if not (isinstance(criteria, str) and criteria.strip()) and not (
        isinstance(steps, list)
        and steps
        and all(isinstance(step, str) and step.strip() for step in steps)
    ):
        _invalid("GEval requires criteria or nonempty evaluation_steps")
    _geval_fields(spec)


def _geval_fields(spec: MetricSpec) -> tuple[str, ...]:
    fields = spec.params.get("evaluation_params")
    if (
        not isinstance(fields, list)
        or not fields
        or any(not isinstance(field, str) or field not in CASE_FIELDS for field in fields)
    ):
        _invalid("GEval evaluation_params must select supported case fields")
    return tuple(str(field) for field in fields)


def validate_config(config: Configuration) -> dict[str, Any]:
    """Check structural settings without constructing a model or making API calls."""
    selected = []
    for spec in config.metrics:
        metric_class(spec)
        required = METRICS[spec.type]
        if spec.type == "GEval":
            required = _geval_fields(spec)
        selected.append({"type": spec.type, "required_fields": list(required)})
    missing = []
    if config.judge_profile.provider is None:
        missing.append("judge_profile.provider")
    if config.judge_profile.model is None:
        missing.append("judge_profile.model")
    provider = config.judge_profile.provider
    return {
        "config_valid": True,
        "judge_configured": not missing,
        "missing": missing,
        "provider": provider,
        "model": config.judge_profile.model,
        "api_key_env": PROVIDERS[provider][1] if provider else None,
        "metrics": selected,
        "validation_scope": "Local structure only; model/API option compatibility requires a real evaluation",
    }


def validate_cases(
    config: Configuration, cases: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return per-case, per-metric input errors before any Judge API operation."""
    status = validate_config(config)
    if not cases:
        _invalid("At least one evaluation case is required")
    errors = []
    for index, case in enumerate(cases):
        unknown = set(case).difference(CASE_FIELDS | {"id"})
        for selected in status["metrics"]:
            missing = []
            for field in selected["required_fields"]:
                value = case.get(field)
                if field in {"retrieval_context", "context"}:
                    valid = (
                        isinstance(value, list)
                        and bool(value)
                        and all(
                            isinstance(item, str) and bool(item.strip())
                            for item in value
                        )
                    )
                else:
                    valid = isinstance(value, str) and bool(value.strip())
                if not valid:
                    missing.append(field)
            if unknown or missing:
                errors.append(
                    {
                        "case_index": index,
                        "metric": selected["type"],
                        "missing_or_invalid": missing,
                        "unknown_fields": sorted(unknown),
                    }
                )
    return errors


def create_judge(profile: JudgeProfile, env_file: Path) -> DeepEvalBaseLLM:
    """Create the explicitly selected native adapter using environment-only credentials."""
    if profile.provider is None or profile.model is None:
        _invalid("Set judge_profile.provider and judge_profile.model before evaluation")
    class_name, key_name = PROVIDERS[profile.provider]
    key = os.environ.get(key_name)
    if not key:
        key = dotenv_values(env_file, interpolate=False).get(key_name)
    if not key or not key.strip():
        _invalid(f"Required API key environment variable is missing: {key_name}")
    adapter = getattr(native_models, class_name)
    return adapter(
        model=profile.model,
        api_key=key,
        **profile.settings.model_dump(exclude_none=True),
    )


def create_metric(spec: MetricSpec, judge: DeepEvalBaseLLM) -> BaseMetric:
    """Construct a fresh metric for one case with the explicitly supplied Judge."""
    metric_type = metric_class(spec)
    params: dict[str, Any] = dict(spec.params)
    if spec.type == "GEval":
        params["evaluation_params"] = [
            LLMTestCaseParams(field) for field in _geval_fields(spec)
        ]
    if "eval_mode" in inspect.signature(metric_type).parameters:
        params["eval_mode"] = "llm"
    return metric_type(model=judge, async_mode=True, verbose_mode=False, **params)


async def evaluate(
    config: Configuration,
    cases: list[dict[str, Any]],
    env_file: Path,
) -> dict[str, Any]:
    """Run selected metrics and return results, retaining individual metric failures."""
    validation = validate_config(config)
    if validation["missing"]:
        _invalid("Judge is not configured: " + ", ".join(validation["missing"]))
    input_errors = validate_cases(config, cases)
    if input_errors:
        return {"status": "invalid_input", "errors": input_errors, "results": []}
    judge = create_judge(config.judge_profile, env_file)
    results = []
    for index, case in enumerate(cases):
        for metric_index, spec in enumerate(config.metrics):
            result = {
                "case_index": index,
                "case_id": case.get("id"),
                "metric": spec.type,
                "metric_index": metric_index,
            }
            try:
                metric = create_metric(spec, judge)
                fields = {key: value for key, value in case.items() if key != "id"}
                await metric.a_measure(LLMTestCase(**fields), _show_indicator=False)
                if (
                    metric.error is not None
                    or metric.score is None
                    or not math.isfinite(metric.score)
                ):
                    _invalid("Metric did not produce a finite score")
                result.update(
                    status="ok",
                    score=metric.score,
                    reason=metric.reason,
                    threshold=metric.threshold,
                    metric_success=metric.is_successful(),
                )
            except Exception as error:  # noqa: BLE001 - Preserve per-metric failures across SDK boundaries.
                result.update(
                    status="error",
                    error_type=type(error).__name__,
                    error="Metric failed; check model options, response schema and case data",
                )
            results.append(result)
    failed = any(result["status"] == "error" for result in results)
    return {"status": "partial_error" if failed else "ok", "results": results}
