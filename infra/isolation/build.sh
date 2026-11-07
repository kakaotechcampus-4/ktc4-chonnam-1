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
# systemctl restart 는 compose 를 시작만 하고 돌아온다. 이름이 같은 옛 컨테이너에서 verify.py 를
# 돌리면 교체될 때 함께 죽어 137 로 끝나므로, ID 가 바뀐 새 컨테이너가 뜰 때까지 기다린다.
old_id=$(docker inspect --format '{{.Id}}' ktc-isolation-browser 2>/dev/null || true)
systemctl restart ktc-isolation-firewall
systemctl restart ktc-isolation-collector
systemctl start ktc-isolation-recreate.timer ktc-isolation-health.timer
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
    echo "새 컨테이너가 60초 안에 뜨지 않았다: sudo journalctl -u ktc-isolation-collector" >&2
    exit 1
fi
timeout 30 docker exec ktc-isolation-browser python /app/verify.py
