# 자체 URL 수집 전환 검증 기록

작업 브랜치: `refactor/internal-url-scanner`.
변경 전 기준: `test/render`의 `d7164888e6d6f9f9fa1b1b95cd90f5b11f66bb93`.
운영 Render·EC2·카카오 설정은 이 작업에서 변경하지 않는다.

## 범위

urlscan 호출을 자체 격리 수집 결과로 교체하고, 확보한 HTML을 기존 AI 페이지 분석에 연결한다.
카드 템플릿은 유지한다. 일반 주소 수집은 공개 HTTPS/443부터 지원하며,
기존 허용 목록 모드는 기본값으로 남긴다.

## 변경 전 확인

Windows / Python 가상환경에서 `.venv/Scripts/python.exe -X utf8 -m pytest -q` 실행:
**1407 passed, 2 skipped, 1 warning (25.96s)**.
경고는 기존 FastAPI/Starlette 테스트 클라이언트의 httpx 사용 관련 경고다.

## 실행 환경의 제약

로컬 Docker 엔진은 28.5.2이며 기존 `ktc-isolation:collector-candidate` 이미지에
Playwright 1.63.0과 Chromium이 설치되어 있다.
그러나 샌드박스를 활성화한 Chromium 실행은 `No usable sandbox`로 실패했다.
저장소의 seccomp 설정을 적용하면 컨테이너 프로세스 실행 전 Docker 런타임에서
procfs/openat2 오류가 발생했다. 브라우저 샌드박스를 해제하지 않았다.
따라서 이 환경에서 실제 브라우저 단계의 성공을 주장할 수 없다.

브라우저의 `navigation_chain`은 문서가 커밋된 주소만 기록하므로 HTTP 302 중간
주소가 빠질 수 있다. 전체 요청 경로를 입증할 때까지 브라우저 단계가 실행된
수집 결과는 공식으로 확정하지 않는다. 정상적인 JS 페이지도 `unresolved`가 될 수 있다.
첫 실제 도메인은 HTTP 리다이렉트 자료로만 찾고, 알 수 없으면 입력 도메인을 표시한다.
확인된 브라우저 최종 URL과 확보한 HTML의 위험 근거는 그대로 분석에 전달한다.

로컬 호스트에서 프록시 없이 공개 HTTPS에 접근하는 사전 점검은
`example.com` HTTP 200, `kiki03st.github.io` 루트 HTTP 404로 응답을 확인했다.
이는 새 수집 경로의 검증과 구분한다.

## 최종 검증

### 실제 공개 HTTPS 수집

Task 1 커밋 `c55380c`에서 로컬 Python으로 수집 API를 ASGI transport에 연결하고,
실제 DNS → 실제 CONNECT 프록시 → TLS 검증을 켠 HTTPX 수집을 실행했다.
브라우저는 `off`, 수집 상한은 8초였다. DNS나 HTTP 응답을 모킹하지 않았다.
입력 URL과 응답의 상태·실패·HTML 길이만 출력했고 본문은 기록하지 않았다.

| 입력 | 결과 | 시간 |
| --- | --- | --- |
| `https://example.com/` | 원본 HTTP 200, HTML 577자, 실패 없음 | 330ms |
| 팀 GitHub Pages `v1/cases/col-01/default.html` | 원본 HTTP 200, HTML 309자, 실패 없음 | 311ms |
| `https://127.0.0.1/` | `blocked_address`, HTML 없음, 최종 주소 미확인 | 0ms |
| `https://169.254.169.254/` | `blocked_address`, HTML 없음, 최종 주소 미확인 | 0ms |
| 테스트 보호 호스트 | `blocked_address`, HTML 없음, 최종 주소 미확인 | 0ms |

이는 새 수집 애플리케이션 경로의 검증이며 EC2 방화벽·실제 브라우저·운영 카카오 테스트는 아니다.

