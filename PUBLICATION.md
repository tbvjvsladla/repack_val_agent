# 공개 범위와 발행

| 구분 | 처리 |
|---|---|
| AGENTS.md·CLAUDE.md, 세 헌장(rules/), 아홉 스킬(skills/)과 내부 테스트, 하네스 어댑터 링크·`.claude/settings.json` | 공개. 기존 라이선스·출처 보존 |
| docs의 역할별 doc_gen_guide.md | 문서의 용도·작성 방식·예시만 공개 |
| docs에 생성된 과업 문서 | 로컬 보존, Git 추적·공개본 제외 |
| campaigns의 README.md와 _template, scripts/check_campaign.py | 캠페인의 규약·틀·형식 검사를 공개. 운영 규칙과 실행기는 skills/progress-monitor에 있음 |
| campaigns의 인스턴스와 ACTIVE | 로컬 보존, Git 추적·공개본 제외 |
| 위키 contract.json·glossary.json·.gitignore | 재사용할 설정으로 공개 |
| envs/.env.example, 스킬의 config.example.yaml·uv.lock | 키 이름과 설정 예시·잠긴 의존성 목록만 공개 |
| envs의 실제 키 파일, 스킬의 활성 config.yaml·.venv | 로컬 보존, Git 추적·공개본 제외 |
| 위키 카드·index·log·receipt·archive·캐시 | 로컬 보존, Git 추적·공개본 제외 |
| PUBLIC_EXPORT.json | 공개 후보마다 새로 생성, 원본 Git 추적 제외 |
| 실제 외부 프로젝트 소스, seed, temp, 루트 tests | 제외 |
| 개인 Codex 설정, 하네스별 로컬 설정과 로컬 훅, 실제 worker config, 인증·비밀값 | 제외. 워커 설정과 훅 설치는 예시만 제공 |
| 원시 stdout/stderr, 모델 응답·실행 상태, 캐시·Ouroboros 데이터 | 제외 |
| 원본 .git와 과거 커밋·작성자 정보 | 제외. 공개본에서 새 이력 시작 |

루트 `/seed/`는 항상 Git 추적과 공개본에서 제외합니다. `skills/*/tests/`는 설치 스킬의 재현 가능한 내부 검사이므로 포함합니다. 루트 `/tests/`와 구분합니다.

## 원본 작업 공간에서 공개본 만들기

`.gitignore`는 이미 추적한 파일과 과거 커밋을 지우지 않습니다. 최초 공개 시 운영 작업 공간의 Git 이력을 그대로 push하지 않고, 실제 설정과 과거 이력을 보존한 채 아래 명령으로 새 공개 후보를 만듭니다. 기존 공개 저장소의 후속 변경은 검증한 파일과 추적 해제만 일반 커밋으로 반영할 수 있습니다.

```bash
python3 scripts/prepare_public.py --output temp/public-release
```

출력은 새 디렉터리여야 합니다. 생성기는 허용된 경로만 복사하고, Git 이력을 가져오지 않으며, 배포 대상 텍스트의 환경값과 식별자를 일반화합니다. docs는 역할 폴더 바로 아래의 doc_gen_guide.md만 복사하며 위키 생성물을 만들거나 복사하지 않습니다. `PUBLIC_EXPORT.json`에는 포함 파일 목록과 치환 건수, 생성 문서·위키 실행 데이터 제외 여부를 기록합니다. 원래의 개인값이나 대응표는 기록하지 않습니다. 원본 과업 문서·worker 설정·Git index·branch·remote를 수정하지 않습니다.

생성기는 알려진 키 형태·개인 경로·세션 UUID를 검사합니다. 최종 파일 목록과 가이드의 내용은 발행 전에 확인합니다. 원본이 다른 세션에서 바뀌면 생성기가 실패하므로 해당 후보를 발행하지 않고 새 경로로 다시 생성합니다.

## 공개 후보 검토와 발행

최종 후보에서 문서 내용과 포함 파일을 검토한 뒤 별도 저장소를 초기화합니다. 원본 작업 공간에서는 아래 명령을 실행하지 않습니다. Git 작성자 설정은 공개해도 되는 이름·주소를 사용합니다.

```bash
cd temp/public-release
git init -b main
git add .
git status --short
git commit -m "Publish reusable agent workflow, skills and document guides"
git remote add origin https://github.com/OWNER/repack_val_agent.git
git push -u origin main
```

후속 발행도 새 후보를 만들어 확인합니다. 공개 저장소에 이미 이력이 있으면 그 공개 저장소의 clone에서 검토된 파일 변경을 반영합니다. 기존 추적 생성물은 로컬 보존을 확인하고 해당 경로만 `git rm --cached`로 제외합니다. 새 `PUBLIC_EXPORT.json`도 배포 검사 산출물이므로 일반 Git 커밋에는 포함하지 않습니다. 운영 원본의 `.git`을 가져오거나 기존 원격을 force push하지 않습니다.

이 전환은 현재 트리와 후속 배포의 정책입니다. 이전 공개 커밋에 있던 문서까지 제거하는 이력 재작성은 수행하지 않습니다. 새 clone에서 로컬 문서를 작성하고 위키를 사용하려면 [문서발행규칙의 초기화·입고 절차](rules/docs.md#wiki-desk-입고)를 따릅니다.
