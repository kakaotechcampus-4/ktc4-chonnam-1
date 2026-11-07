# 격리 서버 수집 API

작성일: 2026-10-10
상태: 운영 중. EC2 검증을 마쳤다. 2026-11-06 21:38 UTC 에 커밋 `d3fad6c` 로 만든 이미지와 systemd 유닛으로 다시 배포했고 `verify.py` 를 통과했다.

운영은 공개 모드(`COLLECTOR_NETWORK_MODE=public`)로 돈다. 코드 기본값은 여전히 허용 목록 모드(`allowlist`)다.
아래 설명은 두 모드를 함께 적는다. 공개 모드에서는 httpx 와 Chromium 이 같은 CONNECT 프록시로 나가고,
프록시가 접속 직전 DNS 응답을 검증해 공인 IPv4 로 연결을 고정한다. 보호 호스트·IP 설정이 필수이며 HTTPS/443 만 연다.
EC2 검증은 [브라우저·방화벽 검증](experiments/2026-10-17-ec2-browser-firewall.md),
[공개 수집 적용 기록](experiments/2026-10-17-public-collector-rollout.md),
[전환 검증 기록](experiments/2026-10-17-internal-url-scanner.md)에 남겼다.

| 문서 | 이 문서와의 관계 |
| --- | --- |
| `docs/isolation-security.md` | 위협 모델과 방어 원칙. 이 문서는 그 원칙을 수집 API 코드로 옮긴 결과다 |
| `docs/experiments/2026-10-07-aws-isolation-setup.md` | EC2 를 처음 잠근 기록과 접속 방법. 이 문서의 배포 순서가 그 위에서 진행된다 |
| `docs/experiments/2026-10-17-public-collector-rollout.md` | 공개 모드로 바꾼 기록. 운영 보호 IP·DNS 값과 복구 방법 |

## 1. 요약

- 메인 서버(Render)가 `POST /collect` 로 URL 하나를 보내면 격리 서버가 수집한 사실만 돌려준다. 판정은 하지 않는다.
- 메인 서버는 문자 속 링크를 모두 이 API 로 수집한다 (`main.py` 의 `collect_url_isolated`). urlscan 과 Shadow 비교는 없어졌다.
- 2단계는 httpx 요청이다. 2단계 HTML 에 JS 의존 신호가 있을 때만 3단계로 Chromium 을 연다.
- 허용 목록 모드(코드 기본값)는 허용한 호스트만 연다. 공개 모드(운영)는 보호 호스트·IP 와 사설·예약 주소를 뺀 공개 HTTPS 주소를 연다. 막을 주소는 요청을 보내기 전에 막는다.
- 응답 형식은 기존 `CollectResult` 그대로이고, 3단계 결과를 담는 `browser` 필드만 더했다.

## 2. 구성

```text
메인 서버(Render) ──HTTPS + Bearer 토큰──▶ :8443 [격리 컨테이너]
                                              ├─ uvicorn + Starlette        collector/app.py
                                              ├─ 2단계 httpx      ──┐       scanner/fetch.py
                                              ├─ 3단계 Chromium   ──┤       collector/browser.py
                                              └─ 검사 프록시(127.0.0.1) ◀┘ ──▶ 허용 목록 모드: egress-hosts 의 IP:443
                                                                              공개 모드: 검증한 공인 IPv4:443
```

