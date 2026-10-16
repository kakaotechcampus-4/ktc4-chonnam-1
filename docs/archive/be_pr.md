# BE PR: 스미싱 URL 검사 프로토타입 → 모듈 구조·콜백 전환 계획

`feature/smishing-message-intake-be` 브랜치의 `backend/src/server/main.py`, `url_utils.py`,
`urlscan_service.py`, `requirements.txt`를 대상으로 한 개발 계획. 이 파일은 코드 설명서가
아니라 **이후 어떻게 개발할 것인가**에 대한 계획 문서이며, 위 파일들이 아직 `develop`에는
없는 프로토타입 상태라는 것을 전제로 한다.

## 현재 프로토타입 상태

| 파일 | 하는 일 | 문제 |
| --- | --- | --- |
| `main.py` | 카카오 웹훅 수신 → `url_utils.split_message`로 링크 추출 → `urlscan_service`로 검사 요청/대기 → 결과를 그대로 응답 | 라우팅·오케스트레이션·외부 호출이 한 함수에 섞여 있고, 계층 분리가 없음. 화이트리스트 대조(결정적 판정)가 아직 없어서 `# TODO: AI에게 전달하여 분석` 주석으로 남아 있음 |
| `url_utils.py` | 정규식으로 URL/본문 분리 (`split_message`) | 정규식 실패(난독화된 URL)에 대비한 LLM 추출 승격 경로가 없음 |
| `urlscan_service.py` | urlscan.io에 스캔 요청 후 최대 10회 × 5초(≈50초) 폴링 | **카카오 스킬 5초 타임아웃, `docs/latency-budget.md`의 4초 예산을 크게 초과한다.** 지금처럼 동기로 기다리면 응답을 제때 못 준다 |
| `requirements.txt` | `fastapi`, `uvicorn[standard]`, `httpx` | `httpx` 추가는 유지, 이후 큐/DB 의존성이 붙을 예정 |

## 개발 계획

1. **레이어 분리** — `main.py`의 로직을 그대로 두지 않고 폴더별 책임대로 쪼갠다.
   - 웹훅 파싱: `api/` (로직 없이 파싱 → `orchestration/`에 위임만, `api/README.md`)
   - 파이프라인 흐름(①~⑥) 전체 조율: `orchestration/`
   - urlscan 호출: `tools/`의 `external_scan_retry`로 편입 (`tools/README.md`에 이미 정의된 도구)
   - AI 호출(문자 해석·대조): `clients/`의 `call_parse`/`call_compare`/`call_investigate` (지금은 TODO 주석만 있음, `clients/README.md`)
2. **URL 추출 이원화** — `url_utils.split_message`는 1단계(정규식, 50ms 예산)로 유지하되, 실패·미검출 시 `clients.call_parse`로 넘겨 LLM이 난독화된 문자에서 URL을 추출하도록 승격 경로를 추가한다 (`docs/latency-budget.md` 표에 이미 정의된 흐름).
3. **화이트리스트 대조 연결** — `data/whitelist.yaml`은 아직 도메인이 비어 있다. 판정의 유일한 근거이므로(CLAUDE.md 절대 원칙 1) 택배사별 도메인을 채우고, `orchestration/`의 ④ 단계에서 대조하도록 연결한다.
4. **콜백 방식으로 전환 (우선순위 높음)** — 지금 `main.py`는 urlscan 결과를 동기로 기다렸다가 응답한다(최대 약 50초). 이대로면 5초 타임아웃을 지킬 수 없다. `api/README.md`·`queue/README.md`에 이미 정의된 방향대로 옮긴다.
   - `api/`는 5초 안에 끝나지 않으면 실제 처리를 기다리지 않고 즉시 `useCallback: true`로 먼저 응답한다.
   - urlscan 요청/폴링(`submit_url_scan`, `wait_for_url_scan_result`)은 `queue/`의 백그라운드 워커에서 실행하도록 옮긴다.
   - 워커가 1분 내에 끝나면 카카오 `callbackUrl`로 결과를 전송하고, 넘기면 콜백을 보내지 않고 `jobs`/`chatbot_sessions` 상태만 갱신해 `check_result` 재요청 때 돌려준다.
   - 상태 전이는 `running → observed → done/held` 한 방향만 허용한다 (`queue/README.md` 규칙 그대로, 이미 지나간 상태로 되돌리지 않음).
   - 단, 카카오 콜백은 "제한된 임시 기능"이라 이 경로에 전체 응답을 의존시키지 않는다(CLAUDE.md 제약) — 콜백 실패·초과 시에도 `GET /api/analyses/{job_id}`로 항상 결과를 조회할 수 있어야 한다.
5. **테스트** — `tests/README.md`의 케이스에 맞춰 `test_skill.py`를 채우고, urlscan 응답은 픽스처로 목업한다 (실제 외부 API 호출 금지).

## 진행 상황 (멘토 피드백 대응, 2026-09-17)

