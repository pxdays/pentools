#!/usr/bin/env python3
"""
Wake-on-LAN + Post-Wake Scanner
Your network device waker and scanner.

Usage:
  python3 wol_scan.py <device_name>    Wake a specific device
  python3 wol_scan.py --all            Wake ALL sleeping devices
  python3 wol_scan.py --list           List all devices and status
"""

import socket
import time
import subprocess
import sys

# Your network devices - edit these as needed
DEVICES = {
    "apple-tv": {
        "mac": "92:42:06:40:F6:1C",
        "ip": "192.168.0.240",
        "name": "Apple TV / HomePod",
    },
    "chromecast": {
        "mac": "5A:41:CC:C5:A6:EC",
        "ip": "192.168.0.96",
        "name": "Chromecast / Google TV",
    },
    "ring-1": {
        "mac": "5C:47:5E:8C:71:26",
        "ip": "192.168.0.108",
        "name": "Ring Doorbell",
    },
    "ring-2": {
        "mac": "54:E0:19:F3:A2:87",
        "ip": "192.168.0.177",
        "name": "Ring Camera (sleeping)",
    },
    "amazon": {
        "mac": "B0:8B:A8:92:39:6F",
        "ip": "192.168.0.178",
        "name": "Amazon Device",
    },
}


def wake(mac: str, broadcast: str = "192.168.0.255"):
    """Send Wake-on-LAN magic packet to MAC address."""
    mac_clean = mac.replace(":", "").replace("-", "")
    if len(mac_clean) != 12:
        return False

    magic = b"\xff" * 6 + bytes.fromhex(mac_clean) * 16

    try:
        for port in (9, 7):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(magic, (broadcast, port))
            sock.close()
        return True
    except Exception:
        return False


def scan(ip: str, label: str = ""):
    """Quick port scan on target IP."""
    tag = f"{label} ({ip})" if label else ip
    print(f"\n  🔍 Scanning {tag}...")

    try:
        r = subprocess.run(
            ["nmap", "-sV", "-F", "--min-rate=3000", ip],
            capture_output=True,
            text=True,
            timeout=30,
        )
        found = False
        for line in r.stdout.split("\n"):
            if "/tcp" in line and "open" in line:
                print(f"     ✅ {line.strip()}")
                found = True
        if not found:
            print(f"     🔒 No open ports")
        return r.stdout
    except subprocess.TimeoutExpired:
        print(f"     ⏱️  Timed out (still asleep)")
    except Exception as e:
        print(f"     ❌ Error: {e}")
    return ""


def list_all():
    """Show all devices with awake/asleep status."""
    print()
    print("  ╔══════════════════════════════════════════════╗")
    print("  ║           Network Device Status             ║")
    print("  ╚══════════════════════════════════════════════╝")
    for key, dev in DEVICES.items():
        up = (
            subprocess.run(
                ["ping", "-c1", "-W1", dev["ip"]], capture_output=True
            ).returncode
            == 0
        )
        icon = "✅ AWAKE" if up else "💤 SLEEPING"
        print(f"  [{key:12s}] {dev['name']:25s} {dev['ip']:15s} {icon}")


def wake_all():
    """Wake every sleeping device, wait, then scan all."""
    print("\n  🔥 Sending Wake-on-LAN to ALL sleeping devices...\n")
    for key, dev in DEVICES.items():
        print(f"  [{key}] {dev['name']} ({dev['mac']})", end="")
        if wake(dev["mac"]):
            print("  ✅ sent")
        else:
            print("  ❌ failed")
        time.sleep(0.3)

    print("\n  ⏳ Waiting 15 seconds for devices to boot...")
    time.sleep(15)

    print("\n  🔍 Scanning all devices now...")
    for key, dev in DEVICES.items():
        scan(dev["ip"], dev["name"])


if __name__ == "__main__":
    if "--list" in sys.argv:
        list_all()
        sys.exit(0)

    if "--all" in sys.argv:
        wake_all()
        sys.exit(0)

    if len(sys.argv) < 2:
        print(__doc__)
        print("  Devices:")
        for key in DEVICES:
            print(f"    {key:12s}  {DEVICES[key]['name']}")
        sys.exit(1)

    target = sys.argv[1].lower()

    if target in DEVICES:
        d = DEVICES[target]
        print(f"\n  🔥 Waking {d['name']} ({d['mac']})...")
        if wake(d["mac"]):
            print("     ✅ Magic packet sent!")
            print("  ⏳ Waiting 10s...")
            time.sleep(10)
            scan(d["ip"], d["name"])
        else:
            print("     ❌ Failed to send")
    else:
        # Treat arg as raw MAC
        ip = sys.argv[2] if len(sys.argv) > 2 else "192.168.0.255"
        print(f"\n  🔥 Sending WoL to {target} on {ip}...")
        if wake(target, ip):
            print("     ✅ Sent!")
        else:
            print("     ❌ Failed")
