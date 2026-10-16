# EC2 공개 HTTPS 수집 적용

2026-10-17 KST, 사용자의 설정 적용 승인 후 EC2 수집기를 공개 모드로 전환했다.
Render 코드·카카오 설정과 PR #100의 병합 상태는 변경하지 않았다.

## 적용 상태

| 항목 | 적용 값 |
| --- | --- |
| 제품 소스 | `147b9c2d4fc6ff123c7214d6055be0167433eed9` |
| 운영 이미지 | `sha256:43da709970f4d877aa122374f752979ef5713ea6aa2e92c63f9cfcd2b5f0e733` |
| 기반 런타임 | 기존 검증 이미지 `sha256:850a1712d1e569bc84b0e40c01e5d0cd32fe27ad7fb67cf93548a8968c071e03` |
| 수집·브라우저 모드 | `public` / `auto` |
| DNS | `1.1.1.1`, TCP·UDP 53만 허용 |
| HTTPS 아웃바운드 | IPv4 TCP/443, 내부·예약·보호 주소는 호스트 방화벽에서 먼저 차단 |
| 보호 IP | `3.39.166.84`, `216.24.57.16`, `216.24.57.18` |
| 보호 호스트 | Render 서비스·격리 API·격리 EC2 공인 DNS 이름 |
| 인바운드 | 기존 Render CIDR의 8443과 EIC SSH 규칙 유지 |

기존 승인 런타임을 로컬 Docker 이미지로 재사용하고 새 `collector/`, `scanner/`, `verify.py`만
COPY해 EC2에서 오프라인 빌드했다. 의존성 다운로드를 위해 호스트 외부 통신을 열지 않았다.
새 이미지의 Python 패키지와 Chromium은 앞선 실제 EC2 검증과 동일하다.

호스트 방화벽을 먼저 적용하고, SG의 지정 DNS를 연 뒤 별도 컨테이너에서 DNS 조회를 검증했다.
해당 DNS가 돌려준 실제 Render IP가 보호 목록과 일치함을 확인한 다음 공개 443을 허용했다.
별도 후보 컨테이너의 공개 수집과 직접 소켓 차단 검사를 통과한 후 운영 컨테이너를 교체했다.

운영 `extra_hosts` 고정값은 제거됐고 실제 Docker DNS를 사용한다. IPv6는 컨테이너 sysctl로
비활성화했다. 토큰과 TLS 인증서는 기존 값을 유지했다.

## 실제 검증

운영 컨테이너 안에서 실제 HTTPS API/8443에 요청했다. 인증서는
`ktc4-cn1-api.duckdns.org` 호스트명으로 검증했고, 기존 토큰은 파일에서만 읽었다.
응답 본문이나 토큰 원문을 기록하지 않았다.

| 주소 | 원본 응답 | HTML | 운영 auto 브라우저 | API 왕복 |
| --- | --- | --- | --- | --- |
| `https://example.com/` | 200 | 577자 | 실행, 실패 없음 | 892ms |
| `https://www.iana.org/` | 200 | 6251자 | 불필요하여 생략 | 324ms |
| 팀 GitHub Pages `col-01` | 200 | 309자 | 불필요하여 생략 | 209ms |

후보 컨테이너에서 브라우저를 `always`로 강제한 시험도 세 주소 모두 실제 HTTPX·Chromium
수집에 성공했다. 해당 시간은 각각 740ms, 1158ms, 1108ms였다. 고정 hosts 항목을 쓰지 않았다.
실제 LLM·카카오 분석 속도를 측정한 결과는 아니다.

- 지정 DNS에서 일반 사이트와 Render·격리 API의 실제 A 레코드 조회 성공.
- 인증 없는 요청은 401, 메타데이터·자체 IP·Render 호스트·HTTP 주소는 `blocked_address`.
- 직접 소켓으로 사설 IP·호스트 게이트웨이·보호 IP 3개 접근 실패.
- 지정하지 않은 DNS 53 및 HTTP 80 접근 실패, IPv6 비활성화 확인.
- 새 운영 컨테이너의 `/app/verify.py` 통과: Chromium 153, namespace·Seccomp-BPF 샌드박스,
  읽기 전용 파일 시스템, 권한 제거 및 비밀값 환경 분리.
- 새 SSH 세션과 방화벽 서비스 정상 상태 확인.

검증은 수집 API의 실제 TLS 루프백 경로를 사용했다. Render에서 들어오는 실제 요청과
카카오 콜백·휴대폰 표시는 아직 확인하지 않았다. Render는 기존 BE 버전이므로
챗봇 전체를 urlscan 없는 흐름으로 바꾸려면 PR #100 병합과 Render 배포가 남아 있다.

## 복구

기존 서비스 파일·비밀값·이미지 정보와 IPv4/IPv6 규칙은 root 전용
`/opt/ktc-isolation-backups/public-20261017-147b9c2`에 보관했다.
전환 전 15분 자동 복구 예약을 걸었고 정상 검증 후 해제했다.
예약 복구는 EC2 수집기·호스트 정책만 복원하며 AWS SG는 별도다.

기존 허용 목록 모드로 되돌릴 때 먼저 실행한다.

```powershell
ssh ktc-isolation sudo -n python3 /opt/ktc-isolation-backups/public-20261017-147b9c2/rollback.py
```

그 뒤 이번에 추가한 SG 규칙 세 개만 제거한다. 기존 GitHub 허용 규칙은 보존했다.

```powershell
aws ec2 revoke-security-group-egress --profile ktc-notebook --region ap-northeast-2 --group-id sg-0b0dfc0677cdd60eb --security-group-rule-ids sgr-0cf0383bdff7b0022 sgr-0e2dafe9560663098 sgr-08f7289ffeeb30e2f
```

복구 스크립트는 준비했지만 운영 전환 성공 후 실제 복구를 수행하지 않았다.
적용 스크립트·로그는 `/home/isolation-admin/ktc-public-rollout-20261017-147b9c2`에 보관했다.
보호 IP는 서비스 DNS가 바뀌면 갱신해야 한다.
