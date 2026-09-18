# backend

FastAPI 스킬 서버. 카카오톡 채널 웹훅을 받아 분석 파이프라인을 오케스트레이션하고, AI 파트를 호출하고, 결과를 조립해 돌려주는 BE의 전체 구현체입니다.

## 이 폴더가 하지 않는 것

- **판단하지 않는다** — 문자 해석, 유형 분류, 대조, 보강판단은 전부 `clients/`를 통해 AI 파트에 위임합니다. 이 폴더 안에서 LLM을 직접 호출하는 코드는 없어야 합니다.
- **템플릿 문구를 직접 짓지 않는다** — 카카오 응답 문구·버튼 구성은 `/kakao-templates`(FE 소유, 선언형)의 몫이고, 이 폴더의 `templates/`는 그 선언을 렌더링하는 엔진만 가집니다.

## 하위 구조

| 폴더 | 역할 |
| --- | --- |
| `src/server/api/` | 카카오 웹훅·분석 엔드포인트 (라우터) |
| `src/server/templates/` | `/kakao-templates` 렌더러 |
| `src/server/orchestration/` | ①~⑥ 파이프라인 흐름, 조회 한도, 이중 검증 |
| `src/server/tools/` | 외부 도구(격리 관측·도메인 조회 등) 실행 |
| `src/server/clients/` | AI 파트 호출 |
| `src/server/models/` | ORM 모델 |
| `src/server/job_queue/` | 비동기 작업 큐 (표준 라이브러리 `queue`와 이름 충돌 방지를 위해 `job_queue`로 명명) |
| `tests/` | 테스트 케이스 10건 + 인젝션 픽스처 |

## 시작하기 전에

- `/contracts/schemas`에 정의된 BE↔AI 스키마를 먼저 확인하세요. 이 저장소의 `clients/`, `orchestration/`의 타입은 전부 거기서 파생됩니다.
- 동의/고지 관련 로직은 어디에도 존재하지 않습니다 (PM 최종 결정). 관련 코드를 발견하면 삭제 대상입니다.
