# HTML Inspection Limits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 수집 HTML을 유한한 비용으로 검사하고, 실제 확인한 근거와 미완료 상태를 보존해 최종 env에 반영한다.

**Architecture:** 기존 stdlib HTMLParser와 PageInspection을 유지하고 입력·수집·분류·출력 경계에 제한을 둔다. BE는 격리 자료를 기존 Python 함수로 전달하고 AI가 분석한다. 수집 인프라와 범용 DOM 목록은 인계 요구사항으로 문서화하며 새 HTTP API·브라우저·분류 체계는 이번에 구현하지 않는다.

**Tech Stack:** Python >=3.11, html.parser, time.monotonic, dataclasses, json, 기존 pytest/pytest-asyncio·Pydantic.

**Spec:** [HTML 검사기 제한과 DOM 수집 입력 설계](../specs/2026-09-26-html-inspection-design.md).

## Global Constraints

2026-09-26 실행 보정: [멘토 대조 기록](../../ai/2026-09-26-mentor-crosscheck.md)을 따른다. Task 1~4는 공유 파서·실패 흐름을 대상으로 하나의 구현·리뷰 단위로 수행하고 Task 5 인계 문서를 함께 검증한다. 혼합 CR/LF 원문 위치 오류도 근거 경계 수정에 포함한다. HTMLParser는 LF 기준으로 행을 계산하므로 source.splitlines()로 원문 행 오프셋을 만들지 않는다. 직접 helper 형태를 고정한 테스트보다 외부 관측 결과를 우선한다.

- HTML 131,072 bytes, 노드 2,000개, 본문 16,000자 제한 유지.
- 초기 제안: 깊이 64, 전체 검사 0.100초, 검사 결과 JSON 256KiB, LLM user JSON 128KiB. 실측 전 고정 운영값이라고 주장하지 않는다.
- 새 의존성·실제 네트워크·JS 실행·BE import를 추가하지 않는다.
- 실패·부분 수집을 안전 또는 악성 근거로 치환하지 않는다.
- 인코딩 불가 str은 ValueError로 거부한다. INVALID_OUTPUT을 입력 오류의 대용으로 쓰지 않는다.
- `inspect_html(info: str) -> PageInspection`과 `finalize_analysis()`의 공개 인자는 유지한다.
- 전체 결과 통합은 [result/null 계획](2026-09-26-ai-result-and-null.md) 적용에 의존한다. 두 계획이 results.py에서 충돌하면 result/null 규칙을 유지한다.
- 100ms는 내부 확인 예산이며 강제 종료 보장이 아니다.

## Review Focus

1. 제한 뒤의 미검사 HTML이 evidence에 섞이는 경우: Task 1의 열린 폼·entity 테스트.
2. 2,000개 미만이어도 깊은 트리·반복 분류로 비용이 커지는 경우: Task 2의 깊이·시계 테스트.
3. 중복 근거·긴 속성으로 출력이 입력보다 커지는 경우: Task 3의 출력·LLM payload 테스트.
4. 정상 결제·로그인·부정문을 위험으로 바꾸는 회귀: Task 4의 정상/의심 대조 및 기존 의미 검증 테스트.
5. 수집 실패가 있는 HTML을 정상으로 승격하거나 정상 메시지를 지우는 경우: Task 4의 최종 함수 통합 테스트.

## 파일 지도

| 파일 | 책임 |
| --- | --- |
| `ai/src/ai/page.py` | 크기 검사, Collector 경계, 깊이·시간·검사 결과 제한 |
| `ai/src/ai/llm/page.py` | LLM user payload 상한, 초과 시 호출 생략 |
| `ai/src/ai/pipeline/analysis.py` | 검사 실패 전달; 기존 경로로 충족하면 수정하지 않음 |
| `ai/tests/test_page.py` | 경계·자원·근거 회귀 |
| `ai/tests/test_page_analysis.py` | LLM 호출 제한·정상/의심 의미 회귀 |
| `ai/tests/test_pipeline_finalize.py` | 최종 env/result 실패 연결 |
| `docs/ai/html-collection-handoff.md` (생성) | 범용 DOM 요소, 수집·BE 책임, 충돌·측정 요구사항 |

작업 디렉터리는 저장소 루트다. 기존 테스트 환경에서 `python -m pytest`를 사용한다. 구현 승인 전에는 아래 단계를 실행하지 않는다.

## Task 1: 입력 경계와 실제 검사 범위

**Files:** `ai/src/ai/page.py`, `ai/tests/test_page.py`.

