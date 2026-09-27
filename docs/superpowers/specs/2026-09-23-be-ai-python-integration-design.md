# BE → AI Python 직접 호출과 최종 분석 통합 설계

## 목적과 합의한 범위

BE가 다음 세 구간을 연결할 수 있도록 AI 코드와 문서를 함께 수정한다.

1. URL을 제외한 문자 본문을 AI에 전달하고 `MessagePart`를 받는다.
2. BE가 만든 URL 분석 결과를 `UrlAnalysis`로 검증해 AI에 전달한다.
3. **AI 최종 함수 한 번으로 격리 페이지 분석과 결과 조립을 수행하고 전체 `AnalysisResponse`를 받는다.**

별도 HTTP 서버 없이 같은 Python 환경에서 `ai.pipeline`을 import한다. BE가 페이지 분석과 결과 조립을 각각 호출하도록 요구하지 않는다. AI는 앞서 받은 문자 결과를 재사용하고 격리 자료를 분석해 완성된 응답을 반환한다. 내부 분석·조립 함수 분리는 구현 세부사항이다.

설계 확정 당시에는 `analyze_message_part()`, `analyze_environment_part()`, `assemble_analysis()`만 있었다. 이후 이 설계에 따른 `finalize_analysis()`와 [BE 연동 코드](../../ai-be-python-integration.md)를 추가했다. 아래는 구현의 설계 기준이며 BE 서비스의 구현·배포 완료를 뜻하지 않는다.

기준은 [Revisions](../../ai/Revisions.md), [인계 문서](../../ai/Revisions-handoff.md), [결과 계약](../../ai-be-final-result-schema.md)다. 코드·테스트는 `ai/` 안에서 수정하고 관련 Markdown 문서를 함께 수정한다. BE·FE·격리 서버 제품 코드는 각 담당자가 구현한다. 작업 브랜치는 `feature/scenario-message-test-ai`, 커밋 메시지는 한글이다.

## AI 최종 함수

`ai.pipeline`에서 다음 함수를 공개한다. 타입은 기존 `ai.types`와 `openai.AsyncOpenAI`를 사용하며 새 입력·응답 모델은 만들지 않는다.

```python
async def finalize_analysis(
    url: UrlAnalysis,
    message: MessagePart | None = None,
    page: IsolatedPage | None = None,
    *,
    failure: FailureCode | None = None,
    client: AsyncOpenAI | None = None,
    model: str | None = None,
) -> AnalysisResponse:
    ...
```

| 입력 | 의미 |
|---|---|
| `url` | BE가 검증한 URL 분석 결과. 필수 |
| `message` | 앞서 분석한 문자 결과. 미확보 시 `None` |
| `page` | 격리 수집 자료 `IsolatedPage(brand, category, info)`. 미확보 시 `None` |
| `failure` | BE가 알고 있는 페이지 수집 실패·시간 초과·부분 수집 사유. 문자 실패 인자가 아님 |
| `client`, `model` | 기존 페이지 분석 함수에 전달할 선택적 설정·테스트 주입값 |

처리 순서는 다음과 같다.

1. `url.official=true`이면 `assemble_analysis(url)` 결과를 반환한다. 페이지 검사·페이지 LLM 호출·client 생성을 하지 않는다. 문자·페이지 자료와 실패 코드는 이 분기 결과에 영향을 주지 않는다.
2. false이면 `analyze_environment_part(page, failure=failure, client=client, model=model)`을 한 번 기다린다.
3. 확보한 env와 전달받은 message를 `assemble_analysis(url, message, env)`에 넣어 전체 결과를 반환한다.

기존 세 함수의 공개와 호환성을 유지한다. 이미 분석한 EnvironmentPart를 가진 기존 호출자는 조립 함수를 계속 사용할 수 있지만, 새 BE 연동의 기본 경로는 `finalize_analysis()`다. 새 함수는 문자 원문이나 BE provider·task를 받지 않는다. URL 조사·격리 수집·BE task 생성과 취소는 BE 책임이다. AI는 BE 모듈을 import하지 않는다.

## 판정·실패·취소 계약

