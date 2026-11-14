# 설치·초기화·업그레이드·제거 (TS판)

## 전제
- Node.js 24 이상. `.ts`를 빌드 없이 직접 실행한다. 런타임 npm 의존성은 없다. 없으면 차단 사유를 보고하고 자동 설치하지 않는다.
- 스킬은 **폴더 전체**가 한 단위다. `SKILL.md`만 복사하는 것은 설치가 아니다.
- 이 설치본의 정본은 하네스 중립 경로 `skills/wiki-desk/`다. 각 이너 하네스는 자기 발견 경로의 디렉터리 심볼릭 링크로 찾는다(Codex `.agents/skills/wiki-desk`, Claude Code `.claude/skills/wiki-desk`). 글로벌 설정 편집이나 플러그인 등록은 필요 없다. `SKILL.md`의 `name`·`description`으로 발견되며, 하네스의 명시 호출(Codex `$wiki-desk`, Claude Code `/wiki-desk`)이나 문서 조회 요청에 따른 자동 선택으로 사용한다.
- Node.js 및 프로젝트 권한은 현재 이너 하네스의 실행 환경에서 확인한다. 한 하네스의 권한 규칙(Claude Code `Bash(...)` 허용 규칙, Codex 권한 프로필)을 다른 하네스에 적용된 것으로 간주하지 않는다.

## 스킬 설치(코드)
1. 조정본의 폴더 전체를 `skills/wiki-desk/`에 설치하고(`.git` 제외) 하네스별 발견 경로에 심볼릭 링크를 둔다. 기존 동명 스킬이 있으면 덮어쓰지 않고 차이를 먼저 검토한다. 원본 배포 태그를 그대로 풀면 원본 호스트 전용 지침이 돌아오므로 [적용 기록](codex.md)에 따라 변경을 다시 적용한다.
2. 설치본에서 `node scripts/package-manifest.ts --check`(파일 집합·sha256)와 `node --test`(결정론 회귀·위생·manifest 게이트 포함)를 확인한다.
3. 프로젝트 루트에서 CLI를 실행한다. 하위 디렉터리에서는 CLI 절대 경로와 `--root <프로젝트 루트>`를 함께 지정한다. 스킬 코드는 공유 코드이고, 위키 계약·카드는 프로젝트 데이터다. Git과 브랜치 동기화가 없는 프로젝트에서도 CLI를 사용할 수 있다.

## 위키 초기화(브랜치별)
| 명령 | 쓰기 |
|---|---|
| `node skills/wiki-desk/src/cli.ts init --contract <합의 계약 파일>` | 없음 — 생성 계획 출력 |
| 위 명령 + `--apply` | `<wiki_dir>/contract.json`·`receipt.json`·`.gitignore`(캐시 제외)·`index.md`·`log.md`·`glossary.json`(자동 추출 켬) 생성 |
| `… sync` → `… sync --apply` | 카드 생성 |
| `… check` | 없음 |

- 기존 위키(`__llm-wiki/`·`__llm_wiki/` 어느 쪽이든)가 있으면 `init`은 거부한다(덮어쓰기 없음).
- `wiki_dir`는 계약의 리터럴 그대로다(`__llm-wiki`와 `__llm_wiki`는 다른 값).
- 적용 후 `status`·`check`·원문 불변을 확인한다. Git 프로젝트는 `git diff --diff-filter=MD -- <source_roots>`를 확인하고, Git이 없는 프로젝트는 실행 전후 원문의 경로 집합·바이트 해시를 비교한다.

- 설치한 프로젝트의 헌장(작업 절차·문서 규칙)에 넣을 항목은 [charter.md](charter.md)를 따른다.

## 업그레이드 (3.0.x → 3.2.0)
3.2.0은 계약에 `search` 필수 키를 더했다. 검증기는 기본값을 채우지 않으므로, 스킬만 바꾸면 `query`·`check` 등이 누락 키를 알려 주는 오류로 멈춘다(예: `계약 search.match_lines_max: 필수 필드 누락 — 예: "match_lines_max": 10 …`).
1. 스킬 폴더를 새 배포본으로 교체하고 `node scripts/package-manifest.ts --check`·`node --test`를 확인한다.
2. `<wiki_dir>/contract.json`의 `search`에 새 키를 넣는다. 값은 `assets/project-contract.example.json`의 같은 키를 그대로 쓴다(측정·출하된 기본값).
3. `<wiki_dir>/glossary.json`이 없으면 `assets/glossary.example.json`을 그 이름으로 복사한다(자동 추출 켬).
4. `status`·`check`·`query`가 exit 0인지 확인한다. 카드 형식은 바뀌지 않았으므로 **재입고는 필요 없다**(`sync` 계획의 생성·갱신 0건). 검색 색인 캐시는 첫 질의 때 자동으로 다시 만든다.

2.x에서 올리면 카드 형식(관례 메타 키 이름)도 바뀐다: 위 절차 뒤 `sync`로 계획을 보고 `sync --apply`로 카드 전체를 다시 쓴다. 적용 전후 원문 경로 집합이 같은지(누락 0) 확인한다.

## 제거
| 명령 | 동작 |
|---|---|
| `… remove` | 계획만 |
| `… remove --apply` | receipt에 기록되고 **변경되지 않은** wiki-desk 생성물(카드·보존 카드·`index.md`·`log.md`·`.gitignore`)과 색인 캐시를 제거. `contract.json`·`receipt.json`(사람 키만 더한 카드의 소유 기록만 남음)·사용자가 수정하거나 추가한 파일·원문은 보존. receipt가 없거나 위키 밖 경로를 담고 있으면 거부 |

거부되면 차이를 보고하고 파일을 보존한다. 원문은 어떤 제거 모드에서도 대상이 아니다.

remove 뒤 `sync --apply`를 실행하면 위키가 다시 채워진다(`index.md`·`log.md`·`.gitignore` 재생성. 사람 키만 더한 카드는 원문 변경 시 갱신, 그 밖의 사람 수정 카드는 `protected`). 위키를 완전히 없애려면 남은 `contract.json`·`receipt.json`·사람 파일을 확인한 뒤 직접 지운다.

## 인식 확인
파일 존재나 `status` 성공은 host가 스킬을 인식했다는 뜻이 아니다. 각 하네스의 스킬 목록(Codex `/skills`·`$` 목록, Claude Code `/` 메뉴)에서 `wiki-desk`를 선택하고 실제 로드를 따로 확인한다. 목록에 나타나지 않으면 해당 하네스를 재시작한다. 파일 존재·CLI 실행·호스트 인식 결과를 하네스별로 분리해 기록한다.
