# AI 파트 안내

기준 설계는 [스미싱 메시지 분석](../docs/archive/ai/smishing-message-intake-ai.md)이다. 이 문서는 전체 프로토타입 요구사항의 구현 완료를 뜻하지 않지만, 아래 구조화 메시지 분석의 JSON·함수 계약은 구현되어 있다.

AI는 링크를 제외한 메시지에서 브랜드·주장 목적·요구 행동을 추출하고, 본문으로 피해 사례를 검색해 참고 유사도를 제공한다. 키워드 압축은 하지 않는다. 관측 사실과 모델 해석을 구분해 설명하며 최종 판정은 결정적 코드가 수행한다. RAG 유사도와 urlscan 점수·brands는 판정을 바꾸지 않는다.

최종 상태는 스미싱 의심·공식 도메인 확인·판단 보류·분석 불가다. 분석 중과 입력 보완 요청은 최종 상태와 구분한다. 공식 도메인 확인은 안전 보증이 아니다.

스캐너 실행과 결과의 공통 구조 변환은 백엔드가 담당한다. AI는 서버 모듈과 카카오 형식에 의존하지 않는다. 백엔드/RAG 연동 계약 등 나머지 상세 사항은 후속 확정 사항이다.
격리 환경은 백엔드와 분리된 전용 서버가 담당한다 (`docs/adr/0002-isolation-server.md`).
AI는 그 결과를 `Observations` 로 받아 해석할 뿐 직접 호출하지 않는다.

모델·검색 실패 시 확인된 근거를 보존하고 설명은 템플릿으로 폴백한다. 변경 시 tests·eval의 기준을 확인한다. 상위 지침과 다른 파트 문서는 후속 동기화가 필요하다.

## 메시지 구조 분석

백엔드는 URL을 제거하고 개인정보를 마스킹한 본문만 AI에 전달한다. AI는
구조화된 분석 결과를 반환하며 링크 위험도 판정은 결정적 로직의 책임이다.

```python
from ai.llm import analyze_message

result = await analyze_message(masked_text)
payload = result.model_dump(mode="json")
```

실행 환경에는 `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` 세 키가 필요하다.
분석 결과의 근거는 입력 본문에서 확인된 문자열만 포함하며, 호출 실패나 빈
입력에서는 동일한 JSON 구조의 `fallback` 결과를 반환한다.

링크도 같은 호출에서 함께 뽑는다([ADR 0007](../docs/adr/0007-llm-link-extraction.md)).
원문을 넘기면 `ai.pipeline.read_message()` 가 원문 대조를 통과한 링크와 링크를 뗀 본문을
돌려준다. 백엔드의 `split_message()` 를 대신하며, 연동 방법은
[BE 연동 문서 §0](../docs/ai-be-python-integration.md)에 있다.

```python
from ai.pipeline import read_message

reading = await read_message(masked_text)
links = [link.url for link in reading.links]
```

문자 추출 실패의 원인은 Python 반환 객체의 `failure`에 보존한다.
이 내부 필드는 직렬화에서 제외되어 기존 추출 JSON과 모델 입력은 유지된다.
문자 파이프라인은 이를 최종 `message.details.reason.failures`에 담는다.
원인 정보가 없는 기존 폴백은 알려진 다른 실패 원인도 없을 때 `missing_result`로 안내한다.

## 판정

```python
from ai.kb.search import search_cases
from ai.llm import analyze_message, explain_verdict, extract_signals
from ai.verdict import decide

extracted = await analyze_message(masked_text)
cases = search_cases(masked_text)
signals = await extract_signals(masked_text, extracted, observations)
verdict = decide(masked_text, extracted, domain_check, observations, signals)
text = await explain_verdict(verdict, observations, cases)
```

`decide()` 는 LLM도 네트워크도 없는 순수함수다. `domain_check` 와 `observations`
는 백엔드가 만들어 넘긴다. AI는 화이트리스트 파일을 읽지 않고 스캐너를 부르지 않는다.

RAG 유사도, 스캐너 점수, 주제 분류는 판정 입력에서 제외한다. LLM이 제안한 신호는
근거가 실제 입력에 존재하는 것만 채택한다.

## 최소 스미싱 시나리오 검증

기존 `analyze_message_part()` → `finalize_analysis(page=None)`를 그대로 사용한다.
AI 제품 코드·공개 API·판정 규칙은 바꾸지 않는다.
기존 회귀 검증은 저장소 루트에서 `python -X utf8 -m pytest ai/tests -q`로 실행한다.
실제 모델 품질과 카카오 전달 성공은 배포 후 별도로 확인한다.
