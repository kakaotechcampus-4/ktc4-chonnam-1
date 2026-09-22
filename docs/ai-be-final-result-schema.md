# BE ↔ AI `ai.pipeline` 결과 계약

이 문서는 새 `ai.pipeline`이 받는 입력과 BE에 반환하는 `AnalysisResponse`를 설명한다. 기준은 커밋 [`9a85dfb`](https://github.com/kakaotechcampus-4/ktc4-chonnam-1/commit/9a85dfbf057e29f22f5b512d09113fa8787116a5)에서 구현된 계약과 [PR #17](https://github.com/kakaotechcampus-4/ktc4-chonnam-1/pull/17)이다. 이 문서의 수정 대상은 [PR #18](https://github.com/kakaotechcampus-4/ktc4-chonnam-1/pull/18)이다.

기존 화이트리스트 기반 API의 정책을 바꾸거나 그 결과를 새 파이프라인에 자동으로 연결하는 계약이 아니다. 여기서 `official`은 공식 도메인 여부가 아니라 BE가 URL 분석 점수를 임계값과 비교해 만든 엄격한 boolean이다. 기존 `DomainCheck`, `DomainMatch`, `decide()`의 화이트리스트 판정과 구분해서 사용한다.

근거 자료는 기준 커밋에 고정한다.

- [Revisions 요구사항](https://github.com/kakaotechcampus-4/ktc4-chonnam-1/blob/9a85dfbf057e29f22f5b512d09113fa8787116a5/docs/ai/Revisions.md)
- [연동 책임과 호출 계약](https://github.com/kakaotechcampus-4/ktc4-chonnam-1/blob/9a85dfbf057e29f22f5b512d09113fa8787116a5/docs/ai/Revisions-handoff.md)
- [입출력 타입과 허용 목록](https://github.com/kakaotechcampus-4/ktc4-chonnam-1/blob/9a85dfbf057e29f22f5b512d09113fa8787116a5/ai/src/ai/types.py)
- [결과 조립 규칙](https://github.com/kakaotechcampus-4/ktc4-chonnam-1/blob/9a85dfbf057e29f22f5b512d09113fa8787116a5/ai/src/ai/pipeline/results.py)
- [메시지·페이지 분석 함수](https://github.com/kakaotechcampus-4/ktc4-chonnam-1/blob/9a85dfbf057e29f22f5b512d09113fa8787116a5/ai/src/ai/pipeline/analysis.py)

## 입력과 공개 함수

새 파이프라인은 하나의 원격 JSON envelope를 받는 API로 정의되지 않았다. 현재 계약은 다음 세 Python 함수다.

| 함수 | 입력 | 반환과 역할 |
| --- | --- | --- |
| `async analyze_message_part(text: str, *, client: AsyncOpenAI \| None = None, model: str \| None = None) -> MessagePart` | BE가 URL을 제거한 문자 본문 | 현재 문자만 분석한 `MessagePart` |
| `async analyze_environment_part(page: IsolatedPage \| None, *, failure: FailureCode \| None = None, client: AsyncOpenAI \| None = None, model: str \| None = None) -> EnvironmentPart` | 성공 시 수집 페이지, 실패 시 `page=None`과 구체적인 실패 코드 | 전달된 HTML과 수집 상태를 분석한 `EnvironmentPart` |
| `assemble_analysis(url: UrlAnalysis, message: MessagePart \| None = None, env: EnvironmentPart \| None = None) -> AnalysisResponse` | 검증된 URL 결과와 두 부분 결과 | I/O 없이 최종 응답 조립 |

`client`와 `model`은 테스트·설정 주입용 선택 인자다. BE가 AI 내부의 LLM 설정이나 호출을 대신 구현하는 입력이 아니다.

### URL 분석 입력

`UrlAnalysis`는 `final_url`, `domain`, `official`을 받는다. `final_url`과 `domain`은 공백뿐인 문자열을 포함해 빈 값을 거부한다. `official`은 문자열 `"true"`, 문자열 `"false"`, 숫자 `0`이나 `1`, `null`을 받지 않는 엄격한 boolean이다.

```json
{
  "final_url": "https://example.com/track",
  "domain": "example.com",
  "official": false
}
```

BE는 URL 분석의 악성 위험도 점수 `score`가 `score <= T`이면 `official=true`, `score > T`이면 `official=false`로 가공한다. 임계값과 같은 점수는 `true`에 포함한다. AI는 원점수와 임계값을 입력받지 않는다. 실제 점수 필드 경로, 임계값 `T`, 점수 누락·조회 실패·범위 초과 처리에는 이 문서가 기본값을 만들지 않으며 BE 연동 계약에서 확정해야 한다.

### 격리 페이지 입력

수집에 성공하면 `IsolatedPage(brand, category, info)`를 전달한다. `info`는 수집한 HTML이며 AI는 이를 실행하거나 페이지에 접속하지 않는다.

```json
{
  "brand": "unknown",
  "category": "unknown",
  "info": "<form><label>아이디<input name=\"username\"></label><label>비밀번호<input type=\"password\"></label></form>"
}
```

`brand`와 `category`는 수집기가 제공한 메타데이터다. 실제 발신자나 페이지의 진위를 확인한 값으로 해석하지 않는다. 페이지 분석이 실패했더라도 이 메타데이터가 아래 허용 목록의 값이면 확보한 정보로 보존할 수 있고, 그 출처를 `reason`에 명시한다. 목록 밖이거나 식별할 수 없는 값은 출력에서 `unknown`으로 남는다.

수집 성공과 실패는 호출부터 구분한다.

- 성공: `analyze_environment_part(IsolatedPage(...))`
- 수집 실패: `analyze_environment_part(None, failure=FailureCode.COLLECTION_FAILED)`
- 시간 초과: `analyze_environment_part(None, failure=FailureCode.TIMEOUT)`
- 부분 자료와 알려진 실패가 함께 있음: `analyze_environment_part(page, failure=...)`

이는 Python 호출 계약이다. HTTP 요청이나 원격 JSON으로 옮길 때 사용할 오류 envelope는 아직 정의되지 않았다.

## 전체 응답

외부 키는 `url`, `message`, `env`, `result` 네 개다. `message`와 `env`는 같은 객체 모양을 유지하지만 서로 다른 출처를 분석하며 `doubt`의 허용 목록도 다르다.

| 필드 | 의미 |
| --- | --- |
| `url` | BE가 전달한 검증된 `UrlAnalysis`. 응답에서도 값을 유지한다. |
| `message` | URL을 제거한 현재 문자 본문을 분석한 결과 |
| `env` | 수집기가 제공한 HTML과 수집 상태를 분석한 결과 |
| `result` | 최종 결과. 항상 `url.official`과 같은 boolean |

두 부분의 공통 모양은 `brand`, `category`, `answer`, `details.doubt`, `details.reason`이다.

- `brand`와 `category`는 이 문서의 닫힌 허용 목록 중 하나다.
- `answer=true`는 해당 출처의 분석을 완료했고 검증된 의심 신호가 없다는 뜻이다.
- `answer=false`는 해당 출처에서 의심 신호를 확인했거나 분석을 완료하지 못했다는 뜻이다. 두 경우는 `reason`으로 구별한다.
- `details.doubt`는 분류값이다. 일반 배송 조회나 로그인 폼 같은 값도 포함하므로 값의 존재만으로 악성을 뜻하지 않는다.
- `details.reason`은 현재 출처에서 확인한 근거나 실패 사실을 설명하는 평문이다.
- `null`은 `official=true` 조기 반환에서만 사용한다. 이때 한 부분의 말단 일부만 `null`로 만들지 않는다.

### `official=false` 전체 응답

아래 예시에서는 문자와 페이지 분석의 `answer`가 모두 `true`지만 `official=false`이므로 `result=false`다. 배송 조회 문자와 일반 로그인 폼은 출처별 분류로 보존한다. 두 값의 차이와 로그인 폼 자체는 결과를 뒤집거나 악성을 확정하는 근거가 아니다. `reason` 문장은 계약의 의미를 보여 주는 예시이며 실제 모델 출력이나 페이지 수집 성공을 주장하지 않는다.

```json
{
  "url": {
    "final_url": "https://example.com/track",
    "domain": "example.com",
    "official": false
  },
  "message": {
    "brand": "CJ대한통운",
    "category": "택배",
    "answer": true,
    "details": {
      "doubt": "배송 조회",
      "reason": "문자에서 '배송 현황을 확인하세요'라고 안내했습니다."
    }
  },
  "env": {
    "brand": "CJ대한통운",
    "category": "택배",
    "answer": true,
    "details": {
      "doubt": "로그인·인증 입력폼",
      "reason": "전달된 HTML에서 계정과 비밀번호 입력을 요구하는 폼 요소를 확인했습니다."
    }
  },
  "result": false
}
```

### `official=true` 조기 반환

`official=true`이면 `message`, `env`, 두 `details` 객체와 모든 키를 유지한다. 각 부분의 `brand`, `category`, `answer`, `details.doubt`, `details.reason`을 모두 `null`로 반환하므로 말단 `null`은 모두 10개다. 분석 실패가 먼저 발생했거나 일부 결과가 준비됐더라도 이 조기 반환 구조가 우선한다.

```json
{
  "url": {
    "final_url": "https://example.com/track",
    "domain": "example.com",
    "official": true
  },
  "message": {
    "brand": null,
    "category": null,
    "answer": null,
    "details": {
      "doubt": null,
      "reason": null
    }
  },
  "env": {
    "brand": null,
    "category": null,
    "answer": null,
    "details": {
      "doubt": null,
      "reason": null
    }
  },
  "result": true
}
```

### 분석 실패 부분 응답

다음 예시는 페이지 접속·수집 실패 사실만 확인했고 다른 페이지 정보는 확보하지 못한 경우다. `answer=null`이나 `없음`으로 실패를 숨기지 않는다.

```json
{
  "brand": "unknown",
  "category": "unknown",
  "answer": false,
  "details": {
    "doubt": "unknown",
    "reason": "페이지 접속·수집에 실패하여 내용을 확인하지 못했으므로 의심으로 처리했습니다."
  }
}
```

실패 전에 확인한 브랜드·분야·행동과 근거가 있다면 그 값은 보존한다. 한 분석이 실패해도 다른 분석의 완료 결과를 지우지 않는다. 실패는 악성, 스미싱 또는 분석 회피가 확인됐다는 뜻이 아니다.

## 최종 결과 결정

유효한 `url.official`을 입력받으면 다음 규칙만 최종 결과를 정한다.

`result = url.official`

LLM 출력, RAG 유사도, `message.answer`, `env.answer`, 브랜드·분야의 일치 여부가 이 값을 뒤집지 않는다.

| `url.official` | 메시지·페이지 상태 | `result` | 응답 처리 |
| --- | --- | --- | --- |
| `true` | 완료, 의심 신호 또는 실패 여부와 무관 | `true` | 두 부분 객체와 키를 유지하고 말단 값 10개를 모두 `null`로 반환 |
| `false` | 두 `answer`가 모두 `true` | `false` | 완료한 분류와 출처별 근거를 제공 |
| `false` | 하나 이상에서 검증된 의심 신호 확인 | `false` | 확인한 신호와 근거를 제공 |
| `false` | 실패·시간 초과·결과 누락 | `false` | 실패한 부분은 `answer=false`, 이미 확보한 결과는 보존 |

`result=false`는 이 서비스의 기준에 따른 의심이다. 악성 또는 스미싱 확정으로 바꾸어 표현하지 않는다. 점수 기준을 통과하지 못했다는 사실을 공식 도메인이 아니라고 확인한 것으로 표현해서도 안 된다.

## 정형값 허용 목록

다음 목록은 기준 구현의 Enum과 정확히 같다. 빈 문자열과 자유 문자열은 출력 정형값으로 사용하지 않는다.

### `Brand`

| 허용 값 |
| --- |
| `CJ대한통운` |
| `CJ택배` |
| `CJ익스프레스` |
| `CJ오쇼핑` |
| `한진택배` |
| `로젠택배` |
| `우체국택배` |
| `DHL` |
| `현대택배` |
| `롯데택배` |
| `CU` |
| `대신택배` |
| `KGB택배` |
| `경동택배` |
| `합동택배` |
| `쿠팡` |
| `옥션` |
| `롯데몰` |
| `카카오톡 선물하기` |
| `7-11` |
| `라쿠텐 익스프레스` |
| `KISA` |
| `검찰청` |
| `unknown` |

### `Topic`

| 허용 값 |
| --- |
| `택배` |
| `쇼핑` |
| `금융` |
| `공공기관` |
| `의료·건강` |
| `보안` |
| `선물·이벤트` |
| `unknown` |

### `MessageDoubt`

| 허용 값 |
| --- |
| `앱 설치` |
| `주소 입력·수정` |
| `주소 확인` |
| `본인 확인` |
| `정보 입력` |
| `사진 확인` |
| `배송 조회` |
| `상세 내용 확인` |
| `주문 취소·환불` |
| `수령·일정 확인` |
| `금전 인출` |
| `전화 응대` |
| `링크 접속` |
| `없음` |
| `unknown` |

### `EnvDoubt`

| 허용 값 |
| --- |
| `앱 다운로드 링크` |
| `로그인·인증 입력폼` |
| `결제 요청 요소` |
| `개인정보 입력폼` |
| `주소 입력폼` |
| `배송 조회 요소` |
| `사진·문서 열람 요소` |
| `없음` |
| `unknown` |

`MessageDoubt`와 `EnvDoubt`는 서로 바꿔 쓰지 않는다. 각 목록의 `없음`은 해당 출처의 분석을 완료했지만 분류 대상 요구나 요소를 확인하지 못했다는 뜻이다. `unknown`은 식별 불가, 목록 밖 또는 실패로 확인하지 못한 상태다. `null`은 `official=true` 조기 반환으로 분석 결과를 제공하지 않은 상태다.

## 근거와 실패 처리

문자, 페이지, RAG 참고 자료는 역할이 다르다.

- 문자 근거는 URL을 제거해 전달받은 현재 본문에 실제로 있는 표현이어야 한다. 다른 문자나 페이지의 문구를 복사하지 않는다.
- RAG 검색 결과는 참고 자료다. 검색된 사례의 브랜드·분야·행동을 현재 입력에서 관측한 사실처럼 복사하지 않는다.
- 페이지 근거는 전달받은 HTML과 별도로 전달된 수집 사실에 한정한다. HTML 요소가 있다는 사실을 실제 화면 노출, 클릭, 다운로드, 폼 제출 또는 정보 전송을 확인했다는 문장으로 확대하지 않는다.
- 일반 로그인 폼, 결제 UI 또는 문자·페이지 분류의 차이는 그 자체로 위험 신호가 아니다. 별도로 검증된 근거가 있어야 해당 부분의 `answer`에 반영한다.
- 하나의 추출·검색·신호 분석이 실패해도 다른 단계에서 확인한 분류와 근거를 보존한다. 실패 이유는 확인한 사실에 맞는 결정적 평문으로 추가한다.
- `reason`은 사용자에게 표시할 수 있는 평문이다. 원본 HTML을 설명 대신 넣거나 실행 가능한 형태로 렌더링하지 않는다.

## 연동 책임

아래 항목은 기준 인계 문서의 연동 요구사항이다. 현재 temp2 브랜치에 BE·FE·격리 환경 구현이 완료됐다는 뜻은 아니다.

### BE

- 원본 메시지에서 URL과 URL을 제거한 본문을 분리하고 입력 타입을 검증한다.
- URL 분석·메시지 분석·격리 환경 수집을 병렬로 시작한다. URL 분석이 끝나 `official`이 도착한 뒤 그 값에 따라 후속 처리를 결정한다.
- `official=true`가 도착하면 불필요한 작업을 취소하고 `assemble_analysis(url)`로 조기 반환한다. 늦게 끝난 결과가 저장된 응답을 덮어쓰지 않게 한다.
- `official=false`이면 확보한 두 부분 결과를 조립한다. 알려진 수집 실패를 단순 누락으로 바꾸지 않고 구체적인 `FailureCode`로 전달한다.
- `response.model_dump(mode="json")`으로 직렬화하며 `exclude_none=True`나 `exclude_unset=True`로 조기 반환 키를 제거하지 않는다.
- 콜백, 저장, 결과 조회 폴백과 외부 시간 제한을 관리한다.

### 격리 환경 수집기

- 성공 시 `{brand, category, info}` 자료를 제공하고 실패·시간 초과·부분 수집을 구별해 BE에 전달한다.
- `info`에는 수집한 HTML을 넣는다. 실제 화면 노출이나 사용자 동작을 관측했다면 HTML과 구분되는 별도 계약이 필요하다.
- 취소와 입력 크기 제한을 BE·AI와 합의한다.

### FE와 사용자 응답 가공

- `result=false`를 의심으로 표시하고 개별 `answer`와 최종 결과를 구분한다.
- `null`, `unknown`, `없음`을 서로 다른 상태로 표시한다.
- 문자와 페이지의 `doubt` 목록을 분리하고, 두 값의 차이나 일반 UI를 악성 확정 문구로 바꾸지 않는다.
- 실패의 `answer=false`에는 `reason`을 함께 표시해 의심 신호 발견과 확인 실패를 구별한다.
