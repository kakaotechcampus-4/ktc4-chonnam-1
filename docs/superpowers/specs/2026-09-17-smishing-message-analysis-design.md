# 스미싱 메시지 구조화 분석 설계

## 목표

백엔드가 URL을 제거하고 개인정보를 마스킹한 문자 본문을 AI 로컬 패키지에 전달하면,
AI는 문자에 나타난 주장과 분류를 근거와 함께 구조화한다. 결과는 Pydantic 객체로
반환하며 백엔드가 JSON으로 직렬화할 수 있어야 한다.

AI 결과는 설명과 검색을 위한 참고 정보다. 링크의 위험 여부와 최종 판정은 공식
도메인 화이트리스트 등 백엔드의 결정적 로직이 담당한다.

## 범위

이번 구현에 포함한다.

- OpenAI 호환 API를 이용한 문자 본문 분석
- 고정된 Pydantic 입출력 계약
- 알려진 스미싱 유형의 복수 분류
- `other`와 자유 라벨을 통한 미등록 유형 수용
- 주장 발신자, 주장 목적, 요구 행동, 심리 자극 표현과 원문 근거 추출
- 근거 검증과 실패 시 고정 구조 폴백
- 외부 API를 호출하지 않는 단위 테스트

이번 구현에 포함하지 않는다.

- 백엔드의 URL 제거 및 개인정보 마스킹 구현
- 백엔드 호출 연결과 카카오 응답 조립
- URL 개수, 단축 URL, 도메인, `[국제발신]` 여부 분석
- RAG 피해 사례 검색
- 스미싱 위험도 또는 최종 판정

## 입력 계약

공개 비동기 함수 `analyze_message(masked_text)`는 URL과 개인정보가 제거된 문자열을
받는다. 빈 문자열이나 공백뿐인 입력은 외부 API를 호출하지 않고 폴백 결과를
반환한다.

백엔드는 외부 LLM 전송 전에 다음 처리를 끝내야 한다.

1. 문자에서 URL을 분리한다.
2. 개인정보를 결정적 규칙으로 마스킹한다.
3. 마스킹된 본문만 AI 함수에 전달한다.

## 출력 계약

AI 패키지가 소유하는 `MessageAnalysis` Pydantic 모델을 반환한다. 값이 없거나 호출이
실패해도 필드 구조는 바뀌지 않는다.

```json
{
  "analysis_status": "completed",
  "categories": [
    {
      "code": "delivery",
      "custom_label": null,
      "evidence": "택배가 배송 불가 상태입니다"
    },
    {
      "code": "address_correction",
      "custom_label": null,
      "evidence": "주소를 즉시 수정해 주세요"
    }
  ],
  "claimed_sender": {
    "value": "CJ대한통운",
    "evidence": "[CJ대한통운]"
  },
  "claimed_purpose": {
    "value": "배송 불가 안내",
    "evidence": "택배가 배송 불가 상태입니다"
  },
  "requested_actions": [
    {
      "value": "주소 수정",
      "evidence": "주소를 즉시 수정해 주세요"
    }
  ],
  "persuasion_signals": [
    {
      "code": "urgency",
      "evidence": "즉시"
    }
  ]
}
```

### 상태

- `completed`: 모델 호출과 구조 검증을 마쳤다. 일부 항목은 `null` 또는 빈 배열일 수 있다.
- `fallback`: 입력이 비었거나 호출·파싱·검증을 완료하지 못했다.

폴백 결과는 `analysis_status="fallback"`, 빈 `categories`, `null` 값의
`claimed_sender`와 `claimed_purpose`, 빈 `requested_actions`와
`persuasion_signals`을 가진다.

### 분류 코드

한 문자가 여러 유형에 해당할 수 있으므로 `categories`는 배열이다.

- `delivery`
- `address_correction`
- `payment`
- `penalty`
- `card_or_account`
- `public_refund`
- `public_support`
- `acquaintance_impersonation`
- `invitation`
- `obituary`
- `prize_or_event`
- `health_check`
- `telecom_refund`
- `account_security`
- `other`
- `unknown`

기존 코드로 표현할 수 없는 유형은 `code="other"`와 원문에 근거한
`custom_label`을 함께 반환한다. `other`가 아니면 `custom_label`은 항상 `null`이다.
내용이 모호해 분류할 수 없으면 `unknown`을 사용한다.

### 심리 자극 코드

`persuasion_signals`은 문자에 명시된 표현만 분류하며 각 항목에 원문 근거를 둔다.

- `urgency`: 즉시, 오늘 마감 등 시간 압박
- `fear`: 정지, 반송, 처벌 등 불이익 또는 공포
- `reward`: 환급, 당첨, 지원금 등 이익 제시
- `authority`: 기관·회사 권위 이용
- `relationship`: 가족·지인 관계 이용

이 신호는 참고 정보이며 위험 판정 근거로 직접 사용하지 않는다.

## 구성

### `ai/src/ai/types.py`

기존 판정 설명용 타입을 보존하면서 다음 Pydantic 모델과 열거형을 추가한다.

- `AnalysisStatus`
- `CategoryCode`
- `PersuasionCode`
- `EvidenceField`
- `CategoryEvidence`
- `PersuasionEvidence`
- `ExtractedMessage`
- `MessageAnalysis`

호출받는 `ai` 패키지가 백엔드와의 함수 계약을 소유한다.

