# 스미싱 분석 재설계 — AI 파트

작성일: 2026-09-18
대상 브랜치: `refactor/smishing-message-intake-ai`
기준 문서: `docs/ai/smishing-message-intake-ai.md`, `CLAUDE.md`, `docs/latency-budget.md`

이 문서는 AI 파트의 역할을 3개로 재정의하고, 격리 환경 도입·RAG 사례 검색·하이브리드
판정을 반영한 설계다. 구현 완료를 뜻하지 않는다.

---

## 0. 무엇이 바뀌는가

기존 설계는 AI를 "메시지 추출 + 설명"으로 한정하고 격리 환경을 프로토타입에서 제외했다.
이번 재설계는 세 가지를 바꾼다.

1. **격리 환경을 포함한다.** 단, 백엔드와 분리된 전용 서버로 둔다.
2. **판정을 하이브리드로 한다.** LLM이 위험 신호를 근거와 함께 제안하고, 결정적 코드가 확정한다.
3. **RAG 피해 사례 검색을 구현한다.** 팀이 수집한 실물 문자를 지식베이스로 쓴다.

작업 범위는 `ai/` 와 `docs/` 에 한정한다. `backend/`, `frontend/` 는 건드리지 않고
필요한 사항은 `docs/integration-requests.md` 로 전달한다.

### 확정된 결정

| # | 결정 | 근거 |
|---|---|---|
| D1 | LLM은 위험 신호를 근거와 함께 제안하고, 결정적 코드가 최종 상태를 확정한다 | 판정 권한을 LLM에 주지 않으면서 관측 해석은 활용 |
| D2 | 격리 환경은 백엔드와 분리된 전용 서버다. 페이지 내용 해석까지 그 서버가 수행한다 | 스캐너 실행은 백엔드 책임, 격리는 독립 리소스 |
| D3 | 격리 분석은 urlscan과 동시에 시작한다. urlscan 결과가 격리를 요구할 때만 결과를 채택한다 | 시간 단축. 리소스 낭비는 감수 |
| D4 | RAG 사례 검색을 이번에 구현한다. 데이터는 팀이 수집한 실물 문자 | 분류(taxonomy)와 별개 기능 |
| D5 | 격리 스텁은 진짜 관측처럼 처리하되 `source` 로 출처를 표시한다 | 파이프라인 전체를 지금 검증하기 위해 |
| D6 | 카카오 5초 제한은 콜백으로 해결한다 | 기존 "콜백에 의존하지 않는다" 제약을 뒤집는 결정 |
| D7 | 통합 분석은 LLM 2회로 나눈다 (신호 제안 → 판정 → 설명 생성) | 신호와 설명의 책임 분리 |

---

## 1. 컴포넌트 경계와 데이터 흐름

### 1.1 컴포넌트

| 컴포넌트 | 책임 |
|---|---|
| 백엔드 (`backend/src/server/`) | 텍스트·링크 분리, 마스킹, urlscan·격리 서버 호출, 게이트 판단, 공통 구조 변환, 저장·조회, 카카오 연동 |
| 격리 서버 (신규, 별도 서비스) | 샌드박스 렌더링 + 페이로드 정적 분석 + 페이지 내용 해석. 구조화 결과 반환 |
| `ai/` (로컬 패키지) | 메시지 추출 / 사례 검색 / 신호 제안 / 결정적 판정 / 설명 생성 |

`ai/` 는 로컬 패키지로 유지한다 (ADR 0001). 격리 서버만 신규 HTTP 서비스다.
`ai/` 는 서버 모듈을 import 하지 않고 카카오 payload 를 구성하지 않는다.

### 1.2 `ai/` 내부 구성

```
ai.llm.analyze.analyze_message()    ① 메시지 추출          [구현됨]
ai.kb.search.search_cases()         ② 사례 검색·유사도      [신규]
ai.llm.signals.extract_signals()    ③a 위험 신호 제안       [신규]
ai.verdict.decide()                 결정적 판정 → 4상태     [신규, 순수함수]
ai.llm.explain.explain()            ③b 설명 생성 + 템플릿 폴백 [기존 파일 확장]
```

