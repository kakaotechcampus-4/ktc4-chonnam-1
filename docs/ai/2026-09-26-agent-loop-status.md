# 에이전트 루프 구현 현황과 책임 분담

멘토 피드백에 대한 정리다. 우선순위는 아래 순서로 둔다.

1. 근거가 부족할 때 AI 가 추가 조회를 선택하는 루프를 **어디까지 구현했는지 구분** (4장)
2. **AI 가 다음 조사나 종료를 선택하는 부분을 세팅** (5장)
3. 선택 구현과 고정 순서 대비 비교 — **후순위** (6장)

오류 처리와 작업 종료 책임(2장), 최종 판정 규칙(3장)은 위 셋의 전제라 함께 적는다.

기준 문서: [Revisions-handoff.md](./Revisions-handoff.md), [PRD 3-1](../PRD.md),
[latency-budget.md](../latency-budget.md), [ADR 0002](../adr/0002-isolation-server.md),
[ADR 0004](../adr/0004-llm-call-budget-ownership.md).

응답 구조 자체는 [ai-be-final-result-schema.md](../ai-be-final-result-schema.md),
BE 가 작성할 호출 구문은 [ai-be-python-integration.md](../ai-be-python-integration.md)
에 있다. 이 문서는 그 구조를 **누가 채우고 누가 끝내는가**와 루프의 진척만 다룬다.

현재 코드에서 확인한 사실과 합의가 필요한 항목을 구분해 적는다. 합의 항목에는
`[ ]` 를 붙였다.

## 1. 실제 호출 흐름

`ai/src/ai/pipeline/` 기준이다. 왼쪽이 그 단계를 실행하는 주체다.

```
BE    ① 원본 문자에서 URL 과 본문을 분리한다
BE    ② urlscan 점수를 임계값 T 와 비교해 official(boolean) 을 만든다
BE    ③ 아래 셋을 병렬로 시작한다
        AI    analyze_message_part(text)   → MessagePart
        격리   페이지 수집                   → IsolatedPage(brand, category, info)
        BE    urlscan 점수 조회
BE    ④ official 이 도착하면 분기한다
        official=true  → await finalize_analysis(url)          나머지 태스크 취소
        official=false → await finalize_analysis(url, message, page, failure=...)
AI    ⑤ finalize_analysis() 내부
        official=true  → assemble_analysis(url)                페이지 분석을 하지 않는다
        official=false → analyze_environment_part(page, failure=...)
                         → assemble_analysis(url, message, env)
BE    ⑥ 직렬화, 콜백 전송, 결과 저장, "결과 확인" 조회 폴백
```

## 2. 오류 처리와 작업 종료

### 원칙

**AI 는 작업을 끝낼지 결정하지 않는다.** AI 공개 함수는 예외를 던지지 않고 항상
구조가 완전한 결과를 반환한다. 더 기다릴지, 취소할지, 사용자에게 무엇을 먼저
보낼지는 BE 가 정한다.

**AI 는 자기 단계의 실패를 결과 안에 담아 돌려준다.** 실패를 예외로 올리면 BE 가
"확보한 근거"와 "실패"를 함께 받을 수 없다. 부분적으로 확보한 근거는 버리지 않는다.

### 책임표

| 항목 | 담당 | 현재 상태 | 위치 |
|---|---|---|---|
| 요청 전체 deadline | BE | 미구현 | `backend/src/server/orchestration/` |
| 작업 취소·정리 | BE | 미구현 | 같음 |
| 늦게 끝난 결과가 저장된 응답을 덮어쓰지 않게 | BE | 미구현 | 같음 |
| 결과 저장과 "결과 확인" 조회 폴백 | BE | 미구현 | 같음 |
| 콜백 전송 실패 처리 | BE | 미구현 | 같음 |
| 조회 횟수 한도 강제 | BE(코드) | 미구현 | PRD 3-4 |
| 단계별 LLM 호출 상한 | AI | 구현 | `llm/analyze.py` 30s · `llm/signals.py` 30s · `llm/page.py` 2.0s |
| KB 검색 상한 | AI | 구현 | `pipeline/analysis.py` 50ms |
| AI 내부 자식 태스크 취소 | AI | 구현 | `pipeline/analysis.py` 의 `finally` |
| 입력 크기 거부 | AI | 구현 | 본문 8,192자 · HTML 131,072바이트 |
| 단계 실패를 결과로 반환 | AI | **부분 구현** | 아래 참조 |

