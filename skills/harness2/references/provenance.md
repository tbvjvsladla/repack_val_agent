# 출처·적용 범위

방법 출처: Ruiyao Xu 외, [Harness²: Recursive Agent Harnessing for an Open World](https://arxiv.org/abs/2610.14224), 2026. 자산화 조사에 사용한 원형 저장소 revision은 `6868817518876de8fd170c6a8354daca273b6191`이다. 원형 README/pyproject는 Apache-2.0을 명시한다.

이 설치본은 논문의 제안→탐색→대조/조합→선택→회고/경험 재사용 구상을 호출자 소유의 실험에 적용한 독립 구현이다. 원형의 코드·프롬프트·benchmark 데이터·연구 엔진은 복사하거나 vendor하지 않았다. 따라서 원형 라이선스를 이 새 코드의 라이선스인 것처럼 표시하지 않는다. 이 참조 문서는 코드 재배포를 허가하거나 새 라이선스를 부여하는 문서가 아니다.

| 구분 | 원형 조사 결과 | 이 설치본 |
|---|---|---|
| 실행 | BenchmarkAdapter와 native executor의 rollout | 중립 request/receipt; 실제 실행은 호출자가 연결 |
| 제안/조합 | 원형 proposer/재귀 스케줄 | 에이전트가 작성; helper가 부모·대조·근거 계보 기록 |
| 선택 | 원형 최종 후보 pool의 선택; baseline 제외 관행 | 필수 신뢰성·기준선 비교 후 호출자의 실험 내 선택, baseline 유지·보류 허용 |
| 판정 | 원형 post-selection judge와 보고 | 외부 평가 receipt; 누락/실패를 분모에 보존, 최종 수용은 호출자 |
| 재개 | 완료 summary/입력/harness identity 확인과 재사용 | 로컬 계약·후보·근거 확인; 원격 미완료 campaign 복구 미구현 |
| 경험 | 원형 domain library | 적용 범위·한계·실행 근거를 붙인 로컬 회고, 후속 승격은 호출자 검토 |

원형 native adapter의 구현, 모델/CLI launcher, 원격 subnode 연결, 3계층 campaign 제어, 자동 빌드/벤치/배포는 제공하지 않는다. 이 설치본의 오프라인 동작 검증은 원 논문 결과 재현이나 실제 대상 프로젝트의 개선 검증이 아니다.

평가 설계 참고: 제공된 *Agent Skills Can Be Harmful: An Empirical Study of Skill-Induced Failures in LLM Agents*와 [Arena HarnessTax](https://arena.ai/blog/coding-agents-harness-tax). 전자의 누락/잘못된 구현·경로/환경 변화와 탐색/검증 부담 관측, 후자의 같은 업무 품질과 전체 실행 부담 분리 방향을 반영했다. 논문의 사례 비율·반복수·회귀 수식을 이 도구의 보편 합격선으로 가져오지 않는다. 과업별 필수 검사·통제·측정 계획은 호출자가 먼저 고정한다.
