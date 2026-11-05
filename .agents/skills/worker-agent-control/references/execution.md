# 네이티브 실행·검증 계약

## 공통

`WS`는 실제 대상 루트, `OUT`은 val_agent의 새 attempt 경로다. 프롬프트는 셸 인용 문제가 없도록 파일/stdin 또는 `subprocess` 인자 배열로 전달한다. 비밀값·인증 파일은 프롬프트나 로그로 가져오지 않는다. `HOME`·`CODEX_HOME`을 다른 용도의 변수로 재지정하지 않는다.

지휘자는 현재 사용자 요청에 맞춰 작업 범위·완료 기준·권한을 먼저 확인한다. 단순 결정론 반복은 적절한 스크립트에 맡기고, 원인 분석·전략 같은 추론 작업은 워커에게 위임한다. 서로 다른 실행 단계를 무리하게 한 호출에 넣지 않고 필요한 중간 결과를 인수한다. 하나의 추론 과제를 기계적으로 쪼개지도 않는다.

## Claude Code (기본)

- 인증 확인: 대상 workdir에서 `claude auth status`. 현재 CLI가 지원하는 상태 명령을 사용하고 성공 exit만 연결 사전조건으로 삼는다. 네트워크·실제 추론 가능성은 discovery가 확인한다.
- discovery: `claude -p --tools Read --allowedTools Read --output-format json --max-turns <예산>`; Read만 제공하므로 Bash/Edit/Write를 작업 도구로 허용하지 않는다. 프로젝트 지침·설정은 유지한다. hooks·MCP·CLI 사용자 영역 기록은 별개이므로 도구 제한을 전체 OS sandbox라고 표현하지 않는다.
- 본작업 예시: 워커 workdir에서 `claude -p --output-format json --max-turns <과업 예산> --allowedTools <필요한 도구>`. 프롬프트는 stdin. 설치 CLI의 `--help`·실제 지원을 확인한다. read-only 검토는 `--tools Read` 또는 필요한 읽기 도구만 제공한다. 편집/실행 작업은 승인된 범위에 필요한 도구를 지정하며 `--dangerously-skip-permissions`를 자동 추가하지 않는다.
- JSON wrapper의 `is_error`, `subtype`, `errors`, `permission_denials`, 실제 result/structured_output을 모두 확인한다. `error_max_turns`와 빈 응답·파싱 실패를 성공으로 처리하지 않는다. `num_turns`는 관측값일 때만 기록한다.
- `--max-turns`는 Claude 실행 예산이다. 난이도·실측·남은 작업으로 편성한다. discovery의 config 값은 가벼운 지침 읽기용 초기값이며 모든 작업의 고정 예산이 아니다.

## Codex CLI (사용자가 워커로 명시)

- 인증 확인: 대상 workdir에서 `codex login status`. `auth.json` 파일 존재를 유효 인증의 유일한 조건으로 삼지 않는다. 키 기반·기존 저장소 설정도 유지하며 실제 호출로 확인한다.
- discovery: `codex exec -C <WS> -s read-only --json --output-schema <schema> -o <OUT/identity.json> -`. prompt는 stdin. 이 호출만 read-only로 제한하며 대상 `.codex/config.toml`을 수정하지 않는다. 실제 선택된 권한은 실행 로그에서도 확인한다. 제한 적용 실패는 연결 성공이 아니다.
- 호스트가 read-only sandbox를 실행할 수 없다면 실패를 보존한다. 기존 워커 프로필 사용을 별도로 승인받은 경우에만 discover.py의 `--codex-permissions inherit`로 새 attempt를 실행한다. 이는 sandbox 인자를 생략해 기존 설정을 따르는 것이며 설정 파일이나 credentials를 바꾸지 않는다. 현재 대상 설정은 `default_permissions = ":danger-full-access"`이므로 파일 비수정은 과제 지시·호출 전후 관측으로 확인할 뿐 OS read-only sandbox로 강제했다고 보고하지 않는다.
- 본작업: `codex exec -C <WS> --json -o <OUT/final.txt> -`. 모델을 정했을 때만 `-m`을 더한다. 권한은 대상 프로젝트와 현재 사용자/managed 정책을 유지한다. 이 프로젝트의 현재 native 권한은 `.codex/config.toml`에 있으므로 legacy sandbox 설정을 파일에 덧붙이지 않는다. 사용자 승인 없이 `--dangerously-bypass-approvals-and-sandbox`, `--ignore-user-config`, `--ignore-rules`를 더하지 않는다.
- Codex `-p`는 print가 아니라 profile이다. Claude `--max-turns`를 Codex에 전달하지 않는다. 시간 예산은 호스트가 추적하며 실행 CLI의 세션·체크포인트로 이어받는다.
- JSONL `error`/`turn.failed`·최종 메시지·exit code를 함께 확인한다. `item.completed`만으로 전체 성공을 선언하지 않는다. `-o` 최종 JSON/텍스트와 JSONL 이벤트 로그는 다른 파일로 보관한다.

## 인수 기록

