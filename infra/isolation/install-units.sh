#!/bin/bash
# 이미 돌아가는 격리 서버에 systemd 유닛만 설치한다. bootstrap.sh 는 sshd, apt 까지 바꾸고
# 잠긴 서버에서는 apt 가 바깥으로 못 나가 멈추므로, 재배포 때는 이 스크립트만 실행한다.
# 유닛을 쓰고 켜 두기만 한다. 실제로 띄우는 것은 build.sh 다.
set -euo pipefail
if [ "$(id -u)" != 0 ]; then
    echo "run as root: sudo bash install-units.sh" >&2
    exit 1
fi
cat > /etc/systemd/system/ktc-isolation-firewall.service <<'EOF'
[Unit]
Description=KTC isolation host firewall
After=network.target docker.service
Requires=docker.service

[Service]
Type=oneshot
ExecStart=/bin/bash /opt/ktc-isolation/lockdown.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
# compose.yaml 은 restart: "no" 다. 컨테이너는 방화벽이 성공한 뒤에만 이 유닛이 띄운다.
cat > /etc/systemd/system/ktc-isolation-collector.service <<'EOF'
[Unit]
Description=KTC isolation collector container
After=ktc-isolation-firewall.service docker.service
Requires=ktc-isolation-firewall.service docker.service
ConditionPathExists=/opt/ktc-isolation/.env

[Service]
WorkingDirectory=/opt/ktc-isolation
ExecStart=/usr/bin/docker compose -f compose.yaml -f compose.egress.yaml --env-file .env --env-file collector.env up --force-recreate --abort-on-container-exit
ExecStop=/usr/bin/docker compose -f compose.yaml -f compose.egress.yaml --env-file .env --env-file collector.env down
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/ktc-isolation-recreate.service <<'EOF'
[Unit]
Description=Recreate the KTC isolation collector container

[Service]
Type=oneshot
ExecStart=/usr/bin/systemctl restart ktc-isolation-collector
EOF
cat > /etc/systemd/system/ktc-isolation-recreate.timer <<'EOF'
[Unit]
Description=Daily KTC isolation collector recreation

[Timer]
OnCalendar=daily
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
EOF
cat > /etc/systemd/system/ktc-isolation-health.service <<'EOF'
[Unit]
Description=Restart the KTC isolation collector when unhealthy

[Service]
Type=oneshot
ExecStart=/bin/bash /opt/ktc-isolation/health-restart.sh
EOF
cat > /etc/systemd/system/ktc-isolation-health.timer <<'EOF'
[Unit]
Description=KTC isolation collector health check

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
# Start only after image installation and direct SSH verification.
systemctl enable ktc-isolation-firewall ktc-isolation-collector ktc-isolation-recreate.timer ktc-isolation-health.timer