`verdict` 는 패키지가 아니라 모듈 하나다. 함수가 하나뿐이라 디렉터리가 불필요하다.

### 1.3 흐름

```
메시지 도착
  ├ 텍스트 / 링크 분리, 마스킹
  ├ 동시 시작 ─┬ urlscan          (~30s)
  │            ├ 격리 서버         (8~12s, 투기적)
  │            └ ① 메시지 추출 + ② 사례 검색  (~1.5s)
  ├ 즉시 응답: "분석 중" + useCallback
  │
  ├ urlscan 도착 → 격리 결과가 필요한가?
  │     필요   → 격리 결과 채택 (이미 도착해 있음)
  │     불필요 → 격리 요청 취소·결과 폐기
  ├ 공통 구조 변환
  ├ ③a 신호 제안 (LLM)
  ├ decide() → 4상태
  ├ ③b 설명 생성 (LLM)
  └ 콜백으로 최종 결과 전송
```

①②는 1.5초면 끝나므로 urlscan 대기 시간 안에 완료된다. 격리(8~12초)도 urlscan(약 30초)보다
빠르므로 게이트 시점에 결과가 이미 있다. 추가 대기는 발생하지 않는다.

### 1.4 공식 도메인 즉답

화이트리스트 대조는 50ms 다. 도메인이 검증된 공식 목록과 일치하면 urlscan 을 기다리지 않고
첫 응답에서 "공식 도메인 확인"을 반환하고 종료한다. 콜백을 쓰지 않는다.

정상 문자가 1초 미만으로 끝난다. 애매한 것만 전체 흐름을 탄다.

### 1.5 시간

| 구간 | 예상 |
|---|---|
| 공식 도메인 즉답 경로 | < 1s |
| 즉시 응답 (접수 안내) | < 1s |
| urlscan | ~30s |
| 격리 서버 | 8~12s (urlscan 과 병렬) |
| ③a 신호 | 2.0s |
| ③b 설명 | 2.0s |
| 전체 | ~35s, 상한 60s |

프로토타입은 소요 시간을 목표로 삼지 않는다. 안정적으로 결과가 나오는 것이 목표다.
향후 urlscan 을 자체 시스템으로 교체해 총 15~20초를 노린다. `ai/` 는 공급자를 모르므로
교체 시 백엔드 어댑터 한 곳만 바뀐다.

### 1.6 콜백 폴백

카카오 콜백은 1회성이고 유효 시간이 있다.

- 주 경로: 콜백으로 최종 결과 전송
- 폴백: 결과를 저장. 콜백 실패·시간 초과 시 "결과 확인" 버튼으로 조회

전체 상한 60초를 넘기면 그 시점까지 확보한 근거로 판정하고 종료한다.

---

## 2. 데이터 계약

### 2.1 수집 샘플에서 확인된 사실

스미싱 수백 건과 정상 알림 5건을 분석한 결과다.

| 축 | 스미싱 | 정상 |
|---|---|---|
| 길이 | 1~2줄 단문 | 다중 라인, 항목형 |
| 송장번호 | 없거나 부분 마스킹 | 풀번호 |
| 보내는분·상품명·주소 | 없음 | 구체적으로 존재 |
| 난독화 | 심함 | 없음 |
| URL 개수 | 1 | 1~2 |

`[Web발신]` 은 양쪽 모두에 붙는다. 신호로 쓸 수 없다.

난독화 수법:

| 수법 | 예 |
|---|---|
| 글자 사이 구분자 | `우-체-국-택-배`, `택 배`, `C·J`, `우체&국택배` |
| 어절 경계 파괴 | `우체국택 배배송했습니다` |
| HTML 엔티티 잔존 | `&nbsp;`, `&l;`, `&g;`, `Camp;J` |
| 브랜드 변형 | `CA/CX/CZ/CG/CW/CS대한통운`, `C+J`, `C;J` |
| 기계번역체 | "구매 한 상품이 배송되었습니다" |

### 2.2 분류는 위험 신호가 아니다

