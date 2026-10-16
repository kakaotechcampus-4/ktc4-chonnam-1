#!/bin/bash
set -euo pipefail
cd /opt/ktc-isolation
docker pull --platform linux/amd64 python:3.12-slim-bookworm
base_image=$(docker image inspect python:3.12-slim-bookworm --format '{{index .RepoDigests 0}}')
docker build --platform linux/amd64 --build-arg "BASE_IMAGE=$base_image" -t ktc-isolation:approved .
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
systemctl restart ktc-isolation-firewall
docker compose -f compose.yaml -f compose.egress.yaml --env-file .env --env-file collector.env \
    up -d --force-recreate
timeout 30 docker exec ktc-isolation-browser python /app/verify.py
