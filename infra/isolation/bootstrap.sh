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
apt-get install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
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
systemctl daemon-reload
# Start only after image installation and direct SSH verification.
systemctl enable ktc-isolation-firewall
docker version --format '{{.Server.Version}}'
