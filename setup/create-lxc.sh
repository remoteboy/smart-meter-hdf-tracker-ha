#!/bin/bash
# Run this on your Proxmox HOST (not inside a container)
# Creates a lightweight Debian LXC for the ESB energy tracker
# Usage: bash create-lxc.sh

set -e

CTID=200          # Change if 200 is taken
HOSTNAME="esb-energy"
STORAGE="local-lvm"   # Change to your storage pool (check: pvesm status)
BRIDGE="vmbr0"
IP="192.168.1.200/24"  # Change to a free IP on your LAN
GW="192.168.1.1"       # Your router IP
MEMORY=256
CORES=1
DISK=4  # GB

echo "==> Downloading Debian template (if needed)..."
pveam update
pveam download local debian-12-standard_12.7-1_amd64.tar.zst 2>/dev/null || true

TEMPLATE=$(pveam list local | grep "debian-12-standard" | tail -1 | awk '{print $1}')

echo "==> Creating LXC container $CTID..."
pct create $CTID $TEMPLATE \
  --hostname $HOSTNAME \
  --storage $STORAGE \
  --rootfs ${STORAGE}:${DISK} \
  --memory $MEMORY \
  --cores $CORES \
  --net0 name=eth0,bridge=$BRIDGE,ip=$IP,gw=$GW \
  --unprivileged 1 \
  --features nesting=1 \
  --start 1 \
  --onboot 1

echo "==> Waiting for container to start..."
sleep 5

echo "==> Installing dependencies inside container..."
pct exec $CTID -- bash -c "
  apt-get update -qq
  apt-get install -y -qq python3 python3-pip python3-venv curl git sqlite3 \
    chromium chromium-driver

  # Create app user
  useradd -m -s /bin/bash esb
  mkdir -p /opt/esb-energy
  chown esb:esb /opt/esb-energy
"

echo ""
echo "==> LXC created and running!"
echo "    Container ID : $CTID"
echo "    IP Address   : ${IP%/*}"
echo ""
echo "Next: Copy the app files and run setup/install.sh inside the container:"
echo "  pct push $CTID ./esb-energy.tar.gz /opt/esb-energy.tar.gz"
echo "  pct exec $CTID -- bash /opt/esb-energy/setup/install.sh"
