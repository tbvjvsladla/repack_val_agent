# campaigns/ — Target Project 개선의 2계층 기록

오래 걸리는 개선 작업을 항목(cell)으로 나누고, 각 항목이 단계(phase)를 밟게 한다. 항목은 검증 묶음(group)에 속하고, 검증은 묶음의 gate에서 한 번 돌린다. 항목의 상태와 사건을 대화 기억이 아니라 이 폴더의 파일에 둔다. 새 세션, 다른 하네스, 새 맥락의 수행자와 검토자가 파일만 읽고 이어갈 수 있게 하기 위해서다.

- 공통 규칙의 정본은 [작업수행규칙](../rules/workflow.md)과 [문서발행규칙](../rules/docs.md)이다.
- 이 문서는 2계층의 **구조와 파일 형식**의 정본이다.
- **운영 규칙**(언제 무엇을 하는가: gate, 재사용, 단계 위임, 정체, 방향 조정)의 정본은 [progress-monitor 스킬](../skills/progress-monitor/SKILL.md)이다. 캠페인을 수행할 때 그 스킬을 읽고 따른다.

## 언제 쓰는가

- Target Project를 개선하는 과업에만 쓴다. 그 밖의 과업은 `docs/`의 네 문서(1계층)만 쓴다.
- 캠페인을 열기 전에 1계층의 interview·plan·checklist가 있어야 한다. plan이 범위, 검사와 기준, 검토 표시, 상한, 처음의 cell과 묶음을 정한다. cell의 분할과 묶음은 수행 중에 바뀔 수 있고 그때마다 사건으로 남는다.
- 1계층은 과업의 큰 범주(의도, 계획, 이력, 통합 검증과 수용)를 담는다. 항목별 상태와 사건은 여기에 두고 1계층 문서에 옮겨 적지 않는다.

## 구조

```
campaigns/
├─ README.md           이 문서 (추적)
├─ _template/          뼈대 (추적)
├─ ACTIVE              활성 인스턴스의 이름 한 줄 (비추적)
└─ <camp-id>/          인스턴스 (비추적)
   ├─ campaign.json    선언
   ├─ cells/<cell>/
   │   ├─ cell.md      이 cell의 현재 상태 요약
   │   └─ phases/{prepare,change}.status.json
   ├─ gates/<group>.status.json   gate의 상태와 검사별 결과. _final은 마지막 전체 검증
   ├─ receipts/<검사 id>/<n>.json 검사를 실행할 때마다 하나. invalidated.json은 무효 표시
   ├─ logs/            실행기가 받은 검사와 증명 명령의 출력
   ├─ journey.jsonl    사건 기록 (추가만)
   ├─ evidence_pointers.json   근거의 위치
   ├─ briefing.md      최신 브리핑 하나 (고쳐 쓴다)
   ├─ monitor.json     종료 훅의 카운터
   ├─ activation.json  이 캠페인이 켠 선택 수단 스킬의 기록
   └─ .lock            실행기의 잠금
```