| 파일 | 하는 일 |
| --- | --- |
| `infra/isolation/collector/app.py` | API 입구. 인증, 요청·응답 크기 제한, 동시 실행 제한, 단계 실행, 마감 |
| `infra/isolation/collector/policy.py` | 대상 검사. https, 포트 443. 허용 목록 모드는 허용 호스트만, 공개 모드는 보호 대상과 공인 IPv4 가 아닌 주소를 뺀다 |
| `infra/isolation/collector/browser.py` | 3단계 실행 조건과 Chromium 렌더링 |
| `infra/isolation/collector/egress_proxy.py` | httpx 와 브라우저의 모든 연결을 검사하는 CONNECT 프록시 |
| `scanner/fetch.py` | 2단계 수집기. 메인 서버와 같은 코드를 `prepare.sh` 로 복사해 쓴다 |
| `scanner/models.py`, `scanner/collect_response.py` | 응답 모델과 메인 서버 쪽 파서 |
| `scanner/isolation_client.py` | 메인 서버 쪽 호출. 17초·1 MiB 상한, 인증서 고정 |
| `scanner/isolation_page.py` | 수집 결과를 AI 입력 `IsolatedPage` 로 바꾼다. 브라우저 HTML 이 있으면 그쪽을 `info` 로 쓰고, `collected_at` 은 메인 서버가 채운다 |
| `infra/isolation/compose.yaml`, `lockdown.sh`, `network_config.py` | 컨테이너 설정과 호스트 방화벽. `network_config.py` 가 규칙을 검증해 만들고 `lockdown.sh` 가 한 번에 적용한다 |
| `infra/isolation/install-units.sh`, `health-restart.sh` | systemd 유닛(방화벽, 컨테이너, 하루 한 번 재생성, 5분마다 상태 확인) |

## 3. API 계약

### 요청

```http
POST /collect HTTP/1.1
Authorization: Bearer <토큰>
Content-Type: application/json

{"url": "https://fake.example-test.kr/track?id=1"}
```

- 본문은 `url` 필드 하나뿐이다. 다른 필드가 있으면 거부한다.
- 본문은 4 KiB, URL 은 2,048자까지 받는다.

### 응답

| 상태 | 뜻 |
| --- | --- |
| 200 | 수집 결과. 수집에 실패해도 200 이고 `failures` 에 이유를 남긴다 |
| 400 | JSON 이 아니거나 필드가 맞지 않음 |
| 401 | 토큰 없음 또는 불일치 |
| 413 | 요청 본문 4 KiB 초과 |
| 503 | 다른 수집이 진행 중. 기다리지 않고 바로 거절한다 |

200 응답 본문은 `CollectResult` 필드 그대로다. 응답 전체는 960 KiB 를 넘지 않게 줄여 보낸다. 메인 서버는 1 MiB 를 넘는 응답을 버린다.

```json
{
  "input_url": "https://fake.example-test.kr/track?id=1",
  "final_url": "https://fake.example-test.kr/track?id=1",
  "redirect_chain": [],
  "status_code": 200,
  "content_type": "text/html; charset=utf-8",
  "html": "<html>…</html>",
  "title": "배송 조회",
  "elapsed_ms": 140,
  "failures": [],
  "browser": null
}
```

`browser` 가 `null` 이면 3단계를 돌리지 않은 것이다. 돌렸으면 아래 값이 들어간다.

| 필드 | 뜻 |
| --- | --- |
| `trigger` | 3단계를 돌린 이유. `script_only_page`, `script_redirect`, `status_403_404`, `always` |
| `final_url` | 마지막으로 실제 열린 주소 |
| `navigation_chain` | 메인 페이지가 이동한 주소. JS 리다이렉트 포함, 최대 10개 |
| `navigation_requests` | 메인 페이지의 이동 요청 전부. 문서가 열리지 않은 HTTP 리다이렉트 경유도 담는다. 최대 20개 |
| `html`, `title` | JS 실행 후 화면. HTML 은 256 KiB 상한 |
| `downloads` | 브라우저가 시작하려던 다운로드 주소. 파일은 받지 않는다. 최대 5개 |
| `blocked_requests` | 막힌 주소로 가는 요청, WebSocket, 팝업을 막은 횟수 |
| `elapsed_ms`, `failures` | 3단계에 걸린 시간과 실패 이유 |

`downloads` 가 비었거나 `blocked_requests` 가 0 인 것은 "본 범위에서 없었다"는 뜻일 뿐 안전하다는 뜻이 아니다.

### failures 값

