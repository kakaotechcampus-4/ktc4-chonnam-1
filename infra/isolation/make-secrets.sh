#!/bin/bash
# EC2 에서 한 번 실행한다. 토큰과 자체 서명 인증서를 만들고, 메인 서버에 넣을 값을 출력한다.
# 침해가 의심되면 다시 실행해 둘 다 바꾼다 (docs/isolation-security.md §5.12).
set -euo pipefail
public_ip=${1:?Pass the isolation server public IP}
cd /opt/ktc-isolation
install -d -m 750 -o root -g 10001 secrets
umask 027
openssl rand -hex 32 > secrets/collect_token
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 90 \
    -subj "/CN=ktc-isolation" -addext "subjectAltName=IP:${public_ip}" \
    -keyout secrets/collector.key -out secrets/collector.crt 2>/dev/null
chown root:10001 secrets/*
chmod 440 secrets/*
echo "Render ISOLATION_API_URL=https://${public_ip}:8443"
echo "Render ISOLATION_API_TOKEN= (secrets/collect_token 내용, 화면에 남기지 말고 복사)"
echo "Render ISOLATION_API_CA_CERT= 아래 인증서 전체"
cat secrets/collector.crt