| 파일 | 담는 것 | 누가 쓰는가 |
|---|---|---|
| `campaign.json` | 목표, 연결된 plan·checklist, 대상, 검사, 묶음, 상한, cell 목록 | plan에 따라 손으로 쓴다. 수행 중의 cell 추가·취소·묶음 재편과 cell의 상태는 실행기가 쓴다. 검토 표시·상한·검사 기준은 사용자 승인 없이 바꾸지 않는다 |
| `cell.md` | 목표, 확인된 사실, 적용한 변경, 검증, 막다른 길, 남은 계획 | 그 단계의 수행자가 단계를 끝낼 때 현재 상태로 고쳐 쓴다 |
| `phases/*.status.json` | 단계의 상태와 증명 | 실행기가 쓴다. `proof.check`와 `proof.expect`만 미리 손으로 적는다 |
| `gates/`, `receipts/`, `logs/` | gate의 결과, 검사 실행의 영수증, 원시 출력 | 실행기가 쓴다 |
| `journey.jsonl` | 사건: 단계 전이, 검사, gate, 구조 변경, 검토, 사람에게 넘김, 정체 | 실행기가 줄을 추가한다. 기존 줄을 고치지 않는다 |
| `evidence_pointers.json` | 원시 근거와 문서의 경로 | 메인 에이전트가 추가한다 |
| `briefing.md` | 최신 브리핑 | 브리핑 작성자(새 맥락의 서브에이전트) |
| `activation.json` | 이 캠페인이 고른 선택 수단 스킬(`requested`), 그중 이 캠페인이 켠 것(`enabled`)과 이미 켜져 있던 것(`pre_existing`), 상태(`open`/`closed`), 닫을 때 끈 것(`turned_off`)과 사람이 이미 꺼 둔 것(`left_as_changed`) | `open --skills`·`close` 때 `scripts/skill_activation.py`가 쓴다. 손으로 고치지 않는다 |

실행기는 `python3 skills/progress-monitor/scripts/pm.py`다. `ACTIVE`에는 활성 인스턴스의 이름 하나만 적는다. 없으면 활성 캠페인이 없는 것이다.

실행기와 lint는 신뢰 경계가 아니다. 파일의 소유자가 손으로 고쳐 상태를 꾸미는 것을 막지 못한다.

## 다시 시작할 때 읽는 순서

대화 기록을 찾지 않는다. 다음 순서로 읽는다.

1. `python3 skills/progress-monitor/scripts/pm.py status`의 출력: cell·단계·gate의 상태, 스스로 할 수 있는 일, 사람을 기다리는 결정.
2. 다음에 할 cell의 `cell.md`. "남은 계획"이 다음 행동이고 "막다른 길"은 다시 시도하지 않는다.
3. 더 필요할 때만 `journey.jsonl`의 마지막 몇 줄과 근거 포인터가 가리키는 원문.

`cell.md`만 읽고 그 cell을 이어갈 수 없다면 `cell.md`가 모자란 것이다. 고쳐 쓴다.

## 캠페인을 여는 법

1. `_template`을 `campaigns/<camp-id>/`로 복사한다.
2. `campaign.json`의 자리표시(`<<FILL>>`)를 plan대로 채운다: 대상, 검사, 묶음, 마지막 전체 검증의 검사, 상한, cell.
3. `cells/_cell`을 cell마다 `cells/<cell>/`로 복사하고 `cells/_cell`을 지운다.
4. 각 cell의 `cell.md`와 단계 파일을 채운다. 단계 파일에는 `cell`, `proof.check`, `proof.expect`를 미리 적는다.
5. `ACTIVE`에 인스턴스 이름을 적는다.
6. 사용자에게 브리핑 설정 두 가지를 묻는다: 단계가 전환될 때 브리핑을 받을지(기본 받음), 몇 시간마다 받을지(기본 없음).
7. 저장소에 활성화 knob(`skill-activation.yaml`)이 있으면 이 캠페인이 켤 선택 수단 스킬을 사용자에게 묻는다. `open`을 `--skills` 없이 부르면 후보와 지금의 on/off를 보여 주고 hitl(`skill-selection`)을 남긴 채 `selection-required`로 멈춘다. 그 후보를 사용자에게 보이고 답을 받는다.
8. `python3 skills/progress-monitor/scripts/pm.py open [--briefing-on-phase true|false] [--briefing-every-hours <수>] [--skills <a,b>|none]`를 부른다. `--skills`로 고른 스킬 중 꺼져 있던 것만 켜고 `activation.json`에 기록한다. 아무것도 켜지 않으려면 `--skills none`을 쓴다. 다른 캠페인의 기록이 열려 있으면 거절하고 닫는 방법을 알려 준다. 브리핑 답이 없었으면 브리핑 인자는 빼고 부른다.
9. `python3 scripts/check_campaign.py campaigns/<camp-id>`가 통과하는지 확인한다.

