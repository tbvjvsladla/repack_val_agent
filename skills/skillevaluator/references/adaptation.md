# Source and adaptation

This folder is NVIDIA SkillEvaluator's bundled agent skill, installed as a project-local skill. It is not a copy of the SkillEvaluator CLI or its Python package. Copyright NVIDIA CORPORATION & AFFILIATES, Apache-2.0; the original [LICENSE](../LICENSE) is included unchanged and the upstream [NOTICE](NOTICE.md) is kept.

Source repository: https://github.com/NVIDIA/SkillEvaluator
Revision: `f469462369404fa9d18c9372feccd32bfb9dbbbb` (2026-11-15)
Source directory: `skills/skillevaluator/` (skill version 1.0.0)

## Changes (2026-11-16)

- `SKILL.md`: added the section "이 저장소에서의 사용" (role as an optional means, target paths, key location in `envs/.env`, report location under `temp/`, cost boundary). The metadata author line names the maintainers instead of a personal address. The rest of the procedure is unchanged.
- `SKILL.md` metadata gains `activation: opt-in`: the skill ships without discovery links and is enabled by the user (`scripts/skill_activation.py`).
  Later replaced: the marker was removed, and the project's activation knob (`skill-activation.yaml`) now lists the skill as optional and off.
- `scripts/run.py` looks for the CLI in this skill's `.venv` before PATH, and for DeepSeek pins Tier 3 to `--agents opencode --agent-model opencode=openai/<model>`.
- Added `config.example.yaml` (two knobs: `provider`, `judge_profile`) and `scripts/run.py`, an adapter that reads the shared envs/ profile and key, clears other provider variables, sets `SKILL_EVAL_LLM_PROVIDER`, the provider key, `SKILL_EVAL_LLM_MODEL`, `LLM_JUDGE_MODEL` (and the DeepSeek base URL through `openai-compatible`), adds `--agent-model` for Tier 3 when no agent is chosen, then runs the CLI. Profile generation settings are reported as not applied because the CLI has no input for them. Tests in `tests/` use a stand-in CLI.
- `references/`, `evals/`: unchanged.
- `references/NOTICE.md`: upstream `NOTICE`, renamed so the public export keeps it.

## Not included

The CLI, its dependencies, CI bundles, Harbor tasks and dashboards stay upstream. Install the CLI as described in `SKILL.md` (Prerequisites). No credentials, MCP registrations or global settings are added.
