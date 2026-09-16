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

## 참고

- 전체 구조: `backend/README.md`
- 지연 예산: `docs/latency-budget.md`
- 절대 원칙·의존 방향: 프로젝트 루트 `CLAUDE.md`