- 최종 `result`는 항상 `url.official`이다. 페이지 분석은 env의 분류·근거를 채우며 최종 boolean을 뒤집지 않는다.
- true이면 url/message/env/result와 두 details 객체를 유지하고 두 part의 말단 값 10개를 모두 null로 반환한다.
- false이면 완료한 문자 결과를 보존하고 재분석하지 않는다. 문자 결과가 없거나 모든 말단 값이 null이면 기존 조립기의 결과 미제공 처리를 적용한다.
- 페이지 수집 실패·시간 초과·부분 수집은 기존 페이지 분석의 `env.answer=false`와 사유로 표현한다. 부분 HTML·메타데이터가 있으면 기존 규칙에 따라 확보한 정보를 보존한다. `page=None, failure=None`은 자료 미제공이다.
- 기존 페이지 분석의 LLM 실패 폴백과 client 수명을 재사용한다. AI가 생성한 client는 AI가 닫고 주입 client는 호출자가 관리한다.
- `asyncio.CancelledError`는 그대로 전파한다. 새 함수에 포괄적인 예외 포착이나 재시도를 추가하지 않는다.
- BE가 dict를 UrlAnalysis와 IsolatedPage로 검증한다. 검증 오류를 임의의 official이나 정상 수집으로 바꾸지 않는다. 최종 함수는 검증된 타입을 받는다.

기존 Enum, 입력 상한, 출처별 근거 검증, HTML 비실행, unknown/없음/null 구분을 유지한다. 일반 로그인·결제 UI나 문자와 페이지의 분류 차이만으로 악성을 확정하지 않는다.

## BE 가이드의 세 구간

최종 사용 가이드는 `docs/ai-be-python-integration.md`에 작성한다. 아래는 구현 후 사용할 계약이며 JSON 변환은 추가 AI 분석 호출이 아닌 직렬화다.

### 1. URL을 제외한 문자 전달

```python
from ai.pipeline import analyze_message_part

message_result = await analyze_message_part(message_text)
```

`message_text`는 URL 제거·개인정보 마스킹을 마친 문자열이다. 기존 `backend/src/server/url_utils.py`의 split_message는 URL 분리를 수행한다. 마스킹은 BE 책임이며 이미 구현돼 있다고 설명하지 않는다. AI 근거는 실제 전달받은 본문을 기준으로 한다.

### 2. URL 분석 결과 준비

```python
from ai.types import UrlAnalysis

url_analysis = UrlAnalysis.model_validate(raw_url_result)
```

```json
{
  "final_url": "https://example.com/track",
  "domain": "example.com",
  "official": false
}
```

검증된 값은 3번 호출의 url 인자로 AI에 전달한다. 검증 자체는 네트워크 전송이 아니다. final_url·domain은 공백뿐인 값을 거부하고 AI가 호스트·오리진 표현을 자동 변환하지 않는다. official은 실제 boolean만 받으며 문자열·숫자·null·누락을 거부한다.

BE는 유효한 악성 위험도 점수에 대해 `score <= T`이면 true, `score > T`이면 false로 만든다. AI는 원점수·임계값을 받지 않는다. 점수 경로, T, 점수 누락·조회 실패·범위 초과 처리는 BE가 확정한다. 기본 점수나 `bool(raw_value)`로 오류를 숨기지 않는다. official은 화이트리스트 일치나 실제 공식 도메인 확인을 뜻하지 않는다.

현재 `backend/src/server/main.py`의 parse_urlscan_result는 url/title/brands만 반환하므로 그대로 사용할 수 없다. BE는 최종 URL·domain·점수 가공 결과를 준비해야 한다.

### 3. 격리 자료 전달과 전체 최종 결과 수신

