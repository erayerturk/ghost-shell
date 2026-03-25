/* ShadowPlug — Frontend */

// ─── Tab Navigation ─────────────────────────

document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById('panel-' + tab.dataset.tab).classList.add('active');
    });
});

// ─── Toast ──────────────────────────────────

function toast(msg, type = 'success') {
    const el = document.getElementById('toast');
    el.textContent = msg;
    el.className = 'toast ' + type;
    setTimeout(() => el.classList.add('show'), 10);
    setTimeout(() => el.classList.remove('show'), 3000);
}

// ─── API ────────────────────────────────────

async function api(path, method = 'GET', body = null) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body) opts.body = JSON.stringify(body);
    try {
        const res = await fetch('/api/' + path, opts);
        return await res.json();
    } catch (e) {
        toast('Connection error', 'error');
        return null;
    }
}

function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024, sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return (bytes / Math.pow(k, i)).toFixed(1) + ' ' + sizes[i];
}

// ─── Status ─────────────────────────────────

async function refreshStatus() {
    const data = await api('status');
    if (!data) return;
    document.getElementById('publicIp').textContent = data.public_ip || '—';
    document.getElementById('vpnRx').textContent = data.vpn_rx || '—';
    document.getElementById('vpnTx').textContent = data.vpn_tx || '—';
    document.getElementById('sysUptime').textContent = data.uptime || '—';

    const usbEl = document.getElementById('usbStatus');
    if (data.usb_status === 'connected') {
        usbEl.textContent = '● Connected';
        usbEl.style.color = 'var(--green)';
    } else {
        usbEl.textContent = '○ Disconnected';
        usbEl.style.color = '';
    }

    const badge = document.getElementById('vpnBadge');
    badge.style.background = '';
    if (data.mode === 'vpn') {
        badge.className = 'badge on';
        badge.textContent = '🛡 VPN';
    } else if (data.mode === 'proxy') {
        badge.className = 'badge on proxy-badge';
        badge.textContent = '🌐 PROXY';
    } else {
        badge.className = 'badge off';
        badge.textContent = '⚠ DIRECT';
    }

    // Update VPN toggle switch
    const vpnToggle = document.getElementById('vpnToggle');
    const vpnLabel = document.getElementById('vpnToggleLabel');
    if (vpnToggle) {
        vpnToggle.checked = data.vpn_connected;
        vpnLabel.textContent = data.vpn_connected ? 'Connected' : 'Disconnected';
    }
}

async function loadVPN() {
    const data = await api('vpn');
    if (!data) return;
    document.getElementById('vpnEndpoint').value = data.endpoint || '';
    document.getElementById('vpnPort').value = data.endpoint_port || '51820';
    document.getElementById('vpnPrivateKey').value = data.private_key || '';
    document.getElementById('vpnPeerPublicKey').value = data.peer_public_key || '';
    document.getElementById('vpnAddress').value = data.address || '';
    document.getElementById('vpnAllowedIPs').value = data.allowed_ips || '0.0.0.0/0';
}

async function saveVPN() {
    const data = {
        endpoint: document.getElementById('vpnEndpoint').value,
        endpoint_port: document.getElementById('vpnPort').value,
        private_key: document.getElementById('vpnPrivateKey').value,
        peer_public_key: document.getElementById('vpnPeerPublicKey').value,
        address: document.getElementById('vpnAddress').value,
        allowed_ips: document.getElementById('vpnAllowedIPs').value,
    };
    const res = await api('vpn', 'POST', data);
    if (res && res.success) { toast('VPN saved, restarting...'); setTimeout(refreshStatus, 5000); }
}

async function testVPN() {
    const el = document.getElementById('testResult');
    const c = document.getElementById('testContent');
    el.style.display = 'block';
    c.innerHTML = 'Testing...';
    const data = await api('vpn/test', 'POST');
    if (!data) { c.innerHTML = '<span style="color:var(--red)">Failed</span>'; return; }
    if (data.success) {
        c.innerHTML = `<span style="color:var(--green)">VPN working</span> — IP: <strong>${data.public_ip}</strong>`;
    } else {
        c.innerHTML = `<span style="color:var(--red)">Failed</span><pre style="font-size:11px;color:var(--text-dim);margin-top:6px">${data.ping_result}</pre>`;
    }
}

