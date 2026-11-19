---
name: skillevaluator
description: Run and interpret SkillEvaluator for agent skills. Use for any skill benchmark or eval - validating or scoring a SKILL.md, writing evals.json test cases or evals config, explaining benchmark reports, verdicts and exit codes, or gating skills in CI.
license: Apache-2.0
compatibility: Requires Python 3.12-3.14 and the skillevaluator CLI; live evaluation also needs Docker (or another backend) and an LLM provider key.
metadata:
  author: NVIDIA SkillEvaluator maintainers (see references/adaptation.md)
  version: "1.0.0"
  domain: [evaluation, ai-ml]
  tags: [skills, evaluation, benchmarking, ci, security]
---

# SkillEvaluator

Run the SkillEvaluator CLI on agent skills and report what the evidence shows:
Tier 1 validation (static, security, quality), Tier 2 deduplication, and Tier 3
live agent evaluation that compares runs with and without the skill.

## 이 저장소에서의 사용

이 스킬은 **선택 가능한 수단**이다. 사용자가 스킬의 품질·안전·효과 측정을 과업에 넣었을 때만 쓴다. 반환하는 것은 SkillEvaluator가 낸 점수·판정·발견 사항·보고서 경로와, 실행하지 않아 증명되지 않은 범위다. 이 결과는 해당 스킬 평가의 범위이며 전체 과업의 수용이 아니다.

- 대상: 이 저장소의 정본 스킬 폴더 `skills/<name>` 또는 사용자가 지정한 경로. 어댑터 링크(`.agents/skills`, `.claude/skills`)를 대상으로 따로 세지 않는다.
- CLI와 의존성은 번들하지 않는다. 설치되어 있지 않으면 설치 방법을 보고하고, 설치·키 추가는 사용자가 정한다.
- **꺼진 채 배포:** 켜고 끄는 것은 프로젝트 루트의 활성화 knob(`skill-activation.yaml`, 로컬 `skill-activation.local.yaml`)이 정한다. 쓰기로 정하면 프로젝트 루트에서 `python3 scripts/skill_activation.py enable skillevaluator --apply`로 켠다(로컬 knob을 고치고 발견 링크 2개를 맞춘다). CLI는 `uv venv --python 3.13 <skill-dir>/.venv && uv pip install --python <skill-dir>/.venv/bin/python "skillevaluator[all]"`로 이 폴더의 `.venv`에 설치하며, 어댑터가 PATH보다 먼저 이 CLI를 찾는다. `config.yaml`은 `config.example.yaml`을 복사해 만든다.
- **LLM 설정은 두 knob뿐이다.** 이 폴더의 `config.yaml`에 `provider`(anthropic / openai / deepseek)와 `judge_profile`(프로젝트 `envs/`의 프로필 파일 이름, 예: `opus_profile.yaml`)을 적는다. 키는 그 provider의 키(예: `ANTHROPIC_API_KEY`)를 프로세스 환경 또는 `envs/.env`에서 읽는다.
- **CLI는 항상 어댑터로 실행한다.** 아래 본문의 `skillevaluator <args>`는 이 저장소에서 `python3 -B <skill-dir>/scripts/run.py exec -- <args>`로 읽는다. 본문의 `export SKILL_EVAL_LLM_PROVIDER=...`나 `NVIDIA_API_KEY` 같은 환경변수를 직접 설정하지 않는다. 어댑터가 실행 전에 그런 변수를 지우고, 설정한 provider·키와 프로필의 model로 평가 모델(`SKILL_EVAL_LLM_MODEL`)과 Judge 모델(`LLM_JUDGE_MODEL`)을 채운다. Tier 3(`tier3`, `validate`의 Tier 3, `doctor`의 Tier 3 점검)에서 에이전트를 따로 고르지 않으면 provider의 기본 에이전트(claude-code / codex / opencode)에 같은 model을 지정한다. deepseek는 CLI의 OpenAI 호환 provider로 연결하고, 그 경로의 opencode는 `openai/<model>`을 받으므로 `--agents opencode --agent-model opencode=openai/<model>`을 붙인다. google은 CLI가 지원하지 않아 거부한다.
- **적용되지 않는 프로필 항목:** CLI는 호출별 생성 옵션을 외부에서 받지 않는다. 프로필의 `settings`(temperature, generation_kwargs)는 적용되지 않으며, 어댑터가 `check`의 `not_applied`와 실행 시 stderr로 알린다. 적용됐다고 보고하지 않는다. Tier 2의 임베딩 provider도 프로필로 정하지 않는다(anthropic은 임베딩이 없다).
- 먼저 `python3 -B <skill-dir>/scripts/run.py check`로 provider·프로필·model·키 존재(값은 출력하지 않음)·CLI 위치를 확인한다. `provider`와 `judge_profile`이 null이면 provider 변수를 모두 지운 채 실행하므로 키가 필요 없는 명령만 동작한다.
- 보고서는 `--output`으로 `temp/` 아래 새 폴더에 쓴다. Tier 3가 대상 스킬 안에 만드는 `evals/results/`는 로컬 결과물이며 커밋하지 않는다.
- Tier 3는 모델 토큰과 샌드박스 시간을 쓴다. 비용이 드는 실행(`--llm`, Tier 2·3)은 과업 계획에 범위를 적은 뒤 한다. `--max-cases 1` 같은 축소 실행은 방향 확인일 뿐 공개 근거가 아니다.