LLM 상한 30초는 현재 메시지 시나리오 테스트 설정이다
([latency-budget.md](../latency-budget.md)). 운영 목표값(추출 1.5초, 신호 2.0초)과
다르며, 이 값으로는 AI 단계만으로 카카오 5초 SLA 를 넘길 수 있다.
**요청 전체의 시간 제한은 AI 단계 상한으로 대신할 수 없고 BE 가 따로 걸어야 한다.**

- [ ] `Revisions-handoff.md` 의 "추출 1.5초·신호 2.0초" 서술을 현재 코드값(둘 다
      30초)과 맞춘다. 지금 두 문서가 다르게 적혀 있다.

### 실패와 근거를 구분해 반환하기 — 현재 격차

분석이 실패하면 해당 파트의 `answer=false` 로 두고 `details.reason` 에 한국어
문장을 넣는다 (`pipeline/results.py` 의 `_FAILURE_REASONS`). `FailureCode` 는
내부 enum 이고 **응답 스키마에 없다.**

그래서 BE 는 아래 둘을 코드로 구분할 수 없다.

- (가) 페이지에서 의심 근거를 찾아서 `answer=false`
- (나) 페이지 분석이 실패해서 보수적으로 `answer=false`

지금은 `details.reason` 문자열을 맞춰보는 수밖에 없고 문구가 바뀌면 깨진다.
FE 작업 목록의 "실패의 `answer=false` 에는 실제 실패 사유를 함께 표시한다"도 이
구분에 기대고 있다.

**제안.** 두 파트에 `failure: FailureCode | null` 을 노출한다. `null` 이면 분석을
끝냈다는 뜻이고, 값이 있으면 그 단계가 실패해 보수적으로 `false` 로 둔 것이다.

- [ ] `failure` 필드 추가 여부. 추가하면 조기 반환의 말단 `null` 개수가 10 개에서
      12 개로 바뀌므로 연동 검증 기준도 함께 고친다.
- [ ] 추가하지 않는다면 BE·FE 가 무엇으로 구분할지 정한다. `details.reason`
      문자열 매칭은 계약으로 쓰지 않는다.

## 3. 최종 판정 규칙

```
result = url.official
```

`types.py` 의 `AnalysisResponse` validator 가 강제하며, 어긋나면 응답이 만들어지지
않는다.

판정 근거는 urlscan 점수와 BE 임계값 `T` 하나뿐이다. 메시지 분석과 페이지 분석은
판정에 관여하지 않고 근거 설명만 만든다. 둘 다 `answer=true` 여도 `official=false`
면 `result=false` 다.

`finalize_analysis()` 한 번 호출이 보장하는 것은 **예외 없이 구조가 완전한 응답이
나온다**는 것뿐이다. 임계값 `T` 가 적절한지, 점수 누락·조회 실패를 어떻게 다룰지는
이 함수가 검사하지 않는다. 호출부가 단순한 것과 결과를 믿을 수 있는 것은 다르다.

- [ ] 임계값 `T` 와 경계값 처리
- [ ] 점수 누락·조회 실패·범위 초과일 때 `official` 을 무엇으로 둘지. 지금은
      boolean 이라 "대조하지 못함"을 표현할 수 없다
- [ ] 두 분석 결과가 `official=true` 를 뒤집을 조건을 둘지. 지금은 없다

## 4. 에이전트 루프 구현 현황

[PRD 3-1](../PRD.md) 의 여섯 칸 기준이다.