정상 롯데 문자도 `delivery` 로 분류된다. `CategoryCode` 는 **메시지 주제**이지 스미싱 유형이
아니다. `types.py` docstring 과 `parse_classify.md` 에 명시하고, `decide()` 입력에서 제외한다.

### 2.3 다중 링크

정상 CJ 문자는 링크를 2개 갖는다 (`dxsmapp.cjlogistics.com`, `www.cjlogistics.com`).
"링크 여러 개면 선택 요청"은 정상 사용자만 막는다. 규칙을 바꾼다.

| 링크 | 처리 |
|---|---|
| 0개 | 입력 보완 요청 (최종 상태 아님) |
| 1개 | 그 링크 분석 |
| N개, 전부 공식 | 공식 도메인 확인 |
| N개, 비공식 1개 | 그 1개 분석 |
| N개, 비공식 2개 이상 | 선택 요청 |

### 2.4 공식 단축 도메인

쿠팡 정상 문자가 `coupa.ng/a/` 를 쓴다. "단축 URL = 의심"은 즉시 오탐이다. 화이트리스트에
공식 단축 도메인을 별도 등재해야 한다. 역으로, 샘플에 나온 도메인을 자동으로 화이트리스트에
넣지 않는다. 사람이 검증한 목록만 쓴다.

### 2.5 정규화 — 검색 전용

```
1. HTML 엔티티 디코딩    &nbsp; &l; &g; &amp;
2. 발신 접두어 제거      [Web발신] [국외발신]
3. 글자 사이 구분자 제거  - · . , ` * + ; & 공백
4. 소문자화
```

**LLM 에는 원문을 그대로 전달한다.** 정규화 텍스트를 주면 LLM 이 내는 `evidence` 가 정규화
문자열이 되어 `analyze.py:_has_evidence()` 의 원문 대조가 깨진다. 정규화는 `ai.kb.normalize`
에만 존재한다.

### 2.6 사례 검색

문자 3-gram Jaccard 유사도를 쓴다.

```python
def _trigrams(s: str) -> set[str]:
    return {s[i:i+3] for i in range(len(s) - 2)}

similarity = len(a & b) / len(a | b)
```

한국어에 난독화까지 겹쳐 단어 토큰화가 무의미하다. `우-체-국-택-배` 는 단어 기반 BM25 로
`우체국택배` 와 매칭되지 않는다. 임베딩 모델도 난독화 텍스트에 약하고 API 호출이 지연을 늘린다.

수백 건 규모에서 집합 연산은 수 ms 다. 외부 의존성이 없다.

알려진 한계 — Jaccard 는 길이 차에 민감하다. 긴 정상 문자와 짧은 스미싱은 유사도가 낮게
나와 유리하지만, 짧은 정상 알림은 짧은 스미싱과 높게 나올 수 있다. 측정 후 부족하면
containment 또는 임베딩으로 승격한다. `ponytail:` 주석으로 표기한다.

### 2.7 KB 레코드

```yaml
---
id: CE-0042
status: curated              # draft | curated — curated만 인덱싱
origin: team_collected
collected_at: 2026-09-18
reviewer: <이름>
normalized: "우체국택배확인부탁합니다"
variants:
  - "우체국택배 확인부탁합니다"
  - "우-체-국-택-배 확-인-부-탁-합-니-다"
categories: [delivery]
claimed_brand: 우체국택배
---
```

정규화 후 동일한 것은 한 레코드로 합치고 원문을 `variants` 에 모은다. 수집 샘플에 중복
변종이 많아 실제 레코드 수는 크게 줄어든다.

기존 `ai/src/ai/kb/README.md` 의 출처·검토자·검토일 규범과 "승인된 레코드만 인덱싱" 규칙을
따른다. `CE-0001` 은 `status: draft` 라 검색에서 제외된다.

### 2.8 검색 결과

```python
class CaseMatch(StrictModel):
    case_id: str
    similarity: float          # 0~1. 스미싱 확률이 아니다
    matched_variant: str
    categories: list[CategoryCode]

class CaseSearchResult(StrictModel):
    status: AnalysisStatus     # completed | fallback
    matches: list[CaseMatch]