async function generateKeys() {
    const data = await api('vpn/generate-keys', 'POST');
    if (!data) return;
    document.getElementById('genPrivateKey').value = data.private_key;
    document.getElementById('genPublicKey').value = data.public_key;
    document.getElementById('keyResult').style.display = 'block';
    toast('Keys generated');
}

async function toggleVPN() {
    const toggle = document.getElementById('vpnToggle');
    const action = toggle.checked ? 'up' : 'down';
    const res = await api('vpn/toggle', 'POST', {action});
    if (res && res.success) {
        toast(res.message);
        setTimeout(refreshStatus, 3000);
    }
}

function useGeneratedKey() {
    document.getElementById('vpnPrivateKey').value = document.getElementById('genPrivateKey').value;
    document.getElementById('keyResult').style.display = 'none';
    toast('Key applied');
}

// ─── VPN Profiles ───────────────────────────

async function loadProfiles() {
    const data = await api('vpn/profiles');
    if (!data) return;
    const el = document.getElementById('profileList');
    if (!data.profiles || data.profiles.length === 0) {
        el.innerHTML = '<span class="hint">No saved profiles</span>';
        return;
    }
    el.innerHTML = data.profiles.map(p => `
        <div class="list-item">
            <span>${p.name} ${p.name === data.active ? '<span class="badge on" style="font-size:10px">active</span>' : ''}</span>
            <span>
                <button class="btn" onclick="activateProfile('${p.name}')" style="padding:3px 8px;font-size:11px">Use</button>
                <button class="btn btn-danger" onclick="deleteProfile('${p.name}')" style="padding:3px 8px;font-size:11px">×</button>
            </span>
        </div>
    `).join('');
}

async function saveProfile() {
    const name = document.getElementById('profileName').value;
    if (!name) { toast('Enter a profile name', 'error'); return; }
    const data = {
        name,
        endpoint: document.getElementById('vpnEndpoint').value,
        endpoint_port: document.getElementById('vpnPort').value,
        private_key: document.getElementById('vpnPrivateKey').value,
        peer_public_key: document.getElementById('vpnPeerPublicKey').value,
        address: document.getElementById('vpnAddress').value,
        allowed_ips: document.getElementById('vpnAllowedIPs').value,
    };
    const res = await api('vpn/profiles', 'POST', data);
    if (res && res.success) { toast('Profile saved'); loadProfiles(); }
}

async function activateProfile(name) {
    const res = await api('vpn/profiles/activate', 'POST', { name });
    if (res && res.success) { toast(`Switched to ${name}`); loadVPN(); loadProfiles(); setTimeout(refreshStatus, 5000); }
}

async function deleteProfile(name) {
    if (!confirm(`Delete profile "${name}"?`)) return;
    const res = await api('vpn/profiles/delete', 'POST', { name });
    if (res && res.success) { toast('Profile deleted'); loadProfiles(); }
}

// ─── Blocking ───────────────────────────────

async function loadBlocking() {
    const data = await api('blocking');
    if (!data) return;
    document.getElementById('blockingEnabled').checked = data.enabled;
    document.getElementById('blockCount').textContent = data.blocked_count + ' domains blocked';
    const el = document.getElementById('customBlockList');
    if (data.custom_domains && data.custom_domains.length > 0) {
        el.innerHTML = data.custom_domains.map(d => `
            <div class="list-item">
                <span>${d}</span>
                <button class="btn btn-danger" onclick="removeBlockDomain('${d}')" style="padding:3px 8px;font-size:11px">×</button>
            </div>
        `).join('');
    } else {
        el.innerHTML = '<span class="hint">No custom domains</span>';
    }
}

async function toggleBlocking() {
    const enabled = document.getElementById('blockingEnabled').checked;
    const action = enabled ? 'enable' : 'disable';
    if (enabled) toast('Downloading blocklists...');
    const res = await api('blocking', 'POST', { action });
    if (res && res.success) { toast(res.message); loadBlocking(); }
}

async function addBlockDomain() {
    const domain = document.getElementById('customBlockDomain').value.trim();
    if (!domain) return;
    const res = await api('blocking', 'POST', { action: 'add_domain', domain });
    if (res && res.success) { document.getElementById('customBlockDomain').value = ''; toast(res.message); loadBlocking(); }
}