각 본작업 attempt에는 workspace·branch, 선택 provider·실제 argv(비밀 제외), 실행 여부·native session ID, 시간/턴 배정·관측, 종료코드·실패 원인, 출력/산출물·직접 검사 결과, 남은 과업·재개 조건을 남긴다. 상태는 `working`, `input-required`, `auth-required`, `failed`, `canceled`, `unknown`, `completed`처럼 관측에 맞춰 정한다. `completed`는 합의된 업무 결과 검증까지 통과했을 때만 사용한다.

다른 CLI나 지휘자의 직접 처리가 필요하면 실행자를 바꾸는 이유와 기존 요청의 수용 범위를 확인하고 별도 attempt로 남긴다. 변경 실행을 원래 provider의 성공으로 집계하지 않는다. 인증·권한 거부·사용자 중지·정체성 불일치·미해결 실행 상태는 그대로 보고한다.

## Capacity 이후 Codex 재개

`scripts/run_codex.py`는 사용자 승인이 있는 명시 model/effort의 Codex 호출을 한 번의 제한된 재시도 묶음으로 실행한다. 이 실행기 사용 전 기존 discovery와 대상 지침·역할·권한이 유효한지 확인한다. OS 격리나 업무 수용을 보증하는 실행기가 아니다.

요청 JSON에는 아래 필드를 모두 둔다. 파일 경로는 호출 작업 디렉터리 기준이며 이동 재개가 필요하면 절대경로를 준다. `authorization_file`은 실제 사용자 원문과 출처만 담은 `{"authorization":"...","source":"..."}`다.
`burst_seconds`는 `attempt_timeout_seconds + backoff_seconds`보다 커야 한다. 첫 resume의 대기와 한 번의 전체 실행·기동 여유를 담지 못하는 예산은 실행 전에 거부한다.

```json
{
  "workspace": "/path/to/trusted-worker",
  "executable": "/path/to/codex",
  "model": "explicit-model-id",
  "effort": "medium",
  "prompt_file": "/path/to/approved-task.txt",
  "authorization_file": "/path/to/user-authorization.json",
  "attempt_timeout_seconds": 900,
  "additional_capacity_attempts": 2,
  "backoff_seconds": 15,
  "burst_seconds": 3600
}
```

```bash
python3 <skill-dir>/scripts/run_codex.py --request request.json --output-dir <new-run-directory>
python3 <skill-dir>/scripts/run_codex.py --request request.json --resume-run <same-run-directory>
```

`receipt.json`과 attempt별 JSONL/stderr/final 메시지를 보존한다. 같은 실행의 동시 호출은 `.lock`으로 거부하며, 남은 lock은 실제 프로세스 상태를 확인한 뒤 처리한다. 자동 삭제하지 않는다. 실행기는 프로젝트/사용자 native 설정을 상속하고 model/effort만 요청값으로 전달하며 설정 파일·인증·권한을 바꾸지 않는다.

자동 재시도 조건은 exit1, `turn.failed`, 모든 구조화 오류의 정확한 `Selected model is at capacity. Please try a different model.` 또는 끝 마침표 없는 같은 문장, 관측된 같은 thread ID, 미완료 상태다. 이 두 문자열만 허용하고 stderr 문장·HTTP 번호·임의 텍스트에서 capacity를 추정하지 않는다. 각 추가 시도 전에 backoff하고 같은 `exec resume <native-id>`로 이어받아 저장된 진행과 실행 중 명령을 먼저 확인하도록 전달한다. 다음 재시도 묶음의 첫 resume도 기존 실패의 재시도이므로 backoff를 생략하지 않는다. `--last`나 조용한 모델 대체는 사용하지 않는다.

반환0/state=returned는 native 성공 반환이며 외부 업무 검증은 별도다. 반환75/state=capacity_deferred면 원시와 같은 세션을 남긴다. 호출자는 사용자에게 같은 승인을 다시 묻거나 과업을 종료하지 않고, 다른 독립 작업을 진행하거나 다음 감독 시점에 **동일 요청으로 다음 묶음을 재개**한다. 묶음의 추가 횟수는0~10이며 과업 수명의 상한이 아니다. 매번 새 묶음의 시간 경계 안에서만 실행한다.

인증/권한·세션 불일치·불완전 이벤트·timeout/신호/사용자 중지는 자동 capacity 재시도하지 않는다. 별도 상태를 회수하고 기존 복구 규약을 따른다. 실제 모델/effort가 native 이벤트에 보고되지 않으면 요청값을 관측값으로 채우지 않는다. 모델 전환은 기존 승인 범위와 별도 귀속을 확인한 새 요청으로 수행한다.

대화형 Codex 호스트가 자기 응답 생성을 중단한 경우, 이 로컬 CLI 실행기가 호스트의 모델 선택을 직접 바꾸지는 못한다. 과업 문서·Git·native receipt의 체크포인트로 재개하며, 지원되지 않는 서버 API/전역 설정으로 우회하지 않는다.

자체 fixture 검사는 `python3 <skill-dir>/scripts/selftest_run_codex.py`다. 실제 subprocess로 capacity→동일 세션→성공, 소진→다음 묶음 재개, 비capacity·세션 불일치·요청 변경·timeout을 검사하며 실제 모델 가용성 근거로 쓰지 않는다.