## 채우는 규칙

- **모르는 값을 그럴듯하게 채우지 않는다.** 정해지지 않았으면 그 사실을 사용자에게 올린다.
- **검사는 실패할 수 있어야 한다.** 검사의 `cmd`와 단계의 `proof.check`는 결과를 직접 읽는 명령으로 쓴다. `expect`는 모든 확인이 통과했을 때만 나오는 표식으로 쓴다. 고정 문구를 찍는 명령은 검사가 아니다.
- **단계의 증명은 가볍게 한다.** `proof.check`는 빌드나 정적 확인 같은 자기 확인이다. 묶음의 검사를 여기서 미리 돌리지 않는다.
- **입력은 넓게 선언한다.** 검사의 `inputs`에는 결과를 바꿀 수 있는 경로를 모두 적는다. 빠뜨린 입력은 잘못된 재사용이 된다.
- **상태는 실행기가 쓴다.** 단계의 `state`, `proof.ok`, `proof.source`, `by`, gate 기록, 영수증을 손으로 쓰지 않는다. 실패해도 실행기가 상태 파일을 쓴다. 파일이 없는 것과 실패한 것은 다른 사실이다.
- **근거는 복사하지 않는다.** 원시 로그와 문서는 원래 자리에 두고 `evidence_pointers.json`에 경로만 적는다. 근거 포인터에는 해시를 적지 않는다. 영수증에 있는 Git 객체 ID와 키의 SHA-256은 다시 쓸 입력이 같은지 가르는 값이며 정답의 증명이 아니다.
- **사건은 `journey.jsonl`에만 적는다.** `cell.md`에는 현재 상태만 둔다. 같은 사건을 여러 파일에 되풀이해 적지 않는다.
- **`cell.md`는 짧게 유지한다.** 항목이 비면 "없음"이라고 적는다.

## 파일의 형식

### campaign.json

내용이 JSON이다. `_`로 시작하는 키는 사람을 위한 안내문이다. `schema_version`은 2다.

| 키 | 뜻 |
|---|---|
| `id` | 인스턴스 이름. 폴더 이름과 같게 한다 |
| `goal` | 캠페인의 목표 한 문장 |
| `plan_ref`, `checklist_ref` | 1계층 plan과 checklist의 저장소 상대 경로 |
| `declared` | 선언한 시각 (시간대를 포함한 ISO 8601) |
| `target.root` | 검사가 읽는 대상 Git 저장소. 절대 경로이거나 `campaigns/` 폴더의 부모를 기준으로 한 상대 경로다 |
| `target.env_probe` | 실행 환경을 가르는 명령의 목록(예: 컴파일러 버전). 출력이 영수증의 키에 들어간다 |
| `phases` | cell이 밟는 단계. 기본은 `prepare`, `change`다. 검증은 단계가 아니다 |
| `review.round_cap` | 새 맥락 검토의 회차 상한. 기본 3. 3보다 크게 적으려면 plan에 사용자 승인을 남긴다 |
| `budgets` | 상한(아래 표). plan에서 승인받은 값으로 적는다 |
| `briefing` | `on_phase`(단계 전환 브리핑), `every_hours`(시간 단위, 없으면 `null`). `open`이 쓴다 |
| `checks[]` | 검사의 선언(아래) |
| `groups[]` | 검증 묶음. `group`(이름), `checks`(gate에서 돌릴 검사의 id) |
| `final.checks` | 캠페인을 닫기 전에 gate의 결과를 재사용하지 않고 돌릴 검사 |
| `cells[]` | `cell`(폴더 이름), `goal`, `group`, `needs`(먼저 끝나야 하는 cell), `review`, `origin`, `parent`, `state` |

**`checks[]`**

