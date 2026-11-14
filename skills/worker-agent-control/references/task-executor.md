# 중립 작업 실행기

Node.js 24+ 기본 모듈만 사용하는 `scripts/task_executor.mjs`다. 하나의 공개 요청을 기존 Claude/Codex CLI로 실행하고 stdout/stderr·PID·native session·첫 종료 결과와 중립 실행 receipt를 남긴다. 실제 평가·main/sub 배치·캠페인 제어는 호출자가 소유한다. 이 실행기는 다른 도구를 선택하거나 부르지 않는다.

```bash
node <skill-dir>/scripts/task_executor.mjs run <새-job-dir> --request <request.json>
node <skill-dir>/scripts/task_executor.mjs status <job-dir>
node <skill-dir>/scripts/task_executor.mjs resume <같은-job-dir> --request <같은-request.json>
node <skill-dir>/scripts/task_executor.mjs receipt <job-dir>
```

`run`/`resume`는 foreground의 한정된 실행 묶음이다. 호스트의 진행 세션으로 관측한다. 종료 0은 native 성공 반환, 75는 capacity/턴 예산의 열린 체크포인트, 1은 실행 실패, 2는 잘못되거나 변경된 입력, 3은 중복/lock/종료된 작업 등 재실행 거부, 4는 실행 중이거나 종료가 불명확해 기동하지 않았다는 뜻이다. 75이면 저장된 부분 결과와 상태를 확인하고 동일 요청으로 다음 묶음을 재개한다. 한 묶음의 한도가 과업 수명이 아니다.

## 입력

최상위 키는 아래와 정확히 같아야 한다. 경로는 절대경로다. `request_identity`는 null 이외의 JSON을 그대로 receipt.identity로 반환한다. 공개 프롬프트만 전달하고 비공개 평가 정답이나 비밀값은 넣지 않는다.

```json
{
  "schema": "task-executor-request/2",
  "request_identity": {"attempt_id": "caller-owned-id"},
  "comparison_key": "caller-owned-input-environment-profile-boundary",
  "executor_id": "native-executor",
  "evidence_mode": "observed",
  "workspace": "/absolute/worker/project",
  "prompt_file": "/absolute/public-prompt.txt",
  "provider": {
    "kind": "claude",
    "executable": "/absolute/claude",
    "start_argv": ["-p", "--model", "{model}", "--effort", "{effort}", "--tools", "Read", "--allowedTools", "Read", "--max-turns", "{max_turns}", "--output-format", "stream-json", "--verbose"],
    "resume_argv": ["-p", "--resume", "{session_id}", "--model", "{model}", "--effort", "{effort}", "--tools", "Read", "--allowedTools", "Read", "--max-turns", "{max_turns}", "--output-format", "stream-json", "--verbose"]
  },
  "profile": {
    "main_profile_id": "caller-profile",
    "sub_profile_id": "caller-profile",
    "cli": "claude",
    "model": "caller-selected-model",
    "effort": "high",
    "expected_primary_models": null
  },
  "budget": {
    "attempt_timeout_seconds": 300,
    "additional_capacity_attempts": 2,
    "backoff_seconds": 15,
    "burst_seconds": 1200,
    "max_turns": 12
  }
}
```

`evidence_mode`는 실제 관측이면 `observed`, 가짜 자식으로 프로토콜을 검사하면 `fixture`다. 이 선언만으로 실제 실행을 증명하지 않는다. main/sub profile ID는 같은 문자열이어야 하며 이름이 같다는 사실만으로 실제 sub 실행을 증명하지 않는다.

Claude 계열의 미지정 model/effort는 **명시적인 null**로 둔다. 그 항목의 placeholder와 literal 선택 flag도 양쪽 argv에서 뺀다. Codex는 명시적인 model/effort 문자열이 필요하다. `expected_primary_models`는 호출자가 실행 전에 고정한 정식 주 모델 이름의 비어 있지 않은 중복 없는 배열 또는 null이다. 요청 alias를 정식 이름이라고 추정하지 않는다. 알 수 없으면 null이며 정확한 프로필 일치를 주장하지 않는다.

`provider.kind: codex`는 start/resume argv를 생략할 수 있다. 기본은 `exec --json -m {model} -c model_reasoning_effort={effort_json} -o {final_file} -`와 `exec resume`에 같은 인자 및 `{session_id}`를 넣는 형태다. Claude는 두 배열을 모두 제공한다. 지원 placeholder는 `{model}`, `{effort}`, `{effort_json}`, `{session_id}`, `{final_file}`, `{max_turns}`, `{workspace}`다. `{max_turns}`가 있으면 budget.max_turns도 양의 정수여야 한다.

