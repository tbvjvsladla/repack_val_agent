# 실험 계약과 CLI

Node 24+ 기본 모듈만 사용한다. SKILL.md와 src/를 함께 복사하면 실행에 seed·외부 패키지가 필요 없다. 모든 명령은 JSON stdout, 잘못된 입력/identity/기존 기록 덮어쓰기는 JSON stderr와 종료 2다. WITHHELD는 유효한 판단 결과로 종료 0이며 PASS를 뜻하지 않는다.

아래 파일 경로는 **명령을 호출한 작업 디렉터리 기준**이다. 이동 후 재개까지 필요하면 계약/입력에 절대경로를 사용한다. 기준선·후보는 변경하지 않는 파일(예: diff 또는 산출물 manifest)로 제공한다. 살아 있는 작업 파일을 참조한 채 수정하지 않는다. CLI는 후보 파일의 실제 코드/패치 적용 여부를 판정하지 않는다.

## 초기화

`node <skill>/src/cli.mjs init <run> contract.json`

```json
{
  "task_id": "task-1",
  "experiment_id": "experiment-1",
  "evaluation_id": "criteria-v1",
  "source_id": "baseline-revision-and-input-v1",
  "owner_id": "main",
  "evaluator_id": "external-validator",
  "goal": "같은 업무 신뢰성을 보존하면서 결정론 구간을 개선한다",
  "evidence_mode": "fixture",
  "scope": ["src", "instructions.md"],
  "required_checks": ["behavior", "handoff"],
  "evaluation_spec": "frozen-evaluation.md",
  "cases": [{"id": "normal", "comparison_key": "input-v1-env-v1-profile-v1-metric-boundary-v1"}],
  "baseline": {"id": "base", "artifact": "baseline.patch"},
  "max_attempts": 12
}
```

`fixture`는 자산/프로토콜 검사용 입력이다. 실제 관측에는 `observed`를 쓰되 그 표기만으로 진실성이 보장되지 않는다. comparison_key에는 입력·환경·profile·측정 경계 등 실제 비교에 필요한 정체성을 연결하고 원시 근거로 입증한다. 필수 검사의 기대 결과는 초기화 전에 evaluation_spec 파일에 호출자가 고정한다. 이 파일도 기준선·근거와 함께 identity를 캡처하고 변경 시 재사용을 거부한다. 엔진/전략 선택 자체가 탐색이면 선택 결과를 억지로 같게 하지 말고 허용 탐색 범위를 맞춘다. checks ID만으로 의미상 기대 결과가 정의되지는 않으며 실제 판정 내용·명세의 타당성은 호출자가 확인한다.

상태는 `<run>/experiment.json`에 원자적으로 저장한다. 한 실행의 동시 쓰기는 `.lock`으로 배제한다. busy 오류는 실행 중 명령 뒤 재시도한다. 비정상 종료 뒤 남은 lock은 주체가 멈췄는지 확인한 뒤 호출자가 처리하며 자동 삭제하지 않는다. 실제 실행 예산/승인 범위와 외부 실행 중단은 호출자가 관리한다. max_attempts는 이 실험에 등록하는 요청 수의 한도다.

## 후보와 조합

`propose <run> proposal.json`

```json
{
  "id": "c1", "parents": ["base"], "author_id": "builder",
  "artifact": "candidate-1.patch",
  "hypothesis": "중복 파싱을 줄이되 출력/오류/인계를 보존할 수 있다",
  "changes": [{"path": "src/parser.rs", "description": "같은 입력의 파싱 결과 재사용"}]
}
```

changes 경로는 계약 scope의 파일 또는 하위 경로여야 한다. 절대경로·..·빈 segment를 거부한다. 선언의 검사이며 실제 패치 내용에 대한 sandbox/코드 검토가 아니다. 둘 이상 부모인 조합은 `contrast_ids` 배열이 필요하며 연결한 대조 시도들이 모든 부모를 다뤄야 한다. 부모는 이미 존재해야 하므로 계보 순환을 만들 수 없다.

## 실행 요청·관측·평가

`request <run> request-input.json`

```json
{"attempt_id": "c1-normal-1", "candidate_id": "c1", "case_id": "normal"}
```

stdout의 identity를 그대로 실행기에 전달한다. case 객체에는 호출자가 추가한 공개 과업 입력·역할·프로필 등도 그대로 전달된다. 비공개 평가 정답·판정 파일은 case에 넣지 않는다. 평가 명세의 경로/본문은 기본 실행 요청에 포함하지 않지만, 실제 파일 접근 통제는 호출자 환경의 책임이다. 요청은 실행 전부터 시도 분모에 포함된다. 요청 ID를 재등록하거나 receipt를 덮어쓸 수 없다. 재시도는 새 attempt_id로 등록하며 case의 최신 시도가 선택 근거가 된다. max_attempts까지 실행하지 않아도 선택할 수 있지만, 등록한 최신 시도가 미완료면 앞선 성공을 골라 채택하지 않는다.

`observe <run> execution-receipt.json`

