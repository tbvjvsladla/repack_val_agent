# Codex 프로젝트 로컬 적용 기록

## 2026-11-14 하네스 중립 전환
- 정본을 `skills/wiki-desk/`로 옮기고 Codex(`.agents/skills/wiki-desk`)·Claude Code(`.claude/skills/wiki-desk`) 발견 경로는 디렉터리 심볼릭 링크로 연결했다. 두 하네스 모두 심볼릭 링크 스킬 폴더를 공식 지원한다(2026-11-14 각 공식 문서 확인).
- SKILL.md·installation.md·README·CLI 주석의 실행 경로와 호스트 문구를 하네스 중립으로 바꿨다. 아래 기록은 최초 Codex 조정 당시의 계보로 보존한다.
- installation.md의 '인식 확인' 줄에 파일 본문 전체가 중복 삽입돼 있던 기존 결함을 같은 변경에서 바로잡았다.

## 기반과 변경 범위
- 기반 패키지: wiki-desk 3.2.0 TypeScript.
- 기반 커밋: `5f4ab40e567a7bb5d578a13d6d87a37ae4726c56`.
- 원본: 프로젝트 안 `seed/wiki-desk/`. 원본 저장소는 변경하지 않고 폴더 전체를 `.agents/skills/wiki-desk/`에 복사했다(`.git` 제외).
- CLI와 검색·입고 로직은 보존한다. CLI 사용 예시 주석만 Codex 경로로 바꿨다.
- 지침과 참조의 실행 경로를 `.agents/skills/wiki-desk/`로 바꾸고 Claude 전용 Bash 허용 규칙 설명을 Codex의 작업 디렉터리·현재 세션 권한 설명으로 교체했다.
- 헌장 예시를 `AGENTS.md`로 안내하며, 실제 프로젝트 규칙·위키 계약은 별도 합의한다.
- `agents/openai.yaml`에 표시 이름·호출 예시와 자동 선택 정책을 명시했다.
- `PACKAGE_MANIFEST.json`은 조정된 설치본의 파일 집합·바이트 기준이다. 원본 배포본과 동일한 바이트라는 뜻은 아니다. 2026-11-22에 digest(sha256)를 뺐다(manifest_version 2.0.0). 설치본 내용은 설치한 프로젝트의 git으로 확인한다([설치](installation.md) 2단계).

## 공식 근거
2026-11-03 확인:
- [OpenAI 공식 Build skills 가이드](https://learn.chatgpt.com/docs/build-skills): 프로젝트 탐색 경로는 `.agents/skills/`, 필수 frontmatter는 `name`·`description`. 명시 호출과 description 기반 자동 선택을 지원한다.
- 같은 가이드의 Optional metadata: `agents/openai.yaml`은 선택 사항이며 `allow_implicit_invocation` 기본값은 true.
- 같은 가이드: 스킬 변경은 자동 감지하며 나타나지 않으면 재시작한다.

## 설치와 운영의 경계
설치 프로젝트 루트를 작업 디렉터리로 사용한다. CLI는 `process.cwd()`를 기본 원문 루트로 삼으므로 스킬 폴더 안에서 문서 명령을 실행하지 않는다. 필요하면 CLI의 절대 경로와 `--root <프로젝트 루트>`를 함께 쓴다.

위키 계약이 없으면 문서 범위·제외 경로·권위·파일명 규칙·입고 시점을 합의한 뒤 `init` 계획부터 확인한다. 예제 계약을 실제 계약으로 자동 승인하지 않는다. 위키 초기화와 첫 입고 전에는 검색 준비가 완료된 상태가 아니다.

## 검증
- 패키지 루트에서 `node scripts/package-manifest.ts --check`, `node --test`.
- 프로젝트 데이터 명령은 프로젝트 루트에서 실행한다. 별도의 임시 프로젝트 계약으로 init → sync → check → query → read를 검증할 수 있다. 실제 원문의 경로·해시를 비교한다.
- Codex의 `/skills` 또는 `$` 목록에서 `wiki-desk` 선택 후 실제 로드를 확인한다. CLI 실행 성공과 호스트 인식은 별개다.

## 업그레이드
원본 태그를 이 폴더에 그대로 덮으면 Codex 지침이 사라진다. 새 배포본과 diff를 검토해 이 조정 범위를 다시 적용하고 manifest를 재생성·검증한다. 원본 upstream 버전은 실제 기반 버전대로 유지한다.

## val_agent의 적용 계약
- 헌장 정본은 프로젝트 루트의 공통 진입 AGENTS.md가 연결하는 `rules/` 아래 파일이다. (2026-11-14 이너 하네스 애그노스틱 전환 전에는 `.codex/rules/`에 있었다.)
- 런타임 계약의 정본은 프로젝트 루트 __llm-wiki/contract.json이다. source_roots는 docs 하나이며 역할별 하위 폴더를 분류한다.
- 프로젝트 문서명에는 역할 접두사를 붙이지 않는다. 기존 parser의 필수 prefix 그룹은 빈 문자열로 캡처해 원래 모듈을 유지한다. 문서 종류는 경로별 authority 규칙과 원문 document_type으로 결정한다.
- 종류별 authority_rank는 모두 0이다. 임의 수치로 계획·실행·사용자 승인의 우열을 만들지 않고 원문의 대상·시점·승인 범위를 읽는다.
- 문서는 공통 task_id와 related_* 전체 상대경로로 연결한다. 같은 과업의 갱신은 기존 경로와 과거 관찰을 보존한 뒤 sync 계획·적용·check로 입고한다.
- 검증 코드와 로그는 루트 temp/ 또는 tests/에 둘 수 있다. 루트 제외 규칙은 설치 스킬 내부 tests에는 적용하지 않는다.
- init의 --contract 경로는 호출 작업 디렉터리 기준이다. --root를 지정해도 이 입력 파일의 경로는 바뀌지 않으므로 외부 디렉터리에서 초기화할 때 계약의 절대 경로를 쓴다.