상류 출처·변경 사항: [적용 기록](references/adaptation.md).

## Purpose

Give an agent one reliable procedure for evaluating skills with
SkillEvaluator: pick the right tier, check readiness, run it, and report
scores, findings and exit codes truthfully.

## When To Use

Use this skill when the user wants to:

- check whether a skill is well-formed, safe, and good enough to publish
- get a quality score, rubric score, security or PII scan for a skill
- find repeated guidance inside a skill or overlap across a skill collection
- measure whether a skill actually helps an agent (Skill Lift, pass@k)
- write or fix `<skill-dir>/evals/evals.json`, `<skill-dir>/evals/config.yml`, custom graders, or Harbor tasks
- read a SkillEvaluator report, verdict, or exit code
- add SkillEvaluator checks to GitHub Actions or GitLab CI

Do not use it to write the target skill's domain content, to review ordinary
application code, or to evaluate an LLM without a skill. For converting an
existing benchmark into a custom grader, read
[references/custom-graders.md](references/custom-graders.md).

## Prerequisites

- A target directory containing `SKILL.md` (or a folder of skills, `.mdc`
  rules, or a plugin manifest).
- The CLI: `pip install --upgrade "skillevaluator[all]"`, or from a checkout
  `uv run --python 3.13 --extra all skillevaluator ...`. Check with
  `skillevaluator --version`. Extras: `llm` (rubric), `security` (scanners),
  `tier2`, `tier3`, `all`, `dashboard`.
- No key for keyless commands (`quality-check`, `security-scan`, `pii-scan`,
  `lint-scripts`, `tier1 PATH --no-llm`, `validate PATH --tiers 1`).
- A provider for LLM stages, Tier 2 and Tier 3. Simplest:
  `export SKILL_EVAL_LLM_PROVIDER=nv_build` and `export NVIDIA_API_KEY=...`.
  Other providers: [references/providers.md](references/providers.md).
- For Tier 3: an agent (`opencode`, `codex`, or `claude-code`) and a sandbox.
  Harbor is installed with the `tier3` extra; you do not run it yourself.
  The default `--env-mode docker` needs a Docker daemon. Without Docker, use
  `--env-mode local` (experimental): it runs the agent CLI already installed
  on the host under Bubblewrap (Linux) or Seatbelt (macOS); trusted skills only.

Never print, echo, or commit API keys; keep them in the host environment.

## Choose The Command

