# AI Result and Null Semantics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `result`를 세 요소의 종합 신뢰 boolean으로 바꾸고, 확보한 분석 결과를 보존하며 null을 정상 산출 실패로 한정한다.

**Architecture:** 기존 `MessagePart`·`EnvironmentPart`의 `answer=True/False/None`으로 정상·의심·실패를 구분한다. 부분 생성 함수에서 실패와 근거를 보존하고 `assemble_analysis()`가 종합 판정을 계산하며 `finalize_analysis()`는 공식 여부와 무관하게 전달된 페이지를 처리한다. 새 서비스·의존성·외부 상태 필드는 추가하지 않는다.

**Tech Stack:** Python >=3.11, Pydantic >=2,<3, 기존 pytest/pytest-asyncio, 기존 OpenAI 클라이언트 모킹.

**Spec:** [AI 종합 판정과 null 의미 변경 설계](../specs/2026-09-26-ai-result-and-null-design.md). 구현 전 반드시 함께 읽는다.

## Global Constraints

2026-09-26 실행 보정: PR #24 대조 결과는 [검토 기록](../../ai/2026-09-26-mentor-crosscheck.md)에 기록한다. 결합된 Task 1~3을 하나의 계약 구현·리뷰 단위로 수행한다. 아래 개별 커밋 문구는 제안이며 중간에 깨진 계약을 커밋하지 않는다. 최종 AnalysisResponse의 직접 model_validate/JSON 경로에서도 answer=null인 부분의 비어 있지 않은 실패 설명을 요구한다. 빈 부분 객체는 조립 입력 sentinel로만 허용한다.

- `result`와 `url.official`은 `StrictBool`을 유지한다. 문자열 상태 도입은 이번 범위 밖이다.
- 최종 판정은 결정적 코드가 수행하며 LLM이 최종 값을 정하지 않는다.
- 정상 완료된 부분은 기존 5개 말단 필드를 모두 채운다. 미완료 부분은 `answer=null`과 실패 설명을 제공하고, 나머지 값은 확보한 범위만 채운다.
- 요청 취소 `CancelledError`는 기존처럼 전파한다.
- 공개 함수 인자·응답 키는 유지한다. BE·HTML 검사기 제한·legacy `decide()`는 수정하지 않는다.
- 실제 수집·LLM API 없이 모킹과 기존 테스트로 검증한다. 새 의존성은 추가하지 않는다.
- 문서만 작성한 시점에는 구현·검증 완료로 표시하지 않는다. 아래 체크박스는 실행 단계에서 갱신한다.

## Review Focus

1. 빈 기본 부분 객체는 정상 생략이 아니라 자료 누락이다. Task 1의 누락 정규화 테스트로 고정한다.
2. 부분 실패 결과에 남은 근거가 조립 시 지워지지 않아야 한다. Task 1의 부분 보존 테스트로 고정한다.
3. 정상 분석의 `unknown`/`none`과 실패 기본값은 다르다. Task 2의 성공·실패 대조 테스트로 고정한다.
4. 의심 근거와 실패가 공존하면 `answer=null`이어도 근거가 남아야 한다. Task 2의 부분 실패 테스트로 고정한다.
5. 공식 URL 분기에서도 페이지 분석의 취소·클라이언트 소유권 규칙이 유지되어야 한다. Task 3의 양쪽 공식 값 매개변수 테스트로 고정한다.

## 파일 지도와 실행 환경

| 파일 | 책임 |
| --- | --- |
| `ai/src/ai/types.py` | 부분·응답 모델 검증 |
| `ai/src/ai/pipeline/results.py` | 부분 생성, 실패 설명, 종합 조립 |
| `ai/src/ai/pipeline/finalize.py` | 페이지 분석과 조립 연결 |
| `ai/src/ai/pipeline/analysis.py` | 실패 상태 전달 확인; 기존 상태로 충분하면 수정하지 않음 |
| `ai/tests/test_revision_types.py` | 부분 값·null·종합 응답 검증 |
| `ai/tests/test_revision_results.py` | 부분 생성·조립 회귀 |
| `ai/tests/test_pipeline_finalize.py` | 공개 최종 함수 호출·취소·소유권 |
| `ai/tests/test_revision_integration.py`, `ai/tests/test_revision_pipeline.py` | 기존 전체 흐름·실패 기대값 갱신 |
| `ai/tests/test_be_integration_examples.py` | 기존 URL-only 호출의 새 실패 결과와 태스크 정리 |
| `docs/ai-be-final-result-schema.md`, `docs/ai-be-python-integration.md`, `ai/src/ai/pipeline/README.md` | 변경 후 계약·호출 설명 |

