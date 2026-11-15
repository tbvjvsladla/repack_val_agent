# 프로젝트 로컬 설치

## 활성화

이 스킬은 비활성 상태로 배포된다. 스킬 발견 링크(`.agents/skills`, `.claude/skills`)와 활성 `config.yaml`, `.venv`가 없고 MCP도 등록되어 있지 않다. 사용자가 쓰기로 정하면 프로젝트 루트에서 켠다.

```bash
python3 scripts/skill_activation.py enable deepeval-pack           # 무엇을 만들지 보기
python3 scripts/skill_activation.py enable deepeval-pack --apply   # 링크 2개 + config.yaml(없을 때만)
```

끌 때는 `disable deepeval-pack --apply`로 링크만 지운다. config·프로필·키·`.venv`는 남는다. MCP 등록은 따로 해제한다.

## 의존성

Python 3.12/3.13과 uv가 필요하다. 스킬 폴더(`<skill-dir>`, 이 저장소에서는 정본 `skills/deepeval-pack`)에서 `uv sync --frozen`으로 잠긴 의존성을 설치한다. DeepEval 4.3.2, MCP Python SDK 2.3.0 및 네 provider SDK를 고정한다. MCP v2의 MCPServer를 사용한다. `.venv`는 머신·아키텍처에 묶이므로 다른 머신의 것을 복사하지 않고 그 자리에서 다시 만든다.

config.yaml이 없을 때만 config.example.yaml을 복사한다. 기존 활성 설정을 덮어쓰지 않는다.

## envs 폴더

Judge 프로필은 프로젝트 루트의 `envs/<name>_profile.yaml`에 둔다(형식: `envs/profile.example.yaml`). config.yaml의 `provider`와 `judge_profile`이 이 파일을 가리킨다. API 키는 `envs/.env`에 둔다(키 이름: `envs/.env.example`). 이미 설정된 프로세스 환경변수가 먼저이고, 없을 때만 이 파일에서 선택한 provider의 키 하나를 읽는다. 프로필은 `--env-file`이 가리키는 파일과 같은 폴더에서 찾는다. `envs/` 아래의 실제 키·프로필 파일과 활성 config는 Git·공개본에 넣지 않는다.

프로젝트 루트는 스킬 폴더 위치로 정한다.

| 스킬 폴더 | 프로젝트 루트 |
| --- | --- |
| `<root>/skills/deepeval-pack` (어댑터 링크로 열어도 같은 정본으로 해석) | `<root>` |
| `<root>/.agents/skills/deepeval-pack`, `<root>/.claude/skills/deepeval-pack` (실폴더 설치) | `<root>` |
| 그 밖의 위치 | 현재 디렉터리 |

`DEEPEVAL_PACK_PROJECT_ROOT`가 있으면 그 값을, `--env-file`을 주면 그 파일을 쓴다.

## MCP 등록

경로는 설치 작업 공간의 실제 절대 경로로 작성한다. 전역 설정을 수정할 필요는 없다.

Codex는 프로젝트 `.codex/config.toml`(로컬 파일, Git 추적 제외)에 등록한다. 신뢰된 프로젝트에서만 로드된다.

```toml
[mcp_servers.deepeval]
command = "<project>/skills/deepeval-pack/.venv/bin/python"
args = ["-B", "<project>/skills/deepeval-pack/scripts/run.py", "serve"]
cwd = "<project>"
startup_timeout_sec = 60
tool_timeout_sec = 300
```

Claude Code는 로컬 범위로 등록한다(사용자 설정에 기록되므로 사용자가 실행한다).

```bash
claude mcp add --scope local deepeval -- <project>/skills/deepeval-pack/.venv/bin/python -B <project>/skills/deepeval-pack/scripts/run.py serve
```

등록 후 새 세션에서 실제 도구 발견과 deepeval_status/deepeval_validate 호출을 확인한다. 현재 실행 중인 호스트에 파일 등록만으로 즉시 도구가 추가됐다고 주장하지 않는다.

스킬은 `skills/deepeval-pack/SKILL.md`가 정본이고 `.agents/skills`·`.claude/skills` 링크로 인식된다. 외부 워커 프로젝트에는 별도로 설치하지 않는다.

## 로컬 자체검사

```bash
.venv/bin/python -B -m unittest discover -s tests -v
.venv/bin/python -m mypy
```

검사는 실제 DeepEval 메트릭을 사용하지만 테스트 Judge/어댑터 대역으로 유료 API 요청을 하지 않는다. 실제 Judge 품질이나 인증 성공의 증거가 아니다.

[Codex 공식 MCP 등록](https://developers.openai.com/codex/mcp/) · [Claude Code MCP](https://code.claude.com/docs/en/mcp) · [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/)
