# 파이프라인

## Revisions: BE가 사용하는 현재 호출 흐름

[BE 연동 코드](../../../../docs/ai-be-python-integration.md)에 문자 전달, URL 결과 준비, 최종 결과 수신 구문이 있다. 기준은 [Revisions](../../../../docs/ai/Revisions.md)와 [결과 계약](../../../../docs/ai-be-final-result-schema.md)이다.

```python
from ai.pipeline import analyze_message_part, finalize_analysis

# BE의 async 함수 안에서 실행한다.
# text는 URL 제거·마스킹된 본문, url은 검증한 UrlAnalysis다.
# page는 수집한 IsolatedPage 또는 None, failure는 수집 실패 코드 또는 None이다.
message = await analyze_message_part(text)
response = await finalize_analysis(url, message, page, failure=failure)
payload = response.model_dump(mode="json")
```

`finalize_analysis()`가 페이지 분석과 결과 조립을 내부에서 처리한다. 기존 문자 결과를 재사용하며, `url.official=true`이면 페이지 분석을 생략하고 전체 구조와 말단 null 10개를 반환한다. 이 경우 BE는 문자·수집 완료를 기다리지 않고 `await finalize_analysis(url)`을 호출할 수 있다. 실제 병렬 처리·취소 정리 예제는 위 연동 문서에 있다.

false이면 AI가 수집 HTML을 분석하고 문자·페이지 결과를 포함한 `AnalysisResponse`를 반환한다. 최종 `result`는 항상 BE가 전달한 점수 기준 boolean인 `url.official`이다. LLM·문자 분석·페이지 분석이 이를 뒤집지 않는다. 실패·부분 자료는 `failure`와 `page`를 함께 전달해 보존한다.

`analyze_environment_part()`와 `assemble_analysis()`는 개별 사용과 기존 호출 호환성을 위해 유지한다. BE의 새 연동에서는 두 함수를 각각 호출할 필요가 없다. URL 조사·페이지 수집·태스크 생성과 취소·저장·콜백은 BE 책임이며 AI는 HTML 실행이나 카카오 응답 가공을 하지 않는다.

## 기존 화이트리스트 기반 흐름

아래는 기존 설계와 legacy API의 설명이다. 위 Revisions의 점수 기반 `official` 및 `result`에 이 판정을 섞지 않는다.

기준은 [AI 설계](../../../../docs/ai/smishing-message-intake-ai.md)와
[재설계 스펙](../../../../docs/superpowers/specs/2026-09-18-smishing-analysis-redesign-design.md)이다.

1. 백엔드가 텍스트와 링크를 분리하고 개인정보를 마스킹한다.
2. 백엔드가 urlscan, 격리 서버, AI 호출을 동시에 시작한다.
3. AI가 본문에서 정보를 추출하고(`analyze_message`) 피해 사례를 검색한다(`search_cases`).
4. 백엔드가 urlscan 결과로 격리 필요 여부를 판단하고, 관측을 공통 구조로 변환한다.
5. AI가 위험 신호를 근거와 함께 제안한다(`extract_signals`).
6. `decide()` 가 도메인 대조 결과와 검증된 신호로 최종 상태를 정한다.
7. AI가 설명을 만든다(`explain_verdict`). 실패하면 템플릿으로 폴백한다.

| 입력 | 처리 |
|---|---|
| 텍스트와 링크 1개 | 추출·검색 및 링크 분석 |
| 링크만 1개 | 추출·검색 생략, 링크 분석만 사용 |
| 텍스트만 | 추출·검색 참고 정보와 링크 입력 요청. 최종 판정 없음 |
| 링크 N개, 전부 공식 | 공식 도메인 확인 |
| 링크 N개, 비공식 1개 | 그 1개 분석 |
| 링크 N개, 비공식 2개 이상 | 하나를 선택하도록 요청 |
| 둘 다 없음 | 문자 입력 요청 |

정상 택배 문자도 링크를 2개 갖는다. 여러 개라는 이유만으로 선택을 요구하면 정상
사용자만 막힌다.

`found` / `checked_absent` / `unknown` 을 구분한다. 빈 목록으로 없음을 추론하지
않는다. `expired` 와 `cloaked_suspect` 는 검사가 깨끗해도 안전이 아니다.

모델·검색 실패가 확인된 근거를 지우지 않으며 설명 실패는 템플릿으로 폴백한다.

장시간 작업은 백엔드가 처리·저장하고 콜백으로 전달한다. 콜백 실패 시 조회 폴백을
쓴다. AI는 카카오 형식에 의존하지 않는다.
