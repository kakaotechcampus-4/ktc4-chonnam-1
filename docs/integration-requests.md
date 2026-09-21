# 다른 파트 요구사항

AI 파트가 동작하려면 백엔드·프론트에 아래가 필요하다. AI 파트는 `backend/`,
`frontend/` 를 수정하지 않는다. 설계 근거는
`docs/superpowers/specs/2026-09-18-smishing-analysis-redesign-design.md`.

## 백엔드 — 데이터 제공

### 화이트리스트 도메인 수집 (블로커)

`backend/src/server/data/whitelist.yaml` 의 5개 택배사가 전부 `domains: []` 다.
비어 있으면 모든 링크가 "목록에 없음"으로 나와 판정이 동작하지 않는다.

정상 문자 샘플에서 나온 후보:

- `cjlogistics.com`, `dxsmapp.cjlogistics.com`
- `smile.hanjin.com`
- `lotteglogisplus.com`
- `coupa.ng` (쿠팡 공식 단축 도메인)

**사람이 검증한 것만 등재한다.** 샘플에 있다는 이유로 자동 등재하지 않는다.
알림 발송용 별도 도메인과 공식 단축 도메인을 함께 모은다. "단축 URL = 의심"으로
처리하면 쿠팡 정상 문자가 즉시 오탐이 된다.

### 입력 전처리

- 텍스트와 링크를 분리한다. AI에는 링크를 제외한 본문만 넘긴다.
- 개인정보를 마스킹한 뒤 넘긴다.

### 도메인 대조 → `DomainCheck`

- 소문자·IDN 정규화를 한 곳에서 수행한다.
- 등록 가능 도메인을 검증 목록과 **완전 일치**로 비교한다. 부분 문자열이나
  단순 접미사로 판정하지 않는다.
- `cloaked_suspect` 인 경우 최종 URL이 아니라 **원본 URL**로 대조한다.
- 타입은 `ai/src/ai/types.py` 의 `DomainCheck` 를 그대로 쓴다.

### 관측 변환 → `Observations`

urlscan 과 격리 서버 결과를 하나의 `Observations` 로 합친다. 타입은
`ai/src/ai/types.py` 소유다.

검사마다 3상태가 **필수**다.

| 상태 | 의미 |
|---|---|
| `found` | 검사에서 확인했다 |
| `checked_absent` | 명시된 검사 범위에서 찾지 못했다 |
| `unknown` | 미지원·실패·미실행으로 알 수 없다 |

빈 목록으로 "없음"을 추론하면 안 된다. 패커로 DEX가 암호화되면 권한을 못 읽는데,
빈 배열을 "권한 없는 앱"으로 읽으면 위험한 APK가 안전으로 통과한다.

`page_state` 는 검사 상태로 표현할 수 없는 두 상황을 위해 필요하다.

- `expired` — 접속은 됐지만 1회성 링크가 소진됐다. 검사는 전부 `checked_absent`
  로 나오지만 안전이 아니라 그 자체가 신호다.
- `cloaked_suspect` — 데이터센터 IP 감지로 정상 사이트로 리다이렉트된 것으로
  보인다. 검사가 깨끗하게 나오지만 원본 링크는 다르다.

### 격리 서버 응답의 필수 필드

| 필드 | 없으면 |
|---|---|
| `status`, `failure_reason` | 실패와 안전을 구분 못 함 |
| `target.input_url` | cloaking 시 원본 도메인을 잃음 |
| `target.page_state` | 만료·cloaking 판별 불가 |
| `evidence.static.checks[].state` | "없음"과 "못 봄"을 구분 못 함 |
| `evidence.static.risk_signals` | 결정적 신호 소실 |
| `unchecked` | 한계 표시 불가 |
| `source` | 스텁이 실제처럼 나감 |

나머지(`elapsed_ms`, `analysis_id`, `sha256`, `size_bytes`, `model_id`,
`raw_response_ref`, `display.*`, `verdict.*`, `evidence.model.*`)는 AI가 판정에
쓰지 않는다. 팀 합의로 정리 가능하다.

`Observations` 는 `extra="ignore"` 이므로 키를 추가해도 AI는 수정하지 않는다.

### 격리 서버의 LLM 판정 필드

격리 서버는 자체 LLM으로 `verdict.label`, `confidence`, `display.*` 를 낸다.
**AI는 이 값들을 판정에 쓰지 않는다.** urlscan 점수와 같은 취급이다. 표시·로그에는
남겨도 되지만, 사용자에게 나가는 최종 문구는 AI의 설명 생성이 만든다. 격리 서버의
`display.headline` 을 그대로 쓰면 격리 결과만 보고 쓴 문장과 최종 판정이 어긋난다.

`evidence.static.*` 은 문자열·Magic Byte 매칭 결과이므로 관측 사실로 채택한다.

## 백엔드 — 실행

- urlscan, 격리 서버, AI 호출 3개를 **동시에 시작**한다.
- urlscan 결과로 격리 필요 여부를 판단한다. 불필요하면 격리 요청을 **취소**한다.
  취소하지 않으면 투기적 실행이 격리 서버를 계속 점유한다.
- 격리 서버 클라이언트와 스텁을 구현한다. 스텁 응답은
  `ai/tests/fixtures/observations_*.json` 을 그대로 쓴다. 같은 데이터를 공유하므로
  스텁과 테스트가 어긋나지 않는다.
- `ISOLATION_ALLOW_STUB` 이 참일 때만 스텁을 쓴다. **운영 환경에서 참이면 기동을
  실패시킨다.** AI는 `source` 전파만 보장하고 차단은 하지 못한다.
- 화이트리스트가 일치하면 스캔을 기다리지 않고 첫 응답에서 종료한다.
- 카카오 `useCallback` + 결과 저장 + 콜백 실패 시 조회 폴백.
- 전체 상한 60초. 넘기면 확보한 근거로 판정하고 종료한다.

## 프론트

- "결과 확인" 버튼 (콜백 폴백 경로).
- 4상태 표시: 스미싱 의심 / 공식 도메인 확인 / 판단 보류 / 분석 불가.
- 근거와 미확인 항목을 구분해 표기한다. `unchecked` 를 "없음"으로 표시하지 않는다.
- 공식 도메인 확인을 "안전"으로 표현하지 않는다.
- 사례 유사도를 표시한다면 "스미싱 확률"이 아니라 "유사한 과거 사례"로 쓴다.
