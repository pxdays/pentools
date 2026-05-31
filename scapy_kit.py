#!/usr/bin/env python3
"""
Scapy Network Toolkit — Probe, poke, and discover.
Custom packet crafting for your own network.

Usage:
  python3 scapy_kit.py arp-scan              Discover all devices
  python3 scapy_kit.py port-knock <ip>         Probe hidden services
  python3 scapy_kit.py syn-scan <ip>           SYN scan all ports
  python3 scapy_kit.py decoy <ip>              Decoy scan (appears from random IPs)
  python3 scapy_kit.py dns-probe <ip>          Enumerate open DNS resolvers
  python3 scapy_kit.py identify <ip>           OS fingerprint + service detection
"""

import sys
import time
from datetime import datetime

try:
    from scapy.all import *
except ImportError:
    print("❌ Scapy not installed. Run: sudo pacman -S python-scapy")
    sys.exit(1)

# Config
IFACE = "wlan0"
NET = "192.168.0.0/24"
BROADCAST = "192.168.0.255"


def arp_scan():
    """ARP scan to find ALL live devices on the network."""
    print(f"\n  📡 ARP Scanning {NET}...\n")
    ans, _ = arping(NET, timeout=5, verbose=False)
    print(f"  {'IP':20s} {'MAC':20s} {'Vendor':30s}")
    print(f"  {'-' * 20} {'-' * 20} {'-' * 30}")
    for sent, rcv in ans:
        ip = rcv.psrc
        mac = rcv.hwsrc
        # Look up vendor from MAC prefix
        vendor = rcv.payload.name if hasattr(rcv.payload, "name") else ""
        print(f"  {ip:20s} {mac:20s} {vendor:30s}")


def syn_scan(ip: str):
    """SYN scan all 65535 ports fast."""
    print(f"\n  ⚡ Full SYN scan of {ip}...\n")

    ans, _ = sr(
        IP(dst=ip) / TCP(dport=(1, 65535), flags="S"),
        timeout=10,
        verbose=False,
        iface=IFACE,
    )

    open_ports = []
    for sent, rcv in ans:
        if rcv.haslayer(TCP) and rcv.getlayer(TCP).flags == 0x12:  # SYN-ACK
            port = rcv.getlayer(TCP).sport
            open_ports.append(port)
            # Send RST to close cleanly
            send(IP(dst=ip) / TCP(dport=port, flags="R"), verbose=False)

    if open_ports:
        print(f"  ✅ Found {len(open_ports)} open ports:")
        for p in sorted(open_ports):
            service = socket.getservbyport(p, "tcp") if p <= 49152 else "unknown"
            print(f"     {p:5d}/tcp  {service}")
    else:
        print(f"  🔒 No open ports found (firewall active)")


def port_knock(ip: str):
    """Port knocking style probe — check if any ports respond to unusual packets."""
    print(f"\n  🔑 Port knocking {ip}...\n")

    # Try different packet types on common ports
    for port in [22, 80, 443, 8080, 8009, 13579, 49152, 35539, 5550]:
        for flags, label in [("S", "SYN"), ("SA", "SYN-ACK"), ("A", "ACK")]:
            pkt = IP(dst=ip) / TCP(dport=port, flags=flags)
            ans, _ = sr(pkt, timeout=1, verbose=False, iface=IFACE)
            for _, rcv in ans:
                if rcv.haslayer(TCP):
                    flags = rcv.getlayer(TCP).flags
                    if flags == 0x12:  # SYN-ACK
                        print(f"     ✅ Port {port} responded to {label} — OPEN")
                    elif flags == 0x14:  # RST-ACK
                        pass  # Closed
            time.sleep(0.1)


def decoy_scan(ip: str):
    """Scan with spoofed source IPs — evades basic logging."""
    print(f"\n  🎭 Decoy scan of {ip} (spoofed from random IPs)...")

    # Generate random decoy IPs (from different subnets)
    decoys = [
        f"10.0.{random.randint(1, 254)}.{random.randint(1, 254)}" for _ in range(3)
    ]
    decoys.append(ip)  # Real target

    for port in [22, 23, 80, 443, 3389, 8080, 13579]:
        src = random.choice(decoys)
        pkt = IP(src=src, dst=ip) / TCP(dport=port, flags="S")
        send(pkt, verbose=False)
        time.sleep(0.1)
    print("  ✅ Decoy packets sent — check your router logs to see them")


def dns_probe(ip: str):
    """Check if device runs an open DNS resolver."""
    print(f"\n  🌐 Probing DNS on {ip}...")
    pkt = IP(dst=ip) / UDP(dport=53) / DNS(rd=1, qd=DNSQR(qname="test.com"))
    ans, _ = sr(pkt, timeout=2, verbose=False)
    for _, rcv in ans:
        if rcv.haslayer(DNS) and rcv.getlayer(DNS).ancount > 0:
            print(f"     ✅ OPEN DNS resolver at {ip}")
            return
    print(f"     ❌ No DNS service")


def identify(ip: str):
    """Identify device type via various probes."""
    print(f"\n  🕵️  Identifying {ip}...\n")

    # Check common service ports
    probes = {
        22: "SSH",
        80: "HTTP",
        443: "HTTPS",
        5000: "UPnP/AirPlay",
        8008: "HTTP-alt",
        8080: "HTTP-proxy",
        8443: "HTTPS-alt",
        13579: "SkyQ API",
        35539: "Amazon DMGR",
    }

    found = []
    for port, name in probes.items():
        pkt = IP(dst=ip) / TCP(dport=port, flags="S")
        ans, _ = sr(pkt, timeout=1, verbose=False)
        for _, rcv in ans:
            if rcv.haslayer(TCP) and rcv.getlayer(TCP).flags == 0x12:
                found.append(f"  {name:15s}  port {port}/tcp  OPEN")
                send(IP(dst=ip) / TCP(dport=port, flags="R"), verbose=False)

    if found:
        print("  Detected services:\n" + "\n".join(found))
    else:
        print("  🔒 No recognizable services. Likely:")
        print("     - Sleeping (try: python3 wol_scan.py --all)")
        print("     - Firewalled (Apple device, Ring, etc.)")
        print("     - IoT device with no open ports")

    # OS fingerprint via TTL
    pkt = IP(dst=ip) / ICMP()
    ans, _ = sr(pkt, timeout=2, verbose=False)
    for _, rcv in ans:
        ttl = rcv.getlayer(IP).ttl
        if ttl <= 64:
            print(f"  💻 OS hint: Linux/Unix (TTL={ttl})")
        elif ttl <= 128:
            print(f"  💻 OS hint: Windows (TTL={ttl})")
        else:
            print(f"  💻 OS hint: Solaris/AIX (TTL={ttl})")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    mode = sys.argv[1]
    target = sys.argv[2] if len(sys.argv) > 2 else None

    modes = {
        "arp-scan": lambda: arp_scan(),
        "syn-scan": lambda: syn_scan(target),
        "port-knock": lambda: port_knock(target),
        "decoy": lambda: decoy_scan(target),
        "dns-probe": lambda: dns_probe(target),
        "identify": lambda: identify(target),
    }

    if mode in modes:
        if mode != "arp-scan" and not target:
            print(f"❌ Usage: python3 scapy_kit.py {mode} <ip>")
            sys.exit(1)
        modes[mode]()
    else:
        print(f"❌ Unknown mode: {mode}")
        print(__doc__)
