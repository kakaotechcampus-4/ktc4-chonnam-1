# 자체 URL 수집·분석 전환 구현 계획

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans, task by task.

**Goal:** urlscan 없이 공개 HTTPS 주소를 자체 격리 수집기로 조사하고 기존 문자·페이지 분석, 콜백, 상세·재조회를 제공한다.
**Architecture:** Render → 인증된 EC2 `/collect` → 제한된 접속 프록시 → 공개 HTTPS. 수집 결과를 기존 AI 입력으로 변환한다.
**Tech Stack:** Python, httpx, asyncio, Playwright, Docker Compose, pytest.
**Spec:** 이 대화에서 승인된 파일별 변경안과 `CLAUDE.md`. 사용자는 코드 수정과 테스트를 요청했고 서버 배포는 이번 단계의 범위가 아니다.

## Global Constraints

- `refactor/internal-url-scanner`에서 작업한다. 기존 카드·카카오 연결은 변경하지 않는다.
- 운영 Render·EC2·AWS 설정을 이번 코드 작업에서 변경하지 않는다. 배포 명령과 되돌리기 방법은 문서로 제공한다.
- 공개 HTTPS/443부터 지원한다. HTTP/80·로그인·CAPTCHA 우회·다운로드 실행은 범위 밖이다.
- 기존 고정 허용 목록 모드는 기본값으로 유지하고 `COLLECTOR_NETWORK_MODE=public`을 명시해야 일반 주소를 방문한다.
- 공개 모드의 DNS 확인과 실제 접속은 같은 검증된 IP를 사용한다. DNS 실패, 내부/비공개/멀티캐스트 주소 혼합, 보호 호스트·IP는 차단한다. IPv6는 이번 컨테이너에서 비활성화한다.
- 공개 모드의 브라우저 및 httpx 수집은 같은 CONNECT 프록시를 통과한다. 네트워크 방화벽에서도 내부 주소와 서비스 주소를 차단한다.
- TLS 검증을 유지한다. 공개 모드에서 인증서 검증 실패 후 직접 접속 재시도를 허용하지 않는다.
- 수집 실패·부분 수집은 안전으로 간주하지 않는다. 실제 자료 없는 최종 URL을 만들어내지 않는다. LLM이 판정을 결정하지 않는다.
- 기존 의존성 재사용, 실제 비밀값·사용자 데이터 기록 금지. 수집/LLM 네트워크 원문 로그를 늘리지 않는다.
- 작업별 RED/GREEN 결과와 변경 파일을 보고한다. 전체 pytest는 통합 후 실행한다.

## Review Focus

DNS 리바인딩, 리다이렉트 내부망 우회, 부분 HTML에서 위험 근거 보존, 수집 실패 시 문자 근거 보존, 취소 시 태스크/소켓 정리.

### Task 1: 공개 HTTPS 접속 정책과 격리 배포 구성

**Files:** `infra/isolation/collector/{policy,app,egress_proxy,browser}.py`, 필요한 작은 접속 정책 모듈, `infra/isolation/{compose.yaml,lockdown.sh,collector.env.example}` 및 배포 보조 파일, 집중 테스트.
**Consumes:** 기존 `CollectResult`, `scanner.fetch.collect_url`, 수집 API 인증·크기·시간 상한.
**Produces:** 고정 허용 목록과 공개 HTTPS 모드를 지원하는 `/collect`. 기존 `create_app(token, allowed_hosts, ...)` 테스트 호환. 공개 모드는 보호 호스트/IP 설정이 없으면 기동 거부. 새로운 네트워크 정책은 프록시 연결 시 IP 검증/고정.

- [ ] RED: 미등록 공개 호스트 수집, DNS 실패, 공인+사설 혼합 응답, 숫자형/IPv4-mapped 내부 주소, 보호 서비스 도메인·IP, 리다이렉트 차단, 실제 연결 대상이 DNS 검증 IP임을 검사한다.
- [ ] GREEN: 프록시의 연결 대상을 검증된 공인 IPv4로 고정하고 HTTPS httpx도 동일 프록시로 보낸다. 브라우저와 프록시의 새 정책 연결. 기존 허용 목록 모드 보존.
- [ ] 배포 코드: 공개 모드에서 지정한 DNS만 허용하고 공개 443 목적지에 대한 내부망/서비스 IP 차단을 먼저 적용한다. 기본 허용 목록 모드의 기존 구성 보존. 빈 보호 설정 또는 잘못된 IP/모드로 방화벽을 넓히지 않는다.
- [ ] 집중 테스트 실행, 실패/성공 근거와 남은 제약을 보고하고 해당 파일만 커밋한다.