같은 수집기 코드를 읽기 전용으로 마운트한 기존 Linux Docker 이미지에서도
Python 3.12.15 / 브라우저 `off`로 실행했다. `example.com`은 원본 HTTP 200,
HTML 577자, 실패 없음(237ms), localhost는 `blocked_address`(1ms)였다.
새 이미지를 빌드하거나 호스트 방화벽을 적용한 시험은 아니며,
Windows Python 3.13.15 외에 Linux/Python 3.12의 HTTP 수집 경로를 확인한 결과다.

### 실제 수집을 연결한 BE 흐름

BE 커밋 `76dfc3c`에서 `/kakao/skill` → 실제 공개 HTTPS 수집 → 실제 응답 파서·AI
내부 파이프라인 → 콜백 처리 → 상세·재조회를 로컬 ASGI로 실행했다.
외부 LLM과 카카오 콜백 응답만 합성 HTTP transport로 대체했다.
단순히 `finalize_analysis` 결과를 고정한 테스트가 아니다.

| 공개 입력 | 수집 횟수 | LLM 모의 요청 | 콜백 모의 요청 | 상세·재조회 | 전체 시간 |
| --- | --- | --- | --- | --- | --- |
| `https://example.com/` | 1 | 3 | 1 | 통과 | 1644ms |
| 팀 GitHub Pages `col-01` | 1 | 3 | 1 | 통과 | 412ms |

문자 설치 요구 신호와 실제 방문 URL이 저장됐고, 상세·재조회가 같은 저장 결과를
사용해 추가 수집·LLM 호출을 하지 않음을 확인했다. 이 시간은 LLM 응답이 모의이므로
실제 운영 분석 속도로 해석하면 안 된다. 실제 LLM의 정확도·운영 카카오 전달·휴대폰
표시는 이 작업에서 검증하지 않았다.

### 회귀 테스트

Windows Python 3.13.15에서 Git의 OpenSSL 경로를 추가하고 실행했다.

```powershell
$env:PATH = 'C:\Program Files\Git\usr\bin;' + $env:PATH
.venv/Scripts/python.exe -X utf8 -m pytest -q
```

결과: **1480 passed, 2 skipped, 1 warning in 26.74s**.
건너뛴 항목은 호스트 Playwright 부재로 인한 브라우저 모듈과,
이미 검토된 안내 자료가 있어 적용 조건이 사라진 기존 안내 자료 테스트다.
경고는 변경 전과 같은 Starlette 테스트 클라이언트 경고다.

추가된 회귀 범위는 DNS 실패·혼합 응답·숫자 주소·실제 연결 IP 고정·내부망 리다이렉트,
보호 IP와 Render 요청 IP가 겹친 방화벽 응답 규칙, nullable 최종 URL,
부분 HTML의 근거 보존, 수집 실패 시 문자 근거 유지, 단축 주소 수집 1회,
단계별 이동 경로 분리, 태스크 취소와 콜백 실패 후 재조회다.
제거된 Shadow·urlscan 전용 테스트는 자체 수집과 현재 작업 소유권 테스트로 교체했다.

최종 리뷰 수정에서는 실제 응답 파서에 JS → 외부 주소 → HTTP 302 → 공식 주소의
생산자 형태를 입력했다. 커밋 경로에 외부 주소가 없는 단축 주소·공식 주소 사례 모두
공식 확정을 거부하고, 확인된 최종 URL과 브라우저 HTML을 보존함을 검증했다.
이 회귀는 합성 수집 응답을 사용했으며 실제 Chromium 실행 결과가 아니다.

`frontend/`, `services/result_card_renderer.py`, `services/result_card_selector.py`는
기준 커밋과 diff가 없다. 카드·선택 규칙은 변경하지 않았다.

## 배포 및 복구

다음은 코드 검토 후 수행할 순서이며, 이 작업에서 실행하지 않았다.

1. 기존 컨테이너 이미지 ID, `collector.env`, `.env`, `compose.egress.yaml`,
   `egress-hosts`, `egress-ips.txt`, 방화벽 규칙을 서버의 별도 백업 위치에 보관한다.
   비밀값이 있는 백업을 Git에 넣지 않는다. 기존 SG 규칙도 기록한다.
