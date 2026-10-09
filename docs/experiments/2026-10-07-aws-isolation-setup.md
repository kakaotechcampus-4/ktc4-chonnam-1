# AWS 격리환경 설정 기록

- 날짜: 2026-10-07
- 기준: [격리 환경 보안 설계](../isolation-security.md) §5.1·5.2·5.11·5.14·5.15
- 적용 범위: 현재 노트북의 SSH 접속과 기존 EC2의 오프라인 격리 실행환경
- 메인 서버: Render. Render 설정, 테스트 링크 목록, 수집 HTTP API는 이번 작업에서 보류했다.

## 접속 방법

저장소 루트의 PowerShell에서 실행한다. 와이파이가 바뀌어도 같은 명령을 사용한다.

```powershell
powershell -NoProfile -File .\infra\isolation\connect.ps1
```

스크립트는 현재 공인 IPv4를 조회하고, **기기별** SSH 규칙 `ktc-isolation-<기기>` 하나를 해당 IP `/32`로 **교체**한 다음 접속한다. 규칙이 없으면 만들고, 같은 기기의 이전 IP는 누적하지 않는다. 기기 이름은 `-Device`(기본값 `notebook`) 또는 환경변수 `KTC_ISOLATION_DEVICE`로 정한다. 두 번째 기기는 `-Device desktop`처럼 다른 이름을 써야 서로의 규칙을 덮어쓰지 않는다. 같은 와이파이의 기기들은 공인 IP를 공유하므로, 현재 IP가 이미 다른 규칙에 있으면 규칙을 바꾸지 않는다. AWS SSO 세션이 만료되면 `aws sso login --profile ktc-notebook`으로 로그인한 뒤 다시 실행한다.

규칙 갱신 후 서버 22번 포트에 닿지 않으면 SSH 전에 멈춘다. VPN이나 연결마다 나가는 주소가 달라지는 네트워크(모바일 핫스팟, 학교망 등)에서는 조회한 IP와 실제 접속 IP가 다를 수 있다. IP와 같은 접속 기기 정보는 저장소에 기록하지 않는다.

`ssh ktc-server`처럼 스크립트를 거치지 않는 접속은 규칙을 갱신하지 않는다. 네트워크가 바뀐 뒤에는 먼저 스크립트를 실행한다.

IP 갱신만 먼저 하려면 아래 명령을 사용한다. EC2 재부팅 후에도 현재 `ssh ktc-server` 별칭은 유효하다. EC2를 중지·시작해 공인 IP가 바뀌면 기본 접속 스크립트는 새 서버 IP를 조회해서 사용한다.

```powershell
powershell -NoProfile -File .\infra\isolation\connect.ps1 -UpdateOnly
ssh ktc-server
```

같은 와이파이의 여러 기기가 공인 IP를 공유할 수 있다. 기기 접근 제한은 IP 규칙과 노트북 전용 SSH 키를 함께 적용한다. 개인키는 노트북의 `~/.ssh/ktc_isolation_ed25519`에만 있으며, 저장소나 EC2에 복사하지 않았다. Windows 파일 접근 권한도 현재 사용자와 SYSTEM으로 제한했다. AWS 관리자 권한으로 서버 설정을 변경할 수 있는 사람까지 차단한다는 의미는 아니다.

## 실제 적용값

| 항목 | 결과 |
| --- | --- |
| 리전 / EC2 | `ap-northeast-2` / `i-0ddf82de2ba56da70`, `t3.medium` |
| 공인 / 사설 IP | `54.180.151.132` / `10.0.1.158` |
| VPC / 서브넷 | `vpc-095f7afbb4b2cdaaa` / `subnet-0559d4952b57b972a` |
| 연결된 SG | `sg-0b0dfc0677cdd60eb` 하나. 해당 EC2에만 연결된 것을 확인하고 변경 |
| SG 인바운드 | TCP 22, 현재 노트북 공인 IP `/32` 하나 |
| SG 아웃바운드 | 허용 규칙 없음. 기존 전체 IPv4 허용 규칙 제거 |
| 접속 기기 IP | 기기별 `/32`. 접속 시 조회·교체되는 값이라 기록하지 않음 |
| SSH 로그인 | `isolation-admin`, 노트북 전용 공개키 하나. 비밀번호·root·ubuntu 로그인 차단 |
| SSH 포워딩 | 로컬 포워딩의 목적지만 `127.0.0.1:*`, `localhost:*` 허용. 에이전트 포워딩 차단 |
| EC2 IAM 역할 | 기존 SSM용 인스턴스 프로파일 분리, 최종 조회 `null` |
| IMDS | `HttpEndpoint=disabled`, 변경 상태 `applied` |
| SSM 에이전트 | snap 서비스 `disabled / inactive` |
| OS / 커널 | Ubuntu 24.04.5 LTS / `7.0.0-1013-aws`, 패키지 갱신 후 재부팅 |
| Docker / Compose | Docker CE `29.8.2` / Compose `5.6.0` |
| 배포 경로 | `/opt/ktc-isolation`, 소유자 `root:root` |
| 로그 | Docker `local`, 파일당 10 MB, 최대 3개 |