```

`decide()` 입력에서 제외한다. 설명과 사용자 표시용 참고 정보로만 쓴다. 검색 실패·자료 부족·
텍스트 없음은 각각 구분하며 유사도 0 으로 대체하지 않는다.

### 2.9 평가 데이터셋

```
ai/eval/datasets/
  smishing.jsonl    수집 스미싱 (URL 자리표시자 유지)
  benign.jsonl      정상 알림 20~30건
```

KB 와 분리한다. 정상 문자는 피해 사례가 아니므로 검색 대상이 아니다. 스미싱도 KB 에 넣은 것과
eval 에 넣은 것을 나눠야 자기 자신을 검색해 유사도 1.0 이 나오는 일이 없다.

측정 항목: 오탐률, 누락률, 추출 항목별 근거 정확도, 유사도 분포 겹침.

### 2.10 개인정보 마스킹

수집한 정상 샘플에는 실제 개인정보가 들어 있다. 저장소는 public 이다. 커밋 전 반드시 마스킹한다.

| 항목 | 처리 |
|---|---|
| 배송 주소 | `<주소>` |
| 송장번호·주문번호 | `<송장번호>` `<주문번호>` |
| 발신 업체명 | `<보내는분>` |
| 상품명 | `<상품명>` |
| URL 내 토큰 | 도메인만 남기고 절단 |

마스킹 후에도 구조는 보존한다. 판별 신호가 "값이 구체적으로 존재하는가"이므로 필드를 통째로
지우면 정상과 스미싱의 차이가 사라진다. 자리표시자로 치환하되 필드는 남긴다.

---

## 3. 격리 서버 계약

### 3.0 `Observations` 와 격리 응답의 관계

`Observations` 는 백엔드가 urlscan 과 격리 서버 결과를 합쳐 만드는 **공통 구조**다.
아래 3.2 의 격리 응답 스키마가 그 기반이며, urlscan 결과도 같은 모양으로 변환되어 합쳐진다.

`ai/` 는 `Observations` 하나만 받는다. 어느 공급자에서 온 관측인지는 각 항목의 출처 필드로만
안다. 공급자가 바뀌어도 `ai/` 는 수정하지 않는다.

### 3.1 요청

```
POST /analyze
{ "url": "https://...", "request_id": "...", "timeout_ms": 30000 }
```

게이트가 "격리 불필요"로 나오면 진행 중인 요청을 취소한다. 취소하지 않으면 투기적 실행이
격리 서버를 계속 점유한다.

### 3.2 응답

```json
{
  "schema_version": "1.0",
  "analysis_id": "uuid-v4",
  "source": "isolation",
  "analyzed_at": "2026-09-18T12:00:00+09:00",
  "elapsed_ms": 9400,

  "status": "success",
  "failure_reason": null,

  "target": {
    "input_url": "https://bit.ly/xxxx",
    "final_url": "https://evil.example/cj",
    "redirect_chain": ["https://bit.ly/xxxx", "https://evil.example/cj"],
    "final_http_status": 200,
    "page_title": "CJ대한통운 배송조회",
    "page_state": "rendered"
  },

  "verdict": {
    "label": "malicious",
    "confidence": "high",
    "decided_by": "model",
    "categories": ["credential_theft", "remote_control"],
    "impersonated_target": { "code": "delivery", "raw": "CJ대한통운" }
  },

  "evidence": {
    "static": {
      "payload_type": "apk",
      "sha256": "...",
      "size_bytes": 3841920,
      "checks": {
        "permissions":  { "state": "found",   "items": ["android.permission.READ_SMS"] },
        "iocs":         { "state": "unknown", "items": [] },
        "form_inputs":  { "state": "checked_absent", "items": [] }
      },
      "risk_signals": ["accessibility_abuse", "commercial_packer"]
    },
    "model": {
      "summary": "...",
      "model_id": "...",
      "raw_response_ref": "s3://.../llm_raw.json"
    },
    "screenshot_ref": "s3://.../view.png"
  },

  "unchecked": [
    { "check": "iocs", "reason": "상용 패커로 DEX 암호화" }
  ],

  "display": { "headline": "...", "detail": "...", "recommended_action": "..." }
}
```

### 3.3 열거형

```python
class CheckState(str, Enum):
    FOUND = "found"                        # 검사에서 확인했다
    CHECKED_ABSENT = "checked_absent"      # 명시된 범위에서 찾지 못했다
    UNKNOWN = "unknown"                    # 미지원·실패·미실행으로 알 수 없다

