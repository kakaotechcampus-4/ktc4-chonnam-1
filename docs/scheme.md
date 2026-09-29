# AI → BE 분석 응답 스키마 (`AnalysisResponse`)

PR #24 멘토 피드백을 반영한 wire 포맷이다. 코드(`ai/src/ai/types.py`) 반영은 추후 작업이다.

> 주석을 포함하므로 엄밀한 JSON 이 아니라 JSONC 다.

---

## 1. 스키마

```jsonc
{
  "url": {
    /* 리다이렉트 이후 최종 URL. 격리 환경이 확인한 값. 빈 문자열 불가 */
    "final_url": "https://delivery-example.com/login",

    /* 화이트리스트 대조 대상 도메인. 빈 문자열 불가 */
    "domain": "delivery-example.com",

    /*
     * BE가 화이트리스트와 대조한 결과 (DomainMatch)
     * "official"       : 공식 도메인과 일치
     * "brand_mismatch" : 브랜드를 주장하지만 공식 도메인과 다름
     * "not_registered" : 대조했으나 목록에 없는 도메인
     * "unresolved"     : 도메인을 확인하지 못해 대조하지 못함
     */
    "official": "not_registered",

    /*
     * URL 스캐너 점수. 공급자(urlscan / 자체 시스템)와 무관하게 같은 형태
     * 판정에 쓰지 않는 기록·비교용 값이다. result 계산에 넣지 않는다
     */
    "scan": {
      /* 원본 점수 -100(정상) ~ 100(악성). 타입: number | null. 받지 못했으면 null */
      "score": 35,

      /* 조회 시각 (ISO 8601). 조회하지 않았으면 null */
      "scanned_at": "2026-09-29T10:00:30Z"
    }
  },

  "message": {
    /* 문자가 주장하는 브랜드 (Brand). 확인하지 못했으면 null */
    "brand": "CJ대한통운",

    /* 문자 주제 (Topic). 확인하지 못했으면 null */
    "category": "delivery",

    /*
     * 단계 상태와 결과 (AnswerState)
     * "no_risk_found" : 분석을 완료했고 채택된 위험 근거가 없음
     * "risk_found"    : 분석을 완료했고 검증된 위험 근거가 하나 이상 있음
     * "partial"       : 일부만 분석함. 그 범위에서 찾은 근거는 signals 에 싣는다
     * "failed"        : 실행했지만 확보한 자료가 없음
     * "not_run"       : 실행하지 않음 (결과 미전달, 수집기 미연결)
     *
     * answer 는 완료 여부를 우선한다. 미완료 상태의 의심 근거는
     * answer 가 아니라 doubts / signals 로 전달한다.
     */
    "answer": "risk_found",

    "details": {
      /*
       * 문자에서 확인한 요구 행동 전부 (MessageDoubt). 원문 순서
       * 위험 여부와 무관한 사실 목록이며 answer 에 영향을 주지 않는다
       * 없거나 확인하지 못했으면 [] — 두 경우는 answer 로 구분한다
       */
      "doubts": [
        { "value": "배송 조회", "evidence": "배송 조회는 아래 링크에서" },
        { "value": "정보 입력", "evidence": "비밀번호를 입력해주세요" }
      ],

      /*
       * 검증을 통과한 위험 근거 전부 (RiskSignalCode)
       * evidence 는 문자 원문에 실제 존재하는 4자 이상 부분문자열
       *
       * answer 별 규칙
       *   "risk_found"          : 1개 이상
       *   "no_risk_found"       : 반드시 []
       *   "partial"             : [] 또는 1개 이상 (분석한 범위에서 찾은 근거)
       *   "failed" / "not_run"  : 반드시 []
       */
      "signals": [
        { "code": "credential_request", "evidence": "비밀번호를 입력해주세요" }
      ],

      "reason": {
        /* 사용자에게 보여줄 근거·한계 설명. 항상 비어 있지 않은 문자열 */
        "text": "문자에서 '비밀번호를 입력해주세요'라고 안내했습니다.",

        /*
         * 미완료 원인 (FailureCode 배열)
         * answer 가 "no_risk_found" 이면 반드시 []
         * answer 가 "risk_found" 이면 반드시 []
         * answer 가 "partial" / "failed" / "not_run" 이면 1개 이상
         */
        "failures": []
      }
    }
  },

  "env": {
    /* 페이지가 보여주는 브랜드 (Brand). 확인하지 못했으면 null */
    "brand": "CJ대한통운",

    /* 페이지 주제 (Topic). 확인하지 못했으면 null */
    "category": "delivery",

    /* message.answer 와 같은 AnswerState */
    "answer": "partial",

    /* 격리 환경의 페이지 수집 시각 (ISO 8601). 수집하지 못했으면 null */
    "collected_at": "2026-09-29T10:00:25Z",

    "details": {
      /*
       * 페이지에서 확인한 요소 전부 (EnvDoubt). 원문 순서
       * evidence 는 검사한 요소의 설명 (예: "주소 입력 필드")
       */
      "doubts": [
        { "value": "로그인·인증 입력폼", "evidence": "비밀번호 입력 필드" }
      ],

      /*
       * 검증을 통과한 위험 근거 전부 (RiskSignalCode)
       * evidence 는 검사에서 실제 확인한 요소의 설명
       * answer 별 규칙은 message.details.signals 와 같음
       *
       * 아래 예시: 일부만 검사했지만(partial) 검사한 범위에서 근거를 찾은 경우
       */
      "signals": [
        { "code": "credential_request", "evidence": "비밀번호 입력 필드" }
      ],

      "reason": {
        "text": "전달된 HTML에서 로그인·인증 입력폼을 확인했습니다. 자료 크기 제한으로 전체를 확인하지 못했습니다.",
        "failures": ["input_too_large"]
      }
    }
  },

  /*
   * 신뢰 가능 여부. 타입: boolean
   * url.official == "official"
   *   && message.answer == "no_risk_found"
   *   && env.answer == "no_risk_found"
   * false 인 이유는 url.official 과 각 answer·signals 에서 읽는다
   * url.scan 은 넣지 않는다
   */
  "result": false
}
```