**Interfaces:** 공개 함수는 유지. 내부 `_Collector`에 `processed_end: int`를 추가한다. `finish()`는 이 원문 경계를 사용한다. 정상 파싱을 모두 마친 경우에만 `processed_end=len(source)`로 설정한다.

- [ ] 기존 imports에 `import ai.page as page_module`을 추가하고 회귀 테스트를 작성한다.

```python
def test_uninspected_tail_is_not_evidence(monkeypatch):
    monkeypatch.setattr(page_module, "MAX_ELEMENTS", 2)
    html = '<form><input type="password"><div>UNREAD_SECRET</div></form>'
    result = inspect_html(html)
    assert result.failure is FailureCode.PARTIAL_CONTENT
    assert result.elements
    assert all("UNREAD_SECRET" not in e.evidence for e in result.elements)
    for e in result.elements:
        assert html[e.start:e.start + len(e.evidence)] == e.evidence


def test_entity_text_cut_does_not_include_tail(monkeypatch):
    monkeypatch.setattr(page_module, "MAX_PAGE_TEXT_CHARS", 2)
    html = '<form><input type="password">&amp;&amp;&amp;UNREAD</form>'
    result = inspect_html(html)
    assert result.failure is FailureCode.PARTIAL_CONTENT
    assert all("UNREAD" not in e.evidence for e in result.elements)


def test_invalid_unicode_is_explicit_input_error():
    with pytest.raises(ValueError, match="UTF-8"):
        inspect_html("\ud800")
```

- [ ] Run: `python -m pytest ai/tests/test_page.py -k "uninspected_tail or entity_text_cut or invalid_unicode" -q`. 기존 동작과의 차이로 FAIL하는지 확인한다.
- [ ] 함수 시작 부분에 문자 수 사전 검사와 UTF-8 검증을 구현한다.

```python
if len(info) > MAX_HTML_BYTES:
    return PageInspection(text="", elements=(), failure=FailureCode.INPUT_TOO_LARGE)
try:
    size = len(info.encode("utf-8"))
except UnicodeEncodeError as error:
    raise ValueError("HTML input must be valid UTF-8 text") from error
if size > MAX_HTML_BYTES:
    return PageInspection(text="", elements=(), failure=FailureCode.INPUT_TOO_LARGE)
```

- [ ] `processed_end`를 콜백 진입 시 현재 토큰 시작 위치까지 갱신한다. 성공한 시작·종료 태그는 알려진 원문 토큰 끝까지 갱신한다. 데이터 토큰 길이는 디코딩된 data 길이로 원문 끝을 계산하지 않는다. 데이터 도중 초과면 토큰 시작까지만 evidence를 인정하고 retained page text는 기존 제한 안에서 보존한다. `finish()`의 열린 노드는 다음처럼 닫는다.

```python
for node in self.stack:
    node.end = max(node.start_tag_end, self.processed_end)
    node.incomplete = True
    if node.tag == "form" and not node.ignored:
        self.malformed_important = True
```

- [ ] 원문 그대로 끝난 미완성 HTML은 기존처럼 PARTIAL_CONTENT로 남기되 이미 읽은 원문 끝까지 보존한다. 기존 `test_unclosed_important_form_preserves_observed_element_as_partial`을 유지한다. 다바이트 크기 경계와 1byte 초과 테스트를 추가한다.

```python
def test_utf8_boundary(monkeypatch):
    monkeypatch.setattr(page_module, "MAX_HTML_BYTES", 3)
    assert inspect_html("가").failure is None
    assert inspect_html("가a").failure is FailureCode.INPUT_TOO_LARGE
```

- [ ] Run: `python -m pytest ai/tests/test_page.py -q`. 해당 테스트 전체 PASS.
- [ ] 변경을 검토하고 관련 파일만 stage/commit: `git commit -m "fix: bound HTML input and preserve inspected evidence ranges"`.

## Task 2: 구조 깊이와 검사 전체 시간 예산

**Files:** `ai/src/ai/page.py`, `ai/tests/test_page.py`.

**Interfaces:** `MAX_DEPTH=64`, `INSPECTION_TIMEOUT_SECONDS=0.100` 추가. 내부 `_InspectionTimeout` 예외와 `_check_deadline(deadline: float) -> None` 함수 추가. collector와 분류 helper가 하나의 deadline을 공유한다. 공개 함수 인자에는 deadline을 추가하지 않는다.

