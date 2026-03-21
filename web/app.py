#!/usr/bin/env python3
"""ShadowPlug — Web UI Backend"""

import subprocess
import json
import os
import re
import time
import random
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__, static_folder='static', static_url_path='')

DATA_DIR = '/opt/shadowplug/data'
os.makedirs(DATA_DIR, exist_ok=True)

# ─── Helpers ────────────────────────────────────────────────

def run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception as e:
        return str(e)

def read_file(path):
    try:
        with open(path, 'r') as f:
            return f.read()
    except:
        return ''

def write_file(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(content)

def read_json(path, default=None):
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except:
        return default or {}

def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)

# ─── Proxy Constants ────────────────────────────────────────

PROXY_FILE = os.path.join(DATA_DIR, 'proxy.json')
PROXY_SERVER = '/opt/shadowplug/web/proxy_server.py'
TPROXY_PORT = 12345

# ─── Proxy Helpers (redsocks-free TPROXY architecture) ──────

def start_proxy_server():
    """Start the transparent proxy server (direct iptables REDIRECT, no redsocks)."""
    run("ps | grep proxy_server.py | grep -v grep | awk '{print $1}' | xargs kill -9 2>/dev/null")
    time.sleep(0.5)
    run(f"python3 {PROXY_SERVER} >> /var/log/proxy-server.log 2>&1 &")
    time.sleep(1)
    return True

def stop_proxy_server():
    run("ps | grep proxy_server.py | grep -v grep | awk '{print $1}' | xargs kill -9 2>/dev/null")

def apply_proxy_iptables(proxy_ip):
    """Apply iptables REDIRECT for USB traffic directly to proxy_server.py.
    Also blocks QUIC (UDP 443) and sets up MASQUERADE for DNS."""
    # Create TPROXY chain
    run("iptables -t nat -N TPROXY 2>/dev/null")
    run("iptables -t nat -F TPROXY")
    for net in ['0.0.0.0/8', '10.0.0.0/8', '127.0.0.0/8', '169.254.0.0/16',
                '172.16.0.0/12', '192.168.0.0/16', '224.0.0.0/4']:
        run(f"iptables -t nat -A TPROXY -d {net} -j RETURN")
    
    proxy_config = read_json(PROXY_FILE, {})
    if proxy_config.get('bypass_dev_ports', False):
        run("iptables -t nat -A TPROXY -p tcp -m multiport --dports 22,9418 -j RETURN")
        
    run(f"iptables -t nat -A TPROXY -d {proxy_ip} -j RETURN")
    run(f"iptables -t nat -A TPROXY -p tcp -j REDIRECT --to-ports {TPROXY_PORT}")
    # Apply to USB interfaces
    for iface in ['usb0', 'usb1']:
        run(f"iptables -t nat -D PREROUTING -i {iface} -p tcp -j TPROXY 2>/dev/null")
        run(f"iptables -t nat -A PREROUTING -i {iface} -p tcp -j TPROXY")
    # Block QUIC (UDP 443) to force Chrome to use TCP
    for iface in ['usb0', 'usb1']:
        run(f"iptables -D FORWARD -i {iface} -p udp --dport 443 -j DROP 2>/dev/null")
        run(f"iptables -I FORWARD -i {iface} -p udp --dport 443 -j DROP")
    # MASQUERADE for non-TCP traffic (DNS UDP etc)
    wan_iface = run("ip route | grep default | grep -v wg0 | awk '{print $5}' | head -1") or 'phy0-sta0'
    run(f"iptables -t nat -D POSTROUTING -s 192.168.7.0/24 -o {wan_iface} -j MASQUERADE 2>/dev/null")
    run(f"iptables -t nat -A POSTROUTING -s 192.168.7.0/24 -o {wan_iface} -j MASQUERADE")

def remove_proxy_iptables():
    """Remove all transparent proxy iptables rules."""
    for iface in ['usb0', 'usb1']:
        run(f"iptables -t nat -D PREROUTING -i {iface} -p tcp -j TPROXY 2>/dev/null")
        run(f"iptables -D FORWARD -i {iface} -p udp --dport 443 -j DROP 2>/dev/null")
    run("iptables -t nat -F TPROXY 2>/dev/null")
    run("iptables -t nat -X TPROXY 2>/dev/null")
    # Also clean legacy redsocks rules
    for iface in ['usb0', 'usb1']:
        run(f"iptables -t nat -D PREROUTING -i {iface} -p tcp -j REDSOCKS 2>/dev/null")
    run("iptables -t nat -F REDSOCKS 2>/dev/null")
    run("iptables -t nat -X REDSOCKS 2>/dev/null")

def get_proxy_ip():
    """Get public IP through the proxy (explicit curl --proxy, safe)."""
    config = read_json(PROXY_FILE, {})
    # Multi-proxy format
    if config.get('proxies') and len(config['proxies']) > 0:
        p = config['proxies'][0]
        proto = p.get('protocol', 'http')
        h, port = p['host'], p['port']
        u, pw = p.get('username', ''), p.get('password', '')
    # Legacy format
    elif config.get('host') and config.get('port'):
        proto = config.get('protocol', 'socks5')
        h, port = config['host'], config['port']
        u, pw = config.get('username', ''), config.get('password', '')
    else:
        return None
    proxy_url = f"{proto}://{u}:{pw}@{h}:{port}" if u and pw else f"{proto}://{h}:{port}"
    return run(f"curl -s --max-time 8 --proxy '{proxy_url}' http://api.ipify.org 2>/dev/null", timeout=12)

# ─── Startup: Restore proxy if it was enabled ───────────────

def restore_proxy_on_startup():
    config = read_json(PROXY_FILE, {})
    if not config.get('enabled'):
        return
    # Multi-proxy format
    if config.get('proxies') and len(config['proxies']) > 0:
        start_proxy_server()
        apply_proxy_iptables(config['proxies'][0]['host'])
        app.logger.info(f"Proxy restored on startup ({len(config['proxies'])} proxies)")
    # Legacy single-proxy format
    elif config.get('host') and config.get('port'):
        start_proxy_server()
        apply_proxy_iptables(config['host'])
        app.logger.info("Proxy restored on startup (legacy format)")

restore_proxy_on_startup()


# ─── Static ─────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

# ─── Status ─────────────────────────────────────────────────