| 키 | 뜻 |
|---|---|
| `id` | 검사의 이름 |
| `cmd`, `cwd` | 명령과 실행 폴더(대상 루트 기준). 종료 코드 0이고 출력에 `expect`가 있으면 통과다 |
| `expect` | 통과했을 때만 출력에 나오는 표식 |
| `inputs` | 이 검사의 결과를 바꿀 수 있는 경로(대상 루트 기준). 대상 저장소가 커밋한 파일이나 폴더여야 한다. glob은 쓰지 않는다. 비었거나 객체 ID를 얻을 수 없으면 매번 실행한다 |
| `env` | 결과를 바꿀 수 있는 환경 변수의 이름 |
| `covers` | 이 검사가 포함하는 더 좁은 검사의 id |
| `timeout_s` | 이 검사의 시간 제한. 없으면 `budgets.check_timeout_s` |

**`budgets`**

| 키 | 기본값 | 뜻 |
|---|---|---|
| `stall_stops` | 2 | 상태 변화 없는 종료 시도가 연속 몇 회면 정체인가 |
| `stall_retries` | 1 | 정체한 일을 다시 시도하는 횟수 |
| `stop_block_cap` | 6 | 상태 변화 없이 종료 훅이 연속으로 막는 상한 |
| `phase_minutes` | 90 | 단계 하나의 시간 예산 |
| `check_timeout_s` | 1800 | 검사 하나의 시간 제한 |
| `exec_retries` | 2 | 같은 입력에서 실행의 실패를 다시 돌리는 횟수 |
| `forced_reruns_per_check`, `forced_reruns_total` | 1, 3 | 강제 재실행의 상한 |
| `auto_cells` | 5 | 수행 중에 스스로 만드는 cell의 상한 |
| `explore_candidates`, `explore_rounds` | 3, 6 | 후보를 실제로 실행하는 방향 조정의 후보 수와 횟수 |

**`cells[]`의 값**

| 키 | 값 |
|---|---|
| `review` | `none`(검사만으로 닫는다), `user-request`(사용자가 독립 검토를 요청했다), `high-impact`(권한·데이터 손실·외부 변경처럼 영향이 크다), `judgment`(명령 하나로 정답을 가를 수 없다) |
| `origin` | `plan`(캠페인을 열 때 있던 것), `auto`(수행 중에 만든 것) |
| `parent` | 이 cell이 갈라져 나온 cell. 없으면 `null` |
| `state` | `open`, `closed`, `cancelled`, `stopped`(사람에게 넘김), `waiting`(사람의 답을 기다림) |

### 단계 상태 파일

`state`는 `pending`, `running`, `done`, `failed` 중 하나다. `attempt`는 시도 번호다. `proof`는 `check`, `expect`, `ok`, `source`를 가진다. `by`는 실행기의 기록 `{"tool": "progress-monitor", "log": "<logs/ 아래 경로>", "executor": "<실행자>"}`다. `by`가 없으면 `done`이 될 수 없다.

### gate 기록

`gates/<group>.status.json`. `_final`은 마지막 전체 검증이고 `members`가 없다.

```json
{"schema_version": 2, "group": "g-alpha", "state": "passed", "runs": 1,
 "started": "...", "ended": "...", "members": ["a-rename", "a-edge"], "hitl": null,
 "checks": [{"check": "alpha-suite", "outcome": "ran", "result": "PASS",
             "receipt": "receipts/alpha-suite/1.json", "via": null},
            {"check": "alpha-unit", "outcome": "covered", "result": null,
             "receipt": null, "via": "receipts/alpha-suite/1.json"}]}
```

- `state`: `pending`, `passed`, `failed`, `waiting`.
- `members`: 통과했을 때의 구성원.
- `outcome`: `ran`(실행함), `forced`(강제로 다시 실행함), `error`(실행이 실패함), `reused`(같은 입력의 통과를 다시 씀), `covered`(넓은 검사의 통과로 덮임), `refused`(거절함). `covered`의 `via`는 덮은 영수증이다.

### 영수증

