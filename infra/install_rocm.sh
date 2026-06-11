#!/usr/bin/env bash
# =============================================================================
# SentinelForge — ROCm Installation & Verification
# Checks for AMD ROCm and installs if missing (Ubuntu 22.04/24.04).
# Run:  sudo bash infra/install_rocm.sh
# =============================================================================
set -euo pipefail

echo "=== SentinelForge: ROCm Setup ==="

# Check if rocm-smi is already available
if command -v rocm-smi &>/dev/null; then
    echo "[OK] ROCm already installed: $(rocm-smi --version 2>/dev/null || echo 'version unknown')"
    rocm-smi
    exit 0
fi

# Detect Ubuntu version
if [ -f /etc/os-release ]; then
    . /etc/os-release
    CODENAME="${UBUNTU_CODENAME:-$(lsb_release -cs 2>/dev/null || echo 'jammy')}"
else
    CODENAME="jammy"
fi

echo "[INFO] ROCm not found. Installing ROCm 6.1 for Ubuntu ${CODENAME}..."

# Add ROCm repository
ROCM_DEB="amdgpu-install_6.1.60101-1_all.deb"
wget -q "https://repo.radeon.com/amdgpu-install/latest/ubuntu/${CODENAME}/${ROCM_DEB}" \
    -O "${ROCM_DEB}" || {
    echo "[ERROR] Failed to download AMD GPU installer for ${CODENAME}."
    echo "        Check https://rocm.docs.amd.com for manual installation."
    exit 1
}

sudo apt-get update -qq
sudo apt-get install -y -qq "./${ROCM_DEB}"
rm -f "${ROCM_DEB}"

# Install ROCm with DKMS
sudo amdgpu-install -y --usecase=rocm,dkms

# Add user to video & render groups
sudo usermod -a -G video,render "$USER" 2>/dev/null || true

echo ""
echo "=============================================="
echo " ROCm installation complete."
echo " Please REBOOT or re-login for group changes."
echo " Then run: rocm-smi"
echo "=============================================="