아래 명령은 저장소 루트, 기존 프로젝트 테스트 환경에서 실행한다. 패키지가 아직 설치되지 않은 실행 환경에 한해 `python -m pip install -e "./ai[test]"`로 기존 의존성을 설치한다. 제품 의존성은 추가하지 않는다.

## Task 1: 부분 타입과 종합 조립 규칙

**Files:** `ai/src/ai/types.py`, `ai/src/ai/pipeline/results.py`, `ai/tests/test_revision_types.py`, `ai/tests/test_revision_results.py`.

**Interfaces:** 기존 `assemble_analysis(url: UrlAnalysis, message: MessagePart | None = None, env: EnvironmentPart | None = None) -> AnalysisResponse`를 유지한다. `answer=None`은 미완료이며 이미 있는 필드와 이유를 보존한다.

- [ ] 기존 ‘공식이면 null’ 테스트를 아래 종합 판정·보존 테스트로 대체한다. 기존 테스트 파일의 imports와 `message_part`, `environment_part` helper를 재사용한다.

```python
@pytest.mark.parametrize("official", [False, True])
@pytest.mark.parametrize("ma", [False, True, None])
@pytest.mark.parametrize("ea", [False, True, None])
def test_aggregate_answers_preserve_parts(official, ma, ea):
    message = MessagePart(brand=Brand.UNKNOWN, category=Topic.UNKNOWN,
        answer=ma, details=MessageDetails(doubt=MessageDoubt.NONE,
            reason="문자 분석 미완료" if ma is None else "문자 분석 결과"))
    env = EnvironmentPart(brand=Brand.UNKNOWN, category=Topic.UNKNOWN,
        answer=ea, details=EnvironmentDetails(doubt=EnvDoubt.NONE,
            reason="환경 분석 미완료" if ea is None else "환경 분석 결과"))
    url = UrlAnalysis(final_url="https://example.com", domain="example.com",
        official=official)
    response = assemble_analysis(url, message, env)
    assert response.result is (official and ma is True and ea is True)
    assert response.message == message
    assert response.env == env
    assert type(response).model_validate_json(response.model_dump_json()) == response


@pytest.mark.parametrize("parts", [(None, None), (MessagePart(), EnvironmentPart())])
def test_missing_parts_mean_failure(parts):
    url = UrlAnalysis(final_url="https://example.com", domain="example.com", official=True)
    response = assemble_analysis(url, *parts)
    assert response.result is False
    for part in (response.message, response.env):
        assert part.answer is None
        assert part.brand is None
        assert part.category is None
        assert part.details.doubt is None
        assert part.details.reason


def test_failed_part_keeps_existing_evidence():
    message = MessagePart(answer=None,
        details=MessageDetails(doubt=MessageDoubt.APP_INSTALL,
            reason="문자에서 앱 설치 요청을 확인했으나 분석 시간이 초과되었습니다."))
    url = UrlAnalysis(final_url="https://example.com", domain="example.com", official=True)
    response = assemble_analysis(url, message)
    assert response.message == message
    assert response.result is False
```

- [ ] Run: `python -m pytest ai/tests/test_revision_results.py -k "aggregate_answers or missing_parts_mean or failed_part_keeps" -q`. 현재 validator 또는 기존 조립 규칙 때문에 FAIL해야 한다.
- [ ] 부분 validator는 `answer is not None`일 때 5개 말단 값의 완전성을 요구하도록 변경한다. `answer=None`일 때는 확보한 필드와 null의 혼합을 허용한다. 기존 StrictBool·enum·추가 키 거부는 유지한다.

```python
# MessagePart와 EnvironmentPart의 기존 validator 내부
if self.answer is not None:
    data = self.model_dump()
    values = (data["brand"], data["category"], data["answer"],
              data["details"]["doubt"], data["details"]["reason"])
    if any(value is None for value in values):
        raise ValueError("completed parts require all fields")
return self
```