```python
from ai.pipeline import finalize_analysis
from ai.types import IsolatedPage

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

raw_page는 `{brand, category, info}`이며 info는 HTML이다. collection_failure는 BE 수집 어댑터가 알고 있는 FailureCode 또는 정상 수집의 None이다.

| 수집 상태 | 최종 호출 인자 |
|---|---|
| 정상 수집 | `page=isolated_page, failure=None` |
| 수집 실패, 자료 없음 | `page=None, failure=FailureCode.COLLECTION_FAILED` |
| 시간 초과, 자료 없음 | `page=None, failure=FailureCode.TIMEOUT` |
| 부분 자료 확보 | `page=partial_page, failure=FailureCode.PARTIAL_CONTENT` |
| 시간 초과 전 일부 자료 확보 | `page=partial_page, failure=FailureCode.TIMEOUT` |

실패를 빈 HTML의 정상 수집으로 바꾸지 않는다. 이 표는 Python 인자 계약이며 격리 서버의 HTTP 오류 envelope는 아니다.

payload에는 url/message/env/result가 모두 있다. `exclude_none=True`, `exclude_unset=True`, None 삭제 후처리를 사용하지 않는다. result=false는 의심이며 악성 확정이 아니다. reason은 평문이다. 저장·FE 전달·카카오 응답 가공·콜백은 BE가 담당하며 이 dict 자체는 카카오 SkillResponse가 아니다.

## 최소 연결 흐름과 BE 책임

가이드에는 세 구간 예제와 하나의 async 연결 예제를 함께 넣는다. BE provider는 실제 저장소 함수와 구분해 예제의 인자로 선언한다.

1. BE가 문자 분석·URL 조사·격리 수집을 동시에 시작한다. 수집 provider는 검증된 `IsolatedPage | None`과 `FailureCode | None`을 반환하며 AI 페이지 분석을 호출하지 않는다.
2. URL 결과를 먼저 기다린다. true이면 `await finalize_analysis(url)`로 전체 응답을 받고 남은 BE task를 취소·정리한다. 문자·수집 작업의 정상 완료를 기다리지 않는다.
3. false이면 문자 결과와 수집 자료를 확보한 뒤 최종 함수를 한 번 호출한다. AI가 페이지 분석과 결과 조립을 마친 뒤 전체 응답을 돌려준다.
4. 모든 경로에서 생성한 BE task의 종료를 확인한다. 취소에 응답하지 않는 외부 작업까지 즉시 종료한다고 보장하지 않는다.

알려진 수집 실패는 provider가 자료와 실패 코드로 반환한다. URL 조회·검증 실패와 예상하지 못한 provider 오류는 BE 오류 경로로 전파한다. URL 결과 없이 정상 AnalysisResponse를 만들어내지 않는다.

최소 예제는 단일 URL 호출 단위를 다룬다. 여러 URL·URL 없음 정책, 저장·늦은 결과 덮어쓰기 방지·콜백 재조회 구현은 별도 BE 작업이다. BE는 외부 요청별 timeout과 요청 전체 deadline을 관리하고 완료 결과를 보존한다. 현재 `CALLBACK_DEADLINE_SECONDS=45.0`을 바꾸지 않으며 남은 시간에 최종 함수의 페이지 분석 시간도 포함해야 한다.

호출 준비로 Python 3.11 이상, BE 실행 환경에서 `python -m pip install ./ai`, LLM_API_KEY·LLM_BASE_URL·LLM_MODEL, 앱 시작 시 `ai.kb.search.load_cases()` 캐시 준비를 안내한다. 비밀 값은 기록하지 않는다.

## 변경 대상

| 파일 | 변경 |
|---|---|
| `ai/src/ai/pipeline/finalize.py` | 기존 분석·조립 함수를 연결하는 async 최종 함수 |
| `ai/src/ai/pipeline/__init__.py` | 새 함수 공개, 기존 export 유지 |
| `ai/tests/test_pipeline_finalize.py` | 최종 호출의 분기·전달·실패·취소 검증 |
| `ai/src/ai/pipeline/README.md` | 새 호출 흐름 안내, 기존 화이트리스트 흐름과 구분 |
| `docs/ai-be-python-integration.md` | BE 세 구간 예제와 최소 연결 흐름 |
| `docs/ai-be-final-result-schema.md` | 새 공개 함수·권장 호출 경로 추가, 응답·Enum 유지 |
| `docs/ai/Revisions-handoff.md` | AI 최종 분석과 BE 수집·오케스트레이션 책임 반영 |

기존 구현 계획과 Revisions의 판정 정책은 다시 작성하지 않는다. 결과 계약 문서의 고정 커밋 근거는 당시 계약 근거로 유지하면서 새 함수는 현재 소스로 연결한다. 새 함수가 과거 커밋에 있었다고 설명하지 않는다.

## 검증·완료 기준

- 공개 import와 기존 세 함수의 호환성 유지.
- true 분기에서 페이지 분석 미호출, 실패·자료가 있어도 전체 구조와 null 10개 반환.
- false 분기에서 페이지 분석 정확히 한 번, page/failure/client/model 전달, 문자 결과 보존·재분석 없음.
- 두 part가 true여도 result=false. 문자 누락·페이지 누락·수집 실패·시간 초과·부분 자료에 기존 계약 적용.
- 취소 전파와 기존 client 수명 계약 유지.
- 전체 응답 JSON 왕복 검증 및 null 키 유지.
- 새 테스트와 AI 전체 테스트를 외부 네트워크 없이 실행. 문서 예제는 대체 provider로 성공·조기 반환·실패·취소 확인.
- 실제 LLM 정확도나 urlscan·격리 서버·카카오 연동 성공을 로컬 테스트 결과로 주장하지 않음.

설계 문서 검토 후 구현 계획에서 순서·테스트 명령을 구체화한다. 구현 후 코드 리뷰에서 부족한 내용이 확인되면 수정·재검증·재리뷰한다.
