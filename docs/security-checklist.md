# 보안 체크리스트

2026-11-07 코드 기준으로 확인했다. 체크한 항목은 근거 위치를 함께 적는다.

- [ ] 프롬프트 인젝션 — 문자 본문은 공격자가 쓴 텍스트다. 지시로 해석되지 않게 분리
- [x] 메인 서버는 의심 URL에 접속하지 않는다. 접속은 격리 환경만 한다 (ADR 0002).
      HEAD 도 DNS·TCP·TLS 를 타므로 방어가 아니다 — 내부 주소에 그대로 도달한다
      → `main.py` `run_analysis` 는 링크마다 `scanner/isolation_client.py` 의 `collect_url_isolated`(격리 `/collect` 호출)만 부른다.
        접속 코드 `scanner/fetch.py` 는 격리 컨테이너에 복사돼 그 안에서만 실행된다 (`infra/isolation/Dockerfile`)
- [x] 리다이렉트 추적은 격리 환경 안에서 한다. 홉 수·시간 상한을 둔다
      → `infra/isolation/collector/app.py` 가 `scanner/fetch.py` 의 `collect_url` 을 부른다. 최대 5홉(`MAX_REDIRECTS`),
        HTTP 2초(시간 초과 시 5초 1회 재시도)·브라우저 6초·정리 여유 3초, 메인 쪽 호출 상한 17초(`ISOLATION_TIMEOUT_SECONDS`)
- [x] 격리 환경의 egress 제한 — 사설 대역·링크로컬·루프백을 네트워크 레벨에서 차단.
      애플리케이션 코드의 IP 검사에 의존하지 않는다
      → `infra/isolation/network_config.py` 가 iptables 규칙을 만들고 `infra/isolation/lockdown.sh` 가 적용한다.
        allowlist 모드는 `egress-ips.txt` 의 공인 IP:443 만, public 모드는 `BLOCKED_CIDRS`(사설·링크로컬·루프백 등)와
        `COLLECTOR_PROTECTED_IPS`(Render 등 보호 서비스)를 DROP 한 뒤 443 만 연다. 컨테이너 → 호스트 새 연결도 막는다.
        검증·적용이 실패하면 전달을 모두 막는다(fail-closed). IPv6 는 루프백 외 전부 DROP.
        실제 배포 값(`collector.env`·`egress-ips.txt`)은 저장소에서 확인할 수 없다
- [ ] 격리 → 메인은 고정 스키마의 구조화된 사실만. 원본 HTML·응답 헤더 원문 금지
      → 응답은 `scanner/collect_response.py` 가 고정 필드로 파싱하고 응답 헤더 원문은 받지 않는다(`content_type` 만).
        다만 페이지 HTML 은 256 KiB 상한으로 그대로 넘어온다(`scanner/models.py` `CollectResult.html`). 미충족
- [ ] 사용자 문자 원문 로깅 정책 (개인정보 포함 가능)
- [ ] LLM 출력이 그대로 카드에 나가기 전 가드
- [ ] `.env` 커밋 여부 CI 검사 — `.gitignore` 는 `.env`·`.env.*` 를 막지만 이를 검사하는 워크플로는 없다
