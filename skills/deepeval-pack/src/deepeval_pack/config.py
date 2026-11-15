"""Load the public configuration contract without creating a Judge."""

from pathlib import Path
import re
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


Provider = Literal["anthropic", "openai", "google", "gemini", "deepseek"]
PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.ya?ml$")


class ProfileFile(BaseModel):
    """A shared Judge profile in envs/: model and generation settings, no credentials."""

    model_config = ConfigDict(extra="forbid", strict=True)
    provider: Provider | None = None
    model: str = Field(min_length=1)
    settings: JudgeSettings = Field(default_factory=JudgeSettings)


class SkillConfig(BaseModel):
    """The on-disk config.yaml: two Judge knobs and the metric list."""

    model_config = ConfigDict(extra="forbid", strict=True)
    provider: Provider | None = None
    judge_profile: str | None = None
    metrics: list[MetricSpec] = Field(min_length=1)


def _read_yaml(path: Path, what: str) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ConfigurationError(f"Cannot read {what}; check the file and its YAML syntax") from error


def load_profile(envs_dir: Path, name: str) -> ProfileFile:
    """Read envs/<name>; only a plain file name inside envs/ is accepted."""
    if not PROFILE_NAME.match(name):
        raise ConfigurationError("judge_profile must be a file name in envs/, e.g. opus_profile.yaml")
    path = (envs_dir / name).resolve()
    if path.parent != envs_dir.resolve() or not path.is_file():
        raise ConfigurationError(f"Judge profile not found in envs/: {name}")
    return ProfileFile.model_validate(_read_yaml(path, f"envs/{name}"))


def load_config(path: Path, envs_dir: Path) -> Configuration:
    """Read config.yaml and adapt its provider + envs profile into the Judge contract."""
    payload = _read_yaml(path, "config.yaml; copy config.example.yaml")
    if isinstance(payload, dict) and isinstance(payload.get("judge_profile"), dict):
        raise ConfigurationError(
            "Inline judge_profile is no longer read; move model/settings to envs/<name>_profile.yaml "
            "and set provider and judge_profile in config.yaml"
        )
    skill = SkillConfig.model_validate(payload)
    profile: dict[str, object] = {"provider": skill.provider}
    if skill.judge_profile is not None:
        loaded = load_profile(envs_dir, skill.judge_profile)
        aliases = {"gemini": "google"}
        if (
            loaded.provider is not None
            and skill.provider is not None
            and aliases.get(loaded.provider, loaded.provider) != aliases.get(skill.provider, skill.provider)
        ):
            raise ConfigurationError(
                f"Profile {skill.judge_profile} is for {loaded.provider}, config provider is {skill.provider}"
            )
        profile.update(model=loaded.model, settings=loaded.settings)
    return Configuration(judge_profile=JudgeProfile.model_validate(profile), metrics=skill.metrics)