호스트 방화벽은 IPv4의 새 외부 연결과 전달, DNS를 차단한다. SSH 응답, 루프백, 호스트의 DHCP 및 Amazon Time Sync(`169.254.169.123:123/UDP`)만 유지한다. IPv6는 루프백 외 통신을 차단했다. Docker의 전달 경로도 차단했다. `ktc-isolation-firewall.service`를 부팅 시 실행하도록 등록했고, 재부팅 후 규칙을 확인했다.

브라우저 컨테이너는 `network_mode: none`으로 실행한다. 현재는 외부 페이지를 열지 않는다. 호스트 서비스에 닿는 경로, 공개 API 포트, 호스트 경로·Docker 소켓 마운트가 없다.

| 컨테이너 설정 | 적용값 |
| --- | --- |
| 컨테이너 | `ktc-isolation-browser`, `linux/amd64`, `restart: unless-stopped` |
| 사용자 / 권한 | UID/GID `10001:10001`, `cap_drop: ALL`, `no-new-privileges` |
| 파일시스템 | 읽기 전용, `/tmp`만 256 MB tmpfs (`nosuid,nodev`) |
| 메모리 / CPU / PID | 1536 MB / 1.5 CPU / 256 |
| 공유 메모리 | 256 MB, 호스트 IPC 공유 없음 |
| 브라우저 | Playwright `1.63.0`, Chromium `153.0.8010.12`, `chromium_sandbox=True` |
| 검증 컨텍스트 | `accept_downloads=False`, `service_workers="block"`, 새 컨텍스트의 쿠키 분리 |

컨테이너는 실행환경 확인을 위해 대기한다. 수집기나 HTTP API를 실행하는 상태는 아니다. 수집 요청별 시간·크기·동시 실행 제한과 인증은 해당 서비스 구현 때 적용한다.

## 이미지와 seccomp 기록

베이스 이미지는 레지스트리 digest로 고정했고, 실행 이미지는 EC2의 정확한 로컬 Docker 이미지 ID를 `.env`에 기록해 실행한다. 실행 컨테이너의 이미지 ID와 기록이 일치함을 확인했다. 실행 이미지는 레지스트리에 게시하지 않았다.

```text
base=python@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
image_id=sha256:04abb097112c18b9db29db2bbae26e316a37d3aebb004d43d1ce1a2e3ddb6350
platform=linux/amd64
seccomp.json=49b877f02e33bf7e7157febd6d130d62c3d81c3029ab1d893fc2bf7a5701bb3f
Dockerfile=63cf12e849304f02a4123259f6badc72c1469e0b68032887f25777d12eb3d26e
verify.py=71aab1bd44341219cce8fb353496baca86204ebd3c8a3f1e80f5db93df94a0d2
```

