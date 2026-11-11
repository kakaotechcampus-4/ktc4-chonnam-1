# Backend

화기니의 FastAPI 기반 백엔드 모듈입니다.

카카오톡 채널에서 전달된 요청을 처리하고, AI 분석 및 외부 검증 기능을 연결하며, 분석 결과를 카카오톡 응답 형식으로 구성하는 역할을 담당합니다.

## 주요 역할

- **요청 처리:** 카카오톡 스킬 서버 요청 및 분석 관련 API 처리
- **분석 흐름 연결:** AI 분석 패키지와 외부 검증 기능 연계
- **비동기 작업 관리:** 분석 작업의 생성·상태 관리 및 결과 전달
- **결과 응답:** 분석 결과를 카카오톡 카드 형식으로 구성
- **보안 검증:** 요청 인증, 콜백 URL 검증, 외부 리소스 접근 제한

## 설계 원칙

### AI와 백엔드의 역할 분리

문자 해석과 위험 신호 추출 등 AI 분석은 AI 패키지에 위임합니다.

백엔드는 분석 기능을 연결하고, 검증 결과를 전달하며, 서비스의 API 및 실행 흐름을 관리합니다.

### 프론트엔드와의 역할 분리

카카오톡 카드의 문구와 템플릿은 `frontend/kakao-templates/`에서 관리합니다.

백엔드는 해당 템플릿과 분석 결과를 결합하여 카카오톡 응답을 생성합니다.

## 주요 파일

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

## 실행 및 테스트

백엔드는 저장소 루트의 `main.py`와 연결되어 실행됩니다.

의존성 설치, 환경변수 설정, 서버 실행 및 테스트 명령어는 [루트 README](../README.md)를 참고하세요.

## 관련 문서

- [문서 인덱스](../docs/README.md)
- [제품 요구사항](../docs/PRD.md)
- [보안 점검](../docs/security-checklist.md)
- [AI·BE 연동](../docs/ai-be-python-integration.md)
- [격리 환경 설계](../docs/isolation-security.md)

## 하지 않는 것

- 동의·고지 관련 로직은 두지 않는다(PM 최종 결정). 관련 코드를 발견하면 삭제 대상이다.