| 칸 | 무엇을 하나 | 담당 | 현재 상태 |
|---|---|---|---|
| ① | 문자 읽기 | 모델 | **구현 (상한 있음)** — `llm/analyze.py` 추출 + `llm/signals.py` 신호 제안 |
| ② | 전송 동의 후 분석 요청 | 사람+코드 | AI 범위 밖 (BE·FE) |
| ③ | 관측 수집 | 코드 | 격리 서버와 BE 가 수행. AI 는 `IsolatedPage` 를 받기만 한다 (ADR 0002) |
| ④ | 주장·행동 대조 | 모델 | **연동 전** — 이번에 `pipeline/compare.py` 로 최소 구현을 추가했다. 아직 어느 경로에서도 호출하지 않는다 |
| ⑤ | 근거 보강 판단 | 모델 | **미구현** |
| ⑥ | 결과·미확인 범위·공식 경로 | 코드+모델 | **부분** — `pipeline/results.py` 가 조립한다. 공식 확인 경로 안내는 미구현 |

### 지금 도구 호출 순서는 코드가 정한다

`finalize_analysis()` 는 `official` 분기 → 페이지 분석 → 조립을 고정 순서로
실행한다. 되돌아가는 경로가 없고 반복도 없다. **모델이 "다음에 무엇을 조회할지"
또는 "여기서 끝낼지"를 고르는 자리는 코드에 없다.**

### ①의 상한 — 통과 가능한 신호가 코드로 제한돼 있다

`pipeline/results.py` 의 `validate_message_signals()` 는 LLM 이 제안한 신호를
이렇게 거른다.

- 인용이 4자 미만이거나 `quote not in text` 면 탈락
- `_SUBJECT_ACTION` 정규식이 **그 인용 범위 안에서** 주어와 동작을 잡아야 통과
- `evidence_source` 가 `MESSAGE` 가 아니면 탈락

`_SUBJECT_ACTION` 은 `INSTALL_PROMPT`, `CREDENTIAL_REQUEST`, `REMOTE_CONTROL`
셋만 갖고 있다. `RiskSignalCode` 는 여덟 개이므로 **나머지 다섯 개는 어떤 경우에도
통과하지 못한다.**

이것은 결함이 아니라 의도된 가드레일이다(근거 없는 신호를 채택하지 않는다).
다만 **모델을 더 잘 부른다고 해서 통과 신호가 늘지 않는다**는 뜻이므로, 루프의
효과를 message 쪽 근거 수로 재려는 계획은 성립하지 않는다. 6장에서 다시 다룬다.

### 미사용 훅 — `analyze_signals` 의 관측 인자

```python
# llm/signals.py
async def analyze_signals(masked_text, extracted, observations, *, case_search=...)
    digest = _observation_digest(observations)   # 관측을 프롬프트에 싣는다

# pipeline/analysis.py
signals = await analyze_signals(text, extracted, None, case_search=cases, ...)
                                              # ↑ 항상 None
```

페이지 관측을 신호 제안에 넣는 경로가 구현돼 있으나 새 파이프라인은 `None` 을
넘긴다. 구 `decide()` 시절의 유산이다. 다만 위 상한 때문에, 여기에 관측을 넣어도
`validate_message_signals()` 가 관측 출처 신호를 전량 버리므로 지금 구조에서는
채택 신호가 늘지 않는다.

- [ ] 이 인자를 쓸지, 지울지 정한다. 쓰려면 관측 출처 신호의 검증 경로가 함께
      필요하다

## 5. 선택 부분 세팅

### 이번에 추가한 것 — ④의 최소 구현

`ai/src/ai/pipeline/compare.py`

```python
def compare_claims(message: MessagePart, env: EnvironmentPart) -> tuple[Conflict, ...]

class Conflict(str, Enum):
    BRAND = "brand_conflict"   # 문자는 한진택배, 페이지는 KISA
    TOPIC = "topic_conflict"   # 문자는 택배, 페이지는 금융
    DOUBT = "doubt_conflict"   # 문자는 배송 조회, 페이지는 로그인 폼
```

- LLM 0 콜, 외부 I/O 없음, 순수 함수다.
- **확인하지 못한 값은 어긋남으로 세지 않는다.** `unknown`, `없음`, 조기 반환의
  `None` 은 전부 "비교하지 않음"이다. 이것을 불일치로 처리하면 수행하지 않은
  확인을 주장하게 된다.
