#!/bin/bash
set -euo pipefail
notebook_public_key=${1:?Pass the notebook SSH public key file}
ssh-keygen -lf "$notebook_public_key" >/dev/null
id isolation-admin >/dev/null 2>&1 || useradd --create-home --shell /bin/bash isolation-admin
install -d -m 700 -o isolation-admin -g isolation-admin /home/isolation-admin/.ssh
install -m 600 -o isolation-admin -g isolation-admin "$notebook_public_key" /home/isolation-admin/.ssh/authorized_keys
printf '%s\n' 'isolation-admin ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/isolation-admin
chmod 440 /etc/sudoers.d/isolation-admin
visudo -cf /etc/sudoers.d/isolation-admin
install -d -m 700 /root/ktc-isolation-backup
if [ ! -e /root/ktc-isolation-backup/sshd_config ]; then
    cp -a /etc/ssh/sshd_config /root/ktc-isolation-backup/sshd_config
fi
cat > /etc/ssh/sshd_config.d/00-ktc-isolation.conf <<'EOF'
AllowUsers isolation-admin
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
AllowAgentForwarding no
X11Forwarding no
AllowTcpForwarding local
PermitOpen 127.0.0.1:* localhost:*
GatewayPorts no
ClientAliveInterval 60
ClientAliveCountMax 3
MaxAuthTries 3
EOF
sshd -t
systemctl reload ssh
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
docker_key=$(gpg --show-keys --with-colons /etc/apt/keyrings/docker.asc | awk -F: '$1 == "fpr" {print $10; exit}')
if [ "$docker_key" != 9DC858229FC7DD38854AE2D88D81803C0EBFCD88 ]; then
    rm -f /etc/apt/keyrings/docker.asc
    echo "unexpected Docker apt key fingerprint: $docker_key" >&2
    exit 1
fi
chmod a+r /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
install -d -m 755 /etc/docker
if [ ! -e /etc/docker/daemon.json ]; then
    printf '%s\n' '{"log-driver":"local","log-opts":{"max-size":"10m","max-file":"3"}}' > /etc/docker/daemon.json
    systemctl restart docker
fi
systemctl enable docker
node_name=$(hostname)
grep -Fq "$node_name" /etc/hosts || printf '127.0.1.1 %s\n' "$node_name" >> /etc/hosts
# First boot and its persisted network configuration must be complete before IMDS is disabled.
touch /etc/cloud/cloud-init.disabled
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
docker version --format '{{.Server.Version}}'