- [ ] 누락 builder는 미상 enum 대신 null과 이유를 반환한다. 조립에서는 `None` 또는 `_all_null`인 빈 객체만 누락 builder로 교체한다. `answer=None` 자체를 교체 조건으로 쓰지 않는다.

```python
def missing_message_part() -> MessagePart:
    return MessagePart(details=MessageDetails(
        reason="문자 분석 결과를 전달받지 못했습니다."))

def missing_environment_part() -> EnvironmentPart:
    return EnvironmentPart(details=EnvironmentDetails(
        reason="환경 분석 결과를 전달받지 못했습니다."))
```

기존 `_all_null` 검사는 전부 null인지 확인하는 용도로만 재사용한다. 위치 이동이 필요하면 하나만 유지한다. 정규화 후 조립과 응답 검증은 다음 동일한 식을 사용한다.

```python
expected = url.official is True and message.answer is True and env.answer is True
# 조립
return AnalysisResponse(url=url, message=message, env=env, result=expected)
# AnalysisResponse validator에서는 self의 각 필드로 같은 expected를 계산
# self.result is not expected이면 ValueError를 발생시킨다.
```

- [ ] `test_revision_types.py`에서 공식 응답도 채워진 부분을 허용하도록 갱신하고, 정상 `answer`와 누락 필드의 조합은 거부하는 검증을 유지한다. 종합 판정과 반대인 `result`는 다음 형태로 검사한다.

```python
with pytest.raises(ValueError):
    type(response).model_validate({**response.model_dump(), "result": not response.result})
```

- [ ] Run: `python -m pytest ai/tests/test_revision_types.py ai/tests/test_revision_results.py -q`. 기존 실패 builder 테스트는 Task 2에서 함께 갱신한다. 이 단계의 새 타입·조립 테스트는 PASS여야 한다.
- [ ] 타입·조립 변경과 해당 테스트만 검토하여 커밋한다: `git commit -m "refactor: separate aggregate result from official flag"`.

## Task 2: 부분 분석의 실패 null과 확보한 값 보존

**Files:** `ai/src/ai/pipeline/results.py`, 필요 시 `ai/src/ai/pipeline/analysis.py`, `ai/tests/test_revision_results.py`, `ai/tests/test_revision_pipeline.py`.

**Interfaces:** `build_message_part()`·`build_environment_part()`의 인자는 유지한다. 기존 `AnalysisStatus`, `FailureCode`, 입력 원문과 `PageInspection`으로 완료 여부·확보 범위를 결정한다. 새 외부 상태 필드는 만들지 않는다.

- [ ] 기존 helper를 사용해 성공과 실패를 구분하는 테스트를 추가한다.

```python
def test_success_unknown_is_not_failure_null():
    part = message_part("안녕하세요")
    assert part.answer is True
    assert part.brand is Brand.UNKNOWN
    assert part.details.doubt is MessageDoubt.NONE


def test_failed_analysis_retains_grounded_request():
    part = message_part("앱을 설치하세요", failure=FailureCode.TIMEOUT)
    assert part.answer is None
    assert part.details.doubt is MessageDoubt.APP_INSTALL
    assert "앱을 설치" in part.details.reason
    assert "초과" in part.details.reason
    assert "의심으로 처리" not in part.details.reason


def test_failed_page_keeps_observed_form():
    part = environment_part('<form><input type="password"></form>',
                            failure=FailureCode.PARTIAL_CONTENT)
    assert part.answer is None
    assert part.details.doubt is EnvDoubt.LOGIN_FORM
    assert part.details.reason


def test_failed_empty_page_does_not_invent_unknown_values():
    part = build_environment_part(None,
        PageInspection(text="", elements=(), failure=FailureCode.MISSING_RESULT),
        PageAnalysis(status=AnalysisStatus.FALLBACK, failure=FailureCode.MISSING_RESULT))
    assert part.answer is None
    assert part.brand is None
    assert part.category is None
    assert part.details.doubt is None
    assert part.details.reason
```

