#!/bin/bash
set -e
echo "installing stress toolkit..."
sudo apt update -qq && sudo apt install -y -qq python3-pip nmap curl > /dev/null 2>&1
sudo pip3 install scapy -q > /dev/null 2>&1

# grab latest from github
curl -sL https://raw.githubusercontent.com/pxdays/pentools/main/stress.py -o ~/pentools/stress.py 2>/dev/null
chmod +x ~/pentools/stress.py

echo ""
echo "  ready."
echo "  python3 ~/pentools/stress.py <target> syn 30"
echo "  python3 ~/pentools/stress.py <target> http 60"
echo "  python3 ~/pentools/stress.py <target> all 30"
echo "  python3 ~/pentools/stress.py <target> verify"
