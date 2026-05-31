# pentools

Network stress testing toolkit. For testing YOUR OWN systems only.

## Quick Start (Codespace)

```bash
python3 stress.py <target-ip> http-flood 60
python3 stress.py <target-ip> syn-flood 60
```

## Methods

- **http-flood** - HTTP GET flood (30 threads)
- **syn-flood** - TCP SYN flood (20 threads, needs scapy)
- **udp-flood** - UDP packet flood

## Install on fresh machine

```bash
sudo apt update && sudo apt install -y python3-pip nmap
sudo pip3 install scapy flask
```

## Legal

Only use against systems you own or have written permission to test.