async function removeBlockDomain(domain) {
    const res = await api('blocking', 'POST', { action: 'remove_domain', domain });
    if (res && res.success) { toast(res.message); loadBlocking(); }
}

// ─── Network: WiFi ──────────────────────────

function wifiBars(signal) {
    if (!signal) return '—';
    const dbm = parseInt(signal);
    if (isNaN(dbm)) return '—';
    const on = 'var(--green)';
    const off = 'rgba(255,255,255,.15)';
    if (dbm >= -50) return `<span style="letter-spacing:1px"><span style="color:${on}">▂▄▆█</span></span>`;
    if (dbm >= -60) return `<span style="letter-spacing:1px"><span style="color:${on}">▂▄▆</span><span style="color:${off}">█</span></span>`;
    if (dbm >= -70) return `<span style="letter-spacing:1px"><span style="color:${on}">▂▄</span><span style="color:${off}">▆█</span></span>`;
    return `<span style="letter-spacing:1px"><span style="color:${on}">▂</span><span style="color:${off}">▄▆█</span></span>`;
}

async function loadWiFi() {
    const data = await api('wifi');
    if (!data) return;
    document.getElementById('wifiSSID').textContent = data.ssid || '(none)';
    const statusEl = document.getElementById('wifiStatus');
    if (data.connected) {
        statusEl.textContent = '● Connected';
        statusEl.style.color = 'var(--green)';
    } else {
        statusEl.textContent = '○ Disconnected';
        statusEl.style.color = 'var(--red)';
    }
    document.getElementById('wifiSignal').innerHTML = wifiBars(data.signal);
    const inetEl = document.getElementById('wifiInternet');
    inetEl.textContent = data.internet ? '✓ Online' : '✗ Offline';
    inetEl.style.color = data.internet ? 'var(--green)' : 'var(--red)';
    document.getElementById('wifiAutoConnect').checked = data.auto_connect !== false;
    // Render saved profiles
    const list = document.getElementById('wifiProfileList');
    if (data.profiles && data.profiles.length > 0) {
        list.innerHTML = data.profiles.map(p => `
            <div class="list-item">
                <span>${p.ssid} ${p.ssid === data.ssid ? '<span class="badge on" style="font-size:10px">active</span>' : ''}</span>
                <span>
                    <button class="btn" onclick="quickConnectWiFi('${p.ssid}')" style="padding:3px 8px;font-size:11px">Connect</button>
                    <button class="btn btn-danger" onclick="removeWiFiProfile('${p.ssid}')" style="padding:3px 8px;font-size:11px">×</button>
                </span>
            </div>
        `).join('');
    } else {
        list.innerHTML = '<span class="hint">No saved networks</span>';
    }
}

async function scanWiFi() {
    const el = document.getElementById('wifiScanResults');
    el.innerHTML = '<span class="hint">Scanning...</span>';
    const data = await api('wifi/scan', 'POST');
    if (!data || !data.networks || data.networks.length === 0) {
        el.innerHTML = '<span class="hint">No networks found</span>';
        return;
    }
    el.innerHTML = data.networks.map(n => `
        <div class="list-item" style="cursor:pointer" onclick="document.getElementById('wifiSSIDInput').value='${n.ssid}';document.getElementById('wifiPassword').focus()">
            <span>${n.known ? '★ ' : ''}${n.ssid}</span>
            <span style="font-size:12px;color:var(--text-dim)">${wifiBars(n.signal)} ${n.encryption !== 'none' ? '🔒' : ''}</span>
        </div>
    `).join('');
}

async function connectWiFi() {
    const ssid = document.getElementById('wifiSSIDInput').value.trim();
    const password = document.getElementById('wifiPassword').value;
    if (!ssid) { toast('Enter SSID', 'error'); return; }
    toast('Connecting...');
    const res = await api('wifi/connect', 'POST', { ssid, password, save_profile: true });
    if (res) {
        toast(res.message, res.connected ? 'success' : 'error');
        document.getElementById('wifiSSIDInput').value = '';
        document.getElementById('wifiPassword').value = '';
        document.getElementById('wifiScanResults').innerHTML = '';
        setTimeout(loadWiFi, 2000);
    }
}

