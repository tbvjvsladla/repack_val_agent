# 공개 범위와 발행

| 구분 | 처리 |
|---|---|
| AGENTS.md, 두 헌장, 설치된 여섯 스킬과 내부 테스트 | 공개. 기존 라이선스·출처 보존 |
| docs의 의도·계획·수행·검증·참고 문서 | 개인 경로·대상명·실제 세션 식별자를 바꿔 공개 |
| 포인터 위키 | 공개 문서를 기준으로 다시 생성 |
| 실제 외부 프로젝트 소스, seed, temp, 루트 tests | 제외 |
| 개인 Codex 설정, 실제 worker config, 인증·비밀값 | 제외. 워커 설정은 예시만 제공 |
| 원시 stdout/stderr, 모델 응답·실행 상태, 캐시·Ouroboros 데이터 | 제외 |
| 원본 .git와 과거 커밋·작성자 정보 | 제외. 공개본에서 새 이력 시작 |

루트 `/seed/`는 항상 Git 추적과 공개본에서 제외합니다. `.agents/skills/*/tests/`는 설치 스킬의 재현 가능한 내부 검사이므로 포함합니다. 루트 `/tests/`와 구분합니다.

## 원본 작업 공간에서 공개본 만들기

`.gitignore`는 이미 추적한 파일과 과거 커밋을 지우지 않습니다. 운영 작업 공간의 Git 이력을 그대로 push하면 안 됩니다. 실제 설정과 과거 이력을 보존한 채 아래 명령으로 새 공개 후보를 만듭니다.

```bash
python3 scripts/prepare_public.py --output temp/public-release
```

출력은 새 디렉터리여야 합니다. 생성기는 허용된 경로만 복사하고, Git 이력을 가져오지 않으며, 문서를 익명화하고 위키를 다시 만듭니다. `PUBLIC_EXPORT.json`에는 포함 파일 목록과 익명화 건수가 기록됩니다. 원래의 개인값이나 대응표는 기록하지 않습니다. 원본 과업 문서·worker 설정·index·branch·remote를 수정하지 않습니다.

생성기는 알려진 키 형태·개인 경로·세션 UUID를 검사합니다. 자동 검사가 문서의 모든 영업정보나 새로운 형태의 비밀값까지 판별하지는 못하므로 최종 파일 목록과 docs의 내용은 발행 전에 읽어야 합니다. 원본이 다른 세션에서 바뀌면 생성기가 실패하므로 해당 후보를 발행하지 않고 새 경로로 다시 생성합니다.

## 공개 후보 검토와 발행

최종 후보에서 문서 내용과 포함 파일을 검토한 뒤 별도 저장소를 초기화합니다. 원본 작업 공간에서는 아래 명령을 실행하지 않습니다. Git 작성자 설정은 공개해도 되는 이름·주소를 사용합니다.

```bash
cd temp/public-release
git init -b main
git add .
git status --short
git commit -m "Publish reusable agent workflow, skills and redacted history"
git remote add origin https://github.com/OWNER/repack_val_agent.git
git push -u origin main
```

후속 발행도 새 후보를 만들어 확인합니다. 공개 저장소에 이미 이력이 있으면 그 공개 저장소의 clone에서 검토된 파일 변경을 반영합니다. 운영 원본의 `.git`을 가져오거나 기존 원격을 force push하지 않습니다.

공개본의 과업 문서에는 원래 시점의 계획·명령·경로가 남습니다. 그 안의 명령을 일괄 실행하지 않습니다. 공개본에 없는 원시 근거의 재현과 외부 프로젝트의 최종 수용은 별도 과업입니다.