| 값 | 나오는 곳 | 뜻 |
| --- | --- | --- |
| `blocked_address` | 2단계 | 막힌 주소(5절). 허용 목록 모드는 목록 밖 주소, 공개 모드는 보호 호스트·IP 와 공인 IPv4 가 아닌 주소. 요청을 보내지 않았다 |
| `timeout` | 2단계, API | 2단계 마감 초과. API 전체 마감을 넘겨도 이 값이다 |
| `connection_failed`, `tls_cert_verify_failed` | 2단계 | 접속 실패, 인증서 검증 실패 |
| `redirect_limit`, `invalid_scheme`, `invalid_url` | 2단계 | 리다이렉트 5홉 초과, http(s) 아님, URL 해석 실패 |
| `html_truncated` | 2·3단계 | HTML 을 256 KiB 에서 잘랐음 |
| `compressed_response_skipped` | 2단계 | 압축 응답이라 본문을 읽지 않았음 |
| `collector_error` | API | 수집기 내부 오류. 다음 요청은 정상 처리된다 |
| `invalid_response`, `url_mismatch` | 메인 서버 파서 | 격리 서버 응답을 믿을 수 없음 |
| `collection_failed` | 메인 서버 | 격리 서버 호출 자체가 실패했다 (설정 없음, 17초 초과, HTTP 오류, 응답 크기 초과). 문자 분석 결과는 그대로 쓴다 |
| `browser_timeout` | 3단계 | 마감 초과. 그때까지 본 화면은 남긴다 |
| `browser_blocked_navigation` | 3단계 | 메인 페이지가 막힌 주소로 이동하려 해서 막았다 |
| `browser_navigation_limit` | 3단계 | 이동 요청이 20개를 넘어 뒤쪽을 기록하지 못했다 |
| `browser_navigation_failed` | 3단계 | 막히지 않은 주소인데 열지 못했다 |
| `browser_crashed`, `browser_error` | 3단계 | 렌더러 크래시, 그 밖의 브라우저 오류. 브라우저를 다시 띄운다 |
| `browser_snapshot_failed` | 3단계 | 페이지가 멈춰 화면을 가져오지 못했다 |

## 4. 단계와 시간 예산

| 단계 | 하는 일 | 상한 |
| --- | --- | --- |
| 2단계 httpx | 리다이렉트를 직접 따라가며 홉마다 대상 검사. HTML 256 KiB | 2초, 시간 초과면 5초로 1회 재시도 |
| 3단계 Chromium | JS 실행 후 이동·폼·다운로드 관찰 | 6초 (대기 포함) |
| API 전체 | 위 두 단계와 정리 여유 3초(`BACKSTOP_EXTRA_SECONDS`) | 2 + 5 + 6 + 3 = 16초. 메인 서버 타임아웃 17초보다 짧다 |

동시 수집은 1건이다(`COLLECTOR_MAX_CONCURRENCY=1`). 대기열은 없고, 넘친 요청은 바로 503 을 받는다. 메인 서버는 이것을 `collection_failed` 로 처리한다.

3단계로 넘기는 조건은 아래와 같다 (`browser.py` 의 `browser_trigger`). 2단계가 실패했거나 HTML 이 없으면 넘기지 않는다.

- 보이는 글자가 200자 미만이고 스크립트가 있음
- 스크립트 안에 `location` 변경, 또는 meta refresh 가 있음
- 응답이 403·404 (클로킹 의심)

3단계는 폼이 나타나거나, 네트워크가 0.5초 잠잠해지거나, 다운로드가 시작되거나, 메인 페이지 이동이 막히면 끝낸다.

## 5. 대상 제한

앞 계층이 뚫려도 다음 계층이 막도록 겹쳐 둔다. "막힌 주소"는 모드마다 다르다.

| 모드 | 막힌 주소 |
| --- | --- |
| 허용 목록 (코드 기본값) | `COLLECTOR_ALLOWED_HOSTS` 에 없는 호스트 전부 |
| 공개 (운영) | 보호 호스트와 그 하위 도메인, 보호 IP, 공인 IPv4 가 아닌 주소(사설·예약·루프백·링크로컬·IPv6), 숫자 IP 옛 표기(`0177.0.0.1` 등) |

