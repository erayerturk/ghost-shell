#!/bin/sh
# ShadowPlug -- Automated Setup Script
# Run on a fresh OpenWrt Pi Zero 2 W via SSH:
#   wget -O- https://raw.githubusercontent.com/erayerturk/shadowplug/main/setup/install.sh | sh
# Or: clone the repo first, then run setup/install.sh

set -e

echo ""
echo "  👻 ShadowPlug Installer"
echo "  ========================"
echo ""

# ─── WiFi Setup ─────────────────────────────

echo "[1/8] WiFi Setup (via Dashboard)"
echo "  WiFi will be configured from the ShadowPlug dashboard."
echo "  Connect via USB first, then go to Network tab to add WiFi."
echo ""

# Enable WiFi radio so scanning works later
uci set wireless.radio0.disabled=0
uci set wireless.@wifi-iface[0].mode='sta'
uci set wireless.@wifi-iface[0].network='wwan'
uci set network.wwan=interface
uci set network.wwan.proto='dhcp'
uci commit
/etc/init.d/network restart

# If already connected to WiFi (re-run), check connectivity
if ping -c 1 -W 3 8.8.8.8 >/dev/null 2>&1; then
    echo "  Already connected to internet!"
else
    echo "  No internet yet — configure WiFi from dashboard after USB setup."
fi

# ─── Prerequisites ──────────────────────────

echo ""
echo "[2/8] Installing packages..."
opkg update
opkg install git git-http wireguard-tools luci-proto-wireguard python3 python3-pip python3-flask kmod-usb-dwc2 kmod-usb-gadget kmod-usb-lib-composite

echo "[3/8] Installing Python dependencies..."
pip3 install flask blinker 2>/dev/null || pip3 install flask

# ─── Clone repo ─────────────────────────────

if [ ! -d "/opt/shadowplug" ]; then
    echo "[*] Cloning ShadowPlug..."
    cd /opt
    git clone https://github.com/erayerturk/shadowplug.git
else
    echo "[*] ShadowPlug already exists, pulling latest..."
    cd /opt/shadowplug && git pull 2>/dev/null || true
fi

# ─── USB Gadget ─────────────────────────────

echo "[4/8] Setting up USB gadget..."
cp /opt/shadowplug/setup/configs/usb-gadget /etc/init.d/usb-gadget
chmod +x /etc/init.d/usb-gadget
/etc/init.d/usb-gadget enable

# Modules
grep -q "dwc2" /etc/modules.d/90-dwc2 2>/dev/null || echo "dwc2" > /etc/modules.d/90-dwc2
grep -q "libcomposite" /etc/modules.d/91-libcomposite 2>/dev/null || echo "libcomposite" > /etc/modules.d/91-libcomposite

# Hotplug
mkdir -p /etc/hotplug.d/net
cp /opt/shadowplug/setup/configs/hotplug-usb-ip /etc/hotplug.d/net/99-usb-ip
chmod +x /etc/hotplug.d/net/99-usb-ip

# USB Watchdog daemon (auto-recover if USB connection drops)
cp /opt/shadowplug/setup/configs/usb-watchdog /usr/bin/usb-watchdog
chmod +x /usr/bin/usb-watchdog
# Add to shadowplug init to start as background daemon
grep -q "usb-watchdog" /etc/init.d/shadowplug 2>/dev/null || true

# ─── Network interfaces ────────────────────

echo "[5/8] Configuring network..."

# USB0 (RNDIS)
uci set network.usb0=interface
uci set network.usb0.proto='static'
uci set network.usb0.ipaddr='192.168.7.1'
uci set network.usb0.netmask='255.255.255.0'
uci set network.usb0.device='usb0'

# USB1 (ECM)
uci set network.usb1=interface
uci set network.usb1.proto='static'
uci set network.usb1.ipaddr='192.168.7.1'
uci set network.usb1.netmask='255.255.255.0'
uci set network.usb1.device='usb1'

# DHCP for USB0
uci set dhcp.usb0=dhcp
uci set dhcp.usb0.interface='usb0'
uci set dhcp.usb0.start='100'
uci set dhcp.usb0.limit='50'
uci set dhcp.usb0.leasetime='12h'
uci delete dhcp.usb0.dhcp_option 2>/dev/null
uci add_list dhcp.usb0.dhcp_option='3,192.168.7.1'
uci add_list dhcp.usb0.dhcp_option='6,192.168.7.1'

# DHCP for USB1
uci set dhcp.usb1=dhcp
uci set dhcp.usb1.interface='usb1'
uci set dhcp.usb1.start='100'
uci set dhcp.usb1.limit='50'
uci set dhcp.usb1.leasetime='12h'
uci delete dhcp.usb1.dhcp_option 2>/dev/null
uci add_list dhcp.usb1.dhcp_option='3,192.168.7.1'
uci add_list dhcp.usb1.dhcp_option='6,192.168.7.1'

