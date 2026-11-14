# 코드 작성 워커의 쓰기 범위

작업 폴더 밖 고정 임시 파일에 쓰는 실제 문제가 확인됐거나 과업이 별도 쓰기 범위를 요구하는 **Claude code-builder** 호출에 선택해 적용한다. 일반 질의·읽기 검토·Terraform/서빙/운영 작업에 자동 적용하지 않는다. 기존 사용자 승인·모델·effort·권한 프로필을 대체하지 않는다.

Linux aarch64/x86_64의 Landlock ABI3 이상, Python3.9 이상, Claude Code2.1.295 이상이 필요하다. `no_new_privs` 때문에 child의 sudo/setuid 승격은 불가능하다. 지원하지 않는 환경은 명시 실패/NOT_RUN으로 남기고 제한 없이 재실행하지 않는다.

## 호출 준비

과업 plan에 이번 source/check 경로와 필요한 검증을 정한다. 작성 워커만 사용하는 새 전용 폴더를 만들고 기존 소스는 `copy2` 등으로 분리 복사한다. 보호 원본·제품과 hardlink를 공유하지 않는다. 실행 중 다른 주체가 write root를 수정하지 않도록 과업 실행을 직렬화한다. helper·설정·ledger를 write root 밖의 지휘자 소유 경로에 둔다.

```bash
python3 <skill-dir>/scripts/prepare_builder_guard.py \
  --workspace <actual-worker-root> \
  --write-dir <new-source-directory> \
  --write-dir <new-check-directory> \
  --output-dir <new-conductor-owned-output-directory> \
  --executable <actual-claude-binary>
```

준비기는 외부 hardlink를 검사하며, 내부 파일 간 hardlink는 허용한다. 설치 CLI 버전과 실제 Landlock child 기동을 확인한 뒤 `guard.json`, `settings.json`, private0700 TMPDIR, 준비 receipt를 출력 폴더에 만든다. 실패 뒤 같은 폴더를 덮어쓰거나 지우지 말고 원인을 확인해 새 준비 경로로 이어간다.

기존 승인된 CLI argv에 `--settings <output-directory>/settings.json`만 추가하고 기존 모델·effort·권한·세션 인자는 유지한다. 도구는 필요한 `Read,Write,Edit,Glob,Grep,Bash` 범위에서 제공한다. `--tools`가 기존 MCP 도구까지 숨기지는 않으므로 훅은 모든 도구에 일치하며, 이6개 외의 MCP·추가 도구 호출은 거부한다. 하위 워커·백그라운드 실행 경로를 임의 추가하지 않는다. 설정은 이 호출에만 전달하고 사용자·프로젝트 settings 파일에 복사하지 않는다.

첫 실제 호출에서 tool 결과와 ledger를 확인한다. `onFailure: block`은 훅 파일 부재·실패·시간 초과에도 호출을 차단한다. 보호장치가 차단한 동작을 다른 도구로 우회하지 않는다. 실제 필요한 추가 쓰기가 발견되면 해당 명령·거부·필요 경로를 반환하고 지휘자가 기존 과업 범위에서 판단한다. capacity 재개에서도 같은 settings·경로·native 세션을 유지하며 부분 산출물부터 확인한다.

## 적용 범위와 한계

- Bash는 kernel Landlock child/descendants 안에서 실행된다. 명시 source/check/private TMPDIR와 `/dev/null` leaf 외의 파일 내용 쓰기·생성·삭제·rename을 제한한다. 심볼릭 링크 대상과 새 외부 hardlink도 이 범위에 따른다. **사전 외부 hardlink가 있으면 준비 단계에서 거부해야 한다.**
- Write/Edit는 실제 해석한 파일 경로가 source/check 안인지 확인한다. 설정·helper·ledger는 그 밖에 둔다. 동시 외부 파일 교체나 적대적 race를 막는 전체 OS sandbox는 아니다.
- read·execute·network, chmod/utime 등 메타데이터, 상속된 stdout/stderr FD, Native SDK 부모의 기존 인증·세션 기록은 제한하지 않는다. 전체 host 무변경·비밀 보호·네트워크 격리로 보고하지 않는다.
- Bash command가 내부 `/bin/bash -c`로 실행되므로 도구 호출 사이 `cd`/export 지속성을 가정하지 않는다. **각 호출에서 명시 경로와 환경을 사용한다.**
- 훅은 `updatedInput`으로 Bash command만 감싼다. `permissionDecision: allow`를 반환하지 않아 기존 native 권한 판단을 유지한다. 허용된 Write/Edit도 원래 권한 흐름을 따른다.

실행 근거에는 준비 receipt, per-call settings/config, 원래 command와 wrapped command ledger, 실제 Native 이벤트·거부·종료 결과를 남긴다. 경계 검증과 업무 검증·최종 수용을 구분한다.

공식 계약: [Claude Code hooks](https://code.claude.com/docs/en/hooks)의 PreToolUse `updatedInput`, `onFailure: block`. 과업별 qualification과 독립 검토는 해당 plan/checklist에 기록한다.
