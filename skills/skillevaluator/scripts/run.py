#!/usr/bin/env python3
"""Adapter: run the SkillEvaluator CLI with this skill's config.yaml and a shared envs/ profile.

config.yaml has two knobs, `provider` and `judge_profile` (a file name in the project's
envs/ folder). The profile supplies the model; the provider's key comes from the process
environment or envs/.env. Every LLM the CLI calls (evaluator, judge and, for Tier 3, the
default agent) is set from these. Key values are never printed.

    python3 -B scripts/run.py check
    python3 -B scripts/run.py exec -- tier1 skills/<name> --no-llm

Needs Python 3.9+ and PyYAML. The CLI is `skillevaluator` on PATH, SKILLEVALUATOR_BIN, or --cli.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

SKILL = Path(__file__).resolve().parents[1]
PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.ya?ml$")
# config provider -> SkillEvaluator provider, key variable, CLI variables for the key, base URL, Tier 3 default agent
PROVIDERS = {
    "anthropic": ("anthropic", "ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY", None, "claude-code"),
    "openai": ("openai", "OPENAI_API_KEY", "OPENAI_API_KEY", None, "codex"),
    "deepseek": ("openai-compatible", "DEEPSEEK_API_KEY", "SKILL_EVAL_LLM_API_KEY", "https://api.deepseek.com", "opencode"),
}
UNSUPPORTED = {"google": "SkillEvaluator has no Google provider", "gemini": "SkillEvaluator has no Google provider"}
# Cleared before every run so only the configured provider can be selected.
SCRUB = (
    "SKILL_EVAL_LLM_PROVIDER", "SKILL_EVAL_LLM_MODEL", "SKILL_EVAL_LLM_BASE_URL", "SKILL_EVAL_LLM_API_KEY",
    "LLM_JUDGE_MODEL", "SKILL_EVAL_JUDGE_MODEL", "NVIDIA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "DEEPSEEK_API_KEY", "SKILL_EVAL_LLM_CREDENTIAL_SOURCE",
)
PROFILE_KEYS = {"provider", "model", "settings"}


class AdapterError(ValueError):
    """config.yaml, the profile or the key does not satisfy the adapter contract."""


def find_project_root(skill_root: Path) -> Path:
    """<root>/skills/<name>, <root>/.agents/skills/<name>, <root>/.claude/skills/<name>; env override first."""
    override = os.environ.get("SKILLEVALUATOR_PROJECT_ROOT")
    if override:
        return Path(override).resolve()
    skills_dir = skill_root.parent
    if skills_dir.name == "skills":
        if skills_dir.parent.name in {".agents", ".claude"}:
            return skills_dir.parent.parent
        return skills_dir.parent
    return Path.cwd().resolve()


def read_yaml(path: Path, what: str) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise AdapterError(f"Cannot read {what}") from error
    if not isinstance(data, dict):
        raise AdapterError(f"{what} must be a mapping")
    return data


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines, no interpolation; a missing file is empty."""
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def resolve(config_path: Path, envs_dir: Path) -> dict:
    """Return the selection without key values."""
    config = read_yaml(config_path, "config.yaml; copy config.example.yaml")
    unknown = set(config) - {"provider", "judge_profile"}
    if unknown:
        raise AdapterError(f"Unknown config.yaml keys: {sorted(unknown)}")
    provider, name = config.get("provider"), config.get("judge_profile")
    result = {"provider": provider, "judge_profile": name, "model": None, "missing": [], "not_applied": []}
    if provider is None:
        result["missing"].append("provider")
    elif provider in UNSUPPORTED:
        raise AdapterError(f"provider {provider}: {UNSUPPORTED[provider]}")
    elif provider not in PROVIDERS:
        raise AdapterError(f"provider must be one of {sorted(PROVIDERS)} or null")
    if name is None:
        result["missing"].append("judge_profile")
        return result
    if not isinstance(name, str) or not PROFILE_NAME.match(name):
        raise AdapterError("judge_profile must be a file name in envs/, e.g. opus_profile.yaml")
    path = (envs_dir / name).resolve()
    if path.parent != envs_dir.resolve() or not path.is_file():
        raise AdapterError(f"Judge profile not found in envs/: {name}")
    profile = read_yaml(path, f"envs/{name}")
    if set(profile) - PROFILE_KEYS:
        raise AdapterError(f"Unknown profile keys in envs/{name}: {sorted(set(profile) - PROFILE_KEYS)}")
    model = profile.get("model")
    if not isinstance(model, str) or not model.strip():
        raise AdapterError(f"envs/{name} needs a model")
    if profile.get("provider") not in (None, provider):
        raise AdapterError(f"Profile {name} is for {profile['provider']}, config provider is {provider}")
    result["model"] = model
    settings = profile.get("settings") or {}
    if not isinstance(settings, dict):
        raise AdapterError(f"envs/{name} settings must be a mapping")
    # The CLI exposes no per-call generation options; say so instead of dropping them silently.
    for key, value in settings.items():
        if key == "generation_kwargs" and isinstance(value, dict):
            result["not_applied"] += [f"settings.generation_kwargs.{k}" for k in value]
        else:
            result["not_applied"].append(f"settings.{key}")
    return result