2. 저장소에서 `bash infra/isolation/prepare.sh`로 동일 버전의 `scanner` 코드를 준비한다.
   승인된 빌드 환경에서 새 이미지를 만들고 이미지 ID를 기록한 뒤 EC2에 전달한다.
   서버의 기존 아웃바운드 제한을 빌드 때문에 임의로 해제하지 않는다.
3. 새 `collector/`, `network_config.py`, `render-egress.py`, `lockdown.sh`,
   `compose.yaml`, `verify.py`와 수집기 이미지를 함께 교체한다.
   `collector.env`에 공개 모드와 아래 필수 값을 설정한다.
   설정을 읽은 `python3 render-egress.py /opt/ktc-isolation`과
   `python3 network_config.py /opt/ktc-isolation`으로 생성·검증한다.
4. 호스트 방화벽에서 지정 DNS의 UDP/TCP 53만 허용하고, 내부·예약·보호 목적지 차단을
   공개 TCP/443 허용보다 먼저 적용한다. 허용된 Render의 API 응답 예외도 유지한다.
   검증된 호스트 규칙을 먼저 적용한 뒤 SG 아웃바운드를 공개 TCP/443 및 지정 DNS로
   조정한다. 인바운드 8443의 Render CIDR 제한은 유지한다.
5. 새 이미지 ID와 설정으로 Compose 컨테이너를 재생성한다. 컨테이너 안에서
   `python /app/verify.py`로 샌드박스·네트워크 제한을 확인한다. 허용된 요청자의
   수집 성공, DNS 응답, 내부·보호 목적지 차단, 브라우저 JS 이동을 실제 환경에서 확인한다.
6. 그 뒤 Render에 이 브랜치의 BE를 배포한다. `ISOLATION_API_URL`,
   `ISOLATION_API_TOKEN`, 필요 시 `ISOLATION_API_CA_CERT` 및 기존 LLM 설정이 필요하다.
   기존 카카오 스킬 URL·카드·시크릿을 바꾸는 작업은 없다. 일반 HTTPS 주소와
   팀 테스트 문자를 카카오에서 보내 콜백·상세·결과 확인을 다시 검증한다.

| 수집기 설정 | 값의 의미 |
| --- | --- |
| `COLLECTOR_NETWORK_MODE=public` | 일반 공개 HTTPS 수집을 명시적으로 활성화 |
| `COLLECTOR_PROTECTED_HOSTS` | Render·격리 API 등 보호할 호스트, 쉼표 구분; 하위 도메인도 차단 |
| `COLLECTOR_PROTECTED_IPS` | 같은 서비스의 실제 IPv4 전체, 쉼표 구분; 공유 CDN IP는 함께 차단됨 |
| `COLLECTOR_DNS_SERVER` | 명시적으로 승인한 IPv4 DNS 서버 하나 |
| `RENDER_CIDRS` | 실제 Render outbound CIDR, 공백 구분 |

보호 IP는 서비스 DNS 변경에 맞춰 갱신해야 한다. 설정이 잘못되면 공개 모드 기동·규칙
생성을 거부하지만, 누락된 서비스를 자동으로 찾아 채우지는 않는다.

실제 EC2에서 기존 `iptables` 명령이 커널 비호환 오류를 낸 기록이 있으므로,
사용 중인 nftables/iptables 백엔드와 Docker 규칙 체계를 먼저 확인한다.
호스트 방화벽을 초기화하거나 보호 규칙 없이 443 전체를 열어서는 안 된다.

복구 시 배포 버전을 기준 커밋 `d716488`로 되돌리고 기존 허용 목록 모드와
GitHub Pages 목적지 제한을 함께 복원한다. 새 이미지·설정으로 교체하기 전에
기존 이미지 ID와 설정 파일을 보관한다. 기준 버전의 BE는 urlscan에 의존하므로
전환 검증이 끝나기 전까지 기존 API 키를 삭제하지 않는다.