uci commit network
uci commit dhcp

# ─── Firewall ──────────────────────────────

echo "[6/8] Configuring firewall..."

# Add USB interfaces to lan zone
uci add_list firewall.@zone[0].network='usb0' 2>/dev/null
uci add_list firewall.@zone[0].network='usb1' 2>/dev/null

# Ensure lan→wg forwarding exists
HAS_FWD=$(uci show firewall 2>/dev/null | grep "forwarding.*src='lan'.*dest='wg'" | wc -l)
if [ "$HAS_FWD" = "0" ]; then
    uci add firewall forwarding
    uci set firewall.@forwarding[-1].src='lan'
    uci set firewall.@forwarding[-1].dest='wg'
fi

uci commit firewall
/etc/init.d/firewall restart

# ─── WireGuard ─────────────────────────────

echo "[7/8] Setting up WireGuard..."

# Generate keys if not exist
if [ ! -f /etc/wireguard/privatekey ]; then
    mkdir -p /etc/wireguard
    wg genkey | tee /etc/wireguard/privatekey | wg pubkey > /etc/wireguard/publickey
    chmod 600 /etc/wireguard/privatekey
fi

PRIVKEY=$(cat /etc/wireguard/privatekey)
PUBKEY=$(cat /etc/wireguard/publickey)

# Check if wg0 interface exists
WG_EXISTS=$(uci show network.wg0 2>/dev/null | wc -l)
if [ "$WG_EXISTS" = "0" ]; then
    uci set network.wg0=interface
    uci set network.wg0.proto='wireguard'
    uci set network.wg0.private_key="$PRIVKEY"
    uci set network.wg0.addresses='10.7.0.2/24'

    # Create wg firewall zone
    uci add firewall zone
    uci set firewall.@zone[-1].name='wg'
    uci set firewall.@zone[-1].network='wg0'
    uci set firewall.@zone[-1].input='ACCEPT'
    uci set firewall.@zone[-1].output='ACCEPT'
    uci set firewall.@zone[-1].forward='ACCEPT'
    uci set firewall.@zone[-1].masq='1'

    uci commit network
    uci commit firewall
fi

echo ""
echo "  Your Pi's public key (add this to your VPN server):"
echo "  $PUBKEY"
echo ""

printf "Configure VPN server now? (y/n): "; read SETUP_VPN

if [ "$SETUP_VPN" = "y" ] || [ "$SETUP_VPN" = "Y" ]; then
    printf "  Server IP: "; read VPN_SERVER
    printf "  Server Port [51820]: "; read VPN_PORT
    VPN_PORT=${VPN_PORT:-51820}
    printf "  Server Public Key: "; read VPN_PUBKEY
    printf "  Client Address [10.7.0.2/24]: "; read VPN_ADDR
    VPN_ADDR=${VPN_ADDR:-10.7.0.2/24}

    uci set network.wg0.addresses="$VPN_ADDR"
    uci add network wireguard_wg0
    uci set network.@wireguard_wg0[-1].public_key="$VPN_PUBKEY"
    uci set network.@wireguard_wg0[-1].endpoint_host="$VPN_SERVER"
    uci set network.@wireguard_wg0[-1].endpoint_port="$VPN_PORT"
    uci set network.@wireguard_wg0[-1].allowed_ips='0.0.0.0/0'
    uci set network.@wireguard_wg0[-1].persistent_keepalive='25'
    uci set network.@wireguard_wg0[-1].route_allowed_ips='1'
    uci commit network

    echo "  VPN configured!"
else
    echo "  Skipped. You can configure VPN from the dashboard later."
fi

# ─── ShadowPlug Web UI ───────────────────

echo "[8/8] Setting up ShadowPlug web UI..."

cp /opt/shadowplug/setup/configs/shadowplug-service /etc/init.d/shadowplug
chmod +x /etc/init.d/shadowplug
/etc/init.d/shadowplug enable

echo ""
echo "╔══════════════════════════════════════╗"
echo "║     ✅ ShadowPlug installed!         ║"
echo "╠══════════════════════════════════════╣"
echo "║                                      ║"
echo "║  1. Connect USB to your computer     ║"
echo "║  2. Open http://192.168.7.1:8080     ║"
echo "║  3. Configure your VPN server        ║"
echo "║                                      ║"
echo "║  LuCI:   http://192.168.7.1          ║"
echo "║  SSH:    ssh root@192.168.7.1         ║"
echo "║                                      ║"
echo "╚══════════════════════════════════════╝"
echo ""
echo "Rebooting in 5 seconds..."
sleep 5
reboot