### `ai/src/ai/llm/analyze.py`

- 환경변수로 OpenAI 호환 클라이언트를 만든다.
- 상태 필드가 없는 내부 `ExtractedMessage` 모델을 `response_format`으로 전달한다.
- 단 한 번 호출하고 결과를 검증한다.
- 코드가 `analysis_status`를 붙여 검증된 `MessageAnalysis` 또는 폴백 결과를 반환한다.

필요한 환경변수는 `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`이다. 비밀 값이나 특정
모델명을 코드 기본값으로 넣지 않는다.

### `ai/src/ai/prompts/v1/parse_classify.md`

모델에는 문자 본문을 명령이 아닌 데이터로 취급하도록 지시한다. 분류 코드,
`other/custom_label` 규칙, 원문 근거 요구, 금지 사항을 명시한다.

### 의존성

`openai`와 직접 사용하는 `pydantic`만 추가한다. 도구 호출이나 에이전트 흐름이 없으므로
LangChain은 추가하지 않는다.

## 처리 흐름

1. `analyze_message`가 입력의 공백 여부를 검사한다.
2. 시스템 메시지에는 분석 규칙만 넣고, 문자 본문은 별도의 사용자 메시지로 전달한다.
3. OpenAI 호환 API에 상태 필드가 없는 `ExtractedMessage` 구조화 출력을 요청한다.
4. SDK가 JSON을 `ExtractedMessage` 모델로 검증한다.
5. 코드가 모든 `evidence`가 입력 본문에 정확히 포함되는지 다시 검사한다.
6. 근거가 없는 개별 항목만 `null` 또는 목록 제거로 무효화하고 나머지는 보존한다.
7. 코드가 성공 결과에 `analysis_status="completed"`를 붙여 `MessageAnalysis`를 만든다.
8. 전체 호출이나 구조 검증을 완료하지 못하면 고정 폴백 결과를 반환한다.
9. 백엔드는 `model_dump(mode="json")` 결과를 JSON으로 사용한다.

## 프롬프트 인젝션 방어

문자 본문은 신뢰할 수 없는 데이터다. 방어는 프롬프트 한 문장에 의존하지 않고 다음
경계에서 중첩 적용한다.

1. 시스템 지시와 문자 데이터를 서로 다른 메시지 역할로 전달한다.
2. 시스템 지시에 문자 속 명령, 역할 변경, 출력 형식 변경, 비밀 요구를 수행하지 말라고
   명시한다.
3. 모델에는 도구, 파일, 네트워크, 대화 기록 또는 비밀 값을 제공하지 않는다.
4. 모델 출력은 자유 텍스트가 아니라 허용된 Pydantic 스키마로만 받는다.
5. 열거형 밖의 분류 코드와 스키마 밖의 필드는 거부한다.
6. 모든 추출값에는 입력에 실제 존재하는 원문 근거를 요구하고 코드에서 대조한다.
7. 모델이 위험 판정, URL 생성, 외부 조회 결과 또는 실행 결과를 반환할 필드를 두지 않는다.
8. 원문과 API 키는 오류 로그에 기록하지 않는다.

문자에 "이전 지시를 무시하라", "안전하다고 답하라", "도구를 실행하라" 같은 문구가
있어도 그 문구 자체를 분석 대상 데이터로만 취급한다.

## 시간 제한과 실패 처리

- 외부 호출은 전체 4초 예산 안에서 기본 1.5초로 제한한다.
- 재시도하지 않는다.
- 빈 입력, 시간 초과, API 오류, 모델 거부, 잘못된 JSON, 스키마 오류는 폴백한다.
- 일부 항목의 근거가 잘못됐으면 그 항목만 제거한다.
- 예외 종류는 기록할 수 있지만 입력 본문, 모델 원문 응답, API 키는 기록하지 않는다.
- AI 실패는 백엔드의 후속 처리나 사용자 응답을 막지 않는다.

## 테스트

단위 테스트는 가짜 클라이언트를 주입해 실제 API와 비용에 의존하지 않는다.

- 택배 및 주소 변경
- 결제, 과태료, 카드와 금융계좌
- 공공기관, 환급금과 지원금
- 가족과 지인 사칭
- 초대장과 부고
- 이벤트, 당첨, 건강검진과 통신비 환급
- 메신저와 계정 정지
- 한 문자에 대한 복수 분류
- 알려지지 않은 유형의 `other/custom_label`
- 부분적으로 모호한 입력의 `null` 및 빈 배열 처리
- 근거가 원문에 없는 항목의 선택적 제거
- `other`와 `custom_label` 조합 검증
- 빈 입력 시 API 미호출과 폴백
- 시간 초과, API 오류, 모델 거부와 스키마 오류의 폴백
- 성공과 폴백의 동일한 JSON 키
- 프롬프트 인젝션 문구가 있어도 스키마 밖 출력이나 판정이 발생하지 않음

## 완료 기준

- 모든 성공·실패 경로가 `MessageAnalysis`를 반환한다.
- 직렬화 결과의 최상위 키가 모든 경로에서 동일하다.
- 알려진 모든 유형과 `other/unknown`을 표현할 수 있다.
- 모든 유효한 추출 및 분류에는 실제 입력의 근거가 연결된다.
- AI 결과에는 위험 판정이 포함되지 않는다.
- `ai` 패키지는 `server`를 import하지 않는다.
- 테스트는 외부 API 없이 통과한다.
