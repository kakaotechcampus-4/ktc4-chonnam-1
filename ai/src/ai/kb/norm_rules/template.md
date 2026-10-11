---
id: NR-0000
status: draft
business: penalty            # delivery | penalty | overseas_payment
kind: procedure              # impersonation | procedure | verification
applies_to:
  org: ""                    # 사칭 패턴이면 문자가 주장하는 기관
  region: ""                 # 전국이 아니면 출처가 말한 지역만
  period: ""                 # 출처 게시·시행 시점
statement: ""
match: []                    # procedure·verification 만. 예: - {category: 택배} / - {brand: 우체국택배}
pattern:                     # kind: impersonation 일 때만
  claimed_org: ""
  pretext: ""
  requested_action: ""
exceptions: []
sources:
  - name: ""
    url: ""
    published_at: 2026-01-01
    checked_at: 2026-01-01
    license: ""
    quote: ""                # 여러 곳을 옮기면 목록
related_cases: []            # 사칭 패턴의 근거 KB 사례
reviewer: 미지정
reviewed_at:
---

# 규범 양식

이 양식과 `status: draft` 레코드는 어디에도 쓰이지 않는다. 사람이 출처 원문과 대조해 검토한 뒤
`curated` 로 바꾸고 `reviewer`·`reviewed_at` 을 채운다.

## 종류

- `impersonation` (사칭 패턴): 문자가 **주장하는** 기관, 내세운 명분, 요구하는 행동. "기관이 보냈다"
  가 아니라 "기관이라고 주장한다" 로 적는다. 근거 KB 사례를 `related_cases` 에 연결한다.
- `procedure` (공식 절차): 진짜 기관이 그 업무를 실제로 알리고 처리하는 방식과 예외.
- `verification` (확인 경로): 문자 속 링크·번호 대신 사용자가 직접 확인할 곳.

## 작성 규칙

- 레코드 하나에 `statement` 한 문장. 출처 원문이 직접 뒷받침하는 내용만 적는다. 원문에서 확인하지
  못한 내용은 단정하지 않는다.
- 공식 출처만 쓴다(기관 누리집, 정책브리핑, KISA, 금융감독원 등). 언론 보도는 공식 출처를 찾는
  단서로만 쓴다.
- `quote` 는 원문을 바꾸지 않고 옮긴다. 줄바꿈·연속 공백만 공백 하나로 합친다.
- `applies_to` 는 출처가 말한 범위만 적는다. 한 지자체의 안내를 전국으로 넓히지 않는다. 다른 기관·
  지역의 예외는 `exceptions` 에 적는다.
- `license` 에 공공누리 유형 등 이용 조건을 적는다. 제4유형(상업적 이용금지·변경금지)이 많다.
- 규범은 위험 판정에 쓰지 않는다. 판정은 `ai/verdict.py` 의 결정적 규칙이 한다.

## 적용 조건 (`match`)

`ai/kb/guidance.py` 의 `find_guidance()` 가 최종 분석 결과의 정해진 값과 대조한다. 판정에는 쓰지 않는다.

- 목록의 항목 중 하나라도 맞으면 적용한다. 한 항목 안의 조건은 모두 맞아야 한다.
- 키: `category`(Topic 값), `brand`(Brand 값), `doubts`(MessageDoubt 값), `official`(DomainMatch 값).
  `category`·`brand` 는 `message`·`env` 중 어느 쪽이 맞아도 된다. `unknown` 은 쓰지 않는다.
- `match` 가 없으면 쓰이지 않는다. `impersonation` 에는 달지 않는다.
- 자료의 적용 범위(`applies_to`)보다 넓게 걸지 않는다. 한 기관의 절차는 그 기관 `brand` 에만 건다.

## 검토 기준 (draft → curated)

검토자가 아래를 모두 확인한 뒤 `status: curated`, `reviewer`, `reviewed_at` 을 채운다.

1. 발췌문(`quote`)이 출처 원문에 그대로 있는가 — 원문을 내려받아 기계로 대조한 결과를 첨부한다.
2. `statement` 가 발췌문이 직접 말하는 범위를 넘지 않는가.
3. 적용 범위(`applies_to`)와 `exceptions` 가 출처와 맞는가.
4. 출처가 아직 열리는가, 더 최신 공지가 있는가.
5. 전화번호가 지금도 맞는가.
6. `match` 가 자료의 적용 범위보다 넓지 않은가.
