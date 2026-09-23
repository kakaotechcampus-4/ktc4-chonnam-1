# BE 담당자용 AI Python 연동 코드

BE는 URL을 제거한 문자 본문을 먼저 분석하고, URL 분석 결과와 격리 수집 자료를 준비한 뒤 **`await finalize_analysis(...)` 한 번으로 전체 최종 결과를 받는다.** 격리 페이지 분석과 결과 조립은 AI 함수 내부에서 수행한다. 문자 분석 결과는 재사용한다.

별도 AI HTTP 서버 없이 BE의 Python 코드에서 AI 패키지를 직접 호출한다. 이 문서의 세 구간은 BE가 작성할 코드 예시이며, 기존 BE 서비스에 연동이 완료됐다는 뜻은 아니다. 반환 스키마와 전체 JSON 예시는 [결과 계약](./ai-be-final-result-schema.md), 파트별 작업은 [인계 문서](./ai/Revisions-handoff.md)를 참고한다.

| 구간 | BE가 작성할 구문 | 반환 |
|---|---|---|
| 1. 문자 전달 | `await analyze_message_part(message_text)` | `MessagePart` |
| 2. URL 결과 준비 | `UrlAnalysis.model_validate(raw_url_result)` | 최종 호출에 전달할 `UrlAnalysis` |
| 3. 전체 최종 결과 수신 | `await finalize_analysis(url, message, page, failure=...)` | `AnalysisResponse` |

## 호출 전 준비

BE 실행 환경은 Python 3.11 이상이어야 한다. 저장소 루트에서 같은 환경의 Python으로 AI 패키지를 설치한다.

```powershell
python -m pip install ./ai
```

- `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`을 실행 환경에 설정한다. 값은 코드에 기록하지 않는다.
- 앱 시작 시 `ai.kb.search.load_cases()`를 한 번 호출해 사례 검색 캐시를 준비한다. 매 요청마다 호출하지 않는다.
- 기본 호출은 `client`, `model`을 생략한다. AI가 생성한 client는 AI가 닫는다. 선택적으로 주입한 client는 BE가 관리한다.

아래 짧은 예제의 `await` 구문은 BE의 async 함수 안에 넣는다. 마지막 절에는 세 구간을 연결하는 완전한 async 함수 예제가 있다.

## 1. BE → AI: URL을 제외한 문자 본문

```python
from ai.pipeline import analyze_message_part

# message_text: BE에서 URL 제거와 개인정보 마스킹을 마친 str
message_result = await analyze_message_part(message_text)
```

BE의 기존 [split_message()](../backend/src/server/url_utils.py)는 URL 목록과 URL을 제거한 본문을 분리한다. 개인정보 마스킹은 BE가 준비해야 한다. URL 분석 결과·원문 전체·페이지 HTML을 본문에 섞지 않는다. 예를 들어 URL 제거 후 본문이 `[CJ대한통운] 배송 현황을 확인하세요`이면 그 문자열을 전달한다.

반환된 `message_result`는 해당 요청의 최종 호출까지 보관한다. BE에서 분류나 `answer`를 다시 계산하지 않는다. 빈 입력·분석 실패도 해당 부분의 `answer=false`와 사유로 반환될 수 있다. 따라서 false가 항상 의심 문구를 발견했다는 뜻은 아니다.

## 2. BE → AI: URL 분석 결과 준비

```python
from ai.types import UrlAnalysis

url_analysis = UrlAnalysis.model_validate(raw_url_result)
```

`raw_url_result`는 BE가 URL 조사를 마치고 준비한 dict다. 유효한 형태는 다음과 같다.

```json
{
  "final_url": "https://example.com/track",
  "domain": "example.com",
  "official": false
}
```

| 필드 | BE가 준비할 값 |
|---|---|
| `final_url` | 확인한 최종 목적지 URL. 빈 값·공백뿐인 값은 거부 |
| `domain` | BE가 정한 도메인 표현. 빈 값·공백뿐인 값은 거부하며 AI가 호스트/오리진 표현을 자동 변환하지 않음 |
| `official` | 점수와 임계값을 비교한 실제 boolean. 문자열 `"false"`, 숫자 `0/1`, null, 누락은 거부 |

BE가 유효한 악성 위험도 점수에 대해 `score <= T`이면 true, `score > T`이면 false를 만든다. 사용할 score 필드 경로·임계값 T·점수 누락·조회 실패·범위 초과 처리는 BE에서 확정한다. AI는 원점수와 임계값을 받지 않는다. 이 `official`은 점수 기준 통과 여부이며 실제 공식 도메인 확인이나 화이트리스트 일치를 뜻하지 않는다.

