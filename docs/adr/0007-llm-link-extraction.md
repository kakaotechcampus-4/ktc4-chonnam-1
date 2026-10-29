# ADR 0007 — 링크 추출을 요구 분석 LLM 으로 옮긴다

상태: 제안 (2026-10-30)

## 맥락

지금 문자는 BE `split_message()`(`backend/src/server/url_utils.py`)가 정규식으로 링크를 떼고,
링크를 뗀 본문만 요구 분석 LLM(`analyze_message`)에 넘긴다. 통화는 BE 를 거치지 않는다.
`read_claims` 가 대본을 읽으면서 말로 부른 주소(`spoken_hosts`)를 함께 뽑지만, 그 값은
결과(`VoiceCaseResult`)에 실리지 않았다. 문자와 통화가 링크를 찾는 방법이 달랐다.

정규식은 이런 주소를 다루지 못한다(2026-10-30 `split_message` 로 확인).

| 문자 | `split_message` 결과 |
| --- | --- |
| `cj-deliver . top 에서 확인` | 링크 없음 |
| `확인: bit[.]ly/abc123`, `hxxps://evil[.]top/login` | 링크 없음 |
| `bit.ly/abc로 접속해 주세요` | `https://bit.ly/abc로` (조사가 주소에 들어감) |
| `https://www.cjlogistics.com@evil.top/a` | `https://www.cjlogistics.com` (실제 접속 호스트는 `evil.top`) |

마지막 줄은 사칭 링크를 공식 주소로 대조하게 만든다.

## 결정

링크는 요구 분석 LLM 이 그 호출 안에서 함께 뽑는다. 정규식으로 찾지 않고, LLM 호출을 늘리지 않는다.

- 문자: `analyze_message` 의 출력에 `urls`(원문에 적힌 그대로의 구간)를 더한다.
  `ai.pipeline.read_message()` 가 `split_message()` 를 대신해 링크와 링크를 뗀 본문을 돌려준다.
  이어지는 `analyze_message_part(message, extracted=...)` 는 같은 읽기를 다시 쓰고 추출 LLM 을 부르지 않는다.
- 통화: `read_claims` 의 `spoken_hosts` 를 `VoiceCaseResult.links`(`origin="spoken"`)로 내보낸다.
- 코드는 찾지 않고 검증만 한다(`ai/src/ai/links.py`).
  - 구간이 원문에 그대로 있어야 한다. 글자를 고친 주소(`cjlogistlcs` → `cjlogistics`)는 버린다.
  - 더 긴 주소에서 일부만 떼어 낸 구간은 버린다(`cjlogistics.com.evil.top` 의 `cjlogistics.com`).
  - 무력화 표기 되살리기, 앞뒤에 붙은 조사·문장 부호 떼기, 정규형(v3 §4)만 한다.
  - 통화 호스트도 문자와 같은 호스트 규칙을 쓴다(V-7). 철자는 확인할 수 없어 공식 대조 근거가 될 수 없다(v3 U-3).

## 이유

- 어떤 글자 덩어리가 링크인지는 문맥을 봐야 안다. 바꿔 적은 주소와 조사 경계를 정규식으로 다 막을 수 없다.
- 판정의 입력은 여전히 코드가 확인한 값이다(CLAUDE.md 원칙 1·2). LLM 이 고친 철자나 잘라 낸 주소는
  원문 대조에서 걸러지므로 공식 판정으로 이어지지 않는다.
- 문자와 통화가 같은 모양(`LinkCandidate`)으로 링크를 넘긴다.

## 결과

- **LLM 이 실패하면 링크를 모른다.** 정규식 폴백이 없다. `read_message` 는 링크 없이
  `extracted.analysis_status="fallback"` 을 돌려준다. BE 는 이것을 "링크 없음"으로 읽지 말고 분석 불가로
  안내한다. 응답은 나가지만(원칙 3) 그 요청의 링크 조사는 빠진다.
- **LLM 이 링크 하나를 통째로 빠뜨리면 코드가 알 수 없다.** 같은 문자에 공식 링크만 남으면 공식 결과가
  나올 수 있다. 평가셋으로 누락률을 잰다(후속).
- **첫 응답 5초 안에는 링크가 있는지 모른다.** 지금의 "링크 없으면 R6 즉답" 분기는 콜백 뒤로 옮긴다.
  v3 B3 도 이 분기를 없애기로 했다.
- **링크 수집이 늦게 시작한다.** 지금은 수집과 문자 분석을 함께 시작한다. 바꾸면 요구 분석 LLM 이 끝난 뒤
  수집을 시작한다. 위험 신호 제안은 그대로 수집과 함께 돈다. BE 가 바꿀 때 `docs/latency-budget.md` 의
  실행 순서를 고친다.
- 스킴이 있는 IPv4 주소(`http://1.2.3.4/…`)도 링크로 넘어간다. 지금 정규식은 IP 를 링크로 보지 않는다.
  격리 수집기의 내부 주소 차단은 그대로 적용된다.
- `analyze_message` 의 결과와 위험 신호 제안 입력에 `urls` 키가 하나 늘었다. 링크를 뗀 본문을 넣는
  기존 경로에서는 빈 목록이다.
- BE 연동(`main.py`)은 BE 담당이 한다. 방법은 [`docs/ai-be-python-integration.md`](../ai-be-python-integration.md) §0.

## 후속

- 링크 추출 평가셋: 바꿔 적은 주소, 조사, 여러 링크, 공식·사칭 혼합 문자. 누락률과 원문 대조 탈락률을 잰다.