| User goal | Command | Needs |
| --- | --- | --- |
| Fast keyless quality score | `skillevaluator quality-check <skill-dir>` | nothing |
| Full static gate for one skill | `skillevaluator tier1 <skill-dir> --no-llm` | scanners (auto-installed) |
| Static gate plus LLM rubric and security review | `skillevaluator tier1 <skill-dir>` | provider |
| LLM rubric only | `skillevaluator rubric-eval <skill-dir>` | provider, `llm` extra |
| Security / PII scan only | `skillevaluator security-scan <skill-dir>`, `skillevaluator pii-scan <skill-dir>` | nothing |
| Repeated content inside one skill | `skillevaluator tier2 <skill-dir>` | provider with embeddings |
| Overlap across a collection | `skillevaluator similarity-check <skills-dir>` | embeddings |
| Does the skill help an agent? | `skillevaluator tier3 <skill-dir>` | provider, agent, Docker |
| Same, without Docker | `skillevaluator tier3 evaluate <skill-dir> -a opencode --env-mode local` | provider, agent CLI on host |
| Everything, with one exit code | `skillevaluator validate <skill-dir>` | all of the above |
| CI-safe keyless gate | `skillevaluator validate <skills-dir> --profile default --tiers 1 -r json,markdown,sarif -o reports` | scanners |
| Set up repo CI | `skillevaluator ci init` | GitHub checkout |

Full flag tables: [Tier 1](references/commands-tier1.md),
[validate](references/commands-validate.md),
[Tier 2, Tier 3 and setup](references/commands-tier2-tier3.md).

## Workflow

1. **Locate the target.** Confirm the path has `SKILL.md` and that its
   frontmatter `name` matches the directory name. For a folder of skills, the
   commands evaluate each skill inside it.
2. **Check readiness before paid runs.** Run `skillevaluator health-check --tier 1`
   for scanners, `skillevaluator doctor --tier 2` for providers, and
   `skillevaluator doctor --env-mode docker` (add `--verify-models`) for Tier 3.
   Fix every failing row first.
3. **Start keyless.** Run `skillevaluator tier1 <skill-dir> --no-llm`. Fix
   CRITICAL and HIGH findings before anything else; `lint` findings are advisory.
4. **Add LLM checks** when a provider is set: `skillevaluator tier1 <skill-dir>`
   (rubric, LLM security, verification) and `skillevaluator tier2 <skill-dir>`.
5. **Prepare Tier 3 cases.** If `<skill-dir>/evals/evals.json` is missing, first
   capture task requests and observable outcomes in `<skill-dir>/evals/EVAL.md`;
   headings alone do not qualify. Generate with
   `skillevaluator create-eval-dataset <skill-dir> --full --prompt <skill-dir>/evals/EVAL.md`,
   then review it. The default prefers author guidance, then matching retained
   traces; existing datasets need `--force` or `--refine`. Skill-only
   generation requires explicit opt-in with `--grounding skill`. Review:
   positive cases (explicit, implicit, contextual) and at least one negative
   case with `"expected_skill": null`. Required shape: top-level `skill_name`
   and an `evals` array; each case has `id`, `prompt`, `expected_output`, and
   `expected_skill` (optional `assertions`, `files`). Validate with
   `skillevaluator tier3 validate <skill-dir> --strict`. Format:
   [references/tier3-datasets.md](references/tier3-datasets.md); run policy and
   scoring: [references/tier3-running.md](references/tier3-running.md).
6. **Run live evaluation.** `skillevaluator tier3 <skill-dir>` (or
   `skillevaluator tier3 evaluate <skill-dir> -a opencode --env-mode docker --n-attempts 3`).
   Use `--max-cases 1` for a cheap smoke run first; such runs are never
   publication evidence.
7. **Read the results** (see below), open `skillevaluator view <skill-dir>`, and
   for follow-up questions run `skillevaluator tier3 feedback <run-dir>`.
8. **Iterate.** Change the skill, rerun the same command with the same cases,
   agent and model, and compare. Treat a single run's lift as directional.

## Read The Results

Exit codes: `0` success, `1` a gate failed, evidence is `INCOMPLETE`, or the
run could not complete, `2` usage error (bad flag or missing path; fix the
command, not the skill).

- **Quality** (0-100): Correctness 35%, Discoverability 25%, Reliability 25%,
  Efficiency 15%. Grades A>=90, B>=80, C>=70. Passing needs `--min-score`
  (default 70) and no HIGH/CRITICAL quality finding.
- **Rubric** (0-100): nine criteria scored 0-10; passing needs the weighted
  score >= `--min-score` and every criterion >= 7.
