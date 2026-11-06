# repack_val_agent

다른 개발언어로 작성된 프로젝트를 Rust로 리팩토링할 때 사용하는 헌장·스킬·문서 작성 가이드 모음입니다. Harness² 방식의 하네스 개선도 지원합니다. 각 작업 공간에서 과업 문서와 포인터 위키를 생성하며 이 작업 이력은 로컬에 보존합니다.

메인 에이전트가 목표와 권한을 정리하고, 외부 워커에게 승인 범위의 구현을 맡긴 뒤 실제 변경과 검사 근거를 확인합니다. Harness² 스킬은 개선 후보·비교·선택·회고의 근거를 관리합니다. 실제 실행과 최종 수용은 호출자가 담당합니다.

## 구성

| 경로 | 내용 |
|---|---|
| [AGENTS.md](AGENTS.md) | 프로젝트 진입 지침 |
| [.codex/rules/workflow.md](.codex/rules/workflow.md) | 역할·승인 범위·검증·중단과 재개 |
| [.codex/rules/docs.md](.codex/rules/docs.md) | 의도 → 계획 → 수행 기록 → 검증 문서 연결 |
| [.agents/skills/](.agents/skills/) | 재사용할 수 있는 스킬과 내부 검사 |
| [docs/](docs/) | 역할별 doc_gen_guide.md: 문서의 용도·작성법·예시 |
| [__llm-wiki/](__llm-wiki/) | 로컬 포인터 위키를 만들 계약·용어집 |
| [PUBLICATION.md](PUBLICATION.md) | 공개 범위와 공개본 생성 절차 |

스킬은 `worker-agent-control`(외부 워커 제어), `rust-repack`(Rust 변환), `harness2`(개선 실험 근거), `code-review`(신규 결함 검토), `pr-review-toolkit`(관점별 검토), `wiki-desk`(프로젝트 문서 조회)로 구성됩니다. 매 과업에서 필요한 도구를 선택합니다.

## 다른 프로젝트에서 사용하기

필요한 스킬만 가져오거나 이 저장소를 지휘자 작업 공간으로 사용할 수 있습니다. 헌장과 문서 배치는 적용할 프로젝트의 기존 규칙을 검토하고 채택합니다. 이 저장소를 복사한 사실이 대상 프로젝트의 수정·실행 권한을 부여하지는 않습니다.

포함된 도구는 Node.js 24 이상과 Python 3 표준 라이브러리를 사용합니다. 외부 워커를 호출하려면 사용할 Claude Code 또는 Codex CLI를 별도로 준비하고 인증해야 합니다. 이 배포본에는 CLI, 인증, 외부 실행 환경이 포함되지 않습니다.

워커를 연결할 때 설정 예시를 복사하고 `workspace`와 `context_files`를 실제 대상에 맞춥니다. `context_files`는 대상 프로젝트에 존재하며 읽어야 하는 지침 파일 목록입니다. 원래 예시의 모든 파일을 새 대상에 만들 필요는 없습니다.

```bash
cp .agents/skills/worker-agent-control/config.example.json .agents/skills/worker-agent-control/config.json
```

구체 호출·권한·결과 확인은 [worker-agent-control](.agents/skills/worker-agent-control/SKILL.md), 개선 실험 계약은 [harness2](.agents/skills/harness2/SKILL.md)에서 확인합니다. 읽기 전용 discovery도 실제 CLI 호출을 발생시키므로 대상과 권한을 정한 뒤 실행합니다.

새 clone에서는 [문서발행규칙의 최초 위키 초기화](.codex/rules/docs.md#wiki-desk-입고)를 한 번 수행합니다. 이후 생성 문서를 입고·검사하고 스킬 자체검사를 실행할 수 있습니다. 외부 프로젝트는 실행하지 않습니다.

```bash
node .agents/skills/wiki-desk/src/cli.ts sync
node .agents/skills/wiki-desk/src/cli.ts sync --apply
node .agents/skills/wiki-desk/src/cli.ts check
node --test .agents/skills/harness2/tests/experiment.test.mjs
```

## 문서 작성과 배포 경계

기본 과업은 interview → plan → devlog → checklist로 연결합니다. 필요할 때 request 문서를 추가합니다. 각 폴더의 가이드에서 역할별 필수 내용을 확인하고, 공통 명명·frontmatter·연결·입고 규칙은 [문서발행규칙](.codex/rules/docs.md)을 따릅니다.

`docs/*/doc_gen_guide.md`만 Git으로 배포합니다. 같은 폴더에 생성하는 실제 문서와 위키 카드·index·log·receipt·캐시는 추적하지 않습니다. 생성 문서의 작성·검증·입고와 기존 과업의 재개는 계속 수행합니다. 기존 로컬 이력은 추적 해제 후에도 보존하며, 이 전환이 과거 Git 커밋까지 제거하지는 않습니다.

기본 라이선스는 [MIT](LICENSE)입니다. 외부에서 가져온 자산의 원래 라이선스와 출처는 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 및 각 스킬 디렉터리에 보존합니다.