@app.route('/api/status')
def status():
    # Get direct public IP
    direct_ip = run("curl -s --max-time 5 http://api.ipify.org 2>/dev/null") or \
                run("wget -qO- http://api.ipify.org 2>/dev/null") or 'unknown'
    wg_output = run("wg show 2>/dev/null")
    vpn_connected = 'latest handshake' in wg_output
    proxy_running = bool(run("ps | grep proxy_server.py | grep -v grep"))
    proxy_config = read_json(PROXY_FILE, {})
    proxy_enabled = proxy_config.get('enabled', False) and proxy_running

    # Determine active mode and displayed IP
    if proxy_enabled:
        mode = 'proxy'
        proxy_ip = get_proxy_ip()
        public_ip = proxy_ip if proxy_ip else direct_ip
    elif vpn_connected:
        mode = 'vpn'
        public_ip = direct_ip
    else:
        mode = 'direct'
        public_ip = direct_ip

    tx, rx = '0 B', '0 B' # Initialize tx, rx for all cases

    # VPN traffic stats
    if vpn_connected:
        for line in wg_output.split('\n'):
            if 'transfer' in line:
                parts = line.split(':',1)[1].strip().split(',')
                if len(parts) == 2:
                    rx = parts[0].strip().replace('received','').strip()
                    tx = parts[1].strip().replace('sent','').strip()

    usb_status = 'disconnected'
    usb_iface = None
    for iface in ['usb0', 'usb1']:
        carrier = run(f"cat /sys/class/net/{iface}/carrier 2>/dev/null")
        if carrier == '1':
            usb_status = 'connected'
            usb_iface = iface
            break

    # Traffic stats: use USB interface stats (total throughput) if USB is connected and not VPN
    def fmt_bytes(b):
        if b < 1024: return f"{b} B"
        if b < 1024*1024: return f"{b/1024:.2f} KiB"
        if b < 1024*1024*1024: return f"{b/(1024*1024):.2f} MiB"
        return f"{b/(1024*1024*1024):.2f} GiB"

    if usb_iface:
        down = int(run(f"cat /sys/class/net/{usb_iface}/statistics/tx_bytes 2>/dev/null") or '0')
        up = int(run(f"cat /sys/class/net/{usb_iface}/statistics/rx_bytes 2>/dev/null") or '0')
        tx = fmt_bytes(down)
        rx = fmt_bytes(up)

    uptime = run("uptime | sed 's/.*up //' | sed 's/,.*//'").strip()

    return jsonify({
        'public_ip': public_ip,
        'vpn_connected': vpn_connected,
        'proxy_running': proxy_running,
        'mode': mode,
        'vpn_tx': tx,
        'vpn_rx': rx,
        'usb_status': usb_status,
        'uptime': uptime,
    })

# ─── VPN ────────────────────────────────────────────────────

VPN_PROFILES_FILE = os.path.join(DATA_DIR, 'vpn_profiles.json')

@app.route('/api/vpn', methods=['GET'])
def vpn_get():
    endpoint = run("uci get network.@wireguard_wg0[0].endpoint_host 2>/dev/null")
    endpoint_port = run("uci get network.@wireguard_wg0[0].endpoint_port 2>/dev/null") or '51820'
    private_key = run("uci get network.wg0.private_key 2>/dev/null")
    peer_public_key = run("uci get network.@wireguard_wg0[0].public_key 2>/dev/null")
    addresses = run("uci get network.wg0.addresses 2>/dev/null")
    allowed_ips = run("uci get network.@wireguard_wg0[0].allowed_ips 2>/dev/null")
    persistent_keepalive = run("uci get network.@wireguard_wg0[0].persistent_keepalive 2>/dev/null") or '25'

    return jsonify({
        'endpoint': endpoint,
        'endpoint_port': endpoint_port,
        'private_key': private_key,
        'peer_public_key': peer_public_key,
        'address': addresses,
        'allowed_ips': allowed_ips,
        'persistent_keepalive': persistent_keepalive,
    })

@app.route('/api/vpn', methods=['POST'])
def vpn_set():
    data = request.json
    cmds = []
    if 'endpoint' in data:
        cmds.append(f"uci set network.@wireguard_wg0[0].endpoint_host='{data['endpoint']}'")
    if 'endpoint_port' in data:
        cmds.append(f"uci set network.@wireguard_wg0[0].endpoint_port='{data['endpoint_port']}'")
    if 'private_key' in data:
        cmds.append(f"uci set network.wg0.private_key='{data['private_key']}'")
    if 'peer_public_key' in data:
        cmds.append(f"uci set network.@wireguard_wg0[0].public_key='{data['peer_public_key']}'")
    if 'address' in data:
        cmds.append(f"uci set network.wg0.addresses='{data['address']}'")
    if 'allowed_ips' in data:
        cmds.append(f"uci set network.@wireguard_wg0[0].allowed_ips='{data['allowed_ips']}'")
    if 'persistent_keepalive' in data:
        cmds.append(f"uci set network.@wireguard_wg0[0].persistent_keepalive='{data['persistent_keepalive']}'")
    cmds.append("uci commit network")
    cmds.append("/etc/init.d/network restart")
    for cmd in cmds:
        run(cmd)

    # Auto-disable proxy when VPN is configured
    proxy_config = read_json(os.path.join(DATA_DIR, 'proxy.json'), {})
    if proxy_config.get('enabled'):
        remove_proxy_iptables()
        stop_proxy_server()
        proxy_config['enabled'] = False
        write_json(os.path.join(DATA_DIR, 'proxy.json'), proxy_config)

    return jsonify({'success': True, 'message': 'VPN configuration updated. Proxy disabled.'})

@app.route('/api/vpn/test', methods=['POST'])
def vpn_test():
    gateway = run("uci get network.wg0.addresses 2>/dev/null").replace('/24','').replace('/32','')
    if gateway:
        parts = gateway.rsplit('.', 1)
        gw = parts[0] + '.1'
    else:
        gw = '10.7.0.1'
    result = run(f"ping -c 2 -W 3 {gw} 2>&1")
    success = '0% packet loss' in result
    public_ip = run("wget -qO- http://api.ipify.org 2>/dev/null")
    return jsonify({'success': success, 'gateway': gw, 'ping_result': result, 'public_ip': public_ip})

@app.route('/api/vpn/generate-keys', methods=['POST'])
def vpn_generate_keys():
    private_key = run("wg genkey")
    public_key = run(f"echo '{private_key}' | wg pubkey")
    return jsonify({'private_key': private_key, 'public_key': public_key})

@app.route('/api/vpn/toggle', methods=['POST'])
def vpn_toggle():
    """Toggle VPN on/off."""
    data = request.json or {}
    action = data.get('action', 'toggle')
    wg_output = run("wg show wg0 2>/dev/null")
    is_up = bool(wg_output and 'interface' in wg_output)

    if action == 'toggle':
        action = 'down' if is_up else 'up'

    if action == 'up':
        run("ifup wg0 2>/dev/null")
        return jsonify({'success': True, 'message': 'VPN started', 'vpn_connected': True})
    else:
        run("ifdown wg0 2>/dev/null")
        return jsonify({'success': True, 'message': 'VPN stopped', 'vpn_connected': False})

# ─── Built-in Default VPN ───────────────────────────────────

LICENSE_FILE = '/opt/shadowplug/data/license.json'
LOCAL_LICENSE_FILE = os.path.expanduser('~/.shadowplug_license.json')
ACTIVE_LICENSE_FILE = LICENSE_FILE if os.path.exists(LICENSE_FILE) else LOCAL_LICENSE_FILE