| 계층 | 막는 것 | 위치 |
| --- | --- | --- |
| 입구 검사 | 막힌 주소, https·443 이 아닌 URL, `user@`·백슬래시·제어 문자가 섞인 URL | `policy.py` |
| 2단계 홉 검사 | 리다이렉트로 막힌 주소에 가는 것. 요청을 보내기 전에 확인한다 | `fetch.py` 의 `url_allowed` |
| 브라우저 route | 막힌 주소로 가는 이동·이미지·스크립트·fetch. 이미지·폰트·미디어는 막히지 않은 주소여도 받지 않는다 | `browser.py` |
| 브라우저 별도 차단 | WebSocket, 서비스 워커, 팝업, 다운로드 본문 | `browser.py` |
| 검사 프록시 | httpx 와 브라우저의 **모든** 연결. 막히지 않은 호스트의 443 만 터널을 연다. 공개 모드는 프록시가 DNS 를 풀어, 응답이 모두 공인·비보호 주소일 때만 그 IPv4 로 접속한다. 평문 HTTP 는 응답 없이 끊는다 | `egress_proxy.py`, `policy.py` 의 `connect_ip` |
| 호스트 방화벽·보안 그룹 | 허용 목록 모드: `egress-hosts` 에 고정한 IP 의 443 만, DNS 없음. 공개 모드: 사설·예약 대역과 보호 IP 를 먼저 버리고, 공개 443 과 지정 DNS 서버의 53(UDP·TCP)만 연다. 두 모드 모두 호스트 서비스로의 연결과 IPv6 를 막는다 | `network_config.py`, `lockdown.sh`, AWS SG |

**검사 프록시를 둔 이유.** Playwright `route` 는 서버 리다이렉트를 따라간 요청을 다시 보여 주지 않는다. "허용 주소 → 허용 주소 → 내부 주소" 2단 리다이렉트가 route 검사를 우회해 내부 서버까지 요청이 간 것을 로컬에서 재현했다. 그래서 연결 단위 검사는 프록시가 맡는다. Playwright 는 프록시를 쓰면 루프백 주소도 프록시로 보낸다.

**막힌 메인 페이지 이동은 204 로 취소한다.** 이동을 끊으면 크롬이 오류 페이지로 화면을 바꿔 버린다. 204 를 주면 이동이 취소되고, 이동을 시도한 페이지 화면이 그대로 남아 근거로 쓸 수 있다.

**브라우저 종류.** 기본 headless shell 을 쓴다. `channel="chromium"` 은 구글 서버로 배경 통신을 시도해 차단 횟수에 섞이는 것을 로컬에서 확인했다.

## 6. 설정값

`compose.yaml` 의 `environment` 에서 정한다. 비밀값은 환경변수로 넣지 않는다.

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `COLLECTOR_ALLOWED_HOSTS` | (허용 목록 모드에서 필수) | 허용 호스트, 쉼표 구분. `compose.egress.yaml` 이 채운다. 허용 목록 모드에서 비어 있으면 서버가 뜨지 않는다. 공개 모드는 쓰지 않는다 |
| `COLLECTOR_BROWSER` | `auto` | `off` 2단계만 / `auto` 신호가 있을 때만 / `always` 매번 (가짜 페이지 시험용) |
| `COLLECTOR_FETCH_TIMEOUT_SECONDS` | `2` | 2단계 1차 상한 |
| `COLLECTOR_FETCH_RETRY_TIMEOUT_SECONDS` | `5` | 1차가 시간 초과일 때 한 번 더 받는 상한 |
| `COLLECTOR_BROWSER_TIMEOUT_SECONDS` | `6` | 3단계 상한 |
| `COLLECTOR_MAX_CONCURRENCY` | `1` | 동시 수집 수. 넘치면 503 |
| `COLLECTOR_BROWSER_RESTART_EVERY` | `50` | 브라우저 재시작 주기 (페이지 수) |
| `COLLECTOR_TOKEN_FILE` | `/run/secrets/collect_token` | 수집 토큰 파일 |

`collector.env` (저장소에 올리지 않음, 예시는 `collector.env.example`)

compose 와 `lockdown.sh` 가 함께 읽는다. 공개 모드는 아래 다섯 값이 모두 필요하다. 값이 틀리면 방화벽은 전달을 모두 막고, `build.sh` 는 `render-egress.py` 에서 멈춘다.