- [ ] 깊이 1은 최상위 요소로 정의한다. void/self-closing 요소도 현재 부모보다 한 단계 깊은 노드이므로 `len(stack)+1`로 확인한다. 깊이 64 허용·65 거부 테스트를 추가하고, 기존 1,200단계 정상 테스트는 새 정책의 PARTIAL_CONTENT 기대값으로 변경한다.

```python
@pytest.mark.parametrize("depth,partial", [(64, False), (65, True)])
def test_depth_boundary(depth, partial):
    result = inspect_html("<div>" * depth + "안내" + "</div>" * depth)
    assert result.failure is (FailureCode.PARTIAL_CONTENT if partial else None)


def test_deadline_uses_monotonic_clock(monkeypatch):
    monkeypatch.setattr(page_module, "monotonic", lambda: 2.0)
    page_module._check_deadline(3.0)
    with pytest.raises(page_module._InspectionTimeout):
        page_module._check_deadline(2.0)
```

- [ ] Run: `python -m pytest ai/tests/test_page.py -k "depth_boundary or deadline_uses" -q`. 새 제한과 함수가 없어 FAIL해야 한다.
- [ ] 입력 검증 전에 deadline을 생성하고 각 단계에서 확인한다.

```python
from time import monotonic

class _InspectionTimeout(Exception):
    pass

def _check_deadline(deadline: float) -> None:
    if monotonic() >= deadline:
        raise _InspectionTimeout

# inspect_html 시작 부분
deadline = monotonic() + INSPECTION_TIMEOUT_SECONDS
# _new_node의 노드 생성 전
if len(self.stack) + 1 > MAX_DEPTH:
    self.limit_reached = True
    raise _InspectionLimit
```

- [ ] `_Collector` 콜백, `_node_text`, `_descendants`, 조상 탐색, label 순회, `_classify`, `_element`, 결과 정규화에서 같은 deadline을 확인한다. 내부 helper의 모든 호출부에 인자를 전달한다. `_classify`는 후보를 순차 생성하고 검증된 요소만 inspect_html의 리스트에 추가하는 방식으로 변경한다. 시간 초과 후 전체를 다시 분류하지 않는다.
- [ ] 파싱·분류·근거 생성 예외 경로에서 TIMEOUT을 PARTIAL_CONTENT로 덮지 않는다. 기존 catch-all 앞에서 `_InspectionTimeout`을 처리하고 이미 확정한 요소와 텍스트만 반환한다.
- [ ] 테스트를 실제 sleep에 의존시키지 않는다. 다음 모킹으로 분류 도중 중단을 검증한다.

```python
def test_classification_timeout_preserves_completed_candidates(monkeypatch):
    previous = inspect_html('<a href="/app">앱 설치</a>').elements[0]
    def interrupted(*args, **kwargs):
        yield previous
        raise page_module._InspectionTimeout
    monkeypatch.setattr(page_module, "_classify", interrupted)
    result = inspect_html('<a href="/app">앱 설치</a>')
    assert result.failure is FailureCode.TIMEOUT
    assert result.elements == (previous,)
```

- [ ] 파싱 도중 중단 테스트는 `_Collector.handle_starttag`를 모킹해 `_InspectionTimeout`을 발생시킨다. 반환 실패가 TIMEOUT이며 이후 분류를 실행하지 않는지 확인한다. 큰 attrs·많은 labels·중첩 form 입력도 시간 확인 경로를 통과시킨다.
- [ ] Run: `python -m pytest ai/tests/test_page.py -q`. PASS. 테스트 반복에서 실제 100ms 경과에 따른 흔들림이 있으면 비시간 테스트의 시계만 고정하며 운영 제한을 테스트 때문에 늘리지 않는다.
- [ ] 검토 후 관련 파일 커밋: `git commit -m "fix: bound HTML depth and inspection work"`.

## Task 3: 검사 결과와 LLM 입력 총량

**Files:** `ai/src/ai/page.py`, `ai/src/ai/llm/page.py`, `ai/tests/test_page.py`, `ai/tests/test_page_analysis.py`.

**Interfaces:** `MAX_INSPECTION_BYTES=262_144`와 `MAX_PAGE_PAYLOAD_BYTES=131_072`를 각 소유 모듈에 추가한다. 기존 `_safe_elements`와 PageInspection 형태는 유지한다. 직렬화 예산은 ensure_ascii=False, separators=(",", ":") 기준이다.

- [ ] 출력 상한 테스트를 추가한다. existing imports에 `json`, `dataclasses.asdict`를 추가한다.