멘토 피드백 3번("동시 요청이 겹치면 첫 응답 시간·메모리가 어떻게 달라지는지 확인")에 대한 실험을 진행했다.
결과: [docs/experiments/2026-09-17-concurrent-request-load.md](../docs/experiments/2026-09-17-concurrent-request-load.md)

- 요약: 동시 요청 5~100건에서는 응답 시간·메모리 모두 눈에 띄게 나빠지지 않았다. 즉 지금 병목은
  "동시 요청 처리"가 아니라 "요청 한 건이 이미 최대 ~50초 걸린다"는 것이다.
- 결론: 위 4번 "콜백 방식으로 전환"은 이 실험 결과와 무관하게 예정대로 진행한다. 동시성 문제가
  없다는 게 전환을 미뤄도 된다는 뜻은 아니다.

### 콜백 전환 구현 및 안전장치 (2026-09-17)

`main.py`에 `useCallback: true` + `BackgroundTasks` + `callbackUrl` 전송 방식을 실제로 구현했다.
- 분석 → 콜백 전송 → `RUNNING_USERS` 해제 순서를 지킨다 (콜백 전송 전에 상태를 먼저 비우면
  콜백이 도착하기 전에 같은 사용자가 새 분석을 또 시작할 수 있다).
- 콜백 응답의 HTTP 상태 코드·본문, 그리고 응답 JSON의 `status`(SUCCESS/FAIL/ERROR) 필드까지 로깅한다.
- 분석 전체에 45초 안전 시간제한(`CALLBACK_DEADLINE_SECONDS`)을 걸었다. 콜백 URL의 정확한 유효
  시간은 카카오 공식 문서에서도 표현이 엇갈려서(개요 5분, 에러 표 1분) 확정하지 않았고, 어느 쪽이든
  사용자를 무한정 기다리게 하지 않도록 보수적으로 끊는다.
- `backend/src/server/queue/` → `job_queue/`로 이름을 바꿨다. 이 폴더가 비어있어도 표준 라이브러리
  `queue` 모듈을 가려서, 실제 urlscan 호출(`httpx`→`anyio`가 내부적으로 `queue`를 가져옴) 시점에
  서버가 죽는 걸 실제로 확인했다. 사용 여부와 무관하게 이름이 겹치는 것 자체가 문제였다.

### 용어 정리 필요 — "판단 보류" / "분석 불가" / "처리 실패"

멘토 피드백 2번("판단 보류와 분석 불가를 구분하라")에 대응하는 과정에서, 팀마다 이미 비슷한
개념을 서로 다른 이름으로 쓰고 있는 게 확인됐다. 구현 전에 이름부터 하나로 합의해야 한다.

| 문서 | 용어 | 정의 |
| --- | --- | --- |
| `docs/ai/smishing-message-intake-ai.md` (AI) | 판단 보류 | 분석은 수행했지만 결론에 필요한 근거가 부족하거나 근거가 서로 충돌함 |
| `docs/ai/smishing-message-intake-ai.md` (AI) | 분석 불가 | 접속 실패·타임아웃 등으로 판정에 필요한 분석을 수행하지 못함 |
| `docs/designs/smishing-message-intake-ux.md` (FE) | 판단 보류 | 확인된 정보가 없거나 URL이 없어 판정 자체를 하지 않음 |
| `docs/designs/smishing-message-intake-ux.md` (FE) | 처리 실패 | "처리 중 문제"로 확인을 마치지 못함 (원인 구분 없음) |

AI팀의 "분석 불가"와 FE팀의 "처리 실패"가 같은 상황(접속 실패·타임아웃)을 가리키는 것으로 보이지만
이름이 다르다. FE 문서 6절 "검토가 필요한 결정" 3번("부분 결과, 판단 보류, 처리 실패를 백엔드가
어떤 상태로 구분해 줄지")도 아직 미결정으로 남아 있다.

**BE 제안**: 화이트리스트 도메인은 일치했지만 urlscan 관측이 실패한 경우처럼, "일부는 확인됐지만
나머지 관측이 실패한" 경계 사례를 팀(AI+FE)과 함께 놓고 이름과 기준을 확정한다. 이 합의가
`models/`의 상태 enum과 `queue/`의 상태 전이, `templates/`의 카드 문구에 그대로 반영된다.

### 콜백 여부, 아직 팀 결정 사항으로 남아있음

FE 문서 6절 1번: "4초 안에 결과가 없을 때 판단 보류로 끝낼지, 나중에 결과를 다시 전달할지" —
이 문서도 아직 미정으로 표시해뒀다. 위 4번 콜백 계획은 BE 쪽 제안이며, FE가 상세 화면에서
"분석 중 → 완료" 상태를 어떻게 보여줄지와 함께 확정해야 한다.

## 참고

- 전체 구조: `backend/README.md`
- 지연 예산: `docs/latency-budget.md`
- 절대 원칙·의존 방향: 프로젝트 루트 `CLAUDE.md`
- FE 설계: `docs/designs/smishing-message-intake-ux.md`
- AI 설계: `docs/ai/smishing-message-intake-ai.md`