재개 배열에서 native resume 동사/flag와 session placeholder만 빼면 시작 배열과 **토큰·순서·flag/value 연결이 동일**해야 한다. 같은 요청·프롬프트·경로·프로필·권한을 유지한다. shell 문자열은 실행하지 않고 argv 배열과 stdin을 사용한다. 인증·환경·기존 native 설정은 상속하며 전역 설정은 수정하지 않는다. bypass flag·`--last`는 거부한다. 도구 제한은 전체 OS sandbox가 아니다. 실제 CLI 지원 인자는 설치본 help로 확인한다.

예산은 attempt_timeout_seconds ≥1, additional_capacity_attempts 0–10, backoff_seconds ≥0이며 burst_seconds는 attempt timeout + backoff보다 커야 한다. 이 설정은 자동 무제한 재시도를 만들지 않는다.

## 상태와 근거

`request.json`/`prompt.txt`는 불변, `job.json`은 관측 상태다. 각 `attempts/attempt-NNNN/`에 기동 전 `launch.json`, 기동 후 PID/start ticks `process.json`, `stdout.log`, `stderr.log`, 첫 종료의 `exit.json`, 해석의 `native.json`을 남긴다. session ID는 실제 native 결과에서 얻는다. `.lock`은 동시 실행을 배제하고 남은 lock은 자동 삭제하지 않는다. PID/start ticks와 원시 종료를 확인한다. Linux 밖에서는 PID 재사용 구분이 제한된다.

Codex capacity는 exit1·turn.failed·turn.completed 없음·모든 구조화 오류가 정확한 `Selected model is at capacity. Please try a different model.`(끝 마침표 없는 형태 포함)·유일한 같은 thread ID·열린 item 없음일 때만 인정한다. stderr·429·다른 문구를 capacity로 추정하지 않는다. backoff 뒤 같은 native 세션으로 이어가고 모델을 조용히 바꾸지 않는다. Claude의 실제 error_max_turns는 성공으로 바꾸지 않고 열린 budget checkpoint로 반환한다. 오류/권한 거부/정체성 불일치/timeout/사용자 취소는 별도 상태다. 취소는 대기와 기동 사이에도 적용하고 새 자식을 기동하지 않는다. 열린 item·실행 중 PID·기동/종료 불명 상태는 재실행하지 않는다.

Claude JSON wrapper와 stream-json을 해석한다. 주 모델 근거는 stream의 system.init.model과 main assistant.message.model이며 synthetic/child/payload는 제외한다. wrapper modelUsage는 누적·자식 포함 가능성이 있어 주 모델로 승격하지 않는다. Codex는 thread/turn의 top-level model만 사용한다. 미보고는 UNVERIFIED/UNREPORTED, 기대가 없으면 NO_EXPECTATION이다. 보고된 주 모델이 고정 기대 집합을 벗어나면 MISMATCH다. effort는 관측하지 않으므로 요청값과 구분해 UNREPORTED로 둔다.

`receipt.json`은 한 번만 쓴다. status PASS는 native 반환 성공이며 업무 판정이 아니다. launch_failed 또는 기동 전 취소는 NOT_RUN, 다른 종료 실패는 FAIL이다. 열린/불명 상태는 receipt를 자동 발행하지 않는다. 호출자가 근거와 이유를 갖고 종료하기로 결정했다면 `receipt --close-open NOT_RUN|FAIL --note <이유>`로 명시할 수 있다. 실행 중 프로세스를 중지하는 명령은 아니다.

공개 metric wall_ms는 실패·재시도·관측 backoff·local overhead를 포함한 실행 묶음별 실측 경과시간의 합이다. active native time과 backoff는 job.bursts에 따로 남기며 off-process 재개 사이의 미관측 공백은 합산하지 않는다. compute/cost/tokens는 신뢰할 계층별 근거가 없으므로 null이다. native 누적 사용량은 raw에 보존하고 호출별 비용으로 전환하지 않는다.

자체 fixture 검사: 전용 빈 임시 폴더를 TMPDIR로 지정해 `node --test <skill-dir>/scripts/checks/task_executor.test.mjs`. 실제 CLI·모델 가용성·재개·원격 캠페인은 별도로 관측한다. source는 Node24+ API를 사용하며 설치 당시 실제 검사는 Node26.7.0에서 수행했다. 자식 process group 밖의 외부 효과는 추적하지 않는다.