```python
def test_result_byte_limit_marks_partial(monkeypatch):
    monkeypatch.setattr(page_module, "MAX_INSPECTION_BYTES", 1024)
    source = '<a href="/app">앱 설치</a>' * 30
    result = inspect_html(source)
    encoded = json.dumps(asdict(result), ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    assert len(encoded) <= 1024
    assert result.failure is FailureCode.PARTIAL_CONTENT
    assert result.elements
```

- [ ] LLM 입력 상한 초과 시 외부 호출을 하지 않는지 고정한다. 기존 `make_parse_client` fixture와 PageProposal을 재사용하고 `import ai.llm.page as page_llm`을 추가한다.

```python
@pytest.mark.asyncio
async def test_payload_limit_skips_llm(monkeypatch, make_parse_client):
    monkeypatch.setattr(page_llm, "MAX_PAGE_PAYLOAD_BYTES", 32)
    client, parse = make_parse_client(parsed=PageProposal())
    result = await page_llm.analyze_page(inspect_html("<p>일반 안내 내용입니다.</p>"),
                                       client=client, model="test")
    assert result.failure is FailureCode.INPUT_TOO_LARGE
    parse.assert_not_awaited()
```

- [ ] Run: `python -m pytest ai/tests/test_page.py ai/tests/test_page_analysis.py -k "byte_limit or payload_limit" -q`. 상한이 없어 FAIL.
- [ ] 후보를 추가하기 전 각 요소의 JSON byte 크기를 계산하고 누적한다. 원문 evidence·text·attributes·fields를 모두 포함한다. 매 요소마다 전체 리스트를 다시 직렬화하는 O(n²) 방식을 피한다. wrapper·콤마·failure 값 공간까지 예약하고, text만으로 초과하면 UTF-8 문자를 깨지 않게 줄인다. 마지막 전체 JSON 길이 검증은 한 번 수행하며 예산 초과 시 뒤의 요소를 제거한다. 검사 완료 항목만 남기고 PARTIAL_CONTENT를 설정한다. TIMEOUT이 있으면 TIMEOUT 유지.
- [ ] LLM user JSON은 기존 직렬화 직후, 클라이언트 생성·모델 조회 전에 상한을 확인한다.

```python
user_content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
if len(user_content.encode("utf-8")) > MAX_PAGE_PAYLOAD_BYTES:
    return _failure(FailureCode.INPUT_TOO_LARGE)
```

- [ ] 검사 결과에 남은 source-grounded 요소는 LLM 입력 초과로 지우지 않는다. 이후 env builder가 inspection과 실패한 PageAnalysis를 함께 받아 부분 근거를 설명하게 한다.
- [ ] Run: `python -m pytest ai/tests/test_page.py ai/tests/test_page_analysis.py -q`. PASS.
- [ ] 검토 후 커밋: `git commit -m "fix: cap inspected HTML evidence and page model payloads"`.

## Task 4: 최종 결과 연결과 정상 사례 회귀

**Files:** `ai/tests/test_pipeline_finalize.py`, `ai/tests/test_page_analysis.py`, 필요 시 `ai/src/ai/pipeline/analysis.py`.

**Prerequisite:** result/null 계획 적용. 적용 전이라면 Task 1~3까지만 검증하고 최종 결과 연결이 완료됐다고 보고하지 않는다.

**Interfaces:** 기존 `analyze_environment_part(page, failure=...)`는 PageInspection 실패를 전달하며 추가 LLM 호출 없이 부분 env를 만든다. 실패 시 message는 그대로 보존한다.

- [ ] 최종 함수 테스트 파일의 기존 url/message helper를 사용해 통합 테스트를 추가한다.

```python
@pytest.mark.asyncio
async def test_html_limit_preserves_message_and_sets_null(monkeypatch):
    import ai.page as page_module
    monkeypatch.setattr(page_module, "MAX_ELEMENTS", 2)
    source_message = message()
    source_page = IsolatedPage(brand="unknown", category="unknown",
        info='<form><input type="password"><div>미확인</div></form>')
    result = await public.finalize_analysis(url(True), source_message, source_page)
    assert result.result is False
    assert result.message == source_message
    assert result.env.answer is None
    assert result.env.details.doubt is EnvDoubt.LOGIN_FORM
    assert result.env.details.reason
```

- [ ] Run: `python -m pytest ai/tests/test_pipeline_finalize.py -k html_limit -q`. 필요 시 failure 전달 경로만 수정한다. 이미 PASS라면 제품 코드를 추가하지 않는다.
- [ ] 아래 정상 콘텐츠를 `test_page.py`에 매개변수화해 의미 있는 일반 페이지가 EMPTY_INPUT이 아님을 검증한다. 이 테스트는 악성 판정 정확도 테스트가 아니다.