| 이름 | 뜻 |
| --- | --- |
| `COLLECTOR_NETWORK_MODE` | `allowlist`(기본값) 또는 `public`. 운영은 `public` |
| `RENDER_CIDRS` | Render Outbound CIDR. 공백 구분. 이 대역에서 오는 8443 만 받는다 |
| `COLLECTOR_PROTECTED_HOSTS` | 공개 모드. 접속하지 않을 호스트, 쉼표 구분. 하위 도메인도 막힌다. Render 서비스, 격리 API, 격리 EC2 이름을 넣는다 |
| `COLLECTOR_PROTECTED_IPS` | 공개 모드. 접속하지 않을 IPv4, 쉼표 구분. 서비스의 공인 IPv4 를 모두 넣는다. 공유 호스팅·CDN IP 를 넣으면 그 IP 의 사이트가 모두 막힌다 |
| `COLLECTOR_DNS_SERVER` | 공개 모드. IPv4 DNS 서버 하나. 이 서버의 53 만 열린다. 보호 IP·루프백·링크로컬 주소는 받지 않는다 |

`egress-hosts` (저장소에 올리지 않음, 예시는 `egress-hosts.example`)

허용 목록 모드에서만 쓴다. 격리 서버가 접속할 수 있는 호스트와 고정 IP 를 `호스트 IP` 한 줄씩 적는다. 가짜 페이지 호스트와 단축 도메인이 들어간다. `build.sh` 가 `render-egress.py` 로 검증한 뒤 두 파일을 만든다.

| 만들어지는 파일 | 쓰는 곳 |
| --- | --- |
| `compose.egress.yaml` | 컨테이너 `/etc/hosts` 고정(`extra_hosts`)과 `COLLECTOR_ALLOWED_HOSTS` |
| `egress-ips.txt` | `lockdown.sh` 가 443 을 열어 줄 IP |

공인 IPv4 가 아니면(사설·루프백·메타데이터·문서용 주소) 거부하고 아무 파일도 바꾸지 않는다. IP 는 노트북에서 `check-egress-ips.py --print` 로 채우고, 배포 전마다 `check-egress-ips.py` 로 바뀌었는지 확인한다.

공개 모드에서 `render-egress.py` 는 `egress-hosts` 를 읽지 않는다. `compose.egress.yaml` 에 DNS 서버와 공개 모드 값만 쓰고 `egress-ips.txt` 는 비운다.

메인 서버(Render) 환경변수

| 이름 | 값 |
| --- | --- |
| `ISOLATION_API_URL` | `https://<격리 서버 공인 IP>:8443` |
| `ISOLATION_API_TOKEN` | `secrets/collect_token` 내용 |
| `ISOLATION_API_CA_CERT` | `secrets/collector.crt` 전체. 이 인증서 하나만 믿는다. 없으면 시스템 CA 로 검증한다 |

## 7. 배포 순서

EC2 호스트는 바깥으로 나갈 수 없다(`network_config.py` 의 `KTC-OUTPUT`). 서버에서 이미지를 빌드하지 않고, 노트북에서 만든 이미지 파일을 `IMAGE_TAR` 로 넘긴다 (아래 "이미 돌아가는 서버에 다시 올리기").

1. 노트북에서 수집 코드를 복사한다.
   ```bash
   bash infra/isolation/prepare.sh
   ```
2. `connect.ps1` 로 접속할 수 있는 상태에서 `infra/isolation/` 를 EC2 의 `/opt/ktc-isolation/` 에 올린다.
3. EC2 에서 `collector.env.example` 을 `collector.env` 로 복사해 6절의 값을 채운다. 허용 목록 모드면 `egress-hosts.example` 도 `egress-hosts` 로 복사해 채운다. `egress-hosts` 의 IP 는 노트북에서 미리 확인한다.
   ```bash
   python3 infra/isolation/check-egress-ips.py --print infra/isolation/egress-hosts.example
   ```
4. 토큰과 인증서를 만든다. 출력된 값은 8번에서 Render 에 넣는다.
   ```bash
   sudo bash /opt/ktc-isolation/make-secrets.sh <격리 서버 공인 IP>
   ```
