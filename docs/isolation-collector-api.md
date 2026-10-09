# 격리 서버 수집 API

작성일: 2026-10-10
상태: 초안. 로컬 검증만 마쳤고 EC2 검증 전이다.

| 문서 | 이 문서와의 관계 |
| --- | --- |
| `docs/isolation-security.md` | 위협 모델과 방어 원칙. 이 문서는 그 원칙을 수집 API 코드로 옮긴 결과다 |
| `docs/aws-isolation-setup.md` | AWS 리소스 설정. 이 문서의 배포 순서가 그 위에서 진행된다 |
| `docs/experiments/2026-10-07-aws-isolation-setup.md` | 현재 EC2 상태와 접속 방법 |
| `docs/superpowers/specs/2026-10-01-smishing-pipeline-design.md` | 2·3단계 페이지 방문 설계. 시간 예산과 3단계 조건의 출처 |

## 1. 요약

- 메인 서버(Render)가 `POST /collect` 로 URL 하나를 보내면 격리 서버가 수집한 사실만 돌려준다. 판정은 하지 않는다.
- 2단계는 httpx 요청이다. 2단계 HTML 에 JS 의존 신호가 있을 때만 3단계로 Chromium 을 연다.
- 카테캠 1단계에서는 허용한 가짜 페이지 호스트만 연다. 목록 밖 주소는 요청을 보내기 전에 막는다.
- 응답 형식은 기존 `CollectResult` 그대로이고, 3단계 결과를 담는 `browser` 필드만 더했다.

## 2. 구성

```text
메인 서버(Render) ──HTTPS + Bearer 토큰──▶ :8443 [격리 컨테이너]
                                              ├─ uvicorn + Starlette        collector/app.py
                                              ├─ 2단계 httpx                scanner/fetch.py
                                              └─ 3단계 Chromium ──▶ 검사 프록시(127.0.0.1) ──▶ TEST_PAGE_IP:443
```

| 파일 | 하는 일 |
| --- | --- |
| `infra/isolation/collector/app.py` | API 입구. 인증, 요청·응답 크기 제한, 동시 실행 제한, 단계 실행, 마감 |
| `infra/isolation/collector/policy.py` | 허용 URL 검사. https, 포트 443, 허용 호스트만 |
| `infra/isolation/collector/browser.py` | 3단계 실행 조건과 Chromium 렌더링 |
| `infra/isolation/collector/egress_proxy.py` | 브라우저의 모든 연결을 검사하는 CONNECT 프록시 |
| `scanner/fetch.py` | 2단계 수집기. 메인 서버와 같은 코드를 `prepare.sh` 로 복사해 쓴다 |
| `scanner/models.py`, `scanner/collect_response.py` | 응답 모델과 메인 서버 쪽 파서 |
| `infra/isolation/compose.yaml`, `lockdown.sh` | 컨테이너 설정과 호스트 방화벽 |

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

200 응답 본문은 `CollectResult` 필드 그대로다. 응답 전체는 480 KiB 를 넘지 않게 줄여 보낸다. 메인 서버는 512 KiB 를 넘는 응답을 버린다.

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
| `html`, `title` | JS 실행 후 화면. HTML 은 128 KiB 상한 |
| `downloads` | 브라우저가 시작하려던 다운로드 주소. 파일은 받지 않는다. 최대 5개 |
| `blocked_requests` | 허용 목록 밖 요청, WebSocket, 팝업을 막은 횟수 |
| `elapsed_ms`, `failures` | 3단계에 걸린 시간과 실패 이유 |

`downloads` 가 비었거나 `blocked_requests` 가 0 인 것은 "본 범위에서 없었다"는 뜻일 뿐 안전하다는 뜻이 아니다.

### failures 값

| 값 | 나오는 곳 | 뜻 |
| --- | --- | --- |
| `blocked_address` | 2단계 | 허용 목록 밖 주소, 또는 사설·루프백 주소. 요청을 보내지 않았다 |
| `timeout` | 2단계, API | 2단계 마감 초과. API 전체 마감을 넘겨도 이 값이다 |
| `connection_failed`, `tls_cert_verify_failed` | 2단계 | 접속 실패, 인증서 검증 실패 |
| `redirect_limit`, `invalid_scheme`, `invalid_url` | 2단계 | 리다이렉트 5홉 초과, http(s) 아님, URL 해석 실패 |
| `html_truncated` | 2·3단계 | HTML 을 128 KiB 에서 잘랐음 |
| `compressed_response_skipped` | 2단계 | 압축 응답이라 본문을 읽지 않았음 |
| `collector_error` | API | 수집기 내부 오류. 다음 요청은 정상 처리된다 |
| `invalid_response`, `url_mismatch` | 메인 서버 파서 | 격리 서버 응답을 믿을 수 없음 |
| `browser_timeout` | 3단계 | 마감 초과. 그때까지 본 화면은 남긴다 |
| `browser_blocked_navigation` | 3단계 | 메인 페이지가 허용 목록 밖으로 이동하려 해서 막았다 |
| `browser_navigation_failed` | 3단계 | 허용한 주소인데 열지 못했다 |
| `browser_crashed`, `browser_error` | 3단계 | 렌더러 크래시, 그 밖의 브라우저 오류. 브라우저를 다시 띄운다 |
| `browser_snapshot_failed` | 3단계 | 페이지가 멈춰 화면을 가져오지 못했다 |