`bool(raw_value)`로 변환하거나 값이 없다고 false를 채우지 않는다. 잘못된 입력은 `pydantic.ValidationError`를 BE 오류 경로에서 처리한다. 유효한 URL 결과 없이 정상 분석 응답을 만들지 않는다.

이 검증 구문 자체는 네트워크 전송이 아니다. 검증한 `url_analysis`를 3번 최종 호출의 `url` 인자로 AI에 전달한다. 현재 [parse_urlscan_result()](../backend/src/server/main.py)는 `url`, `title`, `brands`만 반환하므로 그 dict를 그대로 사용할 수 없다. BE 어댑터에서 위 세 필드를 준비해야 한다.

## 3. AI → BE: 격리 분석까지 포함한 최종 결과 수신

```python
from ai.pipeline import finalize_analysis
from ai.types import IsolatedPage

# raw_page: 수집기가 제공한 dict 또는 자료가 없을 때 None
# collection_failure: 아래 표의 FailureCode 또는 정상 수집의 None
isolated_page = (
    IsolatedPage.model_validate(raw_page) if raw_page is not None else None
)
final_result = await finalize_analysis(
    url=url_analysis,
    message=message_result,
    page=isolated_page,
    failure=collection_failure,
)
payload = final_result.model_dump(mode="json")
```

AI는 `official=false`이면 전달된 HTML을 분석하고 문자 결과와 결합해 전체 스키마를 반환한다. BE가 페이지 분석이나 조립 함수를 추가로 호출할 필요는 없다. `model_dump()`는 반환값을 JSON 호환 dict로 바꾸는 구문이며 추가 분석 호출이 아니다.

수집 자료는 `{brand, category, info}`의 세 문자열이다. `info`는 HTML이며 AI는 이를 실행하거나 URL에 접속하지 않는다. 다음은 형태를 설명하는 합성 HTML 예시다.

```json
{
  "brand": "unknown",
  "category": "unknown",
  "info": "<form><label>비밀번호<input type=\"password\"></label></form>"
}
```

수집 상태는 BE 어댑터가 다음과 같이 전달한다. 코드는 `from ai.types import FailureCode`로 가져온다.

| 수집 상황 | 최종 함수 인자 |
|---|---|
| 정상 수집 | `page=isolated_page, failure=None` |
| 수집 실패, 자료 없음 | `page=None, failure=FailureCode.COLLECTION_FAILED` |
| 시간 초과, 자료 없음 | `page=None, failure=FailureCode.TIMEOUT` |
| 일부 자료만 수집 | `page=partial_page, failure=FailureCode.PARTIAL_CONTENT` |
| 시간 초과 전에 일부 자료 확보 | `page=partial_page, failure=FailureCode.TIMEOUT` |

`partial_page`도 실제 확보한 자료를 담은 `IsolatedPage`다. 실패를 빈 HTML의 정상 수집으로 바꾸거나 알려진 실패 코드를 버리지 않는다. 자료와 실패를 함께 전달하면 확인한 정보와 실패 사유를 보존한다. `brand`, `category`는 수집기 메타데이터이며 실제 발신자·페이지 진위 확인값이 아니다.

이 표는 Python 호출 인자 계약이다. 격리 서버의 HTTP 오류 envelope를 새로 정의하지 않는다. 수집 응답을 이 값들로 옮기는 어댑터는 BE·수집기 담당자가 작성한다. 잘못된 페이지 스키마의 ValidationError를 정상 수집이나 임의의 수집 실패로 바꾸지 않는다.

### 조기 반환과 최종 스키마

URL 결과가 `official=true`이면 문자·수집 작업의 정상 완료를 기다릴 필요 없이 다음처럼 호출한다.

```python
from ai.pipeline import finalize_analysis

final_result = await finalize_analysis(url=url_analysis)
payload = final_result.model_dump(mode="json")
```

이 경로는 페이지 분석·LLM 호출을 생략한다. BE는 불필요한 진행 중 작업을 취소하고 종료를 확인한다. 반대로 false이면 확보한 문자 결과·수집 자료·실패 코드를 전달한다. 문자 결과를 확보하지 못했다면 `message=None`을 전달할 수 있지만, 이는 문자 분석 재시도가 아니라 결과 미제공으로 처리된다.

| 반환 필드 | 내용 |
|---|---|
| `url` | 전달한 final_url/domain/official |
| `message` | 먼저 분석한 문자 결과 |
| `env` | 최종 함수 내부에서 분석한 격리 자료 결과 |
| `result` | 항상 url.official과 같은 boolean |