5. 보안 그룹을 바꾼다. 인바운드 TCP 8443 은 Render CIDR 에서만 연다. 아웃바운드는 허용 목록 모드면 TCP 443 을 `egress-hosts` 의 IP 마다 `/32` 로만, 공개 모드면 TCP 443 과 `COLLECTOR_DNS_SERVER` 의 TCP·UDP 53 을 연다.
6. 유닛을 설치한다. 처음 만드는 서버는 `bootstrap.sh` 가 함께 설치한다. 이미 돌아가는 서버는 `bootstrap.sh` 를 다시 돌리지 않는다(sshd, apt 까지 바꾸고 잠긴 서버에서는 apt 가 멈춘다).
   ```bash
   sudo bash /opt/ktc-isolation/install-units.sh
   ```
7. 빌드한다. `build.sh` 가 `render-egress.py` 로 `compose.egress.yaml` 을 만들고(허용 목록 모드는 `egress-hosts` 검증 포함), 방화벽(`ktc-isolation-firewall`)을 다시 적용하고, `ktc-isolation-collector` 로 컨테이너를 띄우고, `verify.py` 까지 실행한다. 바깥이 막힌 호스트에서는 다른 곳에서 빌드한 이미지를 `IMAGE_TAR=/path/image.tar` 로 넘긴다. 컨테이너는 하루 한 번(`ktc-isolation-recreate.timer`), 그리고 healthcheck 가 unhealthy 일 때(`ktc-isolation-health.timer`) 다시 만든다.
   ```bash
   sudo bash /opt/ktc-isolation/build.sh
   ```
8. Render 에 6절의 환경변수 세 개를 넣는다.

### 이미 돌아가는 서버에 다시 올리기

토큰, 인증서, `collector.env`, `egress-hosts` 는 서버에 있는 것을 그대로 쓴다. 위의 3~5번과 8번은 하지 않는다. 특히 `make-secrets.sh` 를 다시 돌리면 토큰과 인증서가 바뀌어 Render 가 접속하지 못한다.

1. 노트북에서 이미지를 만들어 파일로 저장한다. 서버는 바깥으로 나갈 수 없어 서버에서 빌드하지 않는다.
   ```bash
   bash infra/isolation/prepare.sh
   docker build --platform linux/amd64 -t ktc-isolation:approved infra/isolation
   docker save ktc-isolation:approved -o ktc-isolation.tar
   ```
2. 이미지 파일을 서버의 `/tmp/ktc-isolation.tar` 로, `infra/isolation/` 를 서버로 옮긴다(`ssh ktc-isolation` 이나 `connect.ps1` 경로). `/opt/ktc-isolation/` 에 덮어쓰되 `collector.env`, `egress-hosts`, `secrets/`, `.env` 는 덮어쓰지 않는다. 예전 서버에 `stub_api.py`, `ktc-isolation-stub.service` 가 남아 있으면 지운다.

   **큰 파일 옮기기.** `~/.ssh/config` 의 `ProxyCommand` 터널(`aws ec2-instance-connect open-tunnel` 을 표준 입출력으로 쓰는 방식)은 큰 파일을 올리다 멈춘다. 터널을 따로 열고 그 포트로 보낸다. 526MB 이미지가 약 9분 걸렸다.
   ```powershell
   # 인스턴스 ID 는 connect.ps1 의 $instanceId
   aws ec2-instance-connect open-tunnel --instance-id <인스턴스 ID> --profile ktc-notebook --region ap-northeast-2 --local-port 2222
   # 다른 창에서. HostKeyAlias 를 인스턴스 ID 로 주면 ~/.ssh/known_hosts 의 기존 키로 확인한다
   scp -P 2222 -o ProxyCommand=none -o HostKeyAlias=<인스턴스 ID> -o IdentitiesOnly=yes -i ~/.ssh/ktc_isolation_ed25519 ktc-isolation.tar isolation-admin@127.0.0.1:/tmp/ktc-isolation.tar
   ```
3. 되돌릴 때 쓸 지금 이미지 ID 를 적어 둔다.
   ```bash
   sudo docker inspect ktc-isolation-browser --format '{{.Image}}' | sudo tee /opt/ktc-isolation/previous-image.txt
   ```
