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

## 주요 디렉터리

| 경로 | 역할 |
| --- | --- |
| `src/server/api/` | API 및 웹훅 관련 모듈 |
| `src/server/templates/` | 카카오톡 템플릿 렌더링 |
| `src/server/orchestration/` | 분석 및 검증 작업 흐름 |
| `src/server/tools/` | 외부 검증 도구 연동 |
| `src/server/clients/` | AI 분석 기능 연동 |
| `src/server/models/` | 데이터 모델 관련 모듈 |
| `src/server/job_queue/` | 비동기 작업 관리 |
| `tests/` | 백엔드 테스트 |

## 실행 및 테스트

백엔드는 저장소 루트의 `main.py`와 연결되어 실행됩니다.

의존성 설치, 환경변수 설정, 서버 실행 및 테스트 명령어는 [루트 README](../README.md)를 참고하세요.

## 관련 문서

- [문서 인덱스](../docs/README.md)
- [제품 요구사항](../docs/PRD.md)
- [보안 점검](../docs/security-checklist.md)
- [AI·BE 연동](../docs/ai-be-python-integration.md)
- [격리 환경 설계](../docs/isolation-security.md)
