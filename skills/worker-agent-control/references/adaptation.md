# Codex 프로젝트 로컬 적용 기록

원본: `seed/hermes-subagent-control-plane` v4.3.0 (AhnSangHun, MIT).

- `origin/claude-code-control`: `1f09b96f5659f0008131fa381ad679e666dca11d`
- `origin/codex-cli-control`: `d80eac0fc85b6b607d42e1574b4c84f5a822eb17`

두 브랜치를 checkout/변경하지 않고 읽어 실제 CLI 호출, preflight → discovery → bounded action → 직접 검증, 실행자 무결성·부분 결과 보존 규율을 이식했다. 단순 문자열 치환 사본이 아니다. Hermes 도구·Slack/게이트웨이·특정 과거 업무 사례는 가져오지 않았다. 원본 파일의 해시 manifest를 변경 설치본의 manifest로 재사용하지 않는다.

지휘자는 val_agent Codex 전용이며 워커 기본 Claude Code, 명시 요청 시 Codex CLI다. 하나의 로컬 스킬이 두 provider를 선택하므로 공통 규칙을 중복 설치하지 않는다. 원본의 Agent Card는 필수가 아니며 현재 워커의 실제 AGENTS.md → CLAUDE.md·규칙 체인으로 확인한다. 카드 부재를 보고 후 실제 진입문서로 판단하는 원본 common-rules §B 원칙을 따른다.

2026-11-14 개정(v1.4.0, 과업 `inner-harness-agnostic-261114151405`): 이너 하네스 애그노스틱 정책에 따라 지휘자는 val_agent의 현재 이너 하네스(Codex 또는 Claude Code)다. 위 문단은 최초 설치 당시의 기록으로 보존한다. 정본은 `skills/worker-agent-control/`이고 각 하네스 발견 경로는 심볼릭 링크다.

2026-11-18 개정(v1.5.0, 과업 `evs-rust-native-261118054736` 사용자 결정 U7): 워커 프로젝트 easy-vllm-simulator의 메인↔서브 A2A 평면을 읽기 전용으로 검토했다. 검토 대상은 `terraforming_node` `references/orchestration/a2a_relay.md`, relay의 `pending_hitl.json`과 `## 이어받기` 조립, 그리고 "답의 존재는 승인이 아니다"·"기술 feedback은 답이 아니다"·승인 대필 금지 규칙이다.
- 다음 원칙을 이 스킬에 맞게 옮겼다: 질문 블록·답 원문 전사·같은 세션 재개, 답≠승인, 거부를 사건으로 다루기, 신호≠사용자 취소.
- 워커 고유의 vLLM 리포트 스키마, 러너 사다리 회전, 서브 overlay 배달, 캠페인 셀 술어는 가져오지 않았다. 도메인 전용이거나 조용한 대체 금지·스킬 독립성 규칙과 충돌하기 때문이다.

2026-11-28 개정(v1.6.0, 과업 `worker-profile-restore-261128174721`): 과업 `three-worker-connection-261106101317`(v1.2.0)에서 넣은 여러 워커 프로필(`workers`, `--worker`, `--config`, `selftest_discover.py`)이 공개 정본 `skills/`로 옮겨지지 않아, 그 형식의 로컬 설정에서 discover.py가 KeyError로 멈췄다. 1.5.0 위에 다시 넣었다. 단일 설정·예시 대체·경로 지정 전 거부는 1.5.0대로 두고, `claude_discovery_max_turns`는 Claude discovery에서만 요구한다. 공개본 생성기의 워커별 경로 가리기도 함께 복원했다.

원본의 파일 기반 auth preflight 대신 CLI native auth status를 사용한다. 현재 인증·모델·권한·환경은 상속하며 HOME이나 CODEX_HOME을 다른 변수로 바꾸지 않는다. 원본의 일괄 dot-directory 제한 주장·고정 재시도 금지·기계적 grade 예산을 현재 Codex의 보편 규칙으로 복제하지 않았다. 권한과 재개는 실제 CLI·사용자 정책·워커 헌장이 우선한다.

공식 문서(2026-11-03 실제 열람):

- [Build skills](https://learn.chatgpt.com/docs/build-skills): 프로젝트 `.agents/skills`, name·description 포함 SKILL.md, 선택적 agents/openai.yaml, 명시/자동 선택. 신규 스킬은 자동 감지되며 보이지 않을 때 재시작한다.
- [Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode): `codex exec`, `-C`, read-only sandbox, JSONL 이벤트, `-o` 최종 응답, JSON schema, 기존 인증 재사용. 설치된 `codex exec --help`와 `claude --help`도 대조했다.

실제 설치·검증 근거는 task_id `codex-worker-control-261103192138`의 docs/interview·plan·devlog·checklist에 있다. 이 파일의 설명만으로 실제 호출이나 자동 스킬 선택 성공을 주장하지 않는다.
