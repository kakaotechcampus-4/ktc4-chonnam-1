# backend

실제 서버는 루트 `main.py` 다. 이 폴더에는 `main.py` 가 import 하는 보조 모듈만 있다.

| 파일 | 하는 일 |
| --- | --- |
| `src/server/lifespan.py` | 서버 시작·종료 때 할 일(검색 준비, HTTP 클라이언트 정리) |
| `src/server/http_client.py` | 콜백 전송에 쓰는 공용 HTTP 클라이언트 |
| `src/server/callback_guard.py` | `callbackUrl` 허용 검사(`KAKAO_CALLBACK_HOSTS`) |
| `src/server/media_guard.py` | 음성 메시지 CDN 주소 검사 |
| `src/server/voice_input.py` | STT 전에 음성 파일 크기·형식 검사 |
| `src/server/voice_flow.py` | 통화 녹음 분석 흐름(다운로드 → 형식 검사 → STT → 통화 분석 → 카드) |
| `src/server/jobs.py` | 분석 작업(Job) 저장소와 Job 에 들어가는 값 |
| `src/server/callback.py` | 결과를 카카오 `callbackUrl` 로 보내고 성공 여부를 기록 |
| `src/server/budgets.py` | 분석 전체 상한 등 시간 예산(`docs/latency-budget.md`) |
| `src/server/log.py` | 진단 로그 도우미. `print` 대신 쓴다 |

카드 문구는 `frontend/kakao-templates/cards/`, 카드 렌더러는 `services/kakao_card_renderer.py`,
공식 도메인 목록은 `services/official_domains.py`, 수집 결과와 목록을 대조하는 판정은
`services/official_url_analysis.py` 에 있다.

## 하지 않는 것

- 동의·고지 관련 로직은 두지 않는다(PM 최종 결정). 관련 코드를 발견하면 삭제 대상이다.