```json
{
  "identity": "여기를 request stdout의 identity 객체로 대체",
  "status": "PASS", "executor_id": "worker",
  "comparison_key": "input-v1-env-v1-profile-v1-metric-boundary-v1",
  "evidence": ["execution.log"],
  "metrics": {"compute_ms": 12.5, "wall_ms": 30, "cost_usd": null},
  "note": "어떤 경계와 방법으로 측정했는지 원시 근거에 기록"
}
```

status는 PASS/FAIL/NOT_RUN/NOT_APPLICABLE/STALE. 실행 receipt의 PASS는 실행기 보고이며 필수 검사 PASS를 대신하지 않는다. 지원 metric은 compute_ms/wall_ms/cost_usd/input_tokens/output_tokens이고 값은 유한한 0 이상 또는 null이다. 미관측은 생략/null로 유지한다. 단위와 계층은 고정하며 부모/자식의 포괄 비용을 중복 합산하지 않는다. 측정·외부 판정 실패는 FAIL/NOT_RUN과 note·원시 로그로 남긴다.

`evaluate <run> evaluation-receipt.json`

```json
{
  "identity": "같은 request의 identity 객체",
  "evaluator_id": "external-validator",
  "checks": {"behavior": "PASS", "handoff": "PASS"},
  "evidence": ["independent-check.log"]
}
```

평가자는 계약의 evaluator_id와 같고 실행자 ID와 달라야 한다. 실제 역할/주체는 호출자가 입증한다. 누락 check는 NOT_RUN이며 필수 check의 NOT_APPLICABLE은 PASS가 아니다. 실행과 평가 양쪽에 실제 존재하는 원시 파일 근거가 필요하다. 존재하지 않는 파일은 missing으로 기록되어 성공 자격을 주지 않는다. 나중에 파일이 생겨도 기존 missing receipt는 승격하지 않는다. 새 시도와 새 근거로 수정한다. 파일 내용의 hash는 재사용 identity 검사이며 의미상 정확성·권한·실행 사실의 증명은 아니다.

## 대조·선택·회고

`contrast <run> contrast.json`

```json
{"id": "contrast-1", "attempt_ids": ["base-normal-1", "c1-normal-1"], "observations": ["원시 로그에서 오류 계약은 같고 중복 파싱 감소 가능성을 관측"]}
```

대조는 성공/실패/미실행 상태를 함께 보존한다. 가설이며 정답/인과 증명이 아니다. 예를 들어 부모 base/c1을 조합하려면 위 대조를 `contrast_ids: ["contrast-1"]`로 연결한 새 후보를 등록하고 새 시도로 검증한다.

`select <run> selection.json`

```json
{"owner_id": "main", "candidate_id": "c1", "reason": "고정 신뢰성 검사와 관측을 대조한 실험 내 선택"}
```

모든 case의 최신 baseline/후보가 필수 검사·근거·비교 조건을 충족해야 SELECTED이며, 나머지는 WITHHELD다. baseline도 선택할 수 있다. 성능이 빠르다는 이유로 신뢰성 실패를 우회하지 않는다. 시간 비교는 해당 두 시도가 신뢰성 자격을 갖추고 같은 비교 조건에서 양쪽 compute_ms가 양수일 때 기술적 비율만 반환한다. metric이 없거나 비교 불가면 ratio=null이다. 이는 속도 향상의 통계적 검증이나 실제 대상 성능 판정이 아니다.

`reflect <run> reflection.json`

```json
{
  "cue": "반복 파싱이 관측된 경우",
  "lesson": "이 입력 범위에서는 캐시 후보를 검토할 수 있다",
  "applicability": "이 실험의 입력과 고정된 환경에 한정",
  "limitations": "fixture 검증; 실제 워커/서빙 성능 미평가",
  "attempt_ids": ["c1-normal-1"]
}
```

선택 보류에서도 실행 원시 근거가 있으면 실패의 교훈을 남길 수 있다. 미실행 요청만으로 경험을 만들지 않는다. 반환한 경험은 experiment.json에도 남고 HOST_REVIEW_REQUIRED다. 자동으로 영구 정책/스킬/다른 과업에 배포하지 않는다.

## 상태·재개와 한계

`status <run>` / `resume <run> contract.json`

status는 모든 시도 수·성공/실패/미실행/STALE 수·case별 최신 상태·관측 합계와 unknown 시도 수를 반환한다. FAIL 뒤 재시도 PASS가 있어도 실패는 분모와 관측 합계에 남는다. 실제 campaign 반복/지원/모델 전환 귀속은 이 입력을 생성하는 외부 실행기의 책임이다.

resume은 원래 계약 전체와 캡처한 후보/근거를 확인한다. contract 입력 불일치나 기존 파일 변경/유실 시 재사용을 거부한다. 조건 변경은 새 experiment/evaluation/source identity로 분리한다. 요청 후 관측/평가 전 상태는 그대로 재개할 수 있다. 외부 작업이 아직 실행 중인지·재접속 가능한지·다시 실행하면 중복 효과가 생기는지는 이 로컬 상태만으로 알 수 없다.

후속 시도나 근거 변경으로 기존 선택이 유효하지 않으면 status의 current_status는 STALE다. 저장 당시 선택·대조·회고의 이력은 지우지 않는다. 통제된 로컬 입력의 실수/혼합을 막는 도구이며, 소유자가 상태/ID/파일 전체를 위조하는 상황을 막는 신뢰 경계는 아니다.