def build_env(selection: dict, env_file: Path, base: dict[str, str]) -> tuple[dict[str, str], dict]:
    env = {k: v for k, v in base.items() if k not in SCRUB}
    info = {"key_env": None, "key_found": False, "set": []}
    provider = selection["provider"]
    if provider is None or selection["model"] is None:
        return env, info
    cli_provider, key_name, key_target, base_url, _agent = PROVIDERS[provider]
    key = base.get(key_name) or read_env_file(env_file).get(key_name)
    info["key_env"] = key_name
    if not key:
        raise AdapterError(f"{key_name} is not set in the environment or envs/.env")
    info["key_found"] = True
    env.update({
        "SKILL_EVAL_LLM_PROVIDER": cli_provider, key_target: key,
        "SKILL_EVAL_LLM_MODEL": selection["model"], "LLM_JUDGE_MODEL": selection["model"],
    })
    if base_url:
        env["SKILL_EVAL_LLM_BASE_URL"] = base_url
    info["set"] = sorted({"SKILL_EVAL_LLM_PROVIDER", key_target, "SKILL_EVAL_LLM_MODEL", "LLM_JUDGE_MODEL",
                          *(["SKILL_EVAL_LLM_BASE_URL"] if base_url else [])})
    return env, info


def tiers_value(args: list[str]) -> set[str]:
    """Tiers named by `--tiers 1,3` or `--tiers=1,3`; validate's default (all tiers) counts as including 3."""
    for i, arg in enumerate(args):
        if arg == "--tiers" and i + 1 < len(args):
            return {x.strip() for x in args[i + 1].split(",")}
        if arg.startswith("--tiers="):
            return {x.strip() for x in arg.split("=", 1)[1].split(",")}
    return {"1", "2", "3"}


def cli_args(selection: dict, args: list[str]) -> list[str]:
    """For Tier 3 without an explicit agent choice, pin the provider's default agent to the profile model."""
    if not args or selection["model"] is None or selection["provider"] is None:
        return args
    tier3 = args[0] == "tier3" or (args[0] == "validate" and "3" in tiers_value(args))
    explicit = any(a in {"-a", "--agent", "--agents", "--agent-model"} or a.startswith(("--agent=", "--agent-model=")) for a in args)
    if tier3 and not explicit:
        return [*args, "--agent-model", f"{PROVIDERS[selection['provider']][4]}={selection['model']}"]
    return args


def main() -> int:
    root = find_project_root(SKILL)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["check", "exec"])
    parser.add_argument("--config", type=Path, default=SKILL / "config.yaml")
    parser.add_argument("--env-file", type=Path, default=root / "envs" / ".env")
    parser.add_argument("--cli", default=os.environ.get("SKILLEVALUATOR_BIN", "skillevaluator"))
    argv = sys.argv[1:]
    split = argv.index("--") if "--" in argv else len(argv)
    args = parser.parse_args(argv[:split])
    extra = argv[split + 1:]
    envs_dir = args.env_file.resolve().parent
    try:
        selection = resolve(args.config, envs_dir)
        env, info = build_env(selection, args.env_file, dict(os.environ))
    except AdapterError as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 1
    cli = shutil.which(args.cli)
    if args.command == "check":
        print(json.dumps({"status": "ok", **selection, "judge_configured": not selection["missing"], **info,
                          "cli": cli or f"not found: {args.cli}"}, ensure_ascii=False))
        return 0
    if not extra:
        parser.error("exec needs CLI arguments after --")
    if cli is None:
        print(json.dumps({"status": "error", "error": f"SkillEvaluator CLI not found: {args.cli}"}))
        return 1
    if selection["not_applied"]:
        print(f"[skillevaluator adapter] not applied by the CLI: {', '.join(selection['not_applied'])}", file=sys.stderr)
    return subprocess.run([cli, *cli_args(selection, extra)], env=env).returncode


if __name__ == "__main__":
    sys.exit(main())