## 4. 단계와 시간 예산

| 단계 | 하는 일 | 상한 |
| --- | --- | --- |
| 2단계 httpx | 리다이렉트를 직접 따라가며 홉마다 허용 목록 확인. HTML 128 KiB | 3.5초 |
| 3단계 Chromium | JS 실행 후 이동·폼·다운로드 관찰 | 6초 (대기 포함) |
| API 전체 | 위 두 단계와 정리 시간 | 12.5초. 메인 서버 타임아웃 15초보다 짧다 |

3단계로 넘기는 조건은 설계 문서와 같다. 2단계가 실패했거나 HTML 이 없으면 넘기지 않는다.

- 보이는 글자가 200자 미만이고 스크립트가 있음
- 스크립트 안에 `location` 변경, 또는 meta refresh 가 있음
- 응답이 403·404 (클로킹 의심)

3단계는 폼이 나타나거나, 네트워크가 0.5초 잠잠해지거나, 다운로드가 시작되거나, 메인 페이지 이동이 막히면 끝낸다.

## 5. 대상 제한

카테캠 1단계 기준이다. 앞 계층이 뚫려도 다음 계층이 막도록 겹쳐 둔다.

| 계층 | 막는 것 | 위치 |
| --- | --- | --- |
| 입구 검사 | 허용 호스트가 아닌 URL, `user@`·백슬래시·제어 문자가 섞인 URL | `policy.py` |
| 2단계 홉 검사 | 리다이렉트로 목록 밖에 가는 것. 요청을 보내기 전에 확인한다 | `fetch.py` 의 `url_allowed` |
| 브라우저 route | 목록 밖 이동·이미지·스크립트·fetch. 이미지·폰트·미디어는 허용 호스트여도 받지 않는다 | `browser.py` |
| 브라우저 별도 차단 | WebSocket, 서비스 워커, 팝업, 다운로드 본문 | `browser.py` |
| 검사 프록시 | 브라우저의 **모든** 연결. 허용 호스트의 443 만 터널을 연다. 평문 HTTP 는 응답 없이 끊는다 | `egress_proxy.py` |
| 호스트 방화벽·보안 그룹 | 컨테이너는 `TEST_PAGE_IP:443` 으로만 나간다. 호스트 서비스로의 연결도 막는다 | `lockdown.sh`, AWS SG |

**검사 프록시를 둔 이유.** Playwright `route` 는 서버 리다이렉트를 따라간 요청을 다시 보여 주지 않는다. "허용 주소 → 허용 주소 → 내부 주소" 2단 리다이렉트가 route 검사를 우회해 내부 서버까지 요청이 간 것을 로컬에서 재현했다. 그래서 연결 단위 검사는 프록시가 맡는다. Playwright 는 프록시를 쓰면 루프백 주소도 프록시로 보낸다.

**막힌 메인 페이지 이동은 204 로 취소한다.** 이동을 끊으면 크롬이 오류 페이지로 화면을 바꿔 버린다. 204 를 주면 이동이 취소되고, 이동을 시도한 페이지 화면이 그대로 남아 근거로 쓸 수 있다.

**브라우저 종류.** 기본 headless shell 을 쓴다. `channel="chromium"` 은 구글 서버로 배경 통신을 시도해 차단 횟수에 섞이는 것을 로컬에서 확인했다.

## 6. 설정값

`compose.yaml` 의 `environment` 에서 정한다. 비밀값은 환경변수로 넣지 않는다.

| 이름 | 기본값 | 뜻 |
| --- | --- | --- |
| `COLLECTOR_ALLOWED_HOSTS` | (필수) | 허용 호스트, 쉼표 구분. 비어 있으면 서버가 뜨지 않는다 |
| `COLLECTOR_BROWSER` | `auto` | `off` 2단계만 / `auto` 신호가 있을 때만 / `always` 매번 (가짜 페이지 시험용) |
| `COLLECTOR_FETCH_TIMEOUT_SECONDS` | `3.5` | 2단계 상한 |
| `COLLECTOR_BROWSER_TIMEOUT_SECONDS` | `6` | 3단계 상한 |
| `COLLECTOR_MAX_CONCURRENCY` | `1` | 동시 수집 수. 넘치면 503 |
| `COLLECTOR_BROWSER_RESTART_EVERY` | `50` | 브라우저 재시작 주기 (페이지 수) |
| `COLLECTOR_TOKEN_FILE` | `/run/secrets/collect_token` | 수집 토큰 파일 |

`collector.env` (저장소에 올리지 않음, 예시는 `collector.env.example`)

| 이름 | 뜻 |
| --- | --- |
| `TEST_PAGE_HOST`, `TEST_PAGE_IP` | 가짜 페이지 호스트와 고정 IP. 컨테이너 `/etc/hosts` 에 고정되고 방화벽 허용 대상이 된다 |
| `RENDER_CIDRS` | Render Outbound CIDR. 공백 구분 |

