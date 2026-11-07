#!/bin/bash
set -euo pipefail
cd /opt/ktc-isolation
# 기반 이미지는 Dockerfile 의 digest 로 고정한다. 잠긴 호스트는 바깥으로 못 나가므로
# 다른 곳에서 빌드한 이미지를 IMAGE_TAR=/path/image.tar 로 넘기면 빌드 대신 docker load 한다.
base_image=$(sed -n 's/^ARG BASE_IMAGE=//p' Dockerfile)
if [ -n "${IMAGE_TAR:-}" ]; then
    loaded=$(docker load -i "$IMAGE_TAR" | awk -F': ' '/^Loaded image/ {ref=$2} END {print ref}')
    docker tag "$loaded" ktc-isolation:approved
else
    docker build --platform linux/amd64 -t ktc-isolation:approved .
fi
image_id=$(docker image inspect ktc-isolation:approved --format '{{.Id}}')
printf 'ISOLATION_IMAGE=%s\n' "$image_id" > .env
printf 'base=%s\nimage_id=%s\nplatform=linux/amd64\n' "$base_image" "$image_id" > image-record.txt
sha256sum seccomp.json Dockerfile verify.py scanner/*.py collector/*.py >> image-record.txt
# egress-hosts 를 검증해 compose.egress.yaml 과 egress-ips.txt 를 만들고, 방화벽에 새 IP 를 반영한다.
set -a
. ./collector.env
set +a
python3 render-egress.py /opt/ktc-isolation
sha256sum compose.egress.yaml egress-ips.txt >> image-record.txt
# 수집기를 먼저 멈추면 방화벽 재시작이 수집기로 번지지 않아, 컨테이너는 start 에서 한 번만 만들어진다.
# compose 는 systemctl 이 돌아온 뒤에 컨테이너를 만들고 손으로 띄운 옛 컨테이너도 그때 지우므로,
# 이름이 같은 옛 컨테이너에서 verify.py 를 돌리지 않게 ID 가 바뀐 컨테이너를 기다린다.
old_id=$(docker inspect --format '{{.Id}}' ktc-isolation-browser 2>/dev/null || true)
systemctl stop ktc-isolation-collector
systemctl restart ktc-isolation-firewall
systemctl start ktc-isolation-collector
started=
for _ in $(seq 60); do
    state=$(docker inspect --format '{{.Id}} {{.State.Running}}' ktc-isolation-browser 2>/dev/null || true)
    if [ -n "$state" ] && [ "${state% *}" != "$old_id" ] && [ "${state##* }" = true ]; then
        started=1
        break
    fi
    sleep 1
done
if [ -z "$started" ]; then
    echo "새 컨테이너가 60초 안에 뜨지 않아 verify.py 를 건너뛰었다." >&2
    echo "이전 컨테이너: ${old_id:-없음} / 마지막으로 본 상태: ${state:-컨테이너 없음}" >&2
    echo "sudo journalctl -u ktc-isolation-collector 로 원인을 고친 뒤 build.sh 를 다시 돌린다." >&2
    exit 1
fi
timeout 30 docker exec ktc-isolation-browser python /app/verify.py
# 처음 켜는 상태 확인 타이머는 바로 돌아, 옛 컨테이너가 unhealthy 면 검사 중에 컨테이너를 다시 만든다.
# 그래서 검사 뒤에 켠다. 이미 켜져 있는 서버에서는 아무 일도 하지 않는다.
systemctl start ktc-isolation-recreate.timer ktc-isolation-health.timer
