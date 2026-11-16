# 문서발행규칙

## 배포 가이드와 로컬 문서

`docs/`는 역할별 폴더를 유지하며 각 폴더의 `doc_gen_guide.md` 하나만 Git으로 배포한다. 가이드는 해당 문서의 역할·작성 시점·필수 내용·예시를 설명하고, 공통 명명·메타데이터·연결 규칙은 이 헌장을 참조한다. 새 역할 폴더를 도입하면 같은 이름의 가이드를 둔다. 이 가이드는 과업 문서가 아니므로 과업 frontmatter·시각 기반 파일명을 요구하지 않는다.

실제 과업 문서는 가이드와 같은 역할 폴더에 생성·갱신하고 Git 추적·공개본에서 제외한다. 문서 작성·검증·입고와 같은 과업의 재개 의무는 유지한다. 추적 제외를 문서 삭제나 과거 결정 폐기의 근거로 사용하지 않는다. 기존에 추적한 생성 문서는 로컬 파일을 보존한 채 index에서만 제거한다. `.gitignore`는 과거 커밋을 제거하지 않으며 과거 이력 재작성·원격 발행은 별도 범위다.

## 역할별 배치와 연결

새 개발·변경 과업에는 다음 네 문서를 둔다.

| 폴더 | 역할 | 주요 내용 |
|---|---|---|
| `docs/interview/` | 의도·합의 | 목표·범위·제약·미결정·사용자 결정 |
| `docs/plan/` | 수행 계획 | 순서·영향·완료 기준·검증·복구/재개 |
| `docs/devlog/` | 수행 기록 | 실제 변경·결정·시도·현재 상태·다음 행동 |
| `docs/checklist/` | 검증과 종료 | 기대 조건·관측·상태·근거·독립 검증·수용 |

추가 행동이 필요할 때는 `docs/request/`에 선택 문서를 작성할 수 있다. 자료·작업 대상·우선순위·제약·필요한 결정을 적고 해당 과업과 연결한다. 요청서가 기본 네 문서나 사용자 승인을 대체하지 않는다.

네 문서를 역할별 폴더에 배치하고 공통 `task_id`로 연결한다. `docs/<task_id>/` 구조로 바꾸지 않는다. 과업 ID는 문서 한 묶음에 하나이며, 그 묶음 안에서 최초 부여 후 유지하는 고유 문자열이다. 예: `wiki-desk-bootstrap-261103164515`.

각 문서의 frontmatter에는 `task_id`, `document_type`, `title`, `status`, `created`, `updated`를 둔다. `document_type`은 해당 폴더의 역할명이다. 시각은 시간대가 포함된 ISO 8601로 기록한다. 이전 루프를 잇는 묶음은 `previous_task_id`에 이전 묶음의 `task_id`를 적는다.

다른 세 문서의 경로는 `related_interview`, `related_plan`, `related_devlog`, `related_checklist` 중 해당 키에 **저장소 상대 전체 경로**로 적는다. 자기 문서는 생략한다. 본문의 클릭 링크는 문서 위치에서 올바르게 해석되는 상대 링크로 쓰고 정본 참조는 frontmatter의 전체 경로로 유지한다.

문서 상태는 실제 단계에 맞게 기록한다. 파일 존재·상태 문자열·권위 순위 자체를 사용자 승인으로 취급하지 않는다. 승인과 검증은 대상·범위·시점·근거를 본문에 남긴다.

plan에는 이번 과업에 선택한 도구·역할, 각 입력과 산출물, 연계 순서·권한·실패 후 반환 조건을 적는다. 스킬에 다른 스킬의 존재·역할·호출 지침을 복제하지 않는다. 환경·의존성·지원 조합·보존 계약·허용 수정 경로도 이번 대상에 맞게 정한다.