async function quickConnectWiFi(ssid) {
    toast(`Connecting to ${ssid}...`);
    const res = await api('wifi/auto-connect', 'POST');
    if (res) toast(res.message, res.success ? 'success' : 'error');
    setTimeout(loadWiFi, 3000);
}

async function disconnectWiFi() {
    if (!confirm('Disconnect WiFi?')) return;
    const res = await api('wifi/disconnect', 'POST');
    if (res && res.success) { toast(res.message); setTimeout(loadWiFi, 2000); }
}

async function toggleWiFiAuto() {
    const res = await api('wifi/profiles', 'POST', { action: 'toggle_auto' });
    if (res && res.success) toast(res.message);
}

async function removeWiFiProfile(ssid) {
    if (!confirm(`Remove "${ssid}"?`)) return;
    const res = await api('wifi/profiles', 'POST', { action: 'remove', ssid });
    if (res && res.success) { toast(res.message); loadWiFi(); }
}

// ─── Network: MAC ───────────────────────────

async function loadMAC() {
    const data = await api('mac');
    if (!data) return;
    document.getElementById('macIface').textContent = data.wifi_iface || 'N/A';
    document.getElementById('macWifi').textContent = data.wifi_mac || 'N/A';
}

async function randomizeMAC() {
    const res = await api('mac/randomize', 'POST');
    if (res && res.success) { toast(res.message); loadMAC(); }
}

// ─── Network: Bandwidth ─────────────────────

async function loadBandwidth() {
    const data = await api('bandwidth');
    if (!data) return;
    const el = document.getElementById('bandwidthInfo');
    let html = '';
    for (const [iface, stats] of Object.entries(data)) {
        html += `<div class="list-item"><span>${iface}</span><span>↓ ${formatBytes(stats.rx_bytes)} / ↑ ${formatBytes(stats.tx_bytes)}</span></div>`;
    }
    el.innerHTML = html || '<span class="hint">No data</span>';
}

async function measureSpeed() {
    document.getElementById('speedResult').innerHTML = 'Measuring...';
    const data = await api('bandwidth/speed');
    if (!data) { document.getElementById('speedResult').innerHTML = ''; return; }
    let html = '';
    for (const [iface, stats] of Object.entries(data)) {
        if (stats.rx_speed > 0 || stats.tx_speed > 0) {
            html += `<div class="list-item"><span>${iface}</span><span>↓ ${formatBytes(stats.rx_speed)}/s / ↑ ${formatBytes(stats.tx_speed)}/s</span></div>`;
        }
    }
    document.getElementById('speedResult').innerHTML = html || '<span class="hint">No traffic detected</span>';
}

// ─── Network: Split Tunneling ───────────────

async function loadSplit() {
    const data = await api('split');
    if (!data) return;
    const el = document.getElementById('splitList');
    if (data.bypass && data.bypass.length > 0) {
        el.innerHTML = data.bypass.map(e => `
            <div class="list-item"><span>${e}</span><button class="btn btn-danger" onclick="removeSplit('${e}')" style="padding:3px 8px;font-size:11px">×</button></div>
        `).join('');
    } else {
        el.innerHTML = '<span class="hint">No bypass rules</span>';
    }
}

async function addSplit() {
    const entry = document.getElementById('splitEntry').value.trim();
    if (!entry) return;
    const res = await api('split', 'POST', { action: 'add', entry });
    if (res && res.success) { document.getElementById('splitEntry').value = ''; toast('Bypass added'); loadSplit(); }
}

async function removeSplit(entry) {
    const res = await api('split', 'POST', { action: 'remove', entry });
    if (res && res.success) { toast('Bypass removed'); loadSplit(); }
}

// ─── Network: Firewall ──────────────────────

async function loadFirewall() {
    const data = await api('firewall');
    if (!data) return;
    const el = document.getElementById('fwRuleList');
    if (data.rules && data.rules.length > 0) {
        el.innerHTML = data.rules.map((r, i) => `
            <div class="list-item">
                <span>${r.action} ${r.proto.toUpperCase()}:${r.port} (${r.direction})</span>
                <button class="btn btn-danger" onclick="removeFirewallRule(${i})" style="padding:3px 8px;font-size:11px">×</button>
            </div>
        `).join('');
    } else {
        el.innerHTML = '<span class="hint">No custom rules</span>';
    }
}

