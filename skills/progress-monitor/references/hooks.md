# 종료 훅 설치

메인 에이전트가 턴을 끝내려 할 때 하네스가 `stop-hook`을 부르게 한다. 훅은 활성 캠페인에 스스로 할 수 있는 일이 남아 있으면 종료를 막고 다음 일을 알려 준다. 규칙은 [monitor.md](monitor.md)의 "정체"에 있다.

- 훅 설정은 **추적하지 않는 로컬 파일**에 둔다. 추적하는 설정에는 훅을 두지 않는다. 저장소를 받은 사람의 세션이 뜻하지 않게 막히지 않게 하기 위해서다.
- `campaigns/ACTIVE`가 없으면 훅은 아무것도 출력하지 않고 파일도 쓰지 않는다. 훅을 끄지 않고 캠페인만 닫아도 된다.
- 훅 때문에 세션이 끝나지 않으면 `campaigns/ACTIVE`를 지운다.
- 훅은 어떤 경우에도 종료 코드 0으로 끝난다. 안에서 오류가 나면 막지 않는다.
- 아래 형식은 2026-11-15에 각 하네스의 공식 문서에서 확인한 것이다. 하네스가 바뀌면 다시 확인한다.

## Claude Code

`.claude/settings.local.json`에 더한다. 이 파일은 Git이 무시한다.

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 -B \"${CLAUDE_PROJECT_DIR}/skills/progress-monitor/scripts/pm.py\" stop-hook --harness claude",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

- 훅은 표준 입력으로 `session_id`, `stop_hook_active`, `background_tasks` 등을 받는다.
- 다음 일을 알려 줄 때는 `{"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": "..."}}`를, 정체일 때는 `{"decision": "block", "reason": "..."}`를 낸다. 둘 다 대화를 이어 가게 한다. 아무것도 출력하지 않으면 종료를 허용한다.
- Claude Code는 훅이 연속으로 이어 가게 하는 횟수에 자체 상한(8회)을 둔다. 실행기의 `stop_block_cap`(기본 6)이 그보다 먼저 풀어 준다.
- 종료 훅은 메인 에이전트에서만 불린다. 사용자가 중단하면 불리지 않는다.
- 실행 중인 세션은 설정 파일의 변경을 대개 스스로 읽는다. 읽지 않으면 새 세션에서 확인한다.

## Codex

`.codex/hooks.json`에 더한다. 이 저장소의 `.gitignore`가 이 파일을 무시한다.

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 -B skills/progress-monitor/scripts/pm.py stop-hook --harness codex",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

- 프로젝트의 훅은 신뢰한 프로젝트에서만 읽는다. 처음에는 하네스의 훅 검토 화면에서 확인해야 한다.
- 막을 때는 `{"decision": "block", "reason": "..."}`를 표준 출력에 낸다. 다음 일을 알려 줄 때도 같은 형식을 쓴다.
- Codex의 훅 입력에는 백그라운드 작업 목록이 없다. 백그라운드 작업이 도는 동안 막지 않는 예외는 적용되지 않는다.
- 이 저장소에서는 Codex에서의 실제 발화를 확인하지 않았다. 형식만 문서에서 확인했다. 쓰기 전에 새 세션에서 한 번 확인한다.

## 확인하는 법

1. 훅을 설치한다.
2. 활성 캠페인에 할 일이 남은 상태에서 턴을 끝낸다. 훅이 돌려준 글(다음 일과 명령)이 보이면 발화한 것이다.
3. `status`의 `monitor`에서 묶인 세션과 막은 횟수를 본다.
4. `campaigns/ACTIVE`를 지우고 턴을 끝내 막히지 않는지 본다.

세션을 바꿔 이어받을 때는 `monitor reset`을 한 번 한다. 훅은 처음 부른 세션에만 작용한다.