@app.route('/api/license/status', methods=['GET'])
def license_status():
    """Check if the device has a valid license to show Default VPN."""
    lic = read_json(ACTIVE_LICENSE_FILE, {})
    if lic.get('activated') and lic.get('key'):
        return jsonify({'valid': True, 'license_key': lic['key']})
    return jsonify({'valid': False})

@app.route('/api/vpn/default', methods=['POST'])
def vpn_default_activate():
    """Activate the built-in ShadowPlug default VPN via API."""
    import urllib.request
    import ssl
    
    lic = read_json(ACTIVE_LICENSE_FILE, {})
    if not lic.get('activated') or not lic.get('key'):
        return jsonify({'success': False, 'message': 'Active license required'})
        
    license_key = lic['key']
    
    # Generate new device keys
    private_key = run("wg genkey")
    public_key = run(f"echo '{private_key}' | wg pubkey")
    
    # Register peer on shadowplugpay server
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        data = json.dumps({
            'license_key': license_key,
            'public_key': public_key
        }).encode('utf-8')
        
        req = urllib.request.Request(
            'https://pay.shadowplug.com/api/vpn/register',
            data=data,
            headers={
                'Content-Type': 'application/json',
                'User-Agent': 'ShadowPlug-Device/1.0'
            },
            method='POST'
        )
        
        with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
            result = json.loads(resp.read().decode())
            
        if not result.get('success'):
            return jsonify({'success': False, 'message': result.get('error', 'Registration failed')})
            
        assigned_ip = result['assigned_ip']
        endpoint = result['endpoint']
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Server connection failed: {str(e)}'})

    # Server parameters from API response (no hardcoded values)
    server_pubkey = result.get('server_pubkey', '')
    endpoint_host = result.get('endpoint', '')
    endpoint_port = result.get('endpoint_port', '51820')
    address_cidr = f"{assigned_ip}/32"
    
    if not server_pubkey or not endpoint_host:
        return jsonify({'success': False, 'message': 'Server did not return complete VPN configuration.'})
    
    cmds = [
        f"uci set network.@wireguard_wg0[0].endpoint_host='{endpoint_host}'",
        f"uci set network.@wireguard_wg0[0].endpoint_port='{endpoint_port}'",
        f"uci set network.wg0.private_key='{private_key}'",
        f"uci set network.@wireguard_wg0[0].public_key='{server_pubkey}'",
        f"uci set network.wg0.addresses='{address_cidr}'",
        f"uci set network.@wireguard_wg0[0].allowed_ips='0.0.0.0/0'",
        f"uci set network.@wireguard_wg0[0].persistent_keepalive='25'",
        "uci set network.wg0.dns='1.1.1.1 1.0.0.1'",
        "uci commit network",
        "uci add_list firewall.@zone[1].network='wg0' || true",
        "uci commit firewall",
        "/etc/init.d/network restart",
        "/etc/init.d/firewall restart"
    ]
    
    for cmd in cmds:
        run(cmd)

    # Auto-disable proxy since VPN routing handles everything now
    proxy_config = read_json(os.path.join(DATA_DIR, 'proxy.json'), {})
    if proxy_config.get('enabled'):
        remove_proxy_iptables()
        stop_proxy_server()
        proxy_config['enabled'] = False
        write_json(os.path.join(DATA_DIR, 'proxy.json'), proxy_config)

    return jsonify({'success': True, 'message': 'Built-in VPN connected!'})

@app.route('/api/vpn/default_disconnect', methods=['POST'])
def vpn_default_disconnect():
    cmds = [
        "ifdown wg0",
        "uci set network.@wireguard_wg0[0].endpoint_host=''", 
        "uci commit network",
        "uci del_list firewall.@zone[1].network='wg0' || true",
        "uci commit firewall",
        "/etc/init.d/network restart",
        "/etc/init.d/firewall restart"
    ]
    for cmd in cmds:
        run(cmd)
    return jsonify({'success': True, 'message': 'Built-in VPN disconnected.'})


# ─── VPN Profiles ───────────────────────────────────────────

@app.route('/api/vpn/profiles', methods=['GET'])
def vpn_profiles_get():
    profiles = read_json(VPN_PROFILES_FILE, {'profiles': [], 'active': ''})
    return jsonify(profiles)

@app.route('/api/vpn/profiles', methods=['POST'])
def vpn_profiles_save():
    data = request.json
    name = data.get('name', '')
    if not name:
        return jsonify({'success': False, 'message': 'Name required'})
    profiles = read_json(VPN_PROFILES_FILE, {'profiles': [], 'active': ''})
    profile = {
        'name': name,
        'endpoint': data.get('endpoint', ''),
        'endpoint_port': data.get('endpoint_port', '51820'),
        'private_key': data.get('private_key', ''),
        'peer_public_key': data.get('peer_public_key', ''),
        'address': data.get('address', ''),
        'allowed_ips': data.get('allowed_ips', '0.0.0.0/0'),
    }
    # Update or add
    existing = [i for i, p in enumerate(profiles['profiles']) if p['name'] == name]
    if existing:
        profiles['profiles'][existing[0]] = profile
    else:
        profiles['profiles'].append(profile)
    write_json(VPN_PROFILES_FILE, profiles)
    return jsonify({'success': True, 'message': f'Profile "{name}" saved.'})

@app.route('/api/vpn/profiles/activate', methods=['POST'])
def vpn_profiles_activate():
    data = request.json
    name = data.get('name', '')
    profiles = read_json(VPN_PROFILES_FILE, {'profiles': [], 'active': ''})
    profile = next((p for p in profiles['profiles'] if p['name'] == name), None)
    if not profile:
        return jsonify({'success': False, 'message': 'Profile not found'})
    # Apply profile
    cmds = [
        f"uci set network.wg0.endpoint_host='{profile['endpoint']}'",
        f"uci set network.wg0.endpoint_port='{profile['endpoint_port']}'",
        f"uci set network.wg0.private_key='{profile['private_key']}'",
        f"uci set network.@wireguard_wg0[0].public_key='{profile['peer_public_key']}'",
        f"uci set network.wg0.addresses='{profile['address']}'",
        f"uci set network.@wireguard_wg0[0].allowed_ips='{profile['allowed_ips']}'",
        "uci commit network",
        "/etc/init.d/network restart",
    ]
    for cmd in cmds:
        run(cmd)
    profiles['active'] = name
    write_json(VPN_PROFILES_FILE, profiles)
    return jsonify({'success': True, 'message': f'Switched to "{name}".'})

@app.route('/api/vpn/profiles/delete', methods=['POST'])
def vpn_profiles_delete():
    data = request.json
    name = data.get('name', '')
    profiles = read_json(VPN_PROFILES_FILE, {'profiles': [], 'active': ''})
    profiles['profiles'] = [p for p in profiles['profiles'] if p['name'] != name]
    if profiles['active'] == name:
        profiles['active'] = ''
    write_json(VPN_PROFILES_FILE, profiles)
    return jsonify({'success': True, 'message': f'Profile "{name}" deleted.'})

# ─── Ad Blocking ────────────────────────────────────────────