async function addFirewallRule() {
    const port = document.getElementById('fwPort').value.trim();
    if (!port) { toast('Enter a port', 'error'); return; }
    const data = {
        action: 'add',
        proto: document.getElementById('fwProto').value,
        port,
        direction: document.getElementById('fwDir').value,
        rule_action: document.getElementById('fwAction').value,
    };
    const res = await api('firewall', 'POST', data);
    if (res && res.success) { document.getElementById('fwPort').value = ''; toast(res.message); loadFirewall(); }
}

async function removeFirewallRule(idx) {
    const res = await api('firewall', 'POST', { action: 'remove', index: idx });
    if (res && res.success) { toast(res.message); loadFirewall(); }
}

// ─── Privacy ────────────────────────────────

async function loadPrivacy() {
    const data = await api('privacy');
    if (!data) return;
    document.getElementById('dnsLeakProtection').checked = data.dns_leak_protection;
    document.getElementById('webrtcLeakProtection').checked = data.webrtc_leak_protection;
    document.getElementById('killSwitch').checked = data.kill_switch;
}

async function savePrivacy() {
    const data = {
        dns_leak_protection: document.getElementById('dnsLeakProtection').checked,
        webrtc_leak_protection: document.getElementById('webrtcLeakProtection').checked,
        kill_switch: document.getElementById('killSwitch').checked,
    };
    const res = await api('privacy', 'POST', data);
    if (res && res.success) toast('Privacy updated');
}

// ─── DNS ────────────────────────────────────

async function loadDNS() {
    const data = await api('dns');
    if (!data) return;
    const s = data.servers || [];
    document.getElementById('dns1').value = s[0] || '8.8.8.8';
    document.getElementById('dns2').value = s[1] || '1.1.1.1';
    document.getElementById('forceDns').checked = data.force_dns;
}

async function saveDNS() {
    const servers = [document.getElementById('dns1').value, document.getElementById('dns2').value].filter(s => s);
    const res = await api('dns', 'POST', { servers, force_dns: document.getElementById('forceDns').checked });
    if (res && res.success) toast('DNS saved');
}

function setDNS(a, b) {
    document.getElementById('dns1').value = a;
    document.getElementById('dns2').value = b;
    toast('Preset applied — click Save');
}

// ─── System ─────────────────────────────────

async function loadSystem() {
    const data = await api('system/info');
    if (!data) return;
    document.getElementById('cpuTemp').textContent = (parseInt(data.cpu_temp) / 1000).toFixed(1) + '°C';
    const total = Math.round(parseInt(data.mem_total) / 1024);
    const free = Math.round(parseInt(data.mem_free) / 1024);
    document.getElementById('memUsage').textContent = `${total - free}/${total} MB`;
    const cpuVal = parseFloat(data.cpu_usage || '0');
    document.getElementById('sysLoad').textContent = !isNaN(cpuVal) ? Math.round(cpuVal) + '%' : '—';
}

async function rebootSystem() {
    if (!confirm('Reboot device?')) return;
    await api('system/reboot', 'POST');
    toast('Rebooting...');
}

// ─── Proxy ──────────────────────────────────

async function loadProxy() {
    const data = await api('proxy');
    if (!data) return;

    document.getElementById('proxyMode').value = data.mode || 'round-robin';
    document.getElementById('proxyEnabled').checked = data.enabled || false;
    document.getElementById('bypassDevPorts').checked = data.bypass_dev_ports || false;

    const status = document.getElementById('proxyStatus');
    const proxies = data.proxies || [];
    if (data.running) {
        status.textContent = `Running (${proxies.length} proxy)`;
        status.style.color = '#4caf50';
    } else if (data.enabled) {
        status.textContent = 'Enabled (not running)';
        status.style.color = '#ff9800';
    } else {
        status.textContent = 'Disabled';
        status.style.color = '';
    }

    // Render proxy list
    const list = document.getElementById('proxyList');
    if (proxies.length === 0) {
        list.innerHTML = '<p class="hint">No proxies added yet.</p>';
        return;
    }
    list.innerHTML = proxies.map((p, i) => `
        <div class="list-item" style="display:flex;justify-content:space-between;align-items:center">
            <div>
                <strong>${p.name || 'Proxy ' + (i+1)}</strong>
                <small style="color:var(--text-dim);margin-left:8px">${p.protocol}://${p.host}:${p.port}</small>
            </div>
            <button class="btn btn-danger" onclick="removeProxy(${i})" style="padding:4px 8px;font-size:12px">x</button>
        </div>
    `).join('');
}