true이면 `message`, `env`, 두 `details` 객체와 모든 키를 유지하며 말단 값 10개가 null이다. false이면 두 part의 모든 필드가 채워지며, 실패·미제공 부분은 false와 사유로 표현된다. 두 `answer`가 true여도 최종 `result`는 false일 수 있다. 전체 JSON은 [응답 예시](./ai-be-final-result-schema.md#전체-응답)에 있다.

`exclude_none=True`, `exclude_unset=True`, None 삭제 후처리를 사용하지 않는다. `result=false`는 의심이며 악성 확정이 아니다. 일반 로그인 폼이나 문자·페이지의 분류 차이만으로 악성이라고 표시하지 않는다. `details.reason`은 평문으로 취급한다.

`payload`를 저장하거나 FE로 전달할 수 있다. 카카오 SkillResponse로 변환하고 콜백을 전송하는 작업은 BE가 수행한다. 이 dict 자체가 카카오 응답 형식은 아니다.

## 세 구간을 연결한 최소 병렬 예제

아래는 BE가 작성할 참조 함수다. `get_url_result`와 `collect_page`는 BE가 제공할 async 함수 인자이며 기존 저장소 함수명이 아니다. URL 자체는 이 함수들의 인자나 클로저로 관리한다.

```python
import asyncio
from collections.abc import Awaitable, Callable

from ai.pipeline import analyze_message_part, finalize_analysis
from ai.types import AnalysisResponse, FailureCode, IsolatedPage, UrlAnalysis


async def analyze_request(
    message_text: str,
    get_url_result: Callable[[], Awaitable[UrlAnalysis]],
    collect_page: Callable[
        [], Awaitable[tuple[IsolatedPage | None, FailureCode | None]]
    ],
) -> AnalysisResponse:
    message_task = asyncio.create_task(analyze_message_part(message_text))
    url_task = asyncio.create_task(get_url_result())
    page_task = asyncio.create_task(collect_page())
    tasks = (message_task, url_task, page_task)
    try:
        url = await url_task
        if url.official:
            return await finalize_analysis(url)
        message, collected = await asyncio.gather(message_task, page_task)
        page, failure = collected
        return await finalize_analysis(url, message, page, failure=failure)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
```

| 인자 | BE가 준비할 값·처리 |
|---|---|
| `message_text` | URL 제거·마스킹을 마친 본문 |
| `get_url_result` | URL 조사를 시작하고 점수 가공·UrlAnalysis 검증 후 반환 |
| `collect_page` | 격리 수집을 시작하고 검증된 `(page, failure)` 반환. 페이지 AI 분석은 수행하지 않음 |

이 흐름은 URL 조사·문자 분석·격리 수집을 병렬로 시작한다. false가 확정되고 필요한 자료를 받은 뒤 최종 함수 안에서 페이지를 분석한다. true는 다른 작업의 정상 완료를 기다리지 않지만 `finally`에서 취소 정리를 기다린다. 어댑터는 `CancelledError`를 삼키지 않고 자원을 정리해야 한다. 취소에 응답하지 않는 외부 작업까지 즉시 종료한다고 보장하지 않는다.

알려진 수집 실패는 `collect_page`가 `(None, FailureCode.COLLECTION_FAILED)` 등으로 반환한다. URL 조회·검증 실패와 예상하지 못한 provider 오류는 호출자에게 전파되고 나머지 task는 정리된다. 요청 자체 취소도 전파된다.

## BE 코드에 연결할 위치와 남은 책임

| 현재 위치 | BE가 할 작업 |
|---|---|
| `main.py`의 `kakao_skill()` | URL 분리 이후 마스킹한 본문 준비 |
| `parse_urlscan_result()` 및 URL 어댑터 | 최종 URL·domain·유효한 score 기반 boolean 준비와 검증 |
| 격리 수집 어댑터 | 정상·실패·시간 초과·부분 자료를 `(page, failure)`로 전달 |
| `run_analysis()` | 고정 AI 안내 문자열을 문자 분석과 최종 AI 호출로 교체 |
| `run_analysis_and_callback()` | 결과 보관·카카오 응답 가공·콜백·오류 응답 처리 |

위 최소 예제는 단일 URL의 호출 단위를 설명한다. 요청 전체 deadline, 저장, 늦은 결과의 덮어쓰기 방지, 조회 폴백, URL 없음·다중 URL 정책은 BE에서 구현한다. 외부 요청별 timeout과 전체 시간 예산을 정하고, 완료된 결과를 보존해야 한다. 전체 시간 예산에는 최종 함수의 페이지 분석 시간도 포함한다. 현재 BE의 `CALLBACK_DEADLINE_SECONDS=45.0`을 이 예제가 바꾸지는 않는다.

예제는 대체 LLM·provider를 사용해 성공, 수집 실패, 조기 반환, URL 오류, 취소 정리를 검증한다. 실제 LLM 정확도와 urlscan·격리 서버·카카오 연결은 별도의 연동 검증 대상이다.
