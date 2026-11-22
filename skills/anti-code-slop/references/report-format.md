# 감사 보고서 형식과 판정 항목

## run 폴더

| 파일 | 쓰는 명령 | 내용 |
|---|---|---|
| `run.json` | snapshot | 호출 입력 그대로(대상, 범위, 허용 경로, 수정 유형, 보호 경로, 검사 명령, 위임, 삭제 승인, 기준 ref, 실행자) |
| `snapshot.json` | snapshot | 파일별 SHA-256, 기준 검사 결과, Git이 갖고 있는 파일(저장소·커밋·blob), 백업 목록, 해시 제외 폴더 |
| `scan_before.json`, `scan_after.json` | snapshot, verify | 신호 목록, 검사한 파일과 언어, 검사하지 않은 파일, 제외한 파일과 사유, 해석 실패 |
| `protected.json` | snapshot | 파일별 보호 주석(분류, 원문) |
| `backup/` | snapshot | Git이 수정 전 내용을 그대로 갖고 있지 않은 허용 경로 파일의 원본 |
| `checks/` | snapshot, verify | 검사 명령의 출력 마지막 64KiB |
| `reviews/<role>.json` | 메인 | 검토자 원 출력(`comment`, `structure`, `prose`, `diff`) |
| `report.json` | 메인 | 아래 형식의 감사 보고서 |
| `changes.json` | verify | 추가·삭제·변경 파일과 전후 해시. restore의 근거 |
| `check_outputs.json` | verify | 수정 후 검사가 대상에 쓴 파일과 검사 직전·직후 해시 |
| `verdict-<n>.json` | verify | 판정 결과. 덮지 않고 번호를 올린다 |
| `restore-<n>.json` | restore | 되돌린 파일과 거절 사유 |