async function addProxy() {
    const data = {
        action: 'add',
        name: document.getElementById('proxyName').value.trim() || undefined,
        protocol: document.getElementById('proxyProtocol').value,
        host: document.getElementById('proxyHost').value.trim(),
        port: document.getElementById('proxyPort').value.trim(),
        username: document.getElementById('proxyUser').value.trim(),
        password: document.getElementById('proxyPass').value.trim(),
    };
    if (!data.host || !data.port) { toast('Host and port required', 'error'); return; }
    const res = await api('proxy', 'POST', data);
    if (res && res.success) {
        toast(res.message);
        // Clear form
        document.getElementById('proxyName').value = '';
        document.getElementById('proxyHost').value = '';
        document.getElementById('proxyPort').value = '';
        document.getElementById('proxyUser').value = '';
        document.getElementById('proxyPass').value = '';
        loadProxy();
    }
}

async function removeProxy(index) {
    const res = await api('proxy', 'POST', { action: 'remove', index });
    if (res && res.success) { toast(res.message); loadProxy(); }
}

async function setProxyMode() {
    const mode = document.getElementById('proxyMode').value;
    const res = await api('proxy', 'POST', { action: 'set_mode', mode });
    if (res && res.success) toast(res.message);
}

async function toggleProxy() {
    const enabled = document.getElementById('proxyEnabled').checked;
    if (enabled) {
        const res = await api('proxy/enable', 'POST');
        if (res) {
            toast(res.message, res.success ? 'success' : 'error');
            if (!res.success) document.getElementById('proxyEnabled').checked = false;
        }
    } else {
        const res = await api('proxy/disable', 'POST');
        if (res) toast(res.message);
    }
    loadProxy();
    setTimeout(refreshStatus, 3000);
}

async function toggleBypassDevPorts() {
    const bypass = document.getElementById('bypassDevPorts').checked;
    const res = await api('proxy', 'POST', { action: 'set_bypass_dev_ports', bypass });
    if (res && res.success) {
        toast(res.message);
    } else {
        document.getElementById('bypassDevPorts').checked = !bypass;
    }
}

async function testProxy() {
    const el = document.getElementById('proxyTestResult');
    el.innerHTML = '<span class="hint">Testing proxy...</span>';
    const res = await api('proxy/test', 'POST');
    if (res && res.success) {
        el.innerHTML = `<span style="color:#4caf50;font-weight:bold">Proxy working</span> — IP: ${res.ip}`;
    } else {
        el.innerHTML = `<span style="color:#f44336;font-weight:bold">${res ? res.message : 'Test failed'}</span>`;
    }
}

// ─── Logs ────────────────────────────────────

async function loadLogs() {
    const source = document.getElementById('logSource').value;
    const el = document.getElementById('logContent');
    el.textContent = 'Loading...';
    const data = await api(`logs?source=${source}`);
    if (data) {
        el.textContent = data.content || '(empty)';
        el.scrollTop = el.scrollHeight;
    }
}

async function clearLogs() {
    const source = document.getElementById('logSource').value;
    const data = await api(`logs?source=${source}`, 'DELETE');
    if (data) { toast(data.message); loadLogs(); }
}

// ─── Init ───────────────────────────────────

document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
        if (tab.dataset.tab === 'logs') loadLogs();
    });
});

async function init() {
    await Promise.all([
        refreshStatus(), loadVPN(), loadProfiles(), loadBlocking(),
        loadWiFi(), loadMAC(), loadBandwidth(), loadSplit(), loadFirewall(),
        loadPrivacy(), loadDNS(), loadSystem(), loadProxy()
    ]);
    setInterval(refreshStatus, 10000);
    setInterval(loadSystem, 30000);
    setInterval(loadWiFi, 30000);
}

async function logout() {
    try {
        await fetch('/api/auth/logout', { method: 'POST' });
    } catch(e) {}
    window.location.href = '/login';
}

init();
