#!/bin/bash
# ShadowPlug — VPN Server Setup Script
# Run this on your VPN server (Ubuntu/Debian)

set -e

echo "=== ShadowPlug VPN Server Setup ==="
echo ""

# Install WireGuard
if ! command -v wg &>/dev/null; then
    echo "[*] Installing WireGuard..."
    apt update && apt install -y wireguard
else
    echo "[✓] WireGuard already installed"
fi

# Enable IP forwarding
echo "[*] Enabling IP forwarding..."
sysctl -w net.ipv4.ip_forward=1
grep -q "net.ipv4.ip_forward=1" /etc/sysctl.conf || echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf

# Generate server keys
if [ ! -f /etc/wireguard/server_private ]; then
    echo "[*] Generating server keys..."
    wg genkey | tee /etc/wireguard/server_private | wg pubkey > /etc/wireguard/server_public
    chmod 600 /etc/wireguard/server_private
else
    echo "[✓] Server keys exist"
fi

# Generate client keys (for ShadowPlug Pi)
if [ ! -f /etc/wireguard/client_private ]; then
    echo "[*] Generating client keys..."
    wg genkey | tee /etc/wireguard/client_private | wg pubkey > /etc/wireguard/client_public
    chmod 600 /etc/wireguard/client_private
else
    echo "[✓] Client keys exist"
fi

# Detect primary interface
IFACE=$(ip route | grep default | awk '{print $5}' | head -1)
echo "[*] Detected interface: $IFACE"

# Server IP
SERVER_IP=$(curl -s4 ifconfig.me)
echo "[*] Server IP: $SERVER_IP"

# Create config
echo "[*] Creating WireGuard config..."
cat > /etc/wireguard/wg0.conf << EOF
[Interface]
PrivateKey = $(cat /etc/wireguard/server_private)
Address = 10.7.0.1/24
ListenPort = 51820
PostUp = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o $IFACE -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o $IFACE -j MASQUERADE

[Peer]
# ShadowPlug Pi
PublicKey = $(cat /etc/wireguard/client_public)
AllowedIPs = 10.7.0.2/32
EOF

# Start WireGuard
echo "[*] Starting WireGuard..."
systemctl enable wg-quick@wg0
systemctl restart wg-quick@wg0

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Enter these values in the ShadowPlug dashboard (VPN tab):"
echo ""
echo "  Endpoint:          $SERVER_IP"
echo "  Port:              51820"
echo "  Private Key:       $(cat /etc/wireguard/client_private)"
echo "  Server Public Key: $(cat /etc/wireguard/server_public)"
echo "  Address:           10.7.0.2/24"
echo "  Allowed IPs:       0.0.0.0/0"
echo ""
echo "Server Public Key:   $(cat /etc/wireguard/server_public)"
echo "Client Public Key:   $(cat /etc/wireguard/client_public)"
echo ""