### Task 2: AI 입력 계약과 부분 수집 처리

**Files:** `ai/src/ai/{types.py,pipeline/analysis.py}`, `scanner/isolation_page.py`, 계약·실패 관련 AI/BE 테스트.
**Produces:** `UrlAnalysis.final_url: str | None` (빈 문자열 금지, None은 미확인). `finalize_analysis(url, message, page, failure=...)` 공개 인터페이스 유지.

- [ ] RED: final_url=None 계약, HTML 일부 수집 시 실제 위험 요소 분석과 partial 상태 동시 보존, HTML 없는 실패의 failed 상태, LLM 실패 폴백을 테스트한다.
- [ ] GREEN: 부분 수집 사유만 있는 경우 확보한 HTML 분석을 수행하고 partial 사유를 최종 결과에 보존한다. 실패를 completed/no_risk_found로 승격하지 않는다.
- [ ] 기존 분석·판정 로직 재사용. 수집 시각과 실패를 매핑하고 가짜 brand/category를 결과 근거로 만들지 않는다. 필요한 범위만 변경한다.
- [ ] 집중 계약·AI 테스트 실행, 보고, 해당 파일만 커밋한다.

### Task 3: BE 자체 수집 전환과 urlscan 제거

**Files:** `main.py`, `backend/src/server/{lifespan.py,urlscan_service.py,shadow_tasks.py}`, 공용 HTTP 모듈, `services/link_resolution.py`, `scanner/models.py`, `.env.example`, 관련 BE 테스트.
**Consumes:** 기존 `collect_url_isolated(link) -> CollectResult`, Task 2의 nullable 최종 주소와 실패 계약.

- [ ] RED: urlscan 키 없이 실제 내부 파이프라인·HTTP mock transport로 수집→문자/페이지 분석→콜백→상세→재조회, 수집 실패 시 문자 근거 유지, 단축 주소의 수집 1회, 콜백 실패 폴백·태스크 취소를 검증한다.
- [ ] GREEN: 문자 분석과 수집 병렬 실행. 수집 1회 결과의 redirect_chain/browser.navigation_chain을 단축 주소 해석과 최종 주소 대조에 공유한다. 차단된 이동 시도 주소를 실제로 방문한 최종 주소처럼 쓰지 않는다.
- [ ] `build_ai_url_analysis` 입력을 CollectResult 기반으로 변경. 공식 대조는 입력/첫 실제 도메인 기준이며 확인한 이후 이동으로 하향할 수 있다. 수집 실패로 확인하지 못한 이동을 공식으로 확정하지 않는다.
- [ ] `to_isolated_page`와 `failure`를 최종 분석에 전달. 콜백 마감 및 저장 결과 재사용 유지.
- [ ] 콜백용 공용 HTTP 클라이언트를 분리한 뒤 urlscan 호출·파서·키·비교 전용 데이터/Shadow 코드와 전용 테스트를 정리한다. 관련 테스트는 새 동작으로 교체하며 테스트를 단순 삭제해 숨기지 않는다.
- [ ] 집중 BE 테스트 실행, 보고, 해당 파일만 커밋한다.

### Task 4: 통합 검증과 배포·복구 기록

**Files:** `docs/experiments/2026-10-17-internal-url-scanner.md`, `docs/latency-budget.md`, 관련 README·환경 예시와 최종 회귀 테스트.

- [ ] 전체 pytest를 실행하고 실패를 해결한다. 공개 주소 접속은 테스트 소유 URL/무해한 공개 사이트만 사용한다.
- [ ] 가능하면 실제 로컬 프록시·실제 HTTP 수집으로 공개 HTTPS와 내부 주소 차단을 확인한다. Docker/Playwright 실행 가능 여부도 확인하고 미실행 항목을 구분한다.
- [ ] 운영 서버를 바꾸지 않고 필요한 EC2/Render 배포 단계와 커밋 기준 복구 방법을 기록한다.
- [ ] 독립 리뷰 후 중요한 결함을 수정하고 관련 테스트를 재실행한다. 테스트 수·실행 환경·제약을 사용자에게 보고한다.