class PageState(str, Enum):
    RENDERED = "rendered"
    EXPIRED = "expired"                    # 1회성 링크 소진
    CLOAKED_SUSPECT = "cloaked_suspect"    # 데이터센터 IP 감지로 정상 사이트 리다이렉트 의심
    UNREACHABLE = "unreachable"
```

`CheckState` 3분법은 이 설계에서 가장 틀리기 쉬운 부분이다. 빈 목록으로 "없음"을 추론하면
안 된다. 패커로 DEX 가 암호화되면 권한을 못 읽는데, 빈 배열을 "권한 없는 앱"으로 읽으면
위험한 APK 가 안전으로 통과한다.

`PageState` 는 `CheckState` 로 표현할 수 없는 두 상황을 위해 필요하다.

- `expired` — 접속은 됐지만 "종료된 페이지"다. 검사는 전부 `checked_absent` 로 나오지만
  안전하다는 뜻이 아니라 그 자체가 신호다.
- `cloaked_suspect` — 최종 URL 이 유명 정상 사이트다. 검사가 깨끗하게 나오지만 원본 링크는
  다르다. 최종 URL 로 화이트리스트를 대조하면 안 된다.

### 3.4 필수 필드와 협의 가능 필드

필수 7개. 없으면 판정이 깨진다.

| 필드 | 없으면 |
|---|---|
| `status`, `failure_reason` | 실패와 안전을 구분 못 함 |
| `target.input_url` | cloaking 시 원본 도메인을 잃음 |
| `target.page_state` | 만료·cloaking 판별 불가 |
| `evidence.static.checks[].state` | "없음"과 "못 봄"을 구분 못 함 |
| `evidence.static.risk_signals` | 결정적 신호 소실 |
| `unchecked` | 한계 표시 불가 |
| `source` | 스텁이 실제처럼 나감 |

나머지(`elapsed_ms`, `analysis_id`, `sha256`, `size_bytes`, `model_id`, `raw_response_ref`,
`display.*`, `verdict.*`, `evidence.model.*`)는 `ai/` 가 판정에 쓰지 않는다. 팀 합의로 정리 가능하다.

### 3.5 입력 모델은 strict 금지

```python
class StrictModel(BaseModel):          # LLM 출력용 — 유지
    model_config = ConfigDict(extra="forbid")

class InboundModel(BaseModel):         # 외부 입력용 — 신규
    model_config = ConfigDict(extra="ignore")