BLOCKLIST_FILE = os.path.join(DATA_DIR, 'blocklist.conf')
CUSTOM_BLOCKS_FILE = os.path.join(DATA_DIR, 'custom_blocks.json')

# Restore blocking on startup
if os.path.exists(BLOCKLIST_FILE):
    run("mkdir -p /tmp/dnsmasq.d")
    run(f"cp {BLOCKLIST_FILE} /tmp/dnsmasq.d/blocklist.conf")
    run("/etc/init.d/dnsmasq restart 2>/dev/null")

BLOCKLISTS = {
    'stevenblack': 'https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts',
    'adaway': 'https://adaway.org/hosts.txt',
    'peterlowe': 'https://pgl.yoyo.org/adservers/serverlist.php?hostformat=hosts&showintro=0&mimetype=plaintext',
    'malware': 'https://malware-filter.gitlab.io/malware-filter/urlhaus-filter-hosts-online.txt',
}

def ensure_dnsmasq_confdir():
    """Ensure dnsmasq is configured to read /tmp/dnsmasq.d/ directory."""
    has_confdir = run("uci get dhcp.@dnsmasq[0].confdir 2>/dev/null")
    if has_confdir != '/tmp/dnsmasq.d':
        run("uci set dhcp.@dnsmasq[0].confdir='/tmp/dnsmasq.d'")
        run("uci commit dhcp")

@app.route('/api/blocking', methods=['GET'])
def blocking_get():
    enabled = os.path.exists('/tmp/dnsmasq.d/blocklist.conf')
    custom = read_json(CUSTOM_BLOCKS_FILE, {'domains': []})
    count = 0
    if os.path.exists(BLOCKLIST_FILE):
        count = int(run(f"wc -l < {BLOCKLIST_FILE} 2>/dev/null") or '0')
    return jsonify({
        'enabled': enabled,
        'blocked_count': count,
        'custom_domains': custom.get('domains', []),
        'lists': list(BLOCKLISTS.keys()),
    })

@app.route('/api/blocking', methods=['POST'])
def blocking_set():
    data = request.json
    action = data.get('action', '')

    if action == 'enable':
        # Ensure dnsmasq reads our config directory
        run("mkdir -p /tmp/dnsmasq.d")
        ensure_dnsmasq_confdir()

        # Download and merge blocklists
        all_domains = set()
        for name, url in BLOCKLISTS.items():
            raw = run(f"wget -qO- '{url}' 2>/dev/null")
            for line in raw.split('\n'):
                line = line.strip()
                if line.startswith('#') or not line:
                    continue
                if line.startswith('0.0.0.0 ') or line.startswith('127.0.0.1 '):
                    parts = line.split()
                    if len(parts) >= 2:
                        domain = parts[1].strip()
                        if domain and domain not in ('localhost', 'localhost.localdomain', '0.0.0.0') and '.' in domain:
                            all_domains.add(domain)
        # Convert to dnsmasq format containing both IPv4 and IPv6 null routes
        conf_lines = []
        for d in sorted(all_domains):
            conf_lines.append(f"address=/{d}/0.0.0.0")
            conf_lines.append(f"address=/{d}/::")
            
        write_file(BLOCKLIST_FILE, '\n'.join(conf_lines) + '\n')
        run(f"cp {BLOCKLIST_FILE} /tmp/dnsmasq.d/blocklist.conf")
        
        # Add custom domains
        custom = read_json(CUSTOM_BLOCKS_FILE, {'domains': []})
        if custom.get('domains'):
            custom_lines = []
            for d in custom['domains']:
                custom_lines.append(f"address=/{d}/0.0.0.0")
                custom_lines.append(f"address=/{d}/::")
            run(f"echo '{chr(10).join(custom_lines)}' >> /tmp/dnsmasq.d/blocklist.conf")
        run("/etc/init.d/dnsmasq restart")
        
        # Enforce Transparent DNS Hijacking
        run("uci set firewall.force_dns=redirect")
        run("uci set firewall.force_dns.name='Force_DNS'")
        run("uci set firewall.force_dns.src='lan'")
        run("uci set firewall.force_dns.proto='tcp udp'")
        run("uci set firewall.force_dns.src_dport='53'")
        run("uci set firewall.force_dns.dest_port='53'")
        run("uci set firewall.force_dns.target='DNAT'")
        run("uci commit firewall")
        run("/etc/init.d/firewall restart")
        
        return jsonify({'success': True, 'message': f'Blocking enabled. {len(all_domains)} domains blocked.'})

    elif action == 'disable':
        run("rm -f /tmp/dnsmasq.d/blocklist.conf")
        run("/etc/init.d/dnsmasq restart")
        
        # Remove Transparent DNS Hijacking
        run("uci delete firewall.force_dns 2>/dev/null")
        run("uci commit firewall")
        run("/etc/init.d/firewall restart")
        
        return jsonify({'success': True, 'message': 'Blocking disabled.'})

    elif action == 'add_domain':
        domain = data.get('domain', '').strip()
        if domain:
            custom = read_json(CUSTOM_BLOCKS_FILE, {'domains': []})
            if domain not in custom['domains']:
                custom['domains'].append(domain)
                write_json(CUSTOM_BLOCKS_FILE, custom)
            # Also add to active blocklist
            run(f"echo 'address=/{domain}/0.0.0.0' >> /tmp/dnsmasq.d/blocklist.conf 2>/dev/null")
            run(f"echo 'address=/{domain}/::' >> /tmp/dnsmasq.d/blocklist.conf 2>/dev/null")
            run("/etc/init.d/dnsmasq restart")
        return jsonify({'success': True, 'message': f'"{domain}" blocked.'})

    elif action == 'remove_domain':
        domain = data.get('domain', '').strip()
        custom = read_json(CUSTOM_BLOCKS_FILE, {'domains': []})
        custom['domains'] = [d for d in custom['domains'] if d != domain]
        write_json(CUSTOM_BLOCKS_FILE, custom)
        return jsonify({'success': True, 'message': f'"{domain}" removed.'})

    return jsonify({'success': False, 'message': 'Unknown action.'})

# ─── Split Tunneling ────────────────────────────────────────

SPLIT_FILE = os.path.join(DATA_DIR, 'split_tunnel.json')

@app.route('/api/split', methods=['GET'])
def split_get():
    data = read_json(SPLIT_FILE, {'enabled': False, 'bypass': []})
    return jsonify(data)