`receipts/<검사 id>/<n>.json`. 검사를 실제로 실행할 때마다 하나 생긴다.

```json
{"schema_version": 2, "check": "alpha-suite", "n": 1, "scope": "gate",
 "key": {"cmd": "...", "cwd": ".", "expect": "...", "env": {"NAME": "값"},
         "inputs": {"alpha": "<Git 객체 ID>"}, "host": "...", "env_probe": {"<명령>": "<출력>"}},
 "key_sha256": "...", "reusable": true,
 "result": "PASS", "exit_code": 0, "error": null, "log": "logs/alpha-suite-1.log",
 "started": "...", "ended": "...", "seconds": 1.2, "executor": "...",
 "forced": false, "reason": null, "target": "<실행한 작업 트리>", "explore": null,
 "covers": {"alpha-unit": {"inputs": {"alpha": "<Git 객체 ID>"}}}}
```

- 키가 같으면 같은 입력이다. 실행자(`executor`)는 적지만 키에 넣지 않는다.
- `scope`: `gate`, `final`, `explore`. `result`: `PASS`, `FAIL`, `ERROR`(실행의 실패). `error`: `timeout`, `not-found`, `signal`(종료 코드 129~192 포함), `spawn` 또는 `null`.
- `reusable`이 거짓인 영수증(입력이 비었다, 커밋되지 않았다, 대상이 Git 저장소가 아니다, 입력의 Git 객체 ID를 얻을 수 없다, 검사가 도는 동안 입력이 바뀌었다)은 다시 쓰지 않는다.
- `covers`: 이 검사가 덮는 검사마다 실행 직전의 그 검사의 입력. 그 입력이 커밋되지 않았으면 적지 않는다.
- `receipts/<검사 id>/invalidated.json`은 `{"utc": "...", "reason": "..."}`다. 그 시각보다 앞선 그 검사의 영수증과 덮음은 쓰지 않는다.

### journey.jsonl

한 줄이 JSON 객체 하나다. `utc`(시간대를 포함한 ISO 8601)와 `event`가 필수다. 시각은 뒤로 가지 않는다. 실행기가 쓰는 사건은 다음과 같다. ★ 표시한 사건은 lint가 지금의 구성과 맞춰 본다.

| `event` | 필드 |
|---|---|
| `campaign-open` ★ | `cells`(`[{"cell", "group"}]`), `groups`, `briefing`, `briefing_source`(`user` 또는 `default`), 스킬을 골랐으면 `skills`(`requested`, `enabled`, `pre_existing`) |
| `campaign-close` | `reason`, `final`(마지막 전체 검증의 상태), 기록이 있었으면 `skills`(`turned_off`, `left_as_changed`) |
| `phase-start`, `phase-retry` | `cell`, `phase`, `attempt` |
| `phase-close` | `cell`, `phase`, `ok`, `log` |
| `check` | `check`, `outcome`, `scope`, `receipt`, `reason` |
| `gate`, `final` | `group`(gate만), `state`, `checks`, `seconds` |
| `gate-reopen` | `group`, `reason` |
| `cell-add` ★ | `cell`, `group`, `parent`, `review`, `reason`, 묶음을 새로 만들었으면 `group_checks` |
| `cell-cancel` ★ | `cell`, `reason` |
| `regroup` ★ | `cell`, `from`, `to`, `reason`, 묶음을 새로 만들었으면 `group_checks` |
| `cell-reopen`, `cell-stop` | `cell`, `reason` |
| `cell-resume` | `cell`, `ref` |
| `cell-close` | `cell` |
| `review` | `cell`, `round`, `ran`, `blocking`, `non_blocking`, `clean` |
| `hitl` | `id`, `kind`, `question`, 대상에 따라 `cell`·`group`·`final`·`check`·`cap` |
| `hitl-answer` | `id`, `ref`, `allow` |
| `invalidate` | `check`, `reason` |
| `explore-open` | `id`, `cell`, `reason` |
| `direction` | `id`(탐색을 했으면), `cell`, `selected`, `ref`, `note` |
| `hang` | `item`, `count`, `directive`(`retry` 또는 `bypass`) |
| `monitor-release` | `blocks` |
| `brief` | `path` |

