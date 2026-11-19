---
name: progress-monitor
description: "2계층 캠페인을 수행할 때 쓴다. 검증을 묶음의 gate에서만 한 번 돌리고(재사용·덮임·재실행 거절), 단계를 새 맥락의 수행자에게 넘기며, 정체를 알아채 우회하고, 수행 중의 방향 조정과 사람에게 넘김을 기록한다. 캠페인이 없는 과업에는 쓰지 않는다."
metadata:
  runtime: "Python 3 표준 라이브러리, Git"
---

# 진행 감시 (progress-monitor)

2계층 캠페인을 **꼭 필요한 검증만, 한 번만** 돌리면서 **멈추지 않고** 이어 가기 위한 운영 규칙과 실행기다. 캠페인을 열 때부터 닫을 때까지 이 스킬을 따른다.

- 파일의 구조와 형식의 정본은 `campaigns/README.md`다. 이 스킬은 **언제 무엇을 하는가**의 정본이다.
- 목표, 범위, 검사와 기준, 검토 표시, 상한은 과업의 plan이 정한다. 이 스킬은 그것을 바꾸지 않는다.
- 실행기는 수용을 판정하지 않는다. 수용은 과업의 checklist와 사용자가 한다.

## 지킬 것

1. **검증은 gate에서만 한다.** cell은 `prepare`와 `change`만 밟는다. 묶음의 검사는 구성원의 `change`가 모두 끝난 뒤 `gate run`으로 한 번 돌린다. 단계 안에서 묶음의 검사를 미리 돌리지 않는다.
2. **상태는 실행기가 쓴다.** 단계의 `done`, gate의 결과, 영수증, 사건을 손으로 쓰지 않는다. 실행기의 기록이 없는 `done`은 lint가 잡는다.
3. **통과한 검사를 다시 돌리지 않는다.** 같은 입력으로 통과한 검사는 재사용하고, 넓은 검사가 통과하면 좁은 검사는 덮임으로 닫는다. 같은 입력으로 실패한 검사는 다시 돌리지 않는다. 입력을 고친다.
4. **건너뛴 것은 통과가 아니다.** 재사용과 덮임은 근거가 된 영수증을 가리킨다. 실행하지 않은 검사를 통과했다고 적지 않는다.
5. **단계 하나는 새 맥락의 수행자 하나가 맡는다.** 메인은 주소(캠페인 폴더, cell, 단계)만 넘기고 몇 줄의 결과만 받는다. 수행자의 작업 내용을 메인의 맥락으로 옮겨 적지 않는다.
6. **멈추지 않는다.** 스스로 할 수 있는 일이 남아 있으면 사람의 확인을 기다리지 않고 이어 간다. 한 cell이 멈추면 그 cell과 같은 검증 묶음만 멈추고 다른 묶음을 계속한다.
7. **정체는 한 번 다시 시도하고 우회한다.** 같은 일을 세 번 붙들지 않는다.
8. **방향 조정은 스스로 하고 기록한다.** cell의 추가·취소·묶음 재편은 실행기 명령으로 한다. 목표·수용 기준·범위·검사 기준·상한·검토 표시·권한을 바꾸는 일은 사람에게 넘긴다.
9. **사람을 기다리는 동안 다른 일을 한다.** 넘긴 결정은 시한 없이 기다린다. 시간이 지났다는 이유로 스스로 정하지 않는다.
10. **마지막에 전체 검증을 한 번 돌린다.** 묶음 사이의 상호작용은 여기서 잡는다.

## 흐름

1. **연다.** 인스턴스를 만들고 `ACTIVE`를 적는다(`campaigns/README.md`). 사용자에게 브리핑 설정 두 가지와, 저장소에 활성화 knob이 있으면 이 캠페인이 켤 선택 수단 스킬을 묻고 `open`을 부른다([monitor.md](references/monitor.md)).
2. **단계를 돌린다.** `status`의 "스스로 할 수 있는 일"을 위에서부터 한다. 단계마다 새 수행자에게 수행자 지시문을 넘긴다([monitor.md](references/monitor.md)).
3. **gate를 돌린다.** 묶음의 구성원이 `change`를 모두 끝내면 `gate run`을 부른다. 실패하면 원인을 가려 그 cell을 `cell reopen`으로 다시 연다([gate.md](references/gate.md)).
4. **검토를 받는다.** 검토 표시가 있는 cell은 gate가 통과한 뒤 새 맥락 검토를 받고 `review record`, `cell close`로 닫는다. 검토의 종료 규칙은 `campaigns/README.md`에 있다.
5. **방향을 조정한다.** 일이 달라지면 `cell add`, `cell cancel`, `cell move`로 구성을 바꾼다. 후보가 둘 이상이면 실제로 실행해 비교한다([direction.md](references/direction.md)).
6. **마지막 전체 검증을 돌린다.** 모든 묶음이 통과하거나 멈추거나 취소되면 `final run`을 부른다.
7. **닫는다.** 결과를 과업의 checklist에 적는다. 멈춘 cell과 gate에서 확인되지 않은 검사는 그 상태 그대로 적는다. `close`를 불러 이 캠페인이 켠 스킬을 끄고 `ACTIVE`를 지운다.