- [ ] Run: `python -m pytest ai/tests/test_revision_results.py -k "success_unknown or failed_analysis_retains or failed_page_keeps or failed_empty_page" -q`. 현재 실패를 false로 처리하는 규칙 때문에 FAIL하는지 확인한다.
- [ ] 두 builder의 완료 조건을 유지하고 반환값 계산을 다음처럼 변경한다.

```python
answer = (not accepted) if completed else None
# message: candidates가 있으면 기존 선택 결과를 보존한다.
doubt = select_message_doubt(candidates) if candidates else (
    MessageDoubt.NONE if completed else None)
# environment: inspection.elements가 있으면 기존 선택 결과를 보존한다.
doubt = select_env_doubt(inspection.elements) if inspection.elements else (
    EnvDoubt.NONE if completed else None)
```

- [ ] 실패 문구의 ‘의심으로 처리했습니다’를 제거하고 사실만 설명한다. `_FAILURE_REASONS`는 다음 값으로 교체한다.

```python
_FAILURE_REASONS = {
    FailureCode.COLLECTION_FAILED: "페이지 접속·수집에 실패하여 내용을 확인하지 못했습니다.",
    FailureCode.TIMEOUT: "분석 시간이 초과되어 확인을 완료하지 못했습니다.",
    FailureCode.EMPTY_INPUT: "분석할 자료가 비어 있어 확인하지 못했습니다.",
    FailureCode.MISSING_RESULT: "필요한 자료 또는 분석 결과를 전달받지 못했습니다.",
    FailureCode.INPUT_TOO_LARGE: "자료 크기 제한으로 전체를 확인하지 못했습니다.",
    FailureCode.PARTIAL_CONTENT: "일부 자료만 확보하여 전체를 확인하지 못했습니다.",
    FailureCode.LLM_ERROR: "분석 도중 오류가 발생하여 확인을 완료하지 못했습니다.",
    FailureCode.REFUSED: "분석 요청이 거절되어 확인을 완료하지 못했습니다.",
    FailureCode.INVALID_OUTPUT: "유효한 분석 결과를 확보하지 못했습니다.",
}
```

- [ ] `brand`·`category`는 성공한 추출/페이지 분석 결과를 유지한다. 실패 시 실제 원문 규칙으로 식별된 값이나 유효한 수집 메타데이터만 보존한다. 기본 fallback `unknown`만 남아 있고 근거 출처가 없다면 null로 바꾼다. 기존 메타데이터 출처 설명은 유지한다.

```python
# 문자: 기존 식별 함수의 반환값을 계산한 뒤 적용
if extracted.analysis_status is not AnalysisStatus.COMPLETED:
    brand = None if brand is Brand.UNKNOWN else brand
    category = None if category is Topic.UNKNOWN else category
# 환경: FALLBACK이어도 이미 검증된 non-UNKNOWN analysis 값을 우선 보존한다.
# 확보하지 못한 UNKNOWN 기본값만 None으로 바꾸고 유효한 page 메타데이터로 보완한다.
# page 메타데이터가 없거나 enum 변환이 실패하면 None을 유지한다.
```

- [ ] `test_revision_pipeline.py`의 timeout·거절·부분 수집 기대값을 `answer=None`으로 갱신한다. 기존 성공·의심 신호 검증 테스트는 의미를 변경하지 않는다.
- [ ] Run: `python -m pytest ai/tests/test_revision_results.py ai/tests/test_revision_pipeline.py -q`. 해당 파일 전체 PASS.
- [ ] 부분 결과와 테스트를 검토하여 커밋한다: `git commit -m "fix: preserve partial evidence and distinguish analysis failure"`.

## Task 3: 공식 여부와 무관한 최종 분석 및 문서 정합성

**Files:** `ai/src/ai/pipeline/finalize.py`, `ai/tests/test_pipeline_finalize.py`, `ai/tests/test_revision_integration.py`, `ai/tests/test_be_integration_examples.py`, `docs/ai-be-final-result-schema.md`, `docs/ai-be-python-integration.md`, `ai/src/ai/pipeline/README.md`.

**Interfaces:** `finalize_analysis(url, message=None, page=None, *, failure=None, client=None, model=None) -> AnalysisResponse`를 유지한다. `analyze_environment_part(page, failure=..., client=..., model=...)`를 한 번 호출하고 결과를 조립한다.