그 밖의 사건(원인 판정, 선언의 수정 등)은 실행기의 `event` 명령으로 남긴다.

### 그 밖

- `evidence_pointers.json`의 `pointers[]` 항목은 `kind`, `path`, `cell`, `note`만 가진다. `path`는 저장소 루트 기준 상대 경로이거나 절대 경로다.
- `monitor.json`은 종료 훅의 카운터다: 묶인 세션, 직전 상태의 지문, 변화 없는 시도와 막은 횟수, 항목별 정체 횟수.

## 사람의 확인 없이 이어 가기

2계층은 오래 걸리는 작업을 자율적으로 이어 가기 위한 것이다. 자세한 규칙은 progress-monitor 스킬에 있다.

- 단계나 검토 회차마다 사람의 확인을 기다리지 않는다.
- 한 cell이 멈추면 그 cell과 같은 검증 묶음만 멈춘다. 다른 묶음의 cell을 계속한다.
- 멈춘 cell과 사람을 기다리는 결정은 `journey.jsonl`에 사유와 함께 남고, 브리핑의 맨 위에 모아서 올린다.
- 사용자 승인이 필요한 것은 작업수행규칙이 정한 것(목표·수용 기준·권한의 변경, 외부 발행, 파괴적 변경), 검사 기준과 상한·검토 표시의 변경, 아래의 검토 상한이다.

## 새 맥락 검토

`review`가 `none`이 아닌 cell은 그 묶음의 gate가 통과한 뒤, cell을 닫기 전에 새 맥락 검토를 받는다. 검토자는 호스트의 서브에이전트 기능으로 띄우며 대화 이력을 받지 않는다. 수행 중에 생긴 cell은 부모의 표시와 회차를 물려받고, 부모가 없으면 표시가 없다.

**검토자가 읽는 것**

- 그 cell의 폴더와 `campaign.json`.
- 그 cell이 바꾼 파일.
- 그 묶음의 gate 기록과 영수증, 근거 포인터가 가리키는 원문.
- 연결된 1계층 plan과 checklist.
- `journey.jsonl`에서 그 cell의 이전 검토 사건.

**검토자가 내놓는 것**

지적 목록이다. 지적마다 차단인지 비차단인지, 근거의 위치, 차단이면 어긴 기준의 위치(plan 절, checklist 항목, 규칙 줄), 다시 확인할 명령을 적는다. 검토자는 파일을 고치지 않는다.

**끝내는 규칙**

상한은 채울 목표가 아니다. 정본은 [작업수행규칙](../rules/workflow.md)의 "검토의 종료"이고, cell에서는 다음과 같다.

- 차단은 완료 기준·보존 계약·권한을 어기거나 결과가 틀린 경우다. 그 밖은 비차단이고, 어긴 기준의 위치나 재현(`repro`)이 빠진 지적도 비차단이다.
- 차단 지적이 없으면 그 회차로 닫는다. 비차단 지적은 기록만 하고 캠페인 안에서 고치지 않는다. 브리핑의 잔여로 올린다.
- 차단 지적을 고친 뒤에만 다음 회차를 연다. 고치려면 `cell reopen`으로 다시 열고, gate가 다시 통과한 뒤에 검토를 받는다.
- 2회차부터 검토자는 하나이고(호스트가 정할 수 있으면 추론 강도를 낮게), 앞 회차 차단 지적의 해소와 그 수정이 바꾼 줄의 회귀만 본다. 새 결함을 찾으라고 맡기지 않는다. 늦게 발견한 다른 문제는 비차단으로 남겨 브리핑의 잔여로 올린다.
- 1회차의 검토자 수와 도구는 plan이 사유와 함께 정한다. 정하지 않았으면 하나다. 검토자에게는 차단을 먼저, 비차단은 다섯 건까지만 요청한다.
- 회차는 cell마다 센다. 차단 없이 닫힌 cell은 같은 묶음의 다른 cell이 다음 회차로 가도 다시 검토하지 않는다.
- 결론이 난 지적은 다시 열지 않는다. 어떤 지적이 결론 났는지는 메인 에이전트가 `journey.jsonl`의 검토 사건으로 관리하고 검토자에게 알려 준다.
- 검토자가 실행되지 않았으면 통과로 세지 않는다.
- 회차가 `review.round_cap`에 닿았는데 차단 지적이 남거나, 같은 지적과 수정이 새 증거 없이 되풀이되면 그 cell을 멈추고 사람에게 넘긴다. 통과로 치지 않는다.
- 사람에게 넘긴 cell의 대상을 새 cell이나 새 인스턴스로 다시 선언해 회차를 되돌리지 않는다. 다시 열려면 사용자 승인이 필요하다.