@app.route('/api/split', methods=['POST'])
def split_set():
    data = request.json
    action = data.get('action', '')
    config = read_json(SPLIT_FILE, {'enabled': False, 'bypass': []})

    if action == 'add':
        entry = data.get('entry', '').strip()
        if entry and entry not in config['bypass']:
            config['bypass'].append(entry)
            # Apply: route this IP/CIDR outside VPN
            default_gw = run("ip route | grep default | grep -v wg0 | awk '{print $3}' | head -1")
            if default_gw:
                run(f"ip route add {entry} via {default_gw} 2>/dev/null")
    elif action == 'remove':
        entry = data.get('entry', '').strip()
        config['bypass'] = [b for b in config['bypass'] if b != entry]
        run(f"ip route del {entry} 2>/dev/null")
    elif action == 'enable':
        config['enabled'] = True
        default_gw = run("ip route | grep default | grep -v wg0 | awk '{print $3}' | head -1")
        if default_gw:
            for entry in config['bypass']:
                run(f"ip route add {entry} via {default_gw} 2>/dev/null")
    elif action == 'disable':
        config['enabled'] = False
        for entry in config['bypass']:
            run(f"ip route del {entry} 2>/dev/null")

    write_json(SPLIT_FILE, config)
    return jsonify({'success': True, 'message': 'Split tunnel updated.'})

# ─── WiFi MAC Randomization ─────────────────────────────────

MAC_FILE = os.path.join(DATA_DIR, 'custom_mac.txt')

def get_wifi_iface():
    """Find the active WiFi interface name."""
    for iface in ['phy0-sta0', 'wlan0', 'wlan1']:
        if os.path.exists(f'/sys/class/net/{iface}'):
            return iface
    return None

@app.route('/api/mac', methods=['GET'])
def mac_get():
    wifi_iface = get_wifi_iface()
    wifi_mac = 'N/A'
    if wifi_iface:
        wifi_mac = run(f"cat /sys/class/net/{wifi_iface}/address 2>/dev/null") or 'N/A'
    saved = read_file(MAC_FILE).strip()
    return jsonify({'wifi_iface': wifi_iface or 'unknown', 'wifi_mac': wifi_mac, 'saved': saved})

@app.route('/api/mac/randomize', methods=['POST'])
def mac_randomize():
    wifi_iface = get_wifi_iface()
    if not wifi_iface:
        return jsonify({'success': False, 'message': 'No WiFi interface found'})

    # Generate random MAC (locally administered, unicast)
    mac = [0x02, random.randint(0,255), random.randint(0,255),
           random.randint(0,255), random.randint(0,255), random.randint(0,255)]
    mac_str = ':'.join(f'{b:02x}' for b in mac)

    # Apply: bring interface down, change MAC, bring back up
    run(f"ip link set {wifi_iface} down 2>/dev/null")
    result = run(f"ip link set {wifi_iface} address {mac_str} 2>&1")
    run(f"ip link set {wifi_iface} up 2>/dev/null")

    # Reconnect WiFi
    run("wifi up 2>/dev/null &")

    # Save for persistence
    write_file(MAC_FILE, mac_str + '\n')

    if 'error' in result.lower() or 'cannot' in result.lower():
        return jsonify({'success': False, 'mac': mac_str,
                        'message': f'Failed to set MAC: {result}'})

    return jsonify({'success': True, 'mac': mac_str,
                    'message': f'WiFi MAC changed to {mac_str} — reconnecting...'})

# ─── WiFi Management ────────────────────────────────────────

WIFI_PROFILES_FILE = os.path.join(DATA_DIR, 'wifi_profiles.json')

def load_wifi_profiles():
    return read_json(WIFI_PROFILES_FILE, {'profiles': [], 'auto_connect': True})

def save_wifi_profiles(data):
    write_json(WIFI_PROFILES_FILE, data)

@app.route('/api/wifi', methods=['GET'])
def wifi_get():
    """Current WiFi status."""
    ssid = run("uci get wireless.@wifi-iface[0].ssid 2>/dev/null") or ''
    key = run("uci get wireless.@wifi-iface[0].key 2>/dev/null") or ''
    encryption = run("uci get wireless.@wifi-iface[0].encryption 2>/dev/null") or 'none'
    wifi_iface = get_wifi_iface()
    connected = False
    signal = ''
    ip_addr = ''
    if wifi_iface:
        carrier = run(f"cat /sys/class/net/{wifi_iface}/carrier 2>/dev/null")
        connected = carrier == '1'
        if connected:
            # Use iw for accurate signal (iwinfo reports 0 dBm on brcmfmac)
            signal = run(f"iw dev {wifi_iface} station dump 2>/dev/null | grep signal: | head -1 | awk '{{print $2, $3}}'") or ''
            if not signal or signal.startswith('0 '):
                # Fallback: try Link Quality from iwinfo
                lq = run(f"iwinfo {wifi_iface} info 2>/dev/null | grep 'Link Quality' | awk '{{print $2}}'") or ''
                if '/' in lq:
                    quality = int(lq.split('/')[0]) * 100 // int(lq.split('/')[1])
                    signal = str(-100 + quality) + ' dBm'
            ip_addr = run(f"ip addr show {wifi_iface} 2>/dev/null | grep 'inet ' | awk '{{print $2}}'") or ''
    internet = run("ping -c 1 -W 2 8.8.8.8 >/dev/null 2>&1 && echo ok") == 'ok'
    profiles = load_wifi_profiles()
    return jsonify({
        'ssid': ssid,
        'connected': connected,
        'signal': signal,
        'ip': ip_addr,
        'internet': internet,
        'encryption': encryption,
        'auto_connect': profiles.get('auto_connect', True),
        'profiles': [{'ssid': p['ssid'], 'encryption': p.get('encryption', 'psk2')} for p in profiles.get('profiles', [])],
    })

@app.route('/api/wifi/scan', methods=['POST'])
def wifi_scan():
    """Scan for nearby WiFi networks using iw dev (full channel scan)."""
    wifi_iface = get_wifi_iface() or 'phy0-sta0'
    raw = run(f"iw dev {wifi_iface} scan 2>/dev/null", timeout=15)
    if not raw:
        raw = run("iw dev $(iw dev | grep Interface | awk '{print $2}' | head -1) scan 2>/dev/null", timeout=15)
    networks = []
    current_net = {}
    if raw:
        for line in raw.split('\n'):
            line_s = line.strip()
            if line.startswith('BSS '):
                if current_net.get('ssid'):
                    networks.append(current_net)
                current_net = {'ssid': '', 'signal': '', 'encryption': 'none', 'channel': ''}
            elif line_s.startswith('SSID:'):
                current_net['ssid'] = line_s.split('SSID:', 1)[1].strip()
            elif line_s.startswith('signal:'):
                sig = line_s.split('signal:', 1)[1].strip().split(' ')[0]
                current_net['signal'] = sig + ' dBm'
            elif 'DS Parameter set: channel' in line_s:
                current_net['channel'] = line_s.split('channel')[1].strip()
            elif 'WPA' in line_s or 'RSN' in line_s:
                current_net['encryption'] = 'WPA'
        if current_net.get('ssid'):
            networks.append(current_net)
    # Sort by signal (strongest first)
    def sig_key(n):
        try: return int(n.get('signal','0').replace(' dBm',''))
        except: return -999
    networks.sort(key=sig_key, reverse=True)
    # Remove duplicates
    seen = set()
    unique = []
    for n in networks:
        if n['ssid'] and n['ssid'] not in seen:
            seen.add(n['ssid'])
            unique.append(n)
    # Mark known profiles
    profiles = load_wifi_profiles()
    known_ssids = {p['ssid'] for p in profiles.get('profiles', [])}
    for n in unique:
        n['known'] = n['ssid'] in known_ssids
    return jsonify({'networks': unique})

