# repack_val_agent

> **독립된 에이전트와 도구를 연결해 대상을 개선하고, 그 근거를 다음 개선에 이어갑니다.**

repack_val_agent는 코드베이스·스킬·MCP·플러그인 등의 **개선을 지휘하는 오케스트레이터 작업 공간**입니다. 사용자가 목표와 완료 기준을 정하면, 메인 에이전트가 대상 워커와 도구의 역할을 나누고 실제 변경·검사 근거를 확인합니다. 개선된 대상과 함께, 다음 과업에서 다시 찾을 수 있는 의도·결정·실패·검증 기록을 남깁니다.

## 지휘와 실행을 나누는 개선 협업

대표 협업 모델은 **오케스트레이터와 분리된 대상 워커**입니다. 메인은 사용자와 목표·권한·완료 기준을 정리하고, 워커는 대상 프로젝트의 지침과 허용 범위 안에서 구현·자체검사를 수행합니다. 메인은 반환된 파일과 실행 근거를 확인해 다음 작업과 수용 여부를 판단합니다.

![사용자의 목표를 오케스트레이터가 계획과 작업으로 나누고, worker-agent-control을 통해 별도 워커에 전달합니다. 워커는 외부 프로젝트 또는 repack_target의 실제 대상 루트에서 개선하고 변경·검사 근거를 반환합니다.](assets/orchestration.svg)

여기서 분리하는 것은 **에이전트의 실행 맥락과 책임**입니다. 같은 종류의 CLI를 사용하는 별도 세션도 이 협업을 구성할 수 있습니다. 구체적인 역할 배치·위임·독립 검증 조건은 [작업수행규칙](.codex/rules/workflow.md)과 각 과업의 계획에 둡니다.