- 어느 쪽 값도 고치지 않는다. 반환값은 차이의 목록일 뿐이다
  (`Revisions-handoff.md` 의 "한쪽의 값을 다른 쪽으로 덮어쓰지 않는다").
- 같은 기업 계열(CJ 4종, 롯데 2종)은 값이 달라도 어긋남으로 보지 않는다. 값을
  합치지는 않고 비교할 때만 같은 계열로 취급한다.
- `Conflict` 는 **관측이지 위험 신호가 아니다.** 배송 조회 문자와 로그인 폼
  페이지의 차이는 설명의 재료이지 악성의 근거가 아니다.

테스트는 `ai/tests/test_compare.py` 18 건이다. 전체 645 건 통과.

**공개 범위는 건드리지 않았다.** `test_revision_pipeline.py` 가
`ai.pipeline.__all__` 을 네 함수로 고정하고 있어, `compare_claims` 는 `__all__` 에
넣지 않고 모듈 경로로만 쓴다. BE 연동 계약이 정해지면 그때 공개한다.

- [ ] `_CONSISTENT_ELEMENTS` 표(문자 목적 ↔ 페이지 요소)를 실제 페이지 사례로
      검증한다. 지금은 설계 후보이며 합성 테스트만 통과했다. 격리 환경 작업
      목록의 "정상 로그인·결제·앱 다운로드·주소 입력·조회 화면 사례"와 같은 일이다
- [ ] `compare_claims` 를 `finalize_analysis` 경로에 넣을지, 실험 전용으로 둘지

### 아직 필요한 것 — 선택이 성립하려면

⑤가 의미를 가지려면 세 가지가 더 있어야 한다.

**(가) 부족을 두 종류로 나눈다.**

| 종류 | 예 | 대응 | 모델을 부르나 |
|---|---|---|---|
| 단계 실패 | 검색 타임아웃, 페이지 분석 실패 | 재시도 또는 포기 | **안 부른다** — 코드가 처리 |
| 대조 불일치 | `BRAND`, `TOPIC`, `DOUBT` | 무엇으로 확인할지 선택 | **부른다** |

단계 실패에 대한 올바른 대응은 재시도이지 판단이 아니다. 모델은 "어느 단계가
깨졌나"가 아니라 **"어긋남을 어떻게 확인할까"** 만 고른다. PRD 에서 ⑤가 ④ 다음에
오는 이유가 이것이다.

**(나) 모델에게는 닫힌 enum 만 준다.**

```
conflicts     : [BRAND]
message_brand : 한진택배          (Brand)
env_brand     : KISA              (Brand)
message_doubt : 배송 조회          (MessageDoubt)
env_doubt     : 로그인·인증 입력폼  (EnvDoubt)
```

페이지 원문과 문자 원문은 선택 프롬프트에 넣지 않는다. 전부 닫힌 enum 이라
공격자가 값을 심을 수 없고, "페이지 내용으로 허용 도구 목록·조회 한도가 바뀌지
않는다"(PRD 3-4)가 구조적으로 지켜진다. 동시에 선택에 필요한 정보는 들어간다.

**(다) 비용이 다른 선택지가 있어야 한다.**

| 조회 | `BRAND` 불일치에 무엇을 답하나 | 비용 |
|---|---|---|
| 공식 확인 경로 목록 | 문자가 주장한 브랜드의 등록 도메인 ↔ `url.domain` | DB 조회 |
| 격리 재수집 | 클로킹 의심이면 다른 조건으로 다시 | 8~12초 |
| 없음(종료) | 확보한 것으로 끝낸다 | 0 |

**비용이 비슷한 선택지만 있으면 고를 이유가 없다.** 값싼 조회로 충분한 경우와
비싼 재수집이 필요한 경우를 가르는 것이 모델이 할 일이다. 두 조회 모두 `ai/` 밖에
있으므로 BE 와 입출력 계약을 합의해야 한다 — `ai/` 는 외부를 부르지 않는다
(ADR 0001·0002).