@app.route('/api/wifi/connect', methods=['POST'])
def wifi_connect():
    """Connect to a WiFi network and optionally save as profile."""
    data = request.json
    ssid = data.get('ssid', '').strip()
    password = data.get('password', '').strip()
    save_profile = data.get('save_profile', True)
    if not ssid:
        return jsonify({'success': False, 'message': 'SSID required'})
    encryption = 'psk2' if password else 'none'
    # Apply WiFi config
    run(f"uci set wireless.@wifi-iface[0].ssid='{ssid}'")
    run(f"uci set wireless.@wifi-iface[0].encryption='{encryption}'")
    if password:
        run(f"uci set wireless.@wifi-iface[0].key='{password}'")
    else:
        run("uci delete wireless.@wifi-iface[0].key 2>/dev/null")
    run("uci commit wireless")
    run("wifi reload")
    # Wait for connection
    time.sleep(5)
    connected = run("ping -c 1 -W 3 8.8.8.8 >/dev/null 2>&1 && echo ok") == 'ok'
    if not connected:
        time.sleep(3)
        connected = run("ping -c 1 -W 3 8.8.8.8 >/dev/null 2>&1 && echo ok") == 'ok'
    # Save profile
    if save_profile and password:
        profiles = load_wifi_profiles()
        existing = [i for i, p in enumerate(profiles['profiles']) if p['ssid'] == ssid]
        profile = {'ssid': ssid, 'password': password, 'encryption': encryption}
        if existing:
            profiles['profiles'][existing[0]] = profile
        else:
            profiles['profiles'].append(profile)
        save_wifi_profiles(profiles)
    msg = f'Connected to "{ssid}"' if connected else f'Saved "{ssid}" but connection pending...'
    return jsonify({'success': True, 'connected': connected, 'message': msg})

@app.route('/api/wifi/disconnect', methods=['POST'])
def wifi_disconnect():
    """Disconnect from current WiFi."""
    run("uci set wireless.@wifi-iface[0].ssid=''")
    run("uci delete wireless.@wifi-iface[0].key 2>/dev/null")
    run("uci commit wireless")
    run("wifi reload")
    return jsonify({'success': True, 'message': 'WiFi disconnected.'})

@app.route('/api/wifi/profiles', methods=['GET'])
def wifi_profiles_get():
    return jsonify(load_wifi_profiles())

@app.route('/api/wifi/profiles', methods=['POST'])
def wifi_profiles_set():
    """Add/remove/update WiFi profiles."""
    data = request.json
    action = data.get('action', '')
    profiles = load_wifi_profiles()
    if action == 'remove':
        ssid = data.get('ssid', '')
        profiles['profiles'] = [p for p in profiles['profiles'] if p['ssid'] != ssid]
        save_wifi_profiles(profiles)
        return jsonify({'success': True, 'message': f'"{ssid}" removed.'})
    elif action == 'toggle_auto':
        profiles['auto_connect'] = not profiles.get('auto_connect', True)
        save_wifi_profiles(profiles)
        return jsonify({'success': True, 'message': f'Auto-connect {"enabled" if profiles["auto_connect"] else "disabled"}.'})
    return jsonify({'success': False, 'message': 'Unknown action.'})

@app.route('/api/wifi/auto-connect', methods=['POST'])
def wifi_auto_connect():
    """Try to connect to the strongest known network."""
    profiles = load_wifi_profiles()
    if not profiles.get('profiles'):
        return jsonify({'success': False, 'message': 'No saved profiles.'})
    # Scan networks
    wifi_iface = get_wifi_iface() or 'phy0-sta0'
    raw = run(f"iwinfo {wifi_iface} scan 2>/dev/null", timeout=15)
    if not raw:
        return jsonify({'success': False, 'message': 'Scan failed.'})
    # Parse visible SSIDs
    visible = set()
    for line in raw.split('\n'):
        if 'ESSID:' in line and '"' in line:
            visible.add(line.split('"')[1])
    # Find known networks that are visible
    known_ssids = {p['ssid']: p for p in profiles['profiles']}
    matches = [ssid for ssid in visible if ssid in known_ssids]
    if not matches:
        return jsonify({'success': False, 'message': 'No known networks nearby.'})
    # Connect to first match
    target = matches[0]
    profile = known_ssids[target]
    run(f"uci set wireless.@wifi-iface[0].ssid='{target}'")
    run(f"uci set wireless.@wifi-iface[0].encryption='{profile.get('encryption', 'psk2')}'")
    run(f"uci set wireless.@wifi-iface[0].key='{profile.get('password', '')}'")
    run("uci commit wireless")
    run("wifi reload")
    time.sleep(5)
    connected = run("ping -c 1 -W 3 8.8.8.8 >/dev/null 2>&1 && echo ok") == 'ok'
    return jsonify({'success': connected, 'ssid': target,
                    'message': f'Connected to "{target}"' if connected else f'Failed to connect to "{target}"'})

# ─── WiFi Auto-Connect on Startup ───────────────────────────

def wifi_auto_connect_startup():
    """Try to connect to a known WiFi on startup."""
    profiles = load_wifi_profiles()
    if not profiles.get('auto_connect', True) or not profiles.get('profiles'):
        return
    current_ssid = run("uci get wireless.@wifi-iface[0].ssid 2>/dev/null")
    if current_ssid:
        # Already configured, check if connected
        if run("ping -c 1 -W 3 8.8.8.8 >/dev/null 2>&1 && echo ok") == 'ok':
            return
    app.logger.info("WiFi auto-connect: trying known networks...")
    wifi_iface = get_wifi_iface() or 'phy0-sta0'
    raw = run(f"iwinfo {wifi_iface} scan 2>/dev/null", timeout=15)
    if not raw:
        return
    visible = set()
    for line in raw.split('\n'):
        if 'ESSID:' in line and '"' in line:
            visible.add(line.split('"')[1])
    known = {p['ssid']: p for p in profiles['profiles']}
    for ssid in visible:
        if ssid in known:
            p = known[ssid]
            run(f"uci set wireless.@wifi-iface[0].ssid='{ssid}'")
            run(f"uci set wireless.@wifi-iface[0].encryption='{p.get('encryption', 'psk2')}'")
            run(f"uci set wireless.@wifi-iface[0].key='{p.get('password', '')}'")
            run("uci commit wireless")
            run("wifi reload")
            time.sleep(5)
            if run("ping -c 1 -W 3 8.8.8.8 >/dev/null 2>&1 && echo ok") == 'ok':
                app.logger.info(f"WiFi auto-connect: connected to {ssid}")
                return
    app.logger.info("WiFi auto-connect: no known networks available")

wifi_auto_connect_startup()

# ─── Bandwidth Monitoring ───────────────────────────────────