워커 연결의 기반은 [worker-agent-control](.agents/skills/worker-agent-control/SKILL.md)입니다. [hermes-subagent-control-plane](https://github.com/tbvjvsladla/hermes-subagent-control-plane)을 기초로 이 프로젝트의 Codex 지휘 환경에 맞춰 적용했습니다. 워커별 작업 경로·실행 CLI·진입 지침을 구분하고, 연결 확인과 본작업 결과를 따로 검증합니다. 계보와 적용 차이는 [적용 기록](.agents/skills/worker-agent-control/references/adaptation.md)에 있습니다.

### 개선 대상은 두 곳에 둘 수 있습니다

| 위치 | 작업 방식 | 결과와 Git 경계 |
|---|---|---|
| **외부의 기존 프로젝트** | 원래 작업 공간에 둔 채 워커를 실제 대상 루트에 연결합니다. | 대상의 소스·개선 결과·Git 이력은 해당 프로젝트에서 관리합니다. |
| **`repack_target/` 내부 콘텐츠** | 스킬·MCP·플러그인·코드베이스 등을 준비하고 선택한 콘텐츠 루트에 워커를 연결합니다. | 실제 콘텐츠와 결과는 로컬에 보존하고, val_agent에서는 폴더 지침만 배포합니다. |

두 위치 모두 최종 산출물은 **개선된 대상 자체**입니다. 대상의 위치가 지휘·실행의 책임을 바꾸지는 않습니다. 대상 소스와 결과를 val_agent의 Git 배포에 넣지 않으며, 대상 자체의 커밋·배포는 그 과업의 범위로 정합니다. 실제 대상 루트·허용 수정 범위·검증 방법은 과업 계획에 명시합니다.

## 개선 방향·수단·기반을 분리합니다

개선 목표는 과업마다 다릅니다. 어떤 과업에는 Rust 변환이, 다른 과업에는 결함 검토나 LLM 답변 평가가 필요합니다. repack_val_agent는 **개선 방향을 다루는 도구**, **선택 가능한 수단 도구**, **작업을 받치는 기반 도구**를 구분해 조합합니다.

기반은 **`wiki-desk`와 `worker-agent-control` 두 도구**로 유지하고, 수단 도구는 과업에 맞게 교체·추가합니다. 도구 간 구체적인 연결은 메인 오케스트레이터가 맡습니다.

![harness2가 개선 후보와 비교·회고를 지원하고, 메인이 독립적인 수단 도구를 선택합니다. worker-agent-control과 wiki-desk는 실행 연결과 문서 근거를 받치며, 두 헌장이 작업과 문서의 공통 규칙을 정합니다.](assets/skill-layers.svg)

| 구분 | 도구 | 책임 |
|---|---|---|
| **개선 방향** | [harness2](.agents/skills/harness2/SKILL.md) | 무엇을 바꿔 볼지 제안하고, 후보의 탐색·대조·조합·선택·회고를 근거와 연결합니다. |
| **선택 가능한 수단** | `code-review` · `rust-repack` · `deepeval-pack` · `pr-review-toolkit` 등 | 이번 개선에 필요한 변환·검토·평가를 각자의 책임 범위에서 수행합니다. |
| **기반** | [worker-agent-control](.agents/skills/worker-agent-control/SKILL.md) | 대상 워커를 연결하고, 요청·실행·결과 인수를 다룹니다. |
| **기반** | [wiki-desk](.agents/skills/wiki-desk/SKILL.md) | 프로젝트 문서의 원문 근거를 찾고, 새 기록을 포인터 위키에 입고합니다. |

### 수단 도구는 독립적으로 기동하고, 과업에서 조합합니다

수단 도구를 조합하는 원칙은 **어댑터 패턴**입니다. 각 도구는 자기 입력·수행 범위·산출물·실행 근거를 정의하고 독립적으로 사용할 수 있습니다. 사용자는 필요한 도구의 사용·미사용과 연계 방식을 정하고, 메인은 이를 과업 계획의 역할·입출력·순서로 구체화합니다.

| 수단 도구 | 선택할 때 | 반환하는 책임 범위 |
|---|---|---|
| [code-review](.agents/skills/code-review/SKILL.md) | PR이나 로컬 변경의 신규 결함을 찾을 때 | 근거를 재검증해 엄선한 결함·규칙 위반 |
| [rust-repack](.agents/skills/rust-repack/SKILL.md) | 다른 언어의 로직 또는 기존 Rust를 개선할 때 | 외부 동작을 보존하는 Rust 변환·리팩토링과 자체검사 |
| `deepeval-pack` (별도 설치) | 답변·검색 문맥·기대 답변을 평가할 때 | 설정한 Judge·메트릭에 따른 케이스별 평가 결과 |
| [pr-review-toolkit](.agents/skills/pr-review-toolkit/SKILL.md) | 테스트·주석·오류 처리·타입·품질 등 여러 관점을 검토할 때 | 선택한 관점의 검토 결과 |

예를 들어 동작 보존이 목표인 Rust 전환 과업에서는 `rust-repack`의 결과를 별도 검토에 넘길 수 있고, 검색·답변 품질 과업에서는 `deepeval-pack`을 평가 수단으로 선택할 수 있습니다. 구체적인 조합은 과업에서 정합니다. **한 도구의 완료는 그 도구가 맡은 범위의 완료**이며, 전체 개선의 완료는 메인이 합의한 기준과 실제 근거로 확인합니다.

이 어댑터 구성은 메인이 도구의 계약을 맞춰 연결하는 방식입니다. 도구를 켜고 끄는 선택은 과업 계획에 기록하며, 스킬 내부에 다른 스킬의 호출이나 고정된 후속 순서를 넣지 않습니다.

### Harness²로 개선의 방향과 비교 근거를 다룹니다

[harness2](.agents/skills/harness2/SKILL.md)는 [Google Research의 Harness²](https://github.com/google-research/harness2)의 재귀적 하네스 개선 방법을 이 작업 공간에 내재화한 **독립 구현**입니다. 승인된 목표·변경 범위·필수 검사·기준선·실험 예산 안에서 후보를 제안하고, 실제 실행과 평가 근거를 비교해 선택·보류·회고를 지원합니다.

메인은 이 근거를 바탕으로 어떤 수단을 사용하고 다음에 무엇을 시도할지 정합니다. 실제 실행은 연결된 워커·도구가, 최종 수용은 과업 책임자가 담당합니다. 구현 범위와 연구 원형의 차이는 [출처·적용 범위](.agents/skills/harness2/references/provenance.md)에 정리되어 있습니다.

## 이번 개선을 다음 개선의 출발점으로

개선은 변경 전후의 차이를 합의한 기준으로 확인하는 과정입니다. 목표·보존할 동작·완료 기준을 먼저 정하고, 후보를 실행한 뒤 실제 결과와 필수 검사를 확인합니다. 필요하면 같은 조건의 기준선과 비교하며, 근거에 따라 채택·기준선 유지·보류를 결정합니다.

![목표와 기준에서 시작해 후보·실행·검증·판단·회고를 반복합니다. 과업 전반의 의도·계획·실행·검증 기록을 wiki-desk로 찾을 수 있게 보존하고 다음 개선의 근거로 재사용합니다.](assets/improvement-loop.svg)

성공뿐 아니라 실패한 접근, 비교 조건, 보류 이유와 적용 한계도 남깁니다. 다음 과업은 이 기록의 원문을 조회해 출발점으로 삼고, 현재 대상·환경·기준에 유효한 근거인지 확인합니다. 이것이 이 프로젝트에서 **루프 엔지니어링의 자산**을 쌓는 방식입니다.

이 흐름을 받치는 헌장은 두 개입니다.

| 헌장 | 정하는 내용 |
|---|---|
| [작업수행규칙](.codex/rules/workflow.md) | 목표·권한·역할·도구 독립성·검증·수용·중단과 재개 |
| [문서발행규칙](.codex/rules/docs.md) | 역할별 문서·task_id·연결·명명·갱신·위키 입고 |

과업 기록은 **interview → plan → devlog → checklist**로 연결합니다. 의도와 합의, 수행 계획, 실제 실행, 검증 결과를 같은 과업의 기록으로 이어 가며, 필요하면 `request` 문서를 추가합니다. [wiki-desk](.agents/skills/wiki-desk/SKILL.md)는 이 문서의 위치와 원문 구간을 찾아 줍니다. 검색 순위나 카드 메타데이터를 승인·정답의 대체물로 사용하지 않습니다.

헌장·스킬·역할별 작성 가이드는 재사용할 수 있도록 Git으로 배포합니다. 실제 과업 문서와 생성된 위키 카드·index·log·receipt·캐시는 각 작업 공간에 로컬로 보존합니다.

## 저장소 안내

| 경로 | 내용 |
|---|---|
| [AGENTS.md](AGENTS.md) | 프로젝트 진입 지침 |
| [.codex/rules/workflow.md](.codex/rules/workflow.md) | 역할·승인 범위·검증·중단과 재개 |
| [.codex/rules/docs.md](.codex/rules/docs.md) | 의도 → 계획 → 수행 기록 → 검증 문서 연결 |
| [.agents/skills/](.agents/skills/) | 재사용할 수 있는 스킬과 내부 검사 |
| [assets/](assets/) | README의 SVG 구조도·도구 구성도·개선 루프 |
| [docs/](docs/) | 역할별 doc_gen_guide.md: 문서의 용도·작성법·예시 |
| [__llm-wiki/](__llm-wiki/) | 로컬 포인터 위키를 만들 계약·용어집 |
| `repack_target/` | 로컬 개선 대상과 최종 산출물을 준비할 공간 |
| [PUBLICATION.md](PUBLICATION.md) | 공개 범위와 공개본 생성 절차 |

## 시작하기

필요한 스킬만 가져오거나 이 저장소를 지휘자 작업 공간으로 사용할 수 있습니다. 헌장과 문서 배치는 적용할 프로젝트의 기존 규칙을 검토하고 채택합니다. 이 저장소를 복사한 사실이 대상 프로젝트의 수정·실행 권한을 부여하지는 않습니다.

기반 CLI는 Node.js 24 이상과 Python 3를 사용합니다. 외부 워커를 호출하려면 사용할 Claude Code 또는 Codex CLI를 별도로 준비하고 인증해야 합니다. 개별 수단 도구의 추가 의존성은 해당 스킬의 설치 지침을 따릅니다.

대상 프로젝트의 기존 규칙을 확인하고 목표·허용 수정 범위·완료 기준부터 정합니다.

> “이 대상의 현재 동작을 보존하면서 오류 처리를 개선해 줘. 대상 경로와 완료 기준을 확인하고, 오케스트레이터와 워커의 역할 및 사용할 도구를 계획해 줘.”

워커를 연결할 때 로컬 설정이 없으면 설정 예시를 복사합니다. 기존 설정이 있으면 필요한 항목을 검토해 갱신합니다. 작업 경로·실행 CLI·진입 지침은 실제 대상에 맞추고, 실제 경로가 든 설정은 로컬에만 보관합니다.

```bash
cp .agents/skills/worker-agent-control/config.example.json .agents/skills/worker-agent-control/config.json
```

대상과 호출 권한을 정한 뒤 연결을 확인합니다. 연결 명령·필수 인자·권한·결과 확인은 설치된 [worker-agent-control](.agents/skills/worker-agent-control/SKILL.md)의 지침을 따릅니다. 읽기 전용 discovery도 실제 CLI를 호출하며, 연결 성공과 본작업 완료를 구분합니다. 개선 실험 계약은 [harness2](.agents/skills/harness2/SKILL.md)에서 확인합니다.

새 clone에서는 [문서발행규칙의 최초 위키 초기화](.codex/rules/docs.md#wiki-desk-입고)를 한 번 수행합니다. 이후 생성 문서를 입고·검사하고 스킬 자체검사를 실행할 수 있습니다. 외부 프로젝트는 실행하지 않습니다.

```bash
node .agents/skills/wiki-desk/src/cli.ts sync
node .agents/skills/wiki-desk/src/cli.ts sync --apply
node .agents/skills/wiki-desk/src/cli.ts check
```

선택한 도구는 각 스킬의 실행 계약을 확인합니다. 예를 들어 Harness²의 로컬 자체검사는 다음과 같습니다. 실제 대상의 개선 평가는 과업의 완료 기준으로 별도 수행합니다.

```bash
node --test .agents/skills/harness2/tests/experiment.test.mjs
```

DeepEval 기반 평가를 선택한다면 `deepeval-pack`을 별도로 준비하고 해당 스킬의 설치·MCP 등록 지침을 확인합니다. Judge provider/model과 활성 설정·인증 정보는 각 작업 공간에서 준비합니다.

## 문서 작성과 배포 경계

기본 과업은 interview → plan → devlog → checklist로 연결합니다. 필요할 때 request 문서를 추가합니다. 각 폴더의 가이드에서 역할별 필수 내용을 확인하고, 공통 명명·frontmatter·연결·입고 규칙은 [문서발행규칙](.codex/rules/docs.md)을 따릅니다.

`docs/*/doc_gen_guide.md`만 Git으로 배포합니다. 같은 폴더에 생성하는 실제 문서와 위키 카드·index·log·receipt·캐시는 추적하지 않습니다. 생성 문서의 작성·검증·입고와 기존 과업의 재개는 계속 수행합니다. 기존 로컬 이력은 추적 해제 후에도 보존하며, 이 전환이 과거 Git 커밋까지 제거하지는 않습니다.

기본 라이선스는 [MIT](LICENSE)입니다. 외부에서 가져온 자산의 원래 라이선스와 출처는 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 및 각 스킬 디렉터리에 보존합니다.
