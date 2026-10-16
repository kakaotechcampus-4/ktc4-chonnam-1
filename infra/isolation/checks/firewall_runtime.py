"""Exercise generated rules in three disposable Linux namespaces, never host chains."""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from network_config import firewall_rules


def run(*args):
    return subprocess.run(args, text=True, capture_output=True, check=True).stdout


SERVER = r'''
import socket,threading,time
def tcp(port):
 s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
 s.bind(('0.0.0.0',port));s.listen()
 def serve():
  while True:
   c,_=s.accept()
   try:c.sendall(b'K')
   except OSError:pass
   c.close()
 threading.Thread(target=serve,daemon=True).start()
for p in PORTS:tcp(p)
if DNS:
 def start_udp(address):
  s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind((address,53))
  def udp():
   while True:
    data,addr=s.recvfrom(32);s.sendto(data,addr)
  threading.Thread(target=udp,daemon=True).start()
 for address in ('1.1.1.1','8.8.4.4'):start_udp(address)
print('ready',flush=True)
while True:time.sleep(60)
'''

PROBE = r'''
import json,socket,sys
host,port,source,proto=sys.argv[1:];port=int(port)
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM if proto=='udp' else socket.SOCK_STREAM)
s.settimeout(.5)
try:
 if source!='-':s.bind((source,0))
 s.connect((host,port))
 if proto=='udp':s.send(b'K')
 ok=s.recv(1)==b'K'
except OSError:ok=False
finally:s.close()
print(json.dumps(ok))
'''


def main():
    assert os.geteuid() == 0, "Run with sudo on the validation host"
    prefix = "ktc-check-" + str(os.getpid())
    client, router, origin = [prefix + suffix for suffix in ("-c", "-r", "-o")]
    owned, servers = [], []

    def ns(name, *args):
        return run("ip", "netns", "exec", name, *args)

    def probe(name, host, port, source="-", proto="tcp"):
        return json.loads(ns(name, "python3", "-c", PROBE, host, str(port), source, proto))

    try:
        existing = run("ip", "netns", "list")
        assert prefix not in existing
        for name in (client, router, origin):
            run("ip", "netns", "add", name)
            owned.append(name)
            ns(name, "ip", "link", "set", "lo", "up")
        for left, right, a, b in ((client, router, "client0", "ktc-collector"),
                                  (router, origin, "origin0", "server0")):
            # Both ends move immediately; names exist only inside test namespaces.
            x, y = "kc" + str(os.getpid()), "kr" + str(os.getpid())
            run("ip", "link", "add", x, "type", "veth", "peer", "name", y)
            run("ip", "link", "set", x, "netns", left)
            run("ip", "link", "set", y, "netns", right)
            ns(left, "ip", "link", "set", x, "name", a)
            ns(right, "ip", "link", "set", y, "name", b)
            ns(left, "ip", "link", "set", a, "up")
            ns(right, "ip", "link", "set", b, "up")
        for name, address, interface in ((client, "172.30.250.2/29", "client0"),
                (router, "172.30.250.1/29", "ktc-collector"),
                (router, "93.184.216.1/24", "origin0"), (origin, "93.184.216.34/24", "server0")):
            ns(name, "ip", "addr", "add", address, "dev", interface)
        ns(client, "ip", "route", "add", "default", "via", "172.30.250.1")
        ns(origin, "ip", "route", "add", "172.30.250.0/29", "via", "93.184.216.1")
        aliases = ("10.0.1.1", "169.254.169.254", "3.39.166.84", "8.8.8.8", "8.8.4.4", "1.1.1.1")
        for address in aliases:
            ns(origin, "ip", "addr", "add", address + "/32", "dev", "server0")
            ns(router, "ip", "route", "add", address + "/32", "via", "93.184.216.34")
        ns(router, "sysctl", "-w", "net.ipv4.ip_forward=1")
        for name, ports, dns in ((origin, [443, 80, 53], True), (client, [8443], False)):
            code = "PORTS=" + repr(ports) + ";DNS=" + repr(dns) + "\n" + SERVER
            process = subprocess.Popen(["ip", "netns", "exec", name, "python3", "-u", "-c", code],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            servers.append(process)
            assert process.stdout.readline().strip() == "ready", "fixture did not start"

        # Prove endpoints and reverse routes work before policy denial tests.
        for address in ("93.184.216.34", *aliases):
            assert probe(client, address, 443), ("baseline", address)
        assert probe(client, "1.1.1.1", 53, proto="udp")
        assert probe(client, "8.8.4.4", 53, proto="udp")
        assert probe(origin, "172.30.250.2", 8443, source="8.8.8.8")
        assert probe(origin, "172.30.250.2", 8443, source="8.8.4.4")
        env = {"COLLECTOR_NETWORK_MODE": "public", "COLLECTOR_PROTECTED_HOSTS": "service.example",
            "COLLECTOR_PROTECTED_IPS": "3.39.166.84,8.8.8.8", "COLLECTOR_DNS_SERVER": "1.1.1.1",
            "RENDER_CIDRS": "8.8.8.0/24"}
        rules = firewall_rules(Path("/unused"), env)
        ns(router, "iptables", "-N", "KTC-FORWARD")
        for line in rules.splitlines():
            ns(router, *shlex.split(line))
        ns(router, "iptables", "-A", "KTC-FORWARD", "-j", "DROP")
        ns(router, "iptables", "-A", "FORWARD", "-j", "KTC-FORWARD")
        # Equivalent to Docker forwarding after the restrictive user chain returns.
        ns(router, "iptables", "-A", "FORWARD", "-j", "ACCEPT")
        checks = {}

        def expect(label, actual, wanted):
            checks[label] = actual == wanted
            assert actual == wanted, (label, actual, wanted)

        expect("public_https", probe(client, "93.184.216.34", 443), True)
        for address in ("10.0.1.1", "169.254.169.254", "3.39.166.84", "8.8.8.8"):
            expect("blocked_" + address, probe(client, address, 443), False)
        expect("dns_tcp", probe(client, "1.1.1.1", 53), True)
        expect("dns_udp", probe(client, "1.1.1.1", 53, proto="udp"), True)
        expect("other_dns_tcp", probe(client, "8.8.4.4", 53), False)
        expect("other_dns_udp", probe(client, "8.8.4.4", 53, proto="udp"), False)
        expect("http80", probe(client, "93.184.216.34", 80), False)
        expect("protected_render_api_reply", probe(origin, "172.30.250.2", 8443, source="8.8.8.8"), True)
        expect("unauthorized_api", probe(origin, "172.30.250.2", 8443, source="8.8.4.4"), False)
        print(json.dumps({"kernel_forward_checks": checks, "namespace_only": True}))
        print(ns(router, "iptables", "-nvx", "-L", "KTC-FORWARD"))
    finally:
        for process in servers:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for name in reversed(owned):
            run("ip", "netns", "del", name)
        print(json.dumps({"test_namespaces_removed": owned}))


if __name__ == "__main__":
    main()