승인 기록 원본은 EC2의 `/opt/ktc-isolation/image-record.txt`에 있다. [Playwright v1.63.0의 seccomp 프로필](https://github.com/microsoft/playwright/blob/v1.63.0/utils/docker/seccomp_profile.json)을 가져와 `chroot`의 `CAP_SYS_CHROOT` 조건만 제거했다. 원본 조건에서는 호스트 권한을 전부 제거한 Chromium이 자체 사용자 네임스페이스 안에서 하는 `chroot`도 seccomp에 막혀 종료됐다. 호스트 capability를 추가하지 않고 이 시스템 호출을 필터에서 허용했으며, 커널의 권한 검사와 Chromium 샌드박스는 유지했다. [Docker 권한·seccomp 설명](https://docs.docker.com/engine/containers/run/).

## 검증 결과

| 검사 | 결과 |
| --- | --- |
| 현재 IP 갱신 후 새 SSH 접속 | 통과. 실제 노트북 공인 IP가 세 번 바뀌는 동안 매번 규칙 교체·접속 확인 |
| 예전 IP 규칙이 남은 상황 재현 | SSH 규칙을 테스트 주소 `192.0.2.1/32`로 교체한 뒤 접속 스크립트 실행. 현재 IP `/32`로 갱신·접속 성공. 최종 SG에 현재 IP의 TCP 22 규칙 하나만 남고 아웃바운드 규칙이 없음을 확인 |
| 기존 키로 ubuntu 및 isolation-admin 접속 | 두 계정 모두 `Permission denied (publickey)` |
| IMDS·VPC 내부 주소·외부 HTTPS | 호스트와 컨테이너에서 접속 실패 |
| Amazon DNS / VPC DNS | 호스트에서 TCP·UDP 53 차단 확인 |
| 컨테이너 네트워크·마운트·자원 상한 | Docker inspect 검증 통과. 외부 인터페이스·포트·호스트 마운트 없음 |
| UID·capability·no-new-privileges·seccomp | 실행 중 프로세스에서 검증 통과 |
| 읽기 전용 루트 | 컨테이너 사용자가 소유한 디렉터리에 쓰기 시도, `EROFS` 확인 |
| Chromium 실행 / 샌드박스 | 오프라인 HTML 렌더링 성공. `chrome://sandbox`에서 Namespace·PID·Network·Seccomp-BPF 활성 확인 |
| 컨텍스트별 쿠키 | 이전 컨텍스트의 쿠키가 새 컨텍스트에 없음을 확인 |
| 두 번째 재부팅 후 유지 | 같은 SSH 서버 키로 접속, 방화벽·Docker 활성, 컨테이너 자동 복구 및 위 검증 재통과 |
| 서비스·스크립트 | 실패한 systemd 서비스 0개, `bash -n` 및 PowerShell 파싱 통과 |

브라우저 검증을 다시 실행하려면 다음 명령을 사용한다. 테스트 링크나 Render 설정이 필요하지 않다.

```powershell
powershell -NoProfile -File .\infra\isolation\connect.ps1 -Command 'sudo timeout 30 docker exec ktc-isolation-browser python /app/verify.py'
```

첫 재부팅에서는 IMDS를 끈 상태의 cloud-init이 `DataSourceNone`으로 초기화를 다시 수행해 SSH 서버 키를 재생성했다. AWS `get-console-output`의 서버 공개키와 대조한 뒤 노트북의 해당 호스트 항목만 갱신했다. 초기 부팅이 끝난 서버에서 `/etc/cloud/cloud-init.disabled`를 만들고, 기존 DHCP netplan 파일을 유지했다. 두 번째 재부팅에서 동일 서버 키·네트워크·접속을 확인했다. [cloud-init 비활성화 공식 안내](https://docs.cloud-init.io/en/latest/howto/disable_cloud_init.html).

## 이후 진행할 항목

1. 테스트 링크를 정하면 허용 호스트·고정 IP·포트를 승인하고, 컨테이너 네트워크와 SG·호스트 방화벽을 함께 변경한다. 허용 대상만 성공하고 나머지는 차단되는지 다시 검증한다.
2. 수집 HTTP 요청·응답 형식을 합의하고 API를 구현한다. HTTPS·토큰 인증, 요청별 컨텍스트와 제한, 응답 크기·스키마 검증을 적용한다.
3. Render 연결을 진행할 때 해당 서비스의 Outbound CIDR·인증서 신뢰·공유 토큰을 설정한다.
4. 실제 수집 운영을 위한 차단·오류·자원 초과 감시 기준과 경보를 정하고, EC2 폐기 후 새 인스턴스 재구축 시험을 수행한다. 이번 검증은 기존 EC2 재부팅과 컨테이너 재생성까지 수행했으며 새 EC2 생성 시험은 수행하지 않았다.

현재의 외부 통신 차단 상태에서는 `bootstrap.sh`의 패키지 설치나 `build.sh`의 이미지 다운로드가 성공하지 않는다. 갱신은 수집을 멈춘 관리 시간에 승인된 공급 경로를 임시 허용한 뒤 실행하고, 새 이미지 기록과 검증을 완료한 후 차단 상태로 복구한다. 초기 구성 스크립트는 새 EC2 재구축용이며 이번 작업에서는 기존 서버의 접속을 확인하면서 단계별로 적용했다. 침해 시에는 EC2를 폐기·재생성하는 원문 절차를 따른다.
