"""Load the public configuration contract without creating a Judge."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator
import yaml


class ConfigurationError(ValueError):
    """A configuration or input violates the pack contract."""


class JudgeSettings(BaseModel):
    """Options passed to the chosen native DeepEval model adapter."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    temperature: float | None = Field(default=None, ge=0)
    cost_per_input_token: float | None = Field(default=None, ge=0)
    cost_per_output_token: float | None = Field(default=None, ge=0)
    generation_kwargs: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("generation_kwargs")
    @classmethod
    def validate_generation_options(
        cls, value: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        """Keep credentials, routing, temperature and output schema owned by the adapter."""
        reserved = {
            "model",
            "api_key",
            "base_url",
            "messages",
            "prompt",
            "stream",
            "response_format",
            "response_schema",
            "response_json_schema",
            "temperature",
            "tools",
            "output_config",
        }
        if reserved.intersection(value):
            message = "Reserved generation option; see references/config.md"
            raise ValueError(message)
        return value


class JudgeProfile(BaseModel):
    """Inline Judge selection; empty selection is a valid installation state."""

    model_config = ConfigDict(extra="forbid", strict=True)
    provider: Literal["anthropic", "openai", "google", "gemini", "deepseek"] | None = (
        None
    )
    model: str | None = Field(default=None, min_length=1)
    settings: JudgeSettings = Field(default_factory=JudgeSettings)

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str | None) -> str | None:
        """Reject whitespace-only model names."""
        if value is not None and not value.strip():
            message = "Model name must not be blank"
            raise ValueError(message)
        return value


class MetricSpec(BaseModel):
    """A native metric class name and its constructor options."""

    model_config = ConfigDict(extra="forbid", strict=True)
    type: str
    params: dict[str, JsonValue] = Field(default_factory=dict)


class Configuration(BaseModel):
    """Complete evaluation settings, independent of caller experiment policy."""

    model_config = ConfigDict(extra="forbid", strict=True)
    judge_profile: JudgeProfile
    metrics: list[MetricSpec] = Field(min_length=1)


def load_config(path: Path) -> Configuration:
    """Read YAML without evaluating tags or resolving external model profiles."""
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        message = (
            "Cannot read config.yaml; copy config.example.yaml and check YAML syntax"
        )
        raise ConfigurationError(message) from error
    return Configuration.model_validate(payload)