4. 유닛을 설치하고(위 6번), 옮긴 이미지로 띄운다. `build.sh` 가 방화벽과 컨테이너를 켜고 `verify.py` 를 돌린 뒤 타이머를 켠다.
   ```bash
   sudo bash /opt/ktc-isolation/install-units.sh
   sudo IMAGE_TAR=/tmp/ktc-isolation.tar bash /opt/ktc-isolation/build.sh
   ```
   `build.sh` 는 컨테이너 ID 가 바뀐 새 컨테이너가 뜬 뒤에 `verify.py` 를 돌리고, 그다음에 타이머를 켠다. 60초 안에 새 컨테이너가 뜨지 않으면 `verify.py` 를 건너뛰고 실패한다. 이때는 `sudo journalctl -u ktc-isolation-collector` 로 원인을 고친 뒤, `build.sh` 를 다시 돌리지 않고 검사만 다시 한다.
   ```bash
   sudo docker exec ktc-isolation-browser python /app/verify.py
   ```
5. 아래 8절 확인 목록과 챗봇으로 공식 링크(✅), 가짜 주소(🔎)를 확인한다.

2026-11-06 재배포(커밋 `d3fad6c`)를 이 순서로 했다.

되돌리기: `.env` 의 `ISOLATION_IMAGE` 를 `previous-image.txt` 의 값으로 바꾸고 `sudo systemctl restart ktc-isolation-collector` 를 실행한다. 새 유닛이 뜨지 않으면 `sudo systemctl disable --now ktc-isolation-collector` 뒤에 예전처럼 `docker compose -f compose.yaml -f compose.egress.yaml --env-file .env --env-file collector.env up -d` 로 띄운다.

## 8. EC2 확인 목록

`docs/isolation-security.md` §8 의 번호를 함께 적었다. 결과는 `docs/experiments/` 에 남긴다.

체크한 항목은 2026-10-17 기록(`2026-10-17-ec2-browser-firewall.md`, `2026-10-17-public-collector-rollout.md`)에 결과가 있는 것이다. 2026-11-06 재배포 뒤에도 `verify.py` 를 통과했다.

- [x] `verify.py` 통과. headless shell 렌더러에 `--no-sandbox` 가 없다 (§8-7). Chromium 153 에서 namespace·Seccomp-BPF 샌드박스 확인
- [ ] 토큰 없이, 또는 틀린 토큰으로 `/collect` 호출하면 401 (§8-10). 토큰 없는 401 은 확인했고, 틀린 토큰 기록은 없다
- [ ] Render CIDR 밖에서 8443 접속 실패, 평문 HTTP 접속 실패 (§8-9, §8-19). CIDR 밖 8443 차단은 EC2 임시 네임스페이스에서만 확인했다
- [ ] 메인 서버가 `ISOLATION_API_CA_CERT` 없이 접속하면 실패한다
- [x] 공개 주소·팀 GitHub Pages 수집 성공. `COLLECTOR_BROWSER=always` 로 3단계도 성공
- [ ] 막힌 주소로 리다이렉트하는 가짜 페이지에서 `blocked_address` (§8-4)
- [ ] 이미지·스크립트·iframe·fetch·WebSocket·팝업이 막힌 주소를 부르는 가짜 페이지에서 `blocked_requests` 가 늘고, 방화벽 차단 패킷 수는 늘지 않는다 (§8-20, §8-27). 하위 자원 차단은 확인했고 WebSocket·팝업 기록은 없다
- [x] 컨테이너에서 `169.254.169.254`, 메인 서버, 막힌 주소 접속 실패 (§8-1~3). 사설 IP·호스트 게이트웨이·보호 IP 3개 직접 소켓 차단
- [x] DNS: 허용 목록 모드는 DNS 질의가 나가지 않는다. 공개 모드는 지정 DNS 외 53 이 막힌다. 공개 모드 기준으로 확인
- [x] 무한 루프·느린 응답 페이지 뒤에도 다음 요청 정상 (§8-13). 무한 JS 타임아웃 뒤 복구 확인
- [ ] 가짜 페이지 종류별 최대 메모리와 응답 시간 기록. 응답 시간만 있다
- [x] iptables 방식인지 nftables 방식인지 확인하고, `lockdown.sh` 규칙이 실제로 걸렸는지 확인. 호스트는 nft 규칙이고, `KTC-FORWARD`·`KTC-INPUT` DROP 카운터 증가를 확인했다