checklist에는 자체검사·독립 검토와 실제 통합·기동·시나리오 검사·성능 평가·수용의 항목을 구분한다. 항목 하나가 관측 가능한 결과 하나다. 적용 항목마다 검사 명령·기대 조건·실행 대상과 환경·관측·원시 근거·상태를 연결하고, 미적용은 `NOT_APPLICABLE`과 이유를 기록한다. 사유 없는 `N/A`나 `TBD`로 칸을 비워 두지 않는다. 작성자에게 묻지 않고도 명령을 따라 할 수 있게 쓴다. 성능 비교가 필요하면 기준선·대표 입력·환경·품질 조건과 판정 기준을 실행 전에 정한다. 구체 명령·도구 조합·수치는 과업 문서에 두고 일반 헌장을 특정 언어나 Harness에 맞춰 늘리지 않는다.

## 명명과 최초 발행

- 기본: `<YYMMDDHH>_<한글제목>.md`.
- **같은 역할 폴더의 같은 시간대에 이미 발행된 문서가 있으면** 새 문서는 `<YYMMDDHH_MM_SS>_<한글제목>.md`를 사용한다.
- 시각은 **Asia/Seoul** 기준의 실제 발행 시각이다. 역할 접두사를 파일명에 추가하지 않는다.
- 제목은 한국어를 포함한 간결한 `_` slug로 쓰며 필요하면 영문·숫자를 함께 사용한다.
- 새 파일 생성 시 대상이 없음을 확인한다. 초 단위 시각까지 충돌하면 실제 시각을 다시 취득해 같은 규약으로 재시도하거나 충돌을 보고한다. 기존 파일을 덮거나 임의 순번 형식을 추가하지 않는다.

`interview`가 의도를 담고 Planner가 구현 전에 plan·checklist 초안을 작성한다. devlog는 처음에 계획 상태로 만들 수 있으며 수행 사실이 생기면 기록한다. 미래의 실행을 이미 완료한 사실처럼 적지 않는다.

## 루프 안의 갱신과 새 묶음

네 문서 한 묶음이 한 루프다. 루프 안의 수정과 검증 기록은 같은 묶음의 문서와 `task_id`·파일 경로에서 갱신한다. 파일명의 최초 발행 시각을 최신 수정 시각으로 바꾸지 않는다. `updated`와 본문 변경 이력에 수정 시점·변경 내용·사유를 남긴다.

루프가 닫힌 뒤 같은 대상에 작업이 더 필요하면 네 문서를 새로 발행한다. 새 묶음은 새 `task_id`를 받고 `previous_task_id`로 이전 묶음을 가리킨다. 닫힌 묶음의 문서를 고쳐 이어 쓰지 않는다. 루프가 닫히는 조건과 새 루프를 여는 주체는 [작업수행규칙](workflow.md)을 따른다.

문서에는 현재 상태를 적는다. 사건의 경위는 devlog에, 판정은 checklist에 한 번만 적고 같은 절을 다른 문서에 복사하지 않는다. 2계층 과업은 항목별 사건을 캠페인 인스턴스에 두고 `docs/`에는 큰 범주만 적는다.

계획 변경과 실행 결과, 과거 승인·검증 관찰과 현재 판정을 구분한다. 새 결과로 과거 실패·미실행·수용 기록을 지우지 않는다. 관련 조건이 바뀌면 이전 결과의 현재 적용 여부를 `STALE`로 표시하고 필요한 부분만 재검증한다.

원시 근거는 원래 로그·검사 산출물을 참조하고 문서에는 결과와 위치를 남긴다. 문서·구현·과거 실행·이번 실행의 근거 수준을 구분한다. 검증 결과나 로그의 존재만으로 의미상 정답·승인·형식 증명을 주장하지 않는다.

## 2계층 캠페인

Target Project를 개선하는 과업은 `campaigns/`에 캠페인 인스턴스를 두고 항목(cell)과 단계(phase)로 나눠 수행한다. 구조, 파일 형식, 채우는 규칙의 정본은 [campaigns/README.md](../campaigns/README.md)다. 캠페인의 선언은 1계층 plan과 checklist의 경로를 가리킨다. plan이 범위·검사·검토 표시·상한을 정하고, 항목의 분할과 묶음은 수행 중에 사건 기록과 함께 정한다. checklist가 통합 검증과 수용을 맡는다.

`campaigns/README.md`와 `campaigns/_template/`만 Git으로 배포한다. 인스턴스와 `campaigns/ACTIVE`는 로컬 작업물이다. 실행기가 인스턴스에 쓰는 영수증·로그·브리핑도 같다. 인스턴스는 위키의 원문 범위가 아니다. `python3 scripts/check_campaign.py`가 틀과 인스턴스의 형식을 읽기 전용으로 검사한다.