@app.route('/api/bandwidth')
def bandwidth():
    result = {}
    for iface in ['usb0', 'usb1', 'wg0']:
        rx = run(f"cat /sys/class/net/{iface}/statistics/rx_bytes 2>/dev/null") or '0'
        tx = run(f"cat /sys/class/net/{iface}/statistics/tx_bytes 2>/dev/null") or '0'
        if rx != '0' or tx != '0':
            result[iface] = {'rx_bytes': int(rx), 'tx_bytes': int(tx)}
    return jsonify(result)

@app.route('/api/bandwidth/speed')
def bandwidth_speed():
    """Measure current speed over 1 second."""
    readings = {}
    for iface in ['usb0', 'usb1']:
        rx1 = int(run(f"cat /sys/class/net/{iface}/statistics/rx_bytes 2>/dev/null") or '0')
        tx1 = int(run(f"cat /sys/class/net/{iface}/statistics/tx_bytes 2>/dev/null") or '0')
        readings[iface] = {'rx1': rx1, 'tx1': tx1}
    time.sleep(1)
    for iface in readings:
        rx2 = int(run(f"cat /sys/class/net/{iface}/statistics/rx_bytes 2>/dev/null") or '0')
        tx2 = int(run(f"cat /sys/class/net/{iface}/statistics/tx_bytes 2>/dev/null") or '0')
        readings[iface]['rx_speed'] = rx2 - readings[iface]['rx1']
        readings[iface]['tx_speed'] = tx2 - readings[iface]['tx1']
    return jsonify(readings)

# ─── Firewall ───────────────────────────────────────────────

FW_RULES_FILE = os.path.join(DATA_DIR, 'firewall_rules.json')

@app.route('/api/firewall', methods=['GET'])
def firewall_get():
    rules = read_json(FW_RULES_FILE, {'rules': []})
    return jsonify(rules)

@app.route('/api/firewall', methods=['POST'])
def firewall_set():
    data = request.json
    action = data.get('action', '')
    rules = read_json(FW_RULES_FILE, {'rules': []})

    if action == 'add':
        rule = {
            'proto': data.get('proto', 'tcp'),
            'port': data.get('port', ''),
            'direction': data.get('direction', 'out'),
            'action': data.get('rule_action', 'DROP'),
        }
        rules['rules'].append(rule)
        chain = 'OUTPUT' if rule['direction'] == 'out' else 'INPUT'
        run(f"iptables -A {chain} -p {rule['proto']} --dport {rule['port']} -j {rule['action']} 2>/dev/null")
        write_json(FW_RULES_FILE, rules)
        return jsonify({'success': True, 'message': f"Rule added: {rule['action']} {rule['proto']}:{rule['port']}"})

    elif action == 'remove':
        idx = data.get('index', -1)
        if 0 <= idx < len(rules['rules']):
            rule = rules['rules'].pop(idx)
            chain = 'OUTPUT' if rule['direction'] == 'out' else 'INPUT'
            run(f"iptables -D {chain} -p {rule['proto']} --dport {rule['port']} -j {rule['action']} 2>/dev/null")
            write_json(FW_RULES_FILE, rules)
        return jsonify({'success': True, 'message': 'Rule removed.'})

    return jsonify({'success': False})

# ─── DNS ────────────────────────────────────────────────────

@app.route('/api/dns', methods=['GET'])
def dns_get():
    dns_list = run("uci get dhcp.@dnsmasq[0].server 2>/dev/null") or ''
    dns_forwarding = run("uci get dhcp.@dnsmasq[0].noresolv 2>/dev/null") or '0'
    return jsonify({
        'servers': dns_list.split() if dns_list else ['8.8.8.8', '8.8.4.4'],
        'force_dns': dns_forwarding == '1',
    })

@app.route('/api/dns', methods=['POST'])
def dns_set():
    data = request.json
    servers = data.get('servers', [])
    run("uci delete dhcp.@dnsmasq[0].server 2>/dev/null")
    for s in servers:
        run(f"uci add_list dhcp.@dnsmasq[0].server='{s}'")
    if data.get('force_dns'):
        run("uci set dhcp.@dnsmasq[0].noresolv='1'")
    else:
        run("uci set dhcp.@dnsmasq[0].noresolv='0'")
    run("uci commit dhcp")
    run("/etc/init.d/dnsmasq restart")
    return jsonify({'success': True, 'message': 'DNS updated.'})

# ─── Privacy ────────────────────────────────────────────────

@app.route('/api/privacy', methods=['GET'])
def privacy_get():
    dns_forced = run("uci get dhcp.@dnsmasq[0].noresolv 2>/dev/null") == '1'
    webrtc_block = '0' != run("iptables -L FORWARD -n 2>/dev/null | grep -c 'dpt:3478' || echo 0")
    kill_switch = len(run("iptables -L FORWARD -n 2>/dev/null | grep 'DROP.*!.*wg0' || true")) > 0
    return jsonify({
        'dns_leak_protection': dns_forced,
        'webrtc_leak_protection': webrtc_block,
        'kill_switch': kill_switch,
    })

@app.route('/api/privacy', methods=['POST'])
def privacy_set():
    data = request.json
    if 'dns_leak_protection' in data:
        if data['dns_leak_protection']:
            run("uci set dhcp.@dnsmasq[0].noresolv='1'")
            run("uci delete dhcp.@dnsmasq[0].server 2>/dev/null")
            run("uci add_list dhcp.@dnsmasq[0].server='8.8.8.8'")
            run("uci add_list dhcp.@dnsmasq[0].server='1.1.1.1'")
        else:
            run("uci set dhcp.@dnsmasq[0].noresolv='0'")
        run("uci commit dhcp")
        run("/etc/init.d/dnsmasq restart")
    if 'webrtc_leak_protection' in data:
        if data['webrtc_leak_protection']:
            run("iptables -I FORWARD -p udp --dport 3478 -j DROP 2>/dev/null")
            run("iptables -I FORWARD -p tcp --dport 3478 -j DROP 2>/dev/null")
            run("iptables -I FORWARD -p udp --dport 19302 -j DROP 2>/dev/null")
        else:
            run("iptables -D FORWARD -p udp --dport 3478 -j DROP 2>/dev/null")
            run("iptables -D FORWARD -p tcp --dport 3478 -j DROP 2>/dev/null")
            run("iptables -D FORWARD -p udp --dport 19302 -j DROP 2>/dev/null")
    if 'kill_switch' in data:
        if data['kill_switch']:
            run("iptables -I FORWARD -i usb0 ! -o wg0 -j DROP 2>/dev/null")
            run("iptables -I FORWARD -i usb1 ! -o wg0 -j DROP 2>/dev/null")
        else:
            run("iptables -D FORWARD -i usb0 ! -o wg0 -j DROP 2>/dev/null")
            run("iptables -D FORWARD -i usb1 ! -o wg0 -j DROP 2>/dev/null")
    return jsonify({'success': True, 'message': 'Privacy settings updated.'})

# ─── Residential Proxy ──────────────────────────────────────