- [ ] 기존 `test_true_skips_page_work_and_keeps_all_null_keys`를 아래 테스트로 대체한다. `test_pipeline_finalize.py`의 기존 helper와 imports를 재사용한다.

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("official", [False, True])
async def test_finalize_preserves_supplied_parts_for_both_url_states(official, monkeypatch):
    source_message = message()
    env = EnvironmentPart(brand=Brand.UNKNOWN, category=Topic.UNKNOWN,
        answer=True, details=EnvironmentDetails(doubt=EnvDoubt.NONE, reason="분석 완료"))
    analyze = AsyncMock(return_value=env)
    monkeypatch.setattr(finalizer, "analyze_environment_part", analyze)
    source_page = page()
    response = await public.finalize_analysis(url(official), source_message, source_page)
    assert response.result is official
    assert response.message == source_message
    assert response.env == env
    analyze.assert_awaited_once_with(source_page, failure=None, client=None, model=None)


@pytest.mark.asyncio
async def test_official_url_only_is_failure():
    response = await public.finalize_analysis(url(True))
    assert response.result is False
    assert response.message.answer is None
    assert response.env.answer is None
    assert response.message.details.reason
    assert response.env.details.reason
```

- [ ] Run: `python -m pytest ai/tests/test_pipeline_finalize.py -k "preserves_supplied_parts or official_url_only" -q`. 현재 공식 조기 반환 때문에 FAIL.
- [ ] `finalize_analysis()`에서 공식 조기 반환 두 줄만 제거하고 기존 분석·조립 경로를 공통 사용한다.

```python
env = await analyze_environment_part(page, failure=failure, client=client, model=model)
return assemble_analysis(url, message, env)
```

- [ ] 기존 취소 전파와 주입 클라이언트를 닫지 않는 테스트에 `official=False/True` 매개변수를 추가한다. 기존 partial-page·명시 failure 전달 테스트도 양쪽 값을 검사한다.
- [ ] 통합 테스트의 null 10개·URL 판정 우선 기대값을 제거하고 실제 부분 내용 보존과 종합 결과를 검사한다. `test_be_integration_examples.py`는 BE 예제의 URL-only 호출이 이제 실패 결과를 반환함을 검사하되, 소유한 작업 취소·정리 검증을 유지한다. BE 실행 코드를 대신 수정하지 않는다.
- [ ] 세 연동 문서에 설계의 result/answer/null 표, 정상·누락 JSON 예시, URL-only 호출의 의미 변경을 반영한다. 과거 고정 커밋을 설명하는 기록은 보존하고 현재 동작과 구분한다. 실제 BE 적용이 완료됐다고 쓰지 않는다.
- [ ] Run: `python -m pytest ai/tests/test_pipeline_finalize.py ai/tests/test_revision_integration.py ai/tests/test_be_integration_examples.py -q`. 전체 PASS.
- [ ] Run: `python -m pytest ai/tests -q`. 기존 legacy 판정·근거 검증을 포함한 AI 전체 테스트 PASS. 외부 API를 직접 호출하지 않는다.
- [ ] Run: `git diff --check`. 공백 오류 없음. `git diff --stat`으로 BE·HTML 검사기 구현이 변경되지 않았음을 확인한다.
- [ ] 최종 연결·문서·테스트를 검토하여 커밋한다: `git commit -m "refactor: retain analyses regardless of official status"`.

## 구현 전 검토와 후속 범위

이 문서 작성만으로 실행 승인을 가정하지 않는다. 검토 후 실행한다면 세 작업이 같은 타입과 조립 경로에 의존하므로 단일 에이전트의 순차 실행을 권장한다. 코드 수정·테스트 실행·커밋은 이 계획을 구현할 때 수행한다.

BE에는 후속으로 boolean 의미 변경, 혼합 null 허용, 공식 URL에서도 필요한 자료 전달을 공유해야 한다. 향후 세 문자열 상태 전환 시에는 실패와 의심 공존의 표시 정책과 정확한 상태 문자열을 함께 확정한다. HTML 수신·파싱 제한 강화는 별도 설계로 진행한다.
