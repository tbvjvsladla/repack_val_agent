# 적용 기록

이 스킬이 무엇을 어디서 가져와 어떻게 바꿨는지 적는다.

## 가져온 것

| 출처 | 라이선스 | 가져온 것 | 바꾼 점 |
|---|---|---|---|
| pstack(Lauren Tan), 한국어 번역본 pstack-ko를 거쳐 읽음 | MIT | `no-comments`의 절차(독립 보고, 부모 검수, 기각 후 1회 재실행, 사소한 수정, 근본 수정, 제약 인코딩 제안, 결과 보고), `comment-sicko`의 보존 예외 다섯 가지와 억제 주석 심판, `unslop` 규칙(번호 유지) | 검토자를 주석·구조·산문 셋으로 나눴다. 하위 명령 호출(`/architect`, `/why`)을 빼고 판정 질문으로 바꿨다. 제약 주석은 기본을 보수안(지우지 않고 `blocked`)으로, 호출자 승인 시 원문 방식(지우고 미해결 보고)으로 했다. unslop의 영어 예시는 일부 한국어로 옮겼고, 기계로 잡는 규칙은 형식 규칙과 영어 어휘로 한정했다 |
| pstack 원칙 문서(`fix-root-causes`, `boundary-discipline`, `subtract-before-you-add`, `minimize-reader-load`, `prove-it-works`, `test-behavior-not-implementation`, `type-system-discipline`) | MIT | 판단 기준 | 원문을 싣지 않고 구조 검토자의 판정 질문 S1~S6과 수정 우선순위로 추렸다 |
| pstack `correct`, `migrate-callers-then-delete-legacy-apis`, `outcome-oriented-execution` | MIT | 문제 유형 | 수정 지시로 쓰지 않는다. 해당 문제는 제안으로만 보고한다 |
| 기준 매뉴얼 `h_report_26111907_AI코드슬롭_방지_에이전트_작업지시_매뉴얼.md`(사용자 제공 내부 문서) | 저장소 내부 자료 | 슬롭 신호 8종, 주석 판정 트리, 5.7 보수적 적용안, 감사 보고서 항목, 인코딩 제안의 네 칸 | 규칙표의 `manual_ref`와 판정 항목으로 연결했다 |
| 사용자 지시(2026-11-22): 무결성·일치 검증 | 저장소 내부 결정 | 해시로 파일 정합성을 확인하는 것을 슬롭으로 보는 판단, git → 직접 비교 → 최후의 해시라는 순서 | 외부 출처가 없다. pstack `no-comments`가 주석을 기본으로 지우고 좁은 예외만 남기는 태도를 해시에 옮겼다. 규칙 `ANY-HASH-INTEGRITY`(확정)·`PROSE-HASH-RECORD`(휴리스틱)와 예외 셋을 새로 만들었다 |

## 참고만 한 것(옮긴 코드나 문장 없음)

| 출처 | 라이선스 | 참고한 점 |
|---|---|---|
| unlazy(Leonxlnx) | MIT | 완료를 명령의 종료 코드로 판정하는 방식, 양성·음성 대조군으로 판정기를 시험하는 방식 |
| pr-review-toolkit | Apache-2.0 | 관점별 읽기 전용 검토자와 메인의 검수 구조 |
| pstack-ko(pstack 한국어 번역본) 구조 | MIT | 1.2.0의 계층화. `unslop`처럼 규칙에 고유 번호를 붙여 다른 지침이 번호로 인용하고, `typescript-best-practices`처럼 SKILL.md에는 표만 두고 기준은 references에 두고, `no-comments`·`comment-sicko`처럼 절차와 검토자 지침을 나눈다. 무결성 검토자(`integrity-review.md`)와 규칙표의 `reviewers` 선언이 그 결과다 |
| Anthropic 「Skill authoring best practices」, 「Effective context engineering for AI agents」 | 공개 문서 | SKILL.md는 목차 같은 개요로 두고, 참조는 한 단계 깊이로, 흔들리면 안 되는 판단은 스크립트로 둔다 |

## 새로 만든 것

- 스캐너(`slop_scan.py`), 규칙표(`rules.json`), 판정 스크립트(`slop_gate.py`): Python 3 표준 라이브러리로 새로 작성했다.
- 억제 주석 문법은 각 도구의 공식 문서(Rust Reference의 lint check attributes, ESLint 규칙 설정 문서, typescript-eslint `ban-ts-comment`, ShellCheck 위키의 directive, mypy·flake8·pyright 문서)로 확인했다. pylint의 `# pylint: disable=`는 공식 문서를 이번에 확인하지 못했다.