def load_proxy_config():
    """Load proxy config, auto-migrate old single-proxy format."""
    config = read_json(PROXY_FILE, {'enabled': False, 'mode': 'round-robin', 'proxies': []})
    # Migrate old single-proxy config
    if 'host' in config and 'proxies' not in config:
        old = {k: config.pop(k) for k in ['protocol', 'host', 'port', 'username', 'password'] if k in config}
        if old.get('host'):
            old['name'] = 'Proxy 1'
            config['proxies'] = [old]
        else:
            config['proxies'] = []
        config.setdefault('mode', 'round-robin')
        write_json(PROXY_FILE, config)
    return config

@app.route('/api/proxy', methods=['GET'])
def proxy_get():
    config = load_proxy_config()
    running = bool(run("ps | grep proxy_server.py | grep -v grep"))
    config['running'] = running
    return jsonify(config)

@app.route('/api/proxy', methods=['POST'])
def proxy_set():
    data = request.json
    config = load_proxy_config()

    action = data.get('action', 'save')

    if action == 'add':
        proxy = {
            'name': data.get('name', f'Proxy {len(config["proxies"])+1}'),
            'protocol': data.get('protocol', 'http'),
            'host': data.get('host', ''),
            'port': data.get('port', ''),
            'username': data.get('username', ''),
            'password': data.get('password', ''),
        }
        config['proxies'].append(proxy)
        write_json(PROXY_FILE, config)
        return jsonify({'success': True, 'message': f'Proxy "{proxy["name"]}" added.'})

    elif action == 'remove':
        idx = int(data.get('index', -1))
        if 0 <= idx < len(config['proxies']):
            removed = config['proxies'].pop(idx)
            write_json(PROXY_FILE, config)
            return jsonify({'success': True, 'message': f'Proxy "{removed["name"]}" removed.'})
        return jsonify({'success': False, 'message': 'Invalid index.'})

    elif action == 'set_mode':
        config['mode'] = data.get('mode', 'round-robin')
        write_json(PROXY_FILE, config)
        return jsonify({'success': True, 'message': f'Rotation mode: {config["mode"]}'})

    elif action == 'set_bypass_dev_ports':
        bypass = bool(data.get('bypass', False))
        if bypass:
            vpn_key = run("uci get network.wg0.private_key 2>/dev/null")
            wg_output = run("wg show wg0 2>/dev/null")
            is_up = bool(wg_output and 'interface' in wg_output)
            if not vpn_key or not is_up:
                return jsonify({'success': False, 'message': 'Safety Lock: You must connect to your VPN first to prevent IP leaks!'})

        config['bypass_dev_ports'] = bypass
        write_json(PROXY_FILE, config)
        # Apply instantly if proxy is enabled
        if config.get('enabled') and bool(run("ps | grep proxy_server.py | grep -v grep")):
            run("iptables -t nat -F TPROXY 2>/dev/null")
            apply_proxy_iptables(config['proxies'][0]['host'] if config.get('proxies') else '')
        return jsonify({'success': True, 'message': 'Developer ports bypass ' + ('enabled' if bypass else 'disabled')})

    elif action == 'update':
        idx = int(data.get('index', -1))
        if 0 <= idx < len(config['proxies']):
            for key in ['name', 'protocol', 'host', 'port', 'username', 'password']:
                if key in data:
                    config['proxies'][idx][key] = data[key]
            write_json(PROXY_FILE, config)
            return jsonify({'success': True, 'message': 'Proxy updated.'})
        return jsonify({'success': False, 'message': 'Invalid index.'})

    return jsonify({'success': False, 'message': 'Unknown action.'})

@app.route('/api/proxy/enable', methods=['POST'])
def proxy_enable():
    config = load_proxy_config()
    if not config.get('proxies'):
        return jsonify({'success': False, 'message': 'Add at least one proxy first.'})

    if not start_proxy_server():
        return jsonify({'success': False, 'message': 'Transparent proxy failed to start.'})

    first_proxy = config['proxies'][0]
    apply_proxy_iptables(first_proxy['host'])

    config['enabled'] = True
    write_json(PROXY_FILE, config)
    n = len(config['proxies'])
    mode = config.get('mode', 'round-robin')
    return jsonify({
        'success': True,
        'message': f'Proxy enabled — {n} proxy(s), mode: {mode}'
    })

@app.route('/api/proxy/disable', methods=['POST'])
def proxy_disable():
    remove_proxy_iptables()
    stop_proxy_server()
    config = load_proxy_config()
    config['enabled'] = False
    write_json(PROXY_FILE, config)
    return jsonify({'success': True, 'message': 'Proxy disabled.'})

@app.route('/api/proxy/test', methods=['POST'])
def proxy_test():
    ip = get_proxy_ip()
    if ip and re.match(r'^\d+\.\d+\.\d+\.\d+$', ip):
        return jsonify({'success': True, 'ip': ip, 'message': f'Proxy working — IP: {ip}'})
    else:
        return jsonify({'success': False, 'message': f'Proxy test failed: {ip or "no response"}'})

# ─── System ─────────────────────────────────────────────────

@app.route('/api/system/reboot', methods=['POST'])
def system_reboot():
    run("reboot &")
    return jsonify({'success': True, 'message': 'Rebooting...'})

@app.route('/api/system/info')
def system_info():
    return jsonify({
        'hostname': run("uci get system.@system[0].hostname 2>/dev/null") or 'shadowplug',
        'kernel': run("uname -r"),
        'cpu_temp': run("cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo 0"),
        'mem_total': run("grep MemTotal /proc/meminfo | awk '{print $2}'"),
        'mem_free': run("grep MemAvailable /proc/meminfo | awk '{print $2}'"),
        'disk_usage': run("df -h / | tail -1 | awk '{print $5}'"),
        'load': run("cat /proc/loadavg | awk '{print $1, $2, $3}'"),
    })

# ─── Logs ────────────────────────────────────────────────────

LOG_SOURCES = {
    'app': '/var/log/shadowplug.log',
    'proxy': '/var/log/proxy-server.log',
    'system': '/var/log/messages',
    'dnsmasq': None,  # uses logread
}

@app.route('/api/logs', methods=['GET'])
def logs_get():
    source = request.args.get('source', 'app')
    lines = int(request.args.get('lines', 100))

    if source == 'dnsmasq':
        output = run(f"logread -e dnsmasq | tail -{lines}")
    elif source in LOG_SOURCES and LOG_SOURCES[source]:
        path = LOG_SOURCES[source]
        output = run(f"tail -{lines} {path} 2>/dev/null") or '(empty)'
    else:
        output = '(unknown log source)'

    return jsonify({'source': source, 'content': output})

@app.route('/api/logs', methods=['DELETE'])
def logs_clear():
    source = request.args.get('source', 'app')
    if source in LOG_SOURCES and LOG_SOURCES[source]:
        run(f"> {LOG_SOURCES[source]} 2>/dev/null")
    return jsonify({'success': True, 'message': f'{source} logs cleared.'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80, debug=False)

