# 자체검사와 선택적 의미 분석

## 기본 검사

대상 workspace의 toolchain/MSRV·edition·manifest·feature·target과 기존 검사 명령을 확인한다. 지정된 지원 조합을 사용한다. 배타적 feature가 있을 수 있으므로 `--all-features`를 일괄 적용하지 않는다. 변환 crate만 검사했는지 전체 workspace인지 구분한다.

필수 기본 검사는 fmt·빌드·Clippy·적용 테스트 이식과 통과다. 다음 형태에서 실제 manifest/package/feature 범위를 지정한다.

```sh
cargo fmt --manifest-path <Cargo.toml> -- --check
cargo build --manifest-path <Cargo.toml>
cargo test --manifest-path <Cargo.toml>
cargo clippy --manifest-path <Cargo.toml> -- -D warnings
```

필요하면 승인된 출력 경로에 `cargo fmt`를 먼저 적용한다. 범위 밖 workspace 파일을 자동 포맷하지 않는다. 승인된 기존 lint 기준이 있으면 그 기준과 결과를 명시한다. `allow` 추가·검사 생략·정답 변경을 통과 수단으로 쓰지 않는다.

테스트 수와 원본→Rust 대응을 확인한다. 필터·feature로 0개가 된 테스트를 통과 근거로 쓰지 않는다. 문서 예제가 있다면 해당 doctest를 확인한다. doc/Miri/추가 target 검사는 산출물·요구·위험에 필요할 때 선정하고 미실행을 숨기지 않는다.

명령·작업 경로·버전·feature/환경·종료 코드·테스트 수·원시 로그 위치를 기록한다. 관련 대상·조건의 유효 결과는 재사용하고 영향받은 검사만 재실행한다.

## 실패와 미실행

| 상태 | 의미 |
|---|---|
| PASS | 해당 검사·대상에서 실제 실행·통과 |
| FAIL | 실행 결과가 기준을 위반 |
| NOT_RUN | 환경·도구·입력 부재 또는 아직 미실행, 사유 필요 |
| NOT_APPLICABLE | 해당 산출물에 적용되지 않음, 구체적 이유 필요 |

도구가 없으면 거짓 로그를 만들거나 환경을 자동 설치하지 않는다. 필수 검사 미실행·실패면 전체 변환은 `INCOMPLETE`다. 부분 코드·명령·원인·재개 조건을 반환한다. 선택 보조 도구 부재와 필수 Cargo 도구 부재는 구분한다.

승인 범위에서 원인에 맞춰 수정한다. 새 근거 없이 같은 실패가 반복되거나 환경·권한·계약 결정이 필요하면 멈춘다. 입력의 재시도 한도를 따르며 한도 도달을 합격으로 바꾸지 않는다.

## rust-analyzer 기반 MCP — 있을 때만

기본 검사는 shell/Cargo로 수행할 수 있다. 의미 분석 서버는 필수 의존성이 아니며 자동 등록하지 않는다. 실제 노출된 도구 이름·스키마·workspace를 확인한 뒤 사용한다.

| 목적 | 기능과 한계 |
|---|---|
| 타입·영향 | hover, definition, references, symbols. 인덱싱 전 빈 참조 결과는 ‘사용처 없음’이 아니다. |
| 진단 | diagnostics/workspace_diagnostics의 완료·불완료 여부를 확인한다. cargo check 진단은 링크 빌드·테스트 실행의 대체물이 아니다. |
| 편집 | rename/code_actions/format은 편집 제안일 수 있다. 실제 적용 여부·허용 파일·계약 영향을 확인하고 적용 후 원문·검사를 확인한다. |

다른 workspace의 결과는 대상 근거로 쓰지 않는다. 실패·불완전 진단을 기록하고 가능한 Cargo 검사로 확인한다. 연결 성공만으로 동작 호환성·클린코드 준수가 입증되지는 않는다.