해시 제외 폴더(`.git`, `target`, `node_modules`, `__pycache__`, `.venv`, `venv`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`)와 `*.pyc`는 해시·스캔·복구 모두에서 뺀다. `vendor`, `third_party`, `dist`, `build` 폴더, 생성물 표지(`@generated`, `DO NOT EDIT`)가 있는 파일, 바이너리, 2MB 초과, 잠금 파일, 대상 안에 설치된 이 스킬의 폴더(사유 `anti_code_slop_skill`)는 스캔에서만 뺀다(해시는 해서 범위 이탈을 본다). 스킬 폴더 자체를 대상으로 감사하면 스킬 폴더도 스캔한다.

## 신호 ID

`<규칙 ID>:<대상 상대 경로>:<문맥 해시 10자>:<순번>`. 문맥 해시는 신호 줄, 그 뒤의 첫 비공백·비주석 줄, 신호를 감싸는 항목의 시그니처 줄(들여쓰기 부모를 따라 올라가 처음 만나는 시그니처. `if`·`for`·main 가드 같은 블록은 건너 올라가고, 최상위에서 끝나면 빈 값)로 만든다. 줄 번호와 앞 줄은 들어가지 않아 위쪽이나 형제 항목을 고쳐도 ID가 유지된다. 한 함수 안에서 신호 줄과 다음 줄까지 같은 쌍둥이(예: 두 `except Exception:  # noqa` + `pass`)는 같은 묶음이 된다. 순번은 같은 파일·규칙·문맥 해시 안의 차례다.

## report.json

```json
{
  "executor": "<하네스, 모델 ID>",
  "reviewers": [{"role": "comment|structure|prose|diff", "independent": true, "executor": "<선택>"}],
  "verdicts": [
    {"signal": "<신호 ID>", "verdict": "fix|keep|propose|blocked", "reason": "<근거>", "role": "<역할>"},
    {"rule_id": "<규칙 ID>", "file": "<파일>", "verdict": "keep", "reason": "<묶음 근거>", "role": "<역할>"}
  ],
  "dispositions": [
    {"signal": "<신호 ID>", "status": "justified_keep", "exception": "allowed_suppression|external_constraint",
     "evidence": "<근거>", "proposed_by": "<검토자 역할>", "accepted_by": "main"},
    {"signal": "<신호 ID>", "status": "blocked", "reason": "<왜 고치지 못했는가>"}
  ],
  "proposals": [{"removes": "<제거할 실패>", "owner": "<타입|API|린트|테스트|CI>", "failure_signal": "<위반 시 어떤 명령이 어떤 오류>", "applies_to": "<새 코드만|기존 사례 포함>"}],
  "open_constraints": [{"file": "<파일>", "text": "<제약 주석 원문>", "status": "blocked|accepted_unenforced|out_of_scope"}],
  "findings": [{"file": "<파일>", "line": 0, "role": "<역할>", "note": "<스캐너가 못 잡은 문제>"}],
  "deleted_files": [{"file": "<지우거나 옮긴, 신호가 있던 파일>", "reason": "<왜 지워도 되는가>"}]
}
```

- 판정(`verdicts`)은 휴리스틱 신호마다 하나다. 신호 ID 하나에 붙이거나 `{rule_id, file}` 묶음으로 그 파일의 그 규칙 신호 전체에 붙인다. 내용은 완료를 막지 않는다.
- 처분(`dispositions`)은 허용 경로 안에 남은 맡은 종류의 확정 신호마다 하나다. 일괄 보존을 막으려고 신호 ID 하나에만 붙인다.
- `justified_keep`의 `exception`은 신호의 수정 유형에 허용된 값이어야 한다.

| 수정 유형 | 허용 예외 |
|---|---|
| lint-suppression | `allowed_suppression`(스타일 전용·결함 규칙), `external_constraint` |
| swallowed-error | `external_constraint` |
| commented-code, banner-comment | 없음. 지우거나 `blocked` |
| hash-integrity | `last_resort`(git도 직접 비교도 닿지 않음), `external_constraint`(외부 규격이 digest를 요구), `not_integrity`(무결성·일치 확인에 쓰지 않음). `evidence`에 git·직접 비교를 쓸 수 없는 이유를 적는다 |

넓은 예외를 삼키는 코드에 정당한 이유가 있으면(소멸자나 종료 처리의 최선형 정리) 예외 형식을 좁혀 고친다. 좁힌 결과는 휴리스틱 신호라 판정만 받는다.

## verify 판정 항목

| ID | FAIL 조건 |
|---|---|
| V1 | 허용 경로 밖, 감사 범위 밖, 보호 경로 파일의 추가·삭제·변경. 감사 전용이면 모든 변경 |
| V2 | 보호 주석 원문의 파일별 건수 감소. 삭제 승인이 있고 `open_constraints`에 원문을 `accepted_unenforced`로 적은 제약 주석은 예외 |
| V3 | 허용 경로 안, 맡은 종류의 남은 확정 신호에 `justified_keep`이 없음, `blocked` 처분, 수정 전에 없던 ID의 처분, 일부만 남은 쌍둥이 묶음 |
| V4 | 어느 파일·규칙이든 확정 신호 건수 증가. 수정 전에 검사한 파일이 해석 실패로 빠짐. 신호가 있던 파일이 `deleted_files` 신고 없이 사라지거나 옮겨짐. 허용 경로의 새 파일이 스캔 제외 대상. 수정 전에 검사한 파일은 생성물 표지가 생겨도 그대로 검사한다 |
| V5 | 수정 전 휴리스틱 신호나 새로 생긴 휴리스틱 신호의 판정 누락 |
| V6 | `reviews/comment.json`, `reviews/structure.json` 없음. 산문 파일이 있는데 `reviews/prose.json` 없음. 바뀐 파일이 있는데 `reviews/diff.json` 없음 |
| V7 | 기준 검사 실패·실행 불가(바뀐 파일 수와 무관), 검사도 위임도 없이 수정, 수정 후 검사의 실패·실행 불가·시간 초과. 바뀐 파일이 0개면 `NOT_APPLICABLE` |
| V8 | `report.json` 없음, 최상위 목록 키 누락, 형식 위반, 처분의 필수 칸 누락, 수정 유형에 맞지 않는 예외 |

검사 상태는 `passed`(종료 0), `failed`(그 밖의 종료 코드), `unavailable`(126, 127, bash 없음), `timeout`(기본 600초)이다. verdict에는 맡지 않은 종류의 신호 수량·위치, 허용 경로 안에서 그 건수가 바뀐 목록(`undesignated_changed`, 경고), 검사하지 않은 파일, 정당한 보존 건수가 함께 남는다.