메인 서버(Render) 환경변수

| 이름 | 값 |
| --- | --- |
| `ISOLATION_API_URL` | `https://<격리 서버 공인 IP>:8443` |
| `ISOLATION_API_TOKEN` | `secrets/collect_token` 내용 |
| `ISOLATION_API_CA_CERT` | `secrets/collector.crt` 전체. 이 인증서 하나만 믿는다. 없으면 시스템 CA 로 검증한다 |

## 7. 배포 순서

현재 EC2 는 외부 통신이 막혀 있어 이미지 빌드가 실패한다. 수집을 멈춘 관리 시간에 공급 경로를 임시로 열고 빌드한 뒤 다시 막는다 (`docs/experiments/2026-10-07-aws-isolation-setup.md`).

1. 노트북에서 수집 코드를 복사한다.
   ```bash
   bash infra/isolation/prepare.sh
   ```
2. `connect.ps1` 로 접속할 수 있는 상태에서 `infra/isolation/` 를 EC2 의 `/opt/ktc-isolation/` 에 올린다.
3. EC2 에서 `collector.env.example` 을 `collector.env` 로 복사해 값을 채운다.
4. 토큰과 인증서를 만든다. 출력된 값은 6번에서 Render 에 넣는다.
   ```bash
   sudo bash /opt/ktc-isolation/make-secrets.sh <격리 서버 공인 IP>
   ```
5. 보안 그룹을 바꾼다. 인바운드 TCP 8443 은 Render CIDR 에서만, 아웃바운드 TCP 443 은 `TEST_PAGE_IP/32` 로만 연다.
6. 방화벽을 다시 적용하고 빌드한다. `build.sh` 가 컨테이너를 띄우고 `verify.py` 까지 실행한다.
   ```bash
   sudo systemctl restart ktc-isolation-firewall
   sudo bash /opt/ktc-isolation/build.sh
   ```
7. Render 에 6절의 환경변수 세 개를 넣는다.

## 8. EC2 확인 목록

`docs/isolation-security.md` §8 의 번호를 함께 적었다. 결과는 `docs/experiments/` 에 남긴다.

- [ ] `verify.py` 통과. headless shell 렌더러에 `--no-sandbox` 가 없다 (§8-7)
- [ ] 토큰 없이, 또는 틀린 토큰으로 `/collect` 호출하면 401 (§8-10)
- [ ] Render CIDR 밖에서 8443 접속 실패, 평문 HTTP 접속 실패 (§8-9, §8-19)
- [ ] 메인 서버가 `ISOLATION_API_CA_CERT` 없이 접속하면 실패한다
- [ ] 허용한 가짜 페이지 수집 성공. `COLLECTOR_BROWSER=always` 로 3단계도 성공
- [ ] 목록 밖 주소로 리다이렉트하는 가짜 페이지에서 `blocked_address` (§8-4)
- [ ] 이미지·스크립트·iframe·fetch·WebSocket·팝업이 목록 밖을 부르는 가짜 페이지에서 `blocked_requests` 가 늘고, 방화벽 차단 패킷 수는 늘지 않는다 (§8-20, §8-27)
- [ ] 컨테이너에서 `169.254.169.254`, 메인 서버, 목록 밖 사이트 접속 실패 (§8-1~3)
- [ ] 컨테이너에서 DNS 질의가 나가지 않는다. Docker 내장 DNS 우회 포함
- [ ] 무한 루프·느린 응답 페이지 뒤에도 다음 요청 정상 (§8-13)
- [ ] 가짜 페이지 종류별 최대 메모리와 응답 시간 기록
- [ ] iptables 방식인지 nftables 방식인지 확인하고, `lockdown.sh` 규칙이 실제로 걸렸는지 `iptables -L KTC-FORWARD -v -n` 으로 확인

## 9. 알려진 한계와 후속 작업

| 항목 | 내용 |
| --- | --- |
| 같은 컨테이너 | 수집 API 와 브라우저가 같은 컨테이너·사용자로 돈다. 렌더러 샌드박스가 뚫리면 토큰 파일을 읽을 수 있다. 실제 링크를 여는 카테캠 2단계 전에 컨테이너를 나눈다 |
| EC2 미검증 | headless shell 과 현재 seccomp 프로필의 조합, Chromium 버전 차이(로컬 141, EC2 153)는 EC2 에서 확인해야 한다 |
| 가짜 페이지 인증서 | 운영에서는 브라우저가 인증서 오류를 무시하지 않는다. 가짜 페이지 서버에 정상 인증서가 필요하다 |
| 프록시 본문 | 프록시는 터널만 열고 본문은 보지 않는다. 허용 호스트 안에서 오가는 내용의 크기는 컨테이너 메모리 상한이 막는다 |
| 메인 서버 로그 | `main.py` 의 Shadow 비교 로그는 아직 `browser` 필드를 찍지 않는다 |
| 시간 예산 | 3.5초·6초는 설계 문서의 가설값이다. EC2 실측으로 조정한다 |