```python
@pytest.mark.parametrize("body", [
    "배송이 완료되었습니다.", "예약 일정을 확인하세요.",
    "결제가 완료되었습니다.", "행사 장소와 일정을 안내합니다.",
    "인증번호를 누구에게도 알려주지 마세요.",
    "채용 직무와 근무 시간을 안내합니다.",
])
def test_generic_normal_content_is_not_missing(body):
    result = inspect_html(f"<main><h1>안내</h1><p>{body}</p></main>")
    assert result.failure is None
    assert body in result.text
```

- [ ] `test_page_analysis.py`의 일반 로그인·결제 UI·인용·금지문과 명시적 위험 요구 테스트를 유지해 제한 수정이 위험 판정을 확대하지 않는지 검증한다. 새로운 업종 enum이나 판정 규칙을 추가하지 않는다.
- [ ] Run: `python -m pytest ai/tests/test_page.py ai/tests/test_page_analysis.py ai/tests/test_pipeline_finalize.py -q`, 이어 `python -m pytest ai/tests -q`. 모두 PASS. 의미 회귀나 신규 실패가 없으면 중복 실행하지 않는다.
- [ ] 검토 후 커밋: `git commit -m "test: preserve partial page evidence in final analysis"`.

## Task 5: DOM 수집 인계 문서와 기존 정책 충돌 표시

**Create:** `docs/ai/html-collection-handoff.md`.

**Interfaces:** 기존 IsolatedPage(brand, category, info)와 Python failure 인자만 현재 인터페이스로 명시한다. 원격 스키마·BE 수신 상한·범용 PageElement 확장은 별도 합의 대상이다.

- [ ] 설계의 ‘목적과 책임 경계’, ‘범용 DOM 수집 목록’, ‘정상·의심 사례 비교 기준’, ‘별도 메타데이터 요구사항’을 인계 문서에 담는다. 제목·본문·조건·입력·라벨 참조·폼 관계·목적지·연락·차단 화면·외부 콘텐츠 항목을 빠짐없이 포함한다.
- [ ] 정책 상태를 다음 문구로 시작한다.

```text
이 문서는 격리 수집기와 BE에 전달할 협의안이다. 수집 자료의 분석 주체는
메인 서버 AI이며 BE는 수신 검증과 함수 호출을 담당한다. 기존 보안
체크리스트의 원본 HTML 금지와 info HTML 계약은 아직 정합성 합의가 필요하다.
여기에 적힌 DOM 항목 전부가 현재 AI 검사기에서 해석되는 것은 아니다.
```

- [ ] 수집 단계와 AI 검사 단계의 제한표를 분리한다. 응답 전체 byte 상한은 HTML 128KiB와 별개라는 점, 인코딩 불가 자료는 수신 검증 오류라는 점을 적는다. 값과 형식이 합의되지 않은 원격 필드는 임의로 구현하지 않는다.
- [ ] 저장한 DOM·URL·페이지 지시문도 불신 입력이며 관리자 화면·로그 출력에서 컨텍스트별 이스케이프가 필요하다고 명시한다. 입력 검증을 출력 방어의 대용으로 설명하지 않는다.
- [ ] 실제 AWS 환경 검증 목록을 담는다: 동시 1건, cold start, peak RSS, p95, 과부하 거부, 내부 주소 차단, 컨테이너 강제 종료, 다음 작업 성공, 자료·로그 정리. 숫자는 결과가 아니라 초기 가설로 표시한다.
- [ ] Run: `git diff --check`. 문서 링크와 수치가 설계 및 구현 상수와 일치하는지 확인한다. 과거 ADR은 삭제·재작성하지 않는다.
- [ ] 검토 후 문서 커밋: `git commit -m "docs: define DOM collection handoff and inspection limits"`.

## 실행 전 검토와 완료 보고

본 계획은 문서 작성 요청에 따른 산출물이며 자동으로 구현을 시작하지 않는다. 실행 시 같은 파서·근거 모델을 연속 수정하므로 단일 에이전트의 순차 실행을 권장한다.

완료 보고에는 AI 테스트 결과, 초기 상한의 실측 여부, 인코딩 오류 처리, 아직 합의되지 않은 수집·BE 계약을 구분한다. 이 계획으로 AWS 격리 안전성·일반 스미싱 판별 정확도·100ms 강제 종료가 입증됐다고 보고하지 않는다.