- **Tier 2**: HIGH or CRITICAL duplicate findings exit `1` in `tier2 PATH`.
- **Tier 3**: five dimensions (Security, Correctness, Discoverability,
  Effectiveness, Efficiency), each PASS >= 0.50, NEUTRAL 0.40-0.50, FAIL < 0.40.
  Verdict PASS needs every dimension >= 0.50 for at least one agent. Skill
  Lift = with-skill overall - baseline overall; PASS >= +0.05. `INCONCLUSIVE:
  skill never activated` means no positive trial loaded the skill; fix the
  description or prompts, not the scores. `tier3 PATH` exits `0` on a `FAIL`
  verdict; only an incomplete run exits `1`.
- **Missing scores are `NO SCORE` or `None`, never 0.** Do not average them in.
- **Publication**: require `publication_status == "pass"` in the JSON report,
  not just `overall_passed`.

Report anatomy and JSON fields: [references/reports-and-ci.md](references/reports-and-ci.md).

## Examples

```bash
# Keyless first look
skillevaluator quality-check <skill-dir> -r cli -r json -o <report-dir>

# Static gate for a whole collection, complete report even on failures
skillevaluator validate <skills-dir> --profile default --tiers 1 -c -r json,markdown -o reports

# LLM-backed Tier 1 and Tier 2, dedup advisory
skillevaluator validate <skill-dir> --tiers 1,2 --llm --no-block-on-dedup

# Live A/B evaluation with two agents, retaining trajectories for debugging
skillevaluator tier3 evaluate <skill-dir> -a opencode,codex --n-attempts 3 --harbor-keep-jobs

# Baseline only, to calibrate cases before the skill exists
skillevaluator tier3 evaluate <skill-dir> --skip-with-skill
```

## Limitations

- Tier 1 and the quality score are static: they check structure and wording,
  not whether the skill works. Only Tier 3 observes agent behavior.
- LLM judges (rubric, Tier 3 correctness/effectiveness) vary between runs and
  models; compare runs only with the same model, cases, and attempts.
- Tier 3 costs model tokens and sandbox time per case x attempt x agent x 2
  arms; a generated starter case gives limited coverage.
- `validate --tiers` always includes Tier 1; `--tiers 2` or `--tiers 3` alone is
  rejected. Use `tier2 PATH` or `tier3 PATH` to run a tier on its own.
- Agent credentials come only from the host environment; `<skill-dir>/evals/config.yml`
  `runtime_env` rejects provider keys.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Exit `2` | Unknown flag, bad value, or missing path | Check `skillevaluator <command> --help`; prefix paths named like commands with `./` |
| Check shows `INCOMPLETE` | A scanner is missing or failed | Run `skillevaluator health-check --tier 1`; install the `security` extra or the missing scanner |
| "provider is required" / exit `1` before reports | No `SKILL_EVAL_LLM_PROVIDER` or key, or several keys exported | Set exactly one provider and its key; see providers reference |
| Quality below 70 | Missing `## Prerequisites`, `## Limitations`, `## Troubleshooting`, vague description, no examples | Read each finding's suggestion; do not stuff keywords |
| `INCONCLUSIVE: skill never activated` | Description does not match the prompts | Make the description name the tasks; add explicit-bucket cases |
| Docker trials fail to start | Daemon down or outdated engine | `docker info`; rerun `skillevaluator doctor --env-mode docker` |
| Agent model rejected | Default model not valid for that agent | `--agent-model AGENT=MODEL`, verify with `doctor --verify-models` |
| Run incomplete (`Scored attempt coverage is n/m`) | A trial errored or timed out | Rerun; raise `--timeout-multiplier`; inspect with `--harbor-keep-jobs` |

More cases: [references/troubleshooting.md](references/troubleshooting.md).

## Final Response

Report to the user:

- the exact commands run and their exit codes
- scores and verdicts per tier, quoting the report values (not estimates)
- the top findings to fix, each with its file and suggested change
- report paths (`<report-dir>/...`, `<skill-dir>/evals/results/<run-id>/report.html`)
- what was not run (skipped tiers, missing keys, `--max-cases` smoke runs) and
  therefore not proven