## wiki-desk 입고

스킬 지침은 `skills/wiki-desk/SKILL.md`, 프로젝트 계약은 `__llm-wiki/contract.json`이다. CLI의 작업 디렉터리는 이 프로젝트 루트다. 다른 디렉터리에서는 CLI 절대 경로와 `--root <프로젝트 루트>`를 함께 쓴다.

wiki-desk의 원문 범위는 `docs/` 하나다. 역할별 `doc_gen_guide.md`는 과업 근거가 아니므로 계약의 `excluded_roots`에 각 경로를 등록한다. 새 역할 폴더의 가이드를 추가하면 이 제외 목록도 갱신한다. `seed/`, 헌장·스킬 자체, 임시 검증 코드는 입고하지 않는다. 문서 종류는 역할별 경로로 분류하고 문서의 실제 승인 범위는 원문으로 판단한다.

배포본은 위키 설정(`contract.json`·`glossary.json`·`.gitignore`)만 포함한다. 카드·index·log·receipt·archive·캐시는 각 작업 공간에서 생성하고 로컬에 보존한다. 새 clone에는 생성물 소유 기록인 receipt가 없으므로 최초 입고 전에 다음 초기화를 한 번 수행한다. 기존 receipt·index·log·카드가 있는 작업 공간에는 이 초기화를 수행하거나 덮어쓰지 않는다.

기존 설정 폴더 때문에 `init`은 직접 실행할 수 없다. Bash에서 새 임시 작업 공간에 정식 `init` 계획·적용을 실행하고, 생성된 빈 위키의 index·log·receipt만 가져온다. 추적 중인 계약·용어집과 기존 문서는 수정하지 않는다.

```bash
mkdir -p temp
wiki_bootstrap=$(mktemp -d temp/wiki-bootstrap.XXXXXX)
node skills/wiki-desk/src/cli.ts --root "$wiki_bootstrap" init --contract __llm-wiki/contract.json
node skills/wiki-desk/src/cli.ts --root "$wiki_bootstrap" init --contract __llm-wiki/contract.json --apply
cp "$wiki_bootstrap"/__llm-wiki/{index.md,log.md,receipt.json} __llm-wiki/
```

문서를 발행하거나 갱신한 같은 작업 안에서 그 작업의 문서 전체를 입고한다.

```bash
node skills/wiki-desk/src/cli.ts sync
node skills/wiki-desk/src/cli.ts sync --apply
node skills/wiki-desk/src/cli.ts check
```

계획을 먼저 확인한다. 원문 이동·삭제, 사람이 수정한 카드, `verified` 카드의 재확인은 스킬의 보호 절차를 따른다. `protected`·`reverify`·고아·stale을 숨기거나 임의 삭제로 해소하지 않는다. wiki-desk는 원문을 수정하지 않는다. wiki-desk는 hook·cron·전역 설정을 추가하지 않는다.

## Git 분류

헌장·설치 스킬과 내부 회귀 테스트, 역할별 `docs/*/doc_gen_guide.md`, `campaigns/README.md`와 `campaigns/_template/`, 위키 `contract.json`·`glossary.json`·`.gitignore`는 추적한다. 실제 과업 문서와 캠페인 인스턴스·`campaigns/ACTIVE`, 위키 카드·index·log·receipt·archive·캐시, 공개본 생성 manifest(`PUBLIC_EXPORT.json`)는 로컬 생성물로 제외한다. 원문 없이 해당 문서의 위키 카드만 배포하지 않는다.

루트 `/seed/`, `/temp/`, `/tests/`와 우로보로스 실행 데이터도 제외한다. `repack_target/`은 `README.md`만 추적하고 개선 대상 콘텐츠와 결과는 모든 깊이에서 제외한다. 루트 `/tests/` 제외를 설치 스킬 내부 회귀 테스트에 적용하지 않는다. 추적 해제는 로컬 파일 보존을 확인한 뒤 해당 경로에만 `git rm --cached`로 적용한다.
