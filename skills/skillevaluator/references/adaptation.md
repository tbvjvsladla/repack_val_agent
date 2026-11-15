# Source and adaptation

This folder is NVIDIA SkillEvaluator's bundled agent skill, installed as a project-local skill. It is not a copy of the SkillEvaluator CLI or its Python package. Copyright NVIDIA CORPORATION & AFFILIATES, Apache-2.0; the original [LICENSE](../LICENSE) is included unchanged and the upstream [NOTICE](NOTICE.md) is kept.

Source repository: https://github.com/NVIDIA/SkillEvaluator
Revision: `f469462369404fa9d18c9372feccd32bfb9dbbbb` (2026-11-15)
Source directory: `skills/skillevaluator/` (skill version 1.0.0)

## Changes (2026-11-16)

- `SKILL.md`: added the section "이 저장소에서의 사용" (role as an optional means, target paths, key location in `envs/.env`, report location under `temp/`, cost boundary). The metadata author line names the maintainers instead of a personal address. The rest of the procedure is unchanged.
- `references/`, `evals/`: unchanged.
- `references/NOTICE.md`: upstream `NOTICE`, renamed so the public export keeps it.

## Not included

The CLI, its dependencies, CI bundles, Harbor tasks and dashboards stay upstream. Install the CLI as described in `SKILL.md` (Prerequisites). No credentials, MCP registrations or global settings are added.