---

## 2. enum 정의

| 이름 | 사용 위치 | 값 |
|---|---|---|
| `DomainMatch` | `url.official` | `official`, `brand_mismatch`, `not_registered`, `unresolved` |
| `AnswerState` | `message.answer`, `env.answer` | `no_risk_found`, `risk_found`, `partial`, `failed`, `not_run` |
| `RiskSignalCode` | `details.signals[].code` | `install_prompt`, `credential_request`, `dangerous_permission`, `remote_control`, `oversized_payload`, `packer_detected`, `brand_mismatch` |
| `FailureCode` | `details.reason.failures[]` | `empty_input`, `missing_result`, `collection_failed`, `timeout`, `input_too_large`, `partial_content`, `llm_error`, `refused`, `invalid_output` |

- `Brand`, `Topic`, `MessageDoubt`, `EnvDoubt` 값은 `ai/src/ai/types.py` 의 기존 정의를 그대로 쓴다.
- 단, `MessageDoubt.NONE` / `EnvDoubt.NONE` (`"없음"`) 은 삭제한다. 빈 배열 `[]` 이 대신한다.
- `RiskSignalCode.EXPIRED_LINK` 는 신호가 아니므로 제외한다.

---

## 3. `doubts` 와 `signals`

| | `doubts` | `signals` |
|---|---|---|
| 뜻 | 무엇을 하라고 하는가 | 그중 위험하다고 검증된 것은 무엇인가 |
| 성격 | 중립 사실. 무해한 행동 포함 | 판정 근거 |
| 생성 | 코드 규칙으로 추출 | LLM 제안 → 코드가 원문·요소 존재를 검증 |
| `answer` 영향 | 없음 | 분석을 완료했을 때 `risk_found` / `no_risk_found` 를 가른다 |
| 용도 | FE 안내 ("이 문자는 ○○을 요구해요") | 판정과 "왜 위험한지" 설명 |

