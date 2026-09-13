# backend/src/server/api

카카오 웹훅과 내부 REST 엔드포인트. **로직을 갖지 않습니다** — 요청을 파싱해 `orchestration/`에 넘기고, 그 결과를 응답 형식으로 감싸기만 합니다.

## 엔드포인트

| 엔드포인트 | 역할 | 관련 요구사항 |
| --- | --- | --- |
| 카카오 스킬 웹훅 | `userRequest.utterance`/`user.id`/`callbackUrl`, `action.clientExtra.intent` 파싱 | B01 |
| `POST /api/analyses/parse` | 문자 해석 요청 (외부 전송 없음) | — |
| `POST /api/analyses` | 분석 시작 (동의 필드 없음 — PM 결정) | B05 |
| `GET /api/analyses/{job_id}` | 상태·근거 조회 | B06, B11 |
| `POST /api/analyses/{job_id}/retry` | 재조회 (한도 초과 시 `429`) | B09, B10 |
| `GET /api/official-routes` | 공식 확인 경로 조회 | B14 |
| `DELETE /api/analyses/{job_id}` | 삭제 요청 | B16 |
| `GET /api/web-entry/{token}` | 2차 웹 진입 토큰 검증 | B20 |

## 지켜야 할 규칙

- `intent` 값(`check_result`/`retry`/`show_routes`/`delete_request`)은 **`action.clientExtra`에서만 읽습니다.** 사용자가 위조 가능한 `utterance` 텍스트로 분기하지 않습니다.
- 5초 내 처리를 못 끝내면 `useCallback: true` 응답을 먼저 반환하고, 실제 처리는 `queue/`로 넘깁니다 (B01b).
- 이 폴더에서 직접 DB에 쓰지 않습니다 — `orchestration/`이나 `models/`를 거칩니다.