- [ ] 허용 조회 목록을 확정한다. 각 조회의 입력·출력 타입을 BE 와 합의한다
- [ ] 조회 한도의 초기값과 한도 도달 시 동작. PRD 는 "판단 보류로 종료"라고 적고 있다
- [ ] `ai/` 가 "보강 요청"을 반환하는 형태(`finalize_analysis` 와 별도 함수)를 정한다

## 6. 비교 실험 (후순위)

### 반드시 3-arm 이어야 한다

| arm | 다음 조회를 누가 정하나 | 추가 LLM 호출 |
|---|---|---|
| A · 고정 | 추가 조회 없음 | 0 |
| **B · 규칙표** | `{conflict: lookup}` 딕셔너리 | **0** |
| C · 모델 | 모델이 선택 | 1 |

2-arm(A 대 C)으로 재면 "추가 조회가 도움이 되는가"와 "모델이 고르는 게 도움이
되는가"가 섞인다. **C 가 B 를 이기지 못하면 에이전트가 아니라 딕셔너리를 쓰면
된다.** B 없이 C 가 A 를 이기는 것만 보고 에이전트의 효과라고 결론 내리면 안 된다.

### 측정

| 항목 | 정의 |
|---|---|
| 근거 확보 | 검증을 통과한 인용 수. `details.reason` 이 비지 않았는지가 아니다 |
| 미확인 비율 | `doubt` 가 `unknown` 인 응답 비율 |
| 조회 횟수 | arm 별 실제 실행 횟수 |
| 추가 LLM 호출 | 선택 판단에 쓴 호출 수 |
| 대기시간 | p50 / p95 |

### 실험 전에 정해야 하는 것

- **라벨된 입력이 있어야 한다.** 정답이 없으면 "근거를 더 찾았다"와 "환각이 검증을
  우연히 통과했다"를 구분할 수 없다. PRD 5~6주 항목의 "정답은 별도 근거로 준비"가
  이것이다.
- **적응형 arm 은 콜백 경로에서 돌린다.** 첫 응답 경로에 조회를 얹으면 대기시간
  비교가 불공정해진다. `latency-budget.md` 의 콜백 구간에 조회 1회 예산을 따로 잡는다.
- **이 실험은 판정 정확도를 재지 않는다.** `result = url.official` 이므로 오탐·미탐은
  임계값 `T` 의 함수이고 루프가 바꾸지 못한다. 잴 수 있는 것은 근거 품질과 비용이며,
  그것이 멘토가 말한 "근거 확보나 비용·대기시간"과 일치한다.

- [ ] 라벨 데이터 20건을 `docs/experiments/` 에 만든다
- [ ] 측정 스크립트를 먼저 준비한다. ⑤ 구현 전에도 arm A 수치는 잴 수 있다

## 7. 다음 할 일

**합의가 먼저**

- [ ] `failure` 필드 추가 여부 (2장)
- [ ] 점수 누락·조회 실패 시 `official` 처리 (3장)
- [ ] 허용 조회 목록과 입출력 계약 (5장)
- [ ] 조회 한도와 한도 도달 시 동작 (5장)

**합의 없이 할 수 있는 것**

- [ ] `Revisions-handoff.md` 의 LLM 예산 서술을 코드값과 맞춘다 (2장)
- [ ] `_CONSISTENT_ELEMENTS` 표를 실제 페이지 사례로 검증한다 (5장)
- [ ] BE 의 요청 전체 deadline·취소·저장·콜백 실패 처리를 구현한다 (2장)
- [ ] 라벨 데이터와 측정 스크립트를 준비한다 (6장)

## 부록 — 로컬 환경 메모

`ai/ai_pr.md` 와 `ai/ai_review_fixes.md` 는 작업 메모라 저장소에 올리지 않는다.
작성자 PC 의 `.git/info/exclude` 에만 넣어 두었으므로 **다른 사람의 작업 트리에는
그대로 보인다.** 팀 전체에서 제외하려면 `.gitignore` 로 옮겨야 한다.
