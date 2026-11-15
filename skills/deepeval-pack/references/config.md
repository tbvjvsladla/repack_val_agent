# 설정과 입력 계약

`config.example.yaml`은 배포 예시이고 `config.yaml`은 Git/공개본에서 제외되는 활성 설정이다. provider/model은 설치 시 null이다. 값 변경은 다음 MCP 요청부터 반영된다.

## Judge Knob

`judge_profile.provider`: anthropic / openai / google / deepseek. gemini는 google의 별칭이다.
`judge_profile.model`: 사용자가 선택한 실제 모델 ID. 외부 프로필 경로나 암묵적 기본 모델은 없다.
`judge_profile.settings`:

| 필드 | 의미 |
| --- | --- |
| temperature | 선택한 모델이 지원하는 0 이상의 숫자 |
| generation_kwargs | 해당 provider의 네이티브 생성 옵션 객체 |
| cost_per_input_token, cost_per_output_token | DeepEval이 카탈로그 외 모델에 요구할 수 있는 비용 메타데이터 |

`generation_kwargs`에 모델에 맞는 max_tokens / max_completion_tokens / max_output_tokens, reasoning_effort, thinking 등의 실제 네이티브 키를 지정한다. 네 provider의 옵션 이름과 지원 모델은 서로 다르다. 이 팩은 옵션 이름을 번역하거나 지원하지 않는 옵션을 조용히 제거하지 않는다. 선택한 DeepEval 어댑터가 SDK에 전달한다. temperature는 최상위 settings에 두며 routing·인증·프롬프트·응답 schema는 generation_kwargs로 덮어쓸 수 없다.

빈 settings는 DeepEval/해당 SDK의 생성 기본값을 사용한다. 재현성에 필요한 설정은 명시한다. 로컬 검증은 구조·선택·필수 입력을 검사한다. 실제 모델 존재·API 인증·provider별 생성 옵션 호환성은 실제 평가 시 확인된다. 추론 설정 때문에 발생한 오류를 다른 모델·옵션으로 자동 재시도하지 않는다. SDK 자체의 전송 재시도는 해당 라이브러리의 동작이다.

provider별 API 키 환경변수는 ANTHROPIC_API_KEY / OPENAI_API_KEY / GOOGLE_API_KEY / DEEPSEEK_API_KEY다. 이미 설정된 프로세스 환경변수를 우선하며, 없으면 서버 실행 시 지정한 env 파일에서 해당 키를 읽는다. 값 보간·다른 dotenv 자동 탐색·클라우드 평가 업로드를 사용하지 않는다.

## 메트릭 Knob

`metrics`는 비어 있지 않은 `{type, params}` 목록이다. 같은 종류를 다른 파라미터로 여러 번 사용할 수 있고 결과의 metric_index로 구별한다.

| type | 필수 케이스 필드 |
| --- | --- |
| ContextualRecallMetric | input, actual_output, expected_output, retrieval_context |
| FaithfulnessMetric | input, actual_output, retrieval_context |
| AnswerRelevancyMetric | input, actual_output |
| ContextualPrecisionMetric | input, actual_output, expected_output, retrieval_context |
| ContextualRelevancyMetric | input, actual_output, retrieval_context |
| GEval | params.evaluation_params에 지정한 필드 |

지원 범위는 텍스트 단일 턴 평가다. 다른 DeepEval 도구는 이 설치 버전에서 자동 사용 가능하다고 가정하지 않는다. 지원 종류를 확장하려면 엔진의 카탈로그·필수 입력 계약·검사를 함께 추가한다. MCP 사용/도구 호출 trace 평가 모드는 포함하지 않는다.

params는 설치된 클래스가 받는 JSON 생성자 옵션이다. 공통 threshold는 [0,1], include_reason/strict_mode 등은 boolean이다. Faithfulness의 truths_extraction_limit는 양의 정수다. Judge model, async_mode, verbose_mode, system_one_model, eval_mode, evaluation_template, flaky, rubric는 팩 소유/미지원 옵션이므로 거부한다. 팩은 native LLM 평가를 수행한다.

GEval은 name, evaluation_params와 criteria 또는 evaluation_steps를 지정한다. evaluation_params는 아래 필드 이름 문자열 목록이며 팩이 DeepEval enum으로 변환한다. 별도 Python 클래스·템플릿 import는 설정으로 허용하지 않는다.

## 케이스·반환

각 케이스는 input, actual_output, expected_output(문자열), retrieval_context, context(문자열 배열), 선택 id를 받는다. 메트릭이 요구하는 문자열과 문맥 배열은 비어 있으면 거부한다. 데이터가 없다는 이유로 만점을 반환하지 않는다. 실제 DeepEval의 일부 메트릭보다 이 팩의 텍스트 입력 계약이 더 좁을 수 있다.

```json
{"cases":[{"id":"case-1","input":"질문","actual_output":"실제 답변","expected_output":"기대 답변","retrieval_context":["참조한 원문 구간"]}]}
```

설정·입력 검증은 API 요청을 하지 않는다. Judge 누락은 명시 오류, 입력 오류는 invalid_input, 실제 평가 성공은 ok다. 각 결과는 case_index/id, metric/index, score, reason, threshold, metric_success 또는 error_type/error를 포함한다. 일부 메트릭 실패는 partial_error이며 다른 케이스·메트릭 결과를 보존한다. SDK 예외 원문은 키나 전송 데이터를 포함할 수 있어 반환하지 않는다.

공식 의미: [Contextual Recall](https://deepeval.com/docs/metrics-contextual-recall), [Faithfulness](https://deepeval.com/docs/metrics-faithfulness), [커스텀 Judge 계약](https://deepeval.com/guides/guides-using-custom-llms).

