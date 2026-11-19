# 외부 자산과 출처

프로젝트의 새 코드·헌장·작업 문서에는 루트 [MIT License](LICENSE)를 적용합니다. 기존 외부 자산의 저작권 고지와 라이선스 파일은 그대로 보존하며 루트 고지로 대체하지 않습니다.

| 경로 | 포함된 고지·출처 |
|---|---|
| `skills/code-review/` | [LICENSE](skills/code-review/LICENSE), [적용 기록](skills/code-review/references/adaptation.md) |
| `skills/pr-review-toolkit/` | [LICENSE](skills/pr-review-toolkit/LICENSE), [적용 기록](skills/pr-review-toolkit/references/adaptation.md) |
| `skills/rust-repack/` | [LICENSE](skills/rust-repack/LICENSE), [참고 원칙](skills/rust-repack/references/clean-code.md) |
| `skills/anti-code-slop/` | pstack(MIT, Lauren Tan)의 주석·산문 지침을 한국어판 pstack-ko를 거쳐 적용. [LICENSE](skills/anti-code-slop/LICENSE), [적용 기록](skills/anti-code-slop/references/adaptation.md). 스캐너·판정 스크립트는 프로젝트 구현. unlazy(MIT)와 pr-review-toolkit(Apache-2.0)은 방법만 참고 |
| `skills/worker-agent-control/` | [LICENSE](skills/worker-agent-control/LICENSE), [적용 기록](skills/worker-agent-control/references/adaptation.md) |
| `skills/wiki-desk/` | [LICENSE](skills/wiki-desk/LICENSE), [적용 기록](skills/wiki-desk/references/codex.md), [fixture 출처](skills/wiki-desk/tests/fixtures/okf/ATTRIBUTION.md) |
| `skills/skillevaluator/` | NVIDIA SkillEvaluator의 스킬 정의(Apache-2.0). [LICENSE](skills/skillevaluator/LICENSE), [NOTICE](skills/skillevaluator/references/NOTICE.md), [적용 기록](skills/skillevaluator/references/adaptation.md). CLI는 포함하지 않음 |
| `skills/deepeval-pack/` | 프로젝트의 구현. [DeepEval](https://github.com/confident-ai/deepeval)(Apache-2.0)·MCP Python SDK 등은 `uv.lock`으로 고정해 설치 시 받으며 저장소에 포함하지 않음 |
| `skills/harness2/` | 프로젝트의 독립 구현. [방법 출처와 차이](skills/harness2/references/provenance.md)를 보존하며 원형 연구 엔진은 포함하지 않음 |
| `skills/progress-monitor/` | 프로젝트의 독립 구현. 참고한 방법의 출처는 [campaigns/README.md](campaigns/README.md#출처)에 적었으며 외부 코드는 포함하지 않음 |
| `campaigns/`, `scripts/check_campaign.py` | 프로젝트의 독립 구현. 참고한 방법의 [출처](campaigns/README.md#출처)를 적었으며 외부 코드는 포함하지 않음 |

docs 안에서 링크한 외부 프로젝트·논문·공식 문서는 참고 자료입니다. 루트 `seed/`의 외부 저장소 사본·PDF·자료는 배포에 포함하지 않습니다.