```

LLM 출력(`ExtractedMessage`, `RiskSignal`)은 strict 를 유지한다. 환각 방어에 필요하다.
외부 입력(`Observations`, `DomainCheck`)은 `extra="ignore"` 로 둔다. 그래야 백엔드가 키를
추가·제거해도 `ai/` 를 수정하지 않는다.

### 3.6 LLM 판정 필드 취급

격리 서버는 자체 LLM 으로 `verdict.label`, `confidence`, `display.*` 를 낸다.
이는 우리 판정 원칙과 충돌하므로 **`decide()` 입력에서 제외한다.** urlscan 점수와 동일 취급이다.

버리지는 않는다. 표시·로그·사후 추적에 남긴다. 다만 사용자에게 나가는 최종 문구는 ③b 가 만든다.
`display.*` 를 그대로 쓰면 격리 결과만 보고 쓴 문장과 우리 최종 판정이 어긋난다.

`evidence.static.*` 은 문자열·Magic Byte 매칭 결과이므로 관측 사실로 채택한다.
`risk_signals` 도 결정적 규칙이 붙인 라벨이므로 채택한다.

### 3.7 스텁

동일 스키마, `source: "stub"`. 픽스처 4종:

| 파일 | `payload_type` | 특징 |
|---|---|---|
| `observations_benign.json` | `none` | 전부 `checked_absent` |
| `observations_form.json` | `credential_harvesting_form` | 이름·전화·주민번호 입력 폼 |
| `observations_apk.json` | `apk` | 위험 권한 + 패커, `iocs` 는 `unknown` |
| `observations_failed.json` | `none` | 수집 실패, 전부 `unknown` |

파이프라인은 스텁을 진짜 관측처럼 처리한다. 그래야 스텁으로 짠 규칙이 실제에서도 같게 동작한다.

`ai/` 가 보장하는 것은 `source` 가 결과까지 손실 없이 전파되는 것뿐이다. 운영에서 스텁을
차단하는 것(`ISOLATION_ALLOW_STUB`, 운영에서 참이면 기동 실패)은 백엔드 책임이다.

백엔드 스텁 서버도 같은 픽스처 파일을 응답 본문으로 쓴다. 데이터를 공유하므로 스텁과 테스트가
어긋나지 않는다.

---

## 4. 판정

### 4.1 시그니처

```python
def decide(
    extracted: MessageAnalysis,        # ① 메시지 추출
    domain_check: DomainCheck,         # 화이트리스트 대조 결과
    observations: Observations | None, # 공통 구조
    risk_signals: list[RiskSignal],    # ③a 제안
) -> Verdict
```

`ai/` 는 화이트리스트 파일을 직접 읽지 않는다. 대조 **결과**를 받는다. 스캐너를 부르지도 않는다.
관측 **결과**를 받는다.

입력에서 제외: RAG 유사도, urlscan 점수·categories·brands, `extracted.categories`,
격리 서버의 `verdict.*` 와 `evidence.model.*`.

### 4.2 신호 검증

이름이 겹치는 두 가지를 구분한다.

| 이름 | 출처 | 성격 | 취급 |
|---|---|---|---|
| `evidence.static.risk_signals` | 격리 서버 정적 파서 | 결정적 규칙이 붙인 라벨 | 관측 사실. `Observations` 에 실려 옴 |
| `RiskSignal` (`decide()` 인자) | ③a LLM | 제안 | 아래 검증을 통과해야 채택 |

`risk_signals` 는 후보일 뿐이다. `decide()` 가 채택 전에 거른다.

- 각 신호에 `evidence_ref` 필수 — 원문 인용 또는 관측 항목 ID
- 원문 인용은 입력 본문에 실제 존재해야 한다 (`analyze.py:_has_evidence()` 재사용)
- 관측 ID 는 `observations` 에 실제 존재하고 해당 검사의 `state` 가 `found` 여야 한다
- 어긋난 신호만 버리고 나머지는 유지한다

`analyze.py:_sanitize_extracted()` 와 같은 패턴이다.

### 4.3 4상태 규칙

| 조건 | 상태 | `ReasonCode` |
|---|---|---|
| 분석 대상 도메인이 검증 목록과 완전 일치 | 공식 도메인 확인 | `official_match` |
| 등록된 브랜드 주장 + 도메인이 그 브랜드 공식 목록과 불일치 | 스미싱 의심 | `lookalike` |
| 검증된 위험 신호 존재 | 스미싱 의심 | — |
| `page_state == expired` | 스미싱 의심 | — |
| `page_state == cloaked_suspect` | 판단 보류 (원본 도메인으로 대조). **단 등록 브랜드 사칭이 먼저다** | — |
| 브랜드 미확인·미등록 + 비공식 도메인 + 관측 없음 | 판단 보류 | `not_in_whitelist` |
| 근거 충돌 | 판단 보류 | — |
| 접속 실패·타임아웃·필요한 분석 미수행 | 분석 불가 | `unresolved` |
| 링크 없음 | 최종 상태 아님 (입력 보완 요청) | `no_url` |

위험 신호로 인정하는 것: 설치 파일 유도, 자격증명 입력 폼, 위험 권한(접근성·SMS·설치),
원격 제어 앱 intent, 대용량 패딩 페이로드, 상용 패커 감지.

**등록된 브랜드인지가 오탐 방지의 핵심이다.** 소규모 쇼핑몰이 자체 도메인으로 보낸 정상
문자는 브랜드가 목록에 없으므로 스미싱 의심이 아니라 판단 보류로 간다. 기존 `ReasonCode` 의
`lookalike` 와 `not_in_whitelist` 구분이 그대로 쓰인다.

**클로킹과 브랜드 사칭의 우선순위.** 브랜드 사칭 판정은 원본 URL 의 도메인 대조 결과이지
페이지 내용이 아니다. 클로킹으로 미끼 페이지를 봤더라도 "등록 브랜드를 사칭한 비공식
도메인"이라는 사실은 그대로이므로 스미싱 의심이 유지된다. 클로킹 분기는 그다음이다.

역으로, **클로킹 상태의 관측은 이번 링크의 근거가 아니다.** 관측은 미끼 페이지를 본
것이므로 거기서 나온 `found` 검사를 위험 신호로 채택하지 않는다. 채택하면 미끼 사이트의
로그인 폼을 이번 링크의 자격증명 탈취 근거로 삼는 오탐이 된다. 메시지 본문에서 온 근거는
페이지와 무관하므로 유지한다.

공식 도메인 확인은 안전 보증이 아니다. 해당 도메인이 등록된 공식 도메인이라는 사실만 말한다.

### 4.4 검증

`decide()` 는 LLM 없는 순수함수다. 위 표를 파라미터화한 테이블 테스트로 전수 검증한다.

경계 케이스:

- 신호는 있는데 근거 참조가 깨진 경우
- `unknown` 과 `checked_absent` 구분
- `expired` 인데 검사가 전부 `checked_absent` 인 경우
- `cloaked_suspect` 에서 최종 URL 이 공식 도메인인 경우
- 일부 검사 실패가 이미 확보한 근거를 지우지 않는지

### 4.5 스크린샷

격리 서버가 이미 멀티모달로 화면을 봤다. ③a 가 다시 볼 이유가 없다. 비용과 지연만 늘고
결론은 같다. ③a 는 텍스트 관측만 받고 `screenshot_ref` 는 표시용 참조로 전달한다.

---

## 5. 변경 목록

### 5.1 `ai/` 신규

| 파일 | 내용 |
|---|---|
| `src/ai/kb/normalize.py` | 난독화 정규화 |
| `src/ai/kb/search.py` | curated 로딩 + 3-gram Jaccard |
| `src/ai/llm/signals.py` | ③a `extract_signals()` |
| `src/ai/verdict.py` | `decide()` |
| `src/ai/prompts/v1/signals.md` | ③a 프롬프트 |
| `tests/test_normalize.py` | 난독화 샘플 → 정규화 |
| `tests/test_search.py` | 유사도, 실패 3분법 |
| `tests/test_signals.py` | 근거 검증, 폴백 |
| `tests/test_verdict.py` | 4상태 테이블 테스트 |
| `tests/fixtures/observations_*.json` | 스텁 픽스처 4종 |

### 5.2 `ai/` 수정

| 파일 | 변경 |
|---|---|
| `src/ai/types.py` | `InboundModel`, `RiskSignal`, `Observations`, `DomainCheck`, `CaseMatch`, `CaseSearchResult`, `FinalState`, `CheckState`, `PageState` 추가. `CategoryCode` docstring |
| `src/ai/llm/explain.py` | 4상태 템플릿 추가 + ③b LLM 경로. 템플릿 폴백 유지 |
| `src/ai/prompts/v1/compare.md` | ③b 설명 프롬프트로 확정 |
| `src/ai/prompts/v1/parse_classify.md` | `categories` 가 주제 분류임을 명시 |
| `src/ai/llm/__init__.py` | export 추가 |
| `src/ai/pipeline/README.md` | 흐름 갱신 |
| `README.md` | 격리 제외 문장 삭제 |
| `eval/cases/TC-09.md` | 다중 링크 규칙 |
| `eval/cases/TC-11~13.md` (신규) | 격리 관측 해석, 신호 근거 검증, 스텁 출처 전파 |

삭제: `src/ai/prompts/v1/decide_investigation.md` — 게이트는 백엔드 결정적 코드로 확정.

### 5.3 데이터

| 위치 | 내용 |
|---|---|
| `src/ai/kb/case_examples/CE-*.md` | 수집 스미싱을 정규화·dedup 후 `curated` 등재 |
| `eval/datasets/smishing.jsonl` | 평가용 (KB 와 분리) |
| `eval/datasets/benign.jsonl` | 정상 알림, 마스킹 후 |

변환은 일회용 스크립트로 처리하고 결과만 커밋한다. `ai/scripts/` 를 새로 만들지 않는다.

### 5.4 `docs/` 수정

| 문서 | 변경 |
|---|---|
| `CLAUDE.md` | 원칙 2 (LLM 역할), 제약 (콜백 의존) |
| `docs/ai/smishing-message-intake-ai.md` | 격리 서버 포함, 흐름, 하이브리드 판정원칙, 다중 링크 |
| `docs/latency-budget.md` | 전면 교체 |
| `docs/integration-requests.md` (신규) | 백엔드·프론트 요구사항 |
| `docs/adr/0002-isolation-server.md` (신규) | 격리 환경 별도 서버 결정 |
| `docs/adr/0003-kakao-callback.md` (신규) | 콜백 의존 + 폴백 결정 |

`AGENTS.md` 는 `CLAUDE.md` 참조 stub 이라 자동 반영된다. `contracts/` 는 변경 없다.
BE↔AI 는 여전히 함수 호출이고 타입은 `ai/types.py` 가 소유한다.

### 5.5 `docs/integration-requests.md` 내용

**백엔드 — 데이터 제공**

- `whitelist.yaml` 도메인 수집. 현재 5개 택배사 전부 `domains: []` 다. 정상 샘플에서 나온
  후보: `cjlogistics.com`, `dxsmapp.cjlogistics.com`, `smile.hanjin.com`,
  `lotteglogisplus.com`, `coupa.ng`. 사람 검증 필수. 공식 단축 도메인 별도 등재
- 텍스트·링크 분리, 개인정보 마스킹 후 전달
- 도메인 정규화(소문자·IDN) 및 완전 일치 대조 → `DomainCheck`
- urlscan·격리 결과 → `Observations` 변환. 검사마다 `CheckState` 3상태 필수
- 격리 응답의 필수 7개 필드 (3.4)

**백엔드 — 실행**

- 3개 병렬 시작 (urlscan ∥ 격리 ∥ `ai` 호출)
- 게이트: urlscan 결과로 격리 필요 판단. 불필요 시 격리 요청 취소
- 격리 서버 클라이언트 + 스텁. `ISOLATION_ALLOW_STUB`, 운영에서 참이면 기동 실패
- 공식 도메인 즉답 경로
- 카카오 `useCallback` + 결과 저장 + 콜백 실패 시 조회 폴백
- 전체 상한 60초

**프론트**

- "결과 확인" 버튼
- 4상태 표시, 근거와 미확인 항목 구분 표기

### 5.6 순서

```
1. types.py 확장
2. normalize.py + search.py + KB 등재
3. verdict.py + 테이블 테스트
4. signals.py + explain.py
5. eval 데이터셋 + 오탐 측정
6. 문서 동기화 + integration-requests.md
```

1~3 은 LLM·네트워크 없이 테스트된다.

---

## 6. 이번 범위의 한계

`ai/` 만 건드리므로 엔드투엔드 검증이 불가능하다. 검증되는 것은 함수 단위와 픽스처 기반
통합까지다. 실제 카톡 응답, 병렬 실행, 게이트 동작, 콜백은 백엔드 작업이 붙은 뒤에 확인된다.

`decide()` 의 판정 품질도 `DomainCheck` 가 실제 화이트리스트로 채워져야 측정된다. 그 전까지는
픽스처 기준 정확성만 보장한다.

격리 서버가 아직 없으므로 스텁으로 개발한다. 게이트 경로의 실제 동작은 서버가 생긴 뒤에
검증된다.