**검토 사건의 기록**

검토 결과는 실행기의 `review record <cell> <review.json>`으로 남긴다. 입력은 `round`, `ran`, `blocking`, `non_blocking`, `clean`이다.

```json
{"round": 1, "ran": true, "blocking": [{"id": "r1-1", "where": "src/parser.rs:88", "repro": "cargo test parse_overflow", "settled": false}], "non_blocking": [], "clean": false}
```

## 검사

```bash
python3 scripts/check_campaign.py                         # 틀을 검사한다
python3 scripts/check_campaign.py campaigns/<camp-id>     # 인스턴스를 검사한다
python3 scripts/check_campaign.py --list-control          # 규칙별 자체검사 결과를 본다
```

검사는 파일을 읽기만 한다. 선언한 검사 명령을 실행하지 않는다. 종료 코드는 통과 0, 규칙 위반 1, 사용법 오류나 해석할 수 없는 입력 2다. `schema_version`이 2가 아닌 선언은 해석할 수 없는 입력이다. 실행할 때마다 자체검사를 먼저 돌린다. 실제 틀로 정상 예제를 만들어 통과를 확인하고, 결함을 하나씩 심은 사본이 그 규칙으로만 실패하는지 본다.

| 규칙 | 실패하는 경우 | 적용 |
|---|---|---|
| `proof-blank` | `proof.check`나 `proof.expect`가 비었다 | 틀, 인스턴스 |
| `source-missing` | `state`가 `done`·`failed`인데 `proof.source`가 비었다 | 틀, 인스턴스 |
| `state-invalid` | `state`가 허용값이 아니다 | 틀, 인스턴스 |
| `done-not-ok` | `state`가 `done`인데 `proof.ok`가 참이 아니다 | 틀, 인스턴스 |
| `undeclared` | 선언에 없는 phase 파일이나 cell 폴더가 있다 | 틀, 인스턴스 |
| `cell-summary-incomplete` | `cell.md`의 여섯 항목 중 없거나 빈 것이 있다 | 틀, 인스턴스 |
| `journey-invalid` | 줄이 JSON 객체가 아니다, `utc`·`event`가 없다, 시각이 뒤로 간다 | 틀, 인스턴스 |
| `pointer-invalid` | 허용 밖의 키가 있다, `kind`·`path`가 없다, 경로가 없다 | 틀, 인스턴스 |
| `declared-missing` | 선언한 cell의 폴더, `cell.md`, phase 파일이 없다 | 틀(뼈대 cell), 인스턴스 |
| `placeholder-left` | 자리표시나 `cells/_cell`이 남았다 | 인스턴스 |
| `no-cells` | cell이 하나도 없다 | 인스턴스 |
| `done-unrecorded` | 단계가 `done`인데 실행기의 기록(`by`)이 없거나 거기 적힌 로그가 없다 | 인스턴스 |
| `closed-without-gate` | cell이 `closed`인데 그 묶음의 gate가 `passed`가 아니거나 gate 기록의 구성원에 없다 | 인스턴스 |
| `structure-unlogged` | `campaign-open`의 구성에 그 뒤의 `cell-add`·`cell-cancel`·`regroup` 사건을 차례로 적용한 것이 지금의 구성과 다르다. `campaign-open` 사건이 없으면 적용하지 않는다 | 인스턴스 |
| `auto-cap-exceeded` | `origin`이 `auto`인 cell이 `budgets.auto_cells`에 사용자가 허용한 수를 더한 것보다 많다 | 인스턴스 |
| `check-duplicate` | 검사의 `id`가 겹치거나, 명령·실행 폴더·환경 변수가 모두 같은 검사가 둘 이상이다 | 인스턴스 |
| `ref-invalid` | cell의 묶음, 묶음과 마지막 검증의 검사, `covers`, `parent`, `needs`가 선언에 없는 것을 가리킨다 | 인스턴스 |
| `gate-not-proven` | gate가 `passed`인데 검사별 결과에 통과·재사용·덮임이 아닌 것이 있거나, 가리키는 영수증이 없거나, 선언한 검사의 결과가 빠졌다 | 인스턴스 |