- `signals` 의 근거 원문은 대개 `doubts` 에도 있다.
- `doubts` 만 있고 `signals` 가 없으면 `answer` 는 `no_risk_found` 다. 요구 행동의 존재만으로 위험하다고 보지 않는다.
- 어휘는 1:1 대응이 아니다. `brand_mismatch`, `oversized_payload` 처럼 행동이 아닌 신호는 대응하는 `doubts` 값이 없다.

---

## 4. `answer` 결정 규칙

위에서부터 먼저 맞는 값을 쓴다.

1. 단계를 실행하지 않았으면 `not_run`
2. 실행했지만 확보한 자료가 없으면 `failed`
3. 일부 자료만 분석했으면 `partial` — 찾은 근거가 있어도 `partial`
4. 완료했고 `signals` 가 비어 있지 않으면 `risk_found`
5. 그 외는 `no_risk_found`

`answer` 는 완료 여부를 우선한다. 미완료 상태에서 찾은 의심 근거는 `doubts` / `signals` 로 전달한다.

> **소비자 주의:** `answer == "partial"` 이어도 `signals` 가 비어 있지 않으면 검증된 위험 근거가 있다는 뜻이다.
> FE 안내와 BE 의 KISA 인계 조건은 `answer` 만 보지 말고 `signals` 도 함께 봐야 경고를 놓치지 않는다.

### 사례별 표현

| 사례 | `answer` | `signals` | `failures` |
|---|---|---|---|
| 완료, 근거 있음 | `risk_found` | 1개 이상 | `[]` |
| 완료, 근거 없음 | `no_risk_found` | `[]` | `[]` |
| 자료를 일부만 받음, 근거 없음 | `partial` | `[]` | `["partial_content"]` 또는 `["input_too_large"]` |
| 자료를 일부만 받음, 근거 있음 | `partial` | 1개 이상 | `["partial_content"]` 또는 `["input_too_large"]` |
| 시간 초과 | `partial` 또는 `failed` | 확보한 범위의 근거 | `["timeout"]` |
| 실제 수집 실패 | `failed` | `[]` | `["collection_failed"]` |
| 수집기 미연결로 실행 안 함 | `not_run` | `[]` | `["missing_result"]` |

분석 실패는 `failed` 로, `risk_found` 와 구분되므로 악성으로 안내되지 않는다.

---

## 5. 역할 분담 — urlscan 대체 후

urlscan 은 페이지 접속·관측과 악성 점수 산출을 한 번에 한다. 자체 시스템은 이를 격리 서버(수집)와
메인 서버의 점수기로 나눠 대체한다. wire 의 `url.scan` 형태는 공급자가 바뀌어도 같다.

| 역할 | 담당 | 결과 위치 |
|---|---|---|
| 접속·리다이렉트 추적·페이지 수집 | 격리 서버 | `url.final_url`, `env` |
| 점수 산출 (URL·도메인 특징 + 격리 서버가 넘긴 페이지 특징) | 메인 서버 점수기 | `url.scan` |
| 공식 도메인 대조 | BE 화이트리스트 | `url.official` |

- 점수기를 메인 서버에 두어 격리 서버의 분석 부하를 줄인다.
- 의심 URL 에 요청을 보내는 작업은 격리 서버만 한다. 점수기는 격리 서버가 넘긴 자료를 데이터로만 다루고
  그 안의 URL 로 추가 접속하지 않는다. WHOIS·DNS·위협 피드는 도메인 문자열로만 조회한다.
- `url.scan` 은 판정에 쓰지 않으므로 페이지 특징을 점수에 넣어도 `env` 와 이중 반영되지 않는다.
- 공급자·버전, 항목별 세부 점수, threshold 는 wire 에 싣지 않고 BE 작업 기록에 남긴다.
- 점수기 항목·가중치·검증 절차는 별도 설계 문서에서 정한다.