세션을 이어받았으면 `status`를 먼저 부르고 `monitor reset`을 한 번 한다.

## 명령

`python3 <이 스킬의 경로>/scripts/pm.py [--campaigns <폴더>] [--executor <실행자>] <명령>`

저장소 루트에서 부른다. `--campaigns`의 기본값은 저장소의 `campaigns/`다(환경 변수 `PM_CAMPAIGNS_DIR`). `--executor`에는 실제 실행자(하네스, 모델)를 적는다(환경 변수 `PM_EXECUTOR`). 출력은 JSON이다. 종료 코드는 성공 0, 검사 실패나 거절 1, 사용법 오류나 읽을 수 없는 입력 2다.

| 명령 | 하는 일 |
|---|---|
| `open [--briefing-on-phase true\|false] [--briefing-every-hours <수>] [--skills <a,b>\|none]` | 선언을 확인하고 캠페인을 연다. 활성화 knob이 있는 저장소에서 `--skills`가 없으면 후보를 보이고 `selection-required`로 멈춘다 |
| `close [--reason <사유>]` | 이 캠페인이 켰고 지금도 켜진 선택 수단 스킬만 끄고 `ACTIVE`를 지운다. 마지막 전체 검증이 통과하지 않았으면 사유가 필요하다 |
| `status` | 상태, 스스로 할 수 있는 일, 기다리는 결정, `STALE`인 통과, 브리핑 때를 보인다 |
| `phase start\|close\|retry <cell> <phase>` | 단계를 시작한다, 증명 명령을 실행해 닫는다, 정체한 단계를 다시 시작한다 |
| `gate run <group> [--force <검사> --reason <사유>]` | 묶음의 검사를 규칙대로 돌린다 |
| `gate reopen <group> --reason <사유>` | 통과한 gate를 다시 연다 |
| `final run [--force <검사> --reason <사유>]` | 마지막 전체 검증을 돌린다 |
| `check invalidate <검사> --reason <사유>` | 그 검사의 지금까지의 영수증과 덮음을 무효로 한다 |
| `explore open --cell <cell> --reason <사유>` | 후보를 실제로 실행해 비교하는 방향 조정을 연다 |
| `check run <검사> --explore <id> --target <작업 트리>` | 후보의 작업 트리에서 검사 하나를 돌린다 |
| `explore close <id> --selected <후보> --ref <근거>` | 고른 결과를 사건으로 남긴다 |
| `cell add <spec.json>` | 새 cell을 만든다 |
| `cell cancel\|reopen\|stop <cell> --reason <사유>` | 취소한다, `change`를 다시 연다, 멈춘다 |
| `cell move <cell> <group> --reason <사유> [--checks <id,...>]` | 묶음을 옮긴다 |
| `cell resume <cell> --ref <근거>` | 사용자의 승인으로 멈춘 cell을 다시 연다 |
| `cell close <cell>` | 검토 표시가 있는 cell을 닫는다 |
| `review record <cell> <review.json>` | 검토 결과를 사건으로 남긴다 |
| `hitl open (--cell <cell> \| --group <group> \| --final) --kind <종류> --question <물음>` | 결정을 사람에게 넘긴다 |
| `hitl answer <id> --ref <근거> [--allow]` | 사용자의 답을 기록한다 |
| `event <json>` | 그 밖의 사건을 한 줄 남긴다 |
| `brief [--done <경로>]` | 브리핑에 쓸 사실을 낸다 |
| `metrics` | 검사 실행 수, 재사용·덮임 수와 비율, gate별 시간을 낸다 |
| `monitor reset` | 정체 카운터와 세션 묶음을 지운다 |
| `stop-hook --harness <이름>` | 하네스의 종료 훅에서 부른다([hooks.md](references/hooks.md)) |

## 참조 문서

필요한 것만 읽는다.

- [gate.md](references/gate.md): 검증 묶음, 영수증, 재사용과 덮임, 강제 재실행, 마지막 전체 검증, 상태와 전이.
- [monitor.md](references/monitor.md): 단계 위임과 인계, 지시문 틀, 정체, 브리핑.
- [direction.md](references/direction.md): 방향 조정과 사람에게 넘김.
- [hooks.md](references/hooks.md): 하네스별 종료 훅 설치.

## 한계

- 실행기와 lint는 신뢰 경계가 아니다. 파일의 소유자가 손으로 고쳐 상태를 꾸미는 것을 막지 못한다.
- 덮음 선언이 옳은지(넓은 검사가 정말 좁은 검사를 포함하는지)는 선언한 사람의 책임이다.
- 종료 훅은 메인 에이전트가 턴을 끝낼 때만 불린다. 세션이 죽었을 때 깨우는 장치는 없다.
- 내부 회귀 테스트: `python3 -B -m unittest discover -s <이 스킬의 경로>/tests -v`.