**검사가 보지 않는 것.** 다음은 규약이며 이 검사가 강제하지 않는다.

- `journey.jsonl`의 기존 줄을 고쳤는지.
- 검사와 증명 명령이 실제로 실패할 수 있는 명령인지, 영수증이 사실인지. lint는 명령을 실행하지 않는다.
- 덮음 선언이 옳은지(넓은 검사가 정말 좁은 검사를 포함하는지).
- 검토 회차가 상한 안인지, 검토 표시가 수행 중에 바뀌었는지.

## 끝낼 때

- 모든 묶음이 통과하거나 멈추거나 취소되면 마지막 전체 검증을 돌리고, 그 결과를 1계층 checklist에 통합 검증으로 적는다. 멈춘 cell과 gate에서 확인되지 않은 검사는 그 상태 그대로 적는다.
- `python3 skills/progress-monitor/scripts/pm.py close`로 닫는다. 마지막 전체 검증이 통과하지 않았으면 `--reason <사유>`가 있어야 한다. 이 캠페인이 켰고 지금도 켜진 선택 수단 스킬만 끄고(사람이 중간에 바꾼 값은 그대로 둔다) `ACTIVE`를 지운다.
- 끝난 인스턴스는 로컬에 남긴다. `ACTIVE`를 손으로 지웠다면 `python3 scripts/skill_activation.py campaign-close <camp-id>`로 그 캠페인이 켠 스킬을 끈다. 열린 기록이 남아 있으면 `python3 scripts/skill_activation.py check`가 실패한다.
- 인스턴스와 `ACTIVE`는 Git이 추적하지 않는다. 추적하는 것은 이 문서와 `_template`뿐이다.

## 출처

이 구조는 프로젝트의 독립 구현이다. 다음의 방법을 참고했고 코드는 가져오지 않았다.

- 개선 대상 프로젝트의 캠페인 아티팩트 체인: 선언, cell과 phase, 추가만 하는 여정, 근거 포인터.
- Elephant-Goldfish Model(Apache-2.0): 대화 이력 없이 문서만 읽는 검토자와 검토를 끝내는 규칙.
- unlazy(MIT): 결과 하나에 검사 명령·기대값·근거를 두는 원장과, 그 원장을 읽기만 하는 검사.
- "You don't need long context"(arXiv 2611.05306): 짧은 상태 기록과 원본의 선택적 읽기, 단계마다 새 맥락으로 넘기는 인계. `cell.md`의 여섯 항목은 이 논문 부록의 인계 양식을 따랐다.
- 빌드 도구의 결과 재사용(입력이 같으면 다시 실행하지 않는다)과 영향 기반 검사 선택의 일반적인 방법: 영수증의 키와 덮음 선언.
