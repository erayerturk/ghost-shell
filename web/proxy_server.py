#!/usr/bin/env python3
"""
Transparent proxy server with multi-proxy rotation.
Supports round-robin, random, and sticky (per-domain) strategies.
"""
import socket
import select
import os
import json
import threading
import base64
import struct
import random
import hashlib

DATA_DIR = '/opt/shadowplug/data'
LISTEN_PORT = 12345

SO_ORIGINAL_DST = 80

# ─── Rotation state ─────────────────────────────

_lock = threading.Lock()
_rr_index = 0
_sticky_map = {}  # domain -> proxy index


def load_config():
    try:
        with open(os.path.join(DATA_DIR, 'proxy.json'), 'r') as f:
            return json.load(f)
    except:
        return {}


def pick_proxy(config, domain=None):
    """Pick a proxy from the list based on rotation mode."""
    global _rr_index, _sticky_map

    proxies = config.get('proxies', [])
    if not proxies:
        # Legacy single-proxy fallback
        if config.get('host'):
            return config
        return None

    if len(proxies) == 1:
        return proxies[0]

    mode = config.get('mode', 'round-robin')

    with _lock:
        if mode == 'random':
            return random.choice(proxies)

        elif mode == 'sticky' and domain:
            if domain in _sticky_map:
                idx = _sticky_map[domain]
                if idx < len(proxies):
                    return proxies[idx]
            idx = hash(domain) % len(proxies)
            _sticky_map[domain] = idx
            return proxies[idx]

        else:  # round-robin (default)
            idx = _rr_index % len(proxies)
            _rr_index += 1
            return proxies[idx]


def get_original_dst(sock):
    try:
        dst = sock.getsockopt(socket.SOL_IP, SO_ORIGINAL_DST, 16)
        port = struct.unpack('>H', dst[2:4])[0]
        ip = socket.inet_ntoa(dst[4:8])
        return ip, port
    except:
        return None, None


def get_sni(data):
    try:
        if len(data) < 43 or data[0] != 0x16 or data[5] != 0x01:
            return None
        pos = 44 + data[43]
        pos += 2 + struct.unpack('>H', data[pos:pos+2])[0]
        pos += 1 + data[pos]
        if pos + 2 > len(data):
            return None
        ext_total = struct.unpack('>H', data[pos:pos+2])[0]
        pos += 2
        end = pos + ext_total
        while pos + 4 <= end:
            etype, elen = struct.unpack('>HH', data[pos:pos+4])
            pos += 4
            if etype == 0:
                nlen = struct.unpack('>H', data[pos+3:pos+5])[0]
                return data[pos+5:pos+5+nlen].decode()
            pos += elen
    except:
        pass
    return None


def open_upstream(proxy, host, port):
    proto = proxy.get('protocol', 'http')
    phost = proxy['host']
    pport = int(proxy['port'])
    user = proxy.get('username', '')
    pw = proxy.get('password', '')

    if proto == 'socks5':
        import socks
        s = socks.socksocket()
        s.set_proxy(socks.SOCKS5, phost, pport, True, user, pw)
        s.settimeout(15)
        s.connect((host, int(port)))
        s.settimeout(None)
        return s

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(15)
    s.connect((phost, pport))

    creds = base64.b64encode(f"{user}:{pw}".encode()).decode()
    req = (
        f"CONNECT {host}:{port} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Proxy-Authorization: Basic {creds}\r\n"
        f"Proxy-Connection: Keep-Alive\r\n"
        f"\r\n"
    )
    s.sendall(req.encode())

    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = s.recv(4096)
        if not chunk:
            raise Exception("upstream closed during CONNECT")
        resp += chunk

    first_line = resp.split(b"\r\n")[0].lower()
    if b"200" not in first_line:
        s.close()
        raise Exception(f"upstream rejected: {first_line.decode(errors='replace')}")

    s.settimeout(None)
    return s


def relay(a, b):
    pair = [a, b]
    try:
        while True:
            r, _, x = select.select(pair, [], pair, 60)
            if x or not r:
                break
            for s in r:
                data = s.recv(32768)
                if not data:
                    return
                (b if s is a else a).sendall(data)
    except:
        pass
    finally:
        try: a.close()
        except: pass
        try: b.close()
        except: pass


def handle(client):
    try:
        orig_ip, orig_port = get_original_dst(client)
        if not orig_ip:
            print("[tproxy] SO_ORIGINAL_DST failed!", flush=True)
            client.close()
            return

        config = load_config()

        client.settimeout(0.5)
        try:
            first_packet = client.recv(32768)
        except:
            first_packet = b""
        client.settimeout(None)

        connect_host = orig_ip
        if first_packet:
            sni = get_sni(first_packet)
            if sni:
                connect_host = sni

        # Pick proxy based on rotation strategy
        proxy = pick_proxy(config, domain=connect_host)
        if not proxy or not proxy.get('host'):
            print(f"[tproxy] no proxy available for {connect_host}", flush=True)
            client.close()
            return

        proxy_name = proxy.get('name', proxy['host'])
        print(f"[tproxy] {connect_host}:{orig_port} via {proxy_name}", flush=True)

        upstream = open_upstream(proxy, connect_host, orig_port)
        upstream.sendall(first_packet)
        relay(client, upstream)

    except Exception as e:
        print(f"[tproxy] ERROR: {e}", flush=True)
        try:
            client.close()
        except:
            pass


def main():
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_NOFILE, (65535, 65535))
    except:
        pass

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('0.0.0.0', LISTEN_PORT))
    srv.listen(4096)
    print(f"[tproxy] Transparent proxy on :{LISTEN_PORT}", flush=True)

    while True:
        try:
            client, addr = srv.accept()
            t = threading.Thread(target=handle, args=(client,), daemon=True)
            t.start()
        except KeyboardInterrupt:
            break
        except:
            pass


if __name__ == '__main__':
    main()
