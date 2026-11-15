---
name: deepeval-pack
description: "DeepEval의 LLM-as-a-Judge 메트릭으로 제공된 답변·검색 문맥·기대 답변을 평가한다. 스킬 config.yaml의 provider·judge_profile(envs/의 프로필 파일)과 복수 메트릭 설정을 검증하고 MCP로 케이스별 결과를 반환할 때 사용한다."
metadata:
  activation: opt-in
  version: "0.2.0"
---

# DeepEval 평가 도구

제공된 평가 케이스를 설정된 Judge와 메트릭으로 측정한다. 데이터 준비·검색·개선 후보 생성·실험 설계·통계·최종 수용은 호출자가 결정한다.

1. 이 폴더의 `config.yaml`과 [설정·입력 계약](references/config.md)을 확인한다. Judge는 두 knob으로 정한다. `provider`와 `judge_profile`(프로젝트 `envs/`의 프로필 파일 이름, 예: `deepseek_flash_profile.yaml`)이다. 프로필에는 model과 생성 설정이 있다. 설정과 프로필은 요청마다 다시 읽는다. 둘 중 하나라도 비어 있으면 사용자와 값을 정하기 전 실제 평가를 진행하지 않는다. 기본 모델을 추천하거나 채워 넣지 않는다.
2. MCP `deepeval_status` / `deepeval_validate`로 지원 항목·누락 설정·필수 입력을 확인한다. 이 검사는 API 인증이나 모델별 옵션 호환성을 보증하지 않는다.
3. 평가가 요청되고 Judge가 등록되어 있으면 `deepeval_evaluate(cases=[...])`를 호출한다. 케이스 본문은 선택한 외부 provider에 전달된다. API 키는 환경변수 또는 프로젝트 `envs/.env`에서 읽으며 응답·설정에 적지 않는다.
4. 케이스/메트릭별 점수·이유·상태·오류를 반환한다. `metric_success`는 해당 메트릭의 threshold 결과다. 오류를 점수 0이나 성공으로 바꾸거나 여러 결과를 전체 개선 성공 판정으로 합치지 않는다.

처음 설치된 두 메트릭은 교체 가능한 예시다. Contextual Recall은 기대 답변과 검색 문맥의 대응, Faithfulness는 실제 답변과 검색 문맥의 일치도를 평가한다. 이 점수를 근거 ID의 정확한 확보율·인용 정확성·원문 자체의 진실성으로 해석하지 않는다.

MCP 연결이 없으면 프로젝트 루트에서 로컬 검증을 실행한다. `<skill-dir>`은 이 SKILL.md가 있는 폴더다(정본 `skills/deepeval-pack`, 어댑터 링크 `.agents/skills/deepeval-pack`·`.claude/skills/deepeval-pack`).

```bash
<skill-dir>/.venv/bin/python -B <skill-dir>/scripts/run.py check
```

키 파일은 프로젝트 루트의 `envs/.env`다. 프로젝트 루트는 스킬 폴더 위치로 정한다(`<root>/skills/<name>`, `<root>/.agents/skills/<name>`, `<root>/.claude/skills/<name>`). 다른 위치에 설치했으면 `DEEPEVAL_PACK_PROJECT_ROOT` 또는 `--env-file`로 지정한다.

이 스킬은 비활성 상태로 배포된다(`metadata.activation: opt-in`). 활성화·의존성 설치·MCP 등록(Codex, Claude Code)은 [로컬 설치](references/installation.md)를 참고한다. 프로필 파일은 사용자가 만들며 이 팩은 프로필 생성기를 두지 않는다.

