# backend

실제 서버는 루트 `main.py` 다. 이 폴더에는 `main.py` 가 import 하는 보조 모듈만 있다.

| 파일 | 하는 일 |
| --- | --- |
| `src/server/lifespan.py` | 서버 시작·종료 때 할 일(검색 준비, HTTP 클라이언트 정리) |
| `src/server/http_client.py` | 콜백 전송에 쓰는 공용 HTTP 클라이언트 |
| `src/server/callback_guard.py` | `callbackUrl` 허용 검사(`KAKAO_CALLBACK_HOSTS`) |
| `src/server/media_guard.py` | 음성 메시지 CDN 주소 검사 |
| `src/server/voice_input.py` | STT 전에 음성 파일 크기·형식 검사 |

카드 문구는 `frontend/kakao-templates/cards/`, 카드 렌더러는 `services/kakao_card_renderer.py`,
공식 도메인 목록은 `services/official_domains.py` 에 있다.

## 하지 않는 것

- 동의·고지 관련 로직은 두지 않는다(PM 최종 결정). 관련 코드를 발견하면 삭제 대상이다.