## 9. 알려진 한계와 후속 작업

| 항목 | 내용 |
| --- | --- |
| 같은 컨테이너 | 수집 API 와 브라우저가 같은 컨테이너·사용자로 돈다. 렌더러 샌드박스가 뚫리면 토큰 파일을 읽을 수 있다. 실제 링크를 여는 카테캠 2단계 전에 컨테이너를 나눈다 |
| 가짜 페이지 인증서 | 운영에서는 브라우저가 인증서 오류를 무시하지 않는다. 가짜 페이지 서버에 정상 인증서가 필요하다 |
| 프록시 본문 | 프록시는 터널만 열고 본문은 보지 않는다. 터널 안에서 오가는 내용의 크기는 컨테이너 메모리 상한이 막는다 |
| 시간 예산 | 2초·5초·6초는 가설값이다(2초·5초는 2026-11-06 응답 14건 기준, 최대 1.36초). EC2 실측으로 조정한다 |
| 고정 IP (허용 목록 모드) | 단축 서비스·가짜 페이지 IP 가 바뀌면 접속이 실패한다. 메인 서버는 그 링크를 판단 보류로 안내하므로 틀린 안내는 나가지 않지만, 배포 전마다 `check-egress-ips.py` 로 확인한다 |
| 단축 도메인 목록 | 메인 서버 `services/link_resolution.py` 의 `SHORTENER_HOSTS` 를 고칠 때, 허용 목록 모드면 `egress-hosts` 도 함께 고친다. 한쪽에만 있으면 그 단축 주소는 늘 판단 보류가 된다 |
| JS·CSS 원문 | `browser.html` 은 `page.content()` 그대로라 인라인 스크립트·스타일이 들어 있다. 메인 서버는 이것을 그대로 `IsolatedPage.info` 로 넘기고, AI 쪽 `ai/page.py` 가 script·style 안의 글자를 읽지 않는다 |
| iframe·Shadow DOM | `page.content()` 는 메인 프레임만 직렬화한다. AI 에는 `없음` 이 아니라 `미확인` 으로 전달해야 한다 |

## 10. 단축 주소 해석

문자 속 링크가 bit.ly 같은 범용 단축 주소면, 그 도메인으로는 공식 여부를 판정할 수 없다. 최종 도메인을 쓰면 "단축 주소 → 공격자 주소 → (스캐너에게만) 공식 사이트" 경로에서 공격자 주소를 공식으로 안내하게 된다.

그래서 리다이렉트 경로에서 단축 도메인만 걷어내고 처음 나오는 실제 도메인으로 판정한다 (`services/link_resolution.py`).

```text
bit.ly/a  → (격리 서버가 요청) → Location: https://cj-delivery.xyz/...
cj-delivery.xyz → (허용 목록 모드: 목록 밖이라 요청하지 않음 / 공개 모드: 계속 따라감) → 경로에 기록
→ 판정 도메인 = cj-delivery.xyz
```

| 항목 | 내용 |
| --- | --- |
| 경로를 얻는 곳 | 격리 서버 2단계의 `redirect_chain`. 허용 목록 모드는 단축 서비스에만 접속하고 그 다음 주소는 기록만 한다. 메인 서버는 사용자 링크에 접속하지 않는다 |
| 판정 | 첫 실제 도메인으로 `check_link_official`. 최종 주소, 리다이렉트 경로, 브라우저 이동 기록은 공식 판정을 깎는 데만 쓴다 (`main.py` 의 `build_ai_url_analysis`, ADR 0009) |
| 실패 | 격리 서버 미설정·실패·시간 초과, 단축 서비스가 리다이렉트 대신 안내 페이지를 줄 때, (허용 목록 모드) 단축 서비스 IP 가 바뀌었을 때는 판단 보류(`unresolved`) |
| 모든 링크 | 단축 주소가 아닌 링크도 같은 경로로 수집해 판정에 쓴다. 이때 첫 실제 도메인은 입력 링크의 도메인이다 |
| 목록 | 범용 단축 도메인만 넣는다. 기관이 직접 운영하는 짧은 도메인(coupa.ng 등)은 공식 도메인 목록에 넣는다 |
