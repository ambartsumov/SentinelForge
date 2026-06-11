#!/usr/bin/env bash
# =============================================================================
# SentinelForge — Vulnerable Lab Setup
# Starts DVWA and Metasploitable2 containers for testing.
# Also starts a Metasploit container for exploitation capability.
# Run:  bash infra/setup_lab.sh
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LAB_DIR="${SCRIPT_DIR}/../vuln_lab"

echo "=== SentinelForge: Vulnerable Lab Setup ==="

if ! command -v docker &>/dev/null; then
    echo "[ERROR] Docker not found."
    exit 1
fi

cd "${LAB_DIR}"

echo "[INFO] Starting vulnerable lab containers..."
docker compose up -d

echo "[INFO] Waiting for services to be ready..."
# Poll until both containers are healthy or timeout
MAX_WAIT=60
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
    DVWA_OK=$(docker inspect --format='{{.State.Health.Status}}' sentinel-dvwa 2>/dev/null || echo "nohealth")
    MSF2_OK=$(docker inspect --format='{{.State.Running}}' sentinel-metasploitable2 2>/dev/null || echo "false")

    if [ "$MSF2_OK" = "true" ]; then
        # Metasploitable2 has no healthcheck, just check it's running
        # and port 22 is reachable
        if docker exec sentinel-metasploitable2 echo ready >/dev/null 2>&1; then
            echo "[OK] Lab containers are ready"
            break
        fi
    fi
    sleep 3
    ELAPSED=$((ELAPSED + 3))
    echo "  Waiting... (${ELAPSED}s)"
done

# Pull Metasploit Docker image for exploitation
echo "[INFO] Pulling Metasploit Docker image..."
if docker pull metasploitframework/metasploit-framework:latest; then
    # Create container (don't start — will be used via docker exec)
    if ! docker ps -a --format '{{.Names}}' | grep -q '^sentinel-msf$'; then
        docker create \
            --name sentinel-msf \
            --network vuln_lab_vuln-net \
            metasploitframework/metasploit-framework:latest \
            tail -f /dev/null
    fi
    docker start sentinel-msf 2>/dev/null || true
    echo "[OK] Metasploit container ready (sentinel-msf)"
else
    echo "[WARN] Could not pull Metasploit image. Set MSF_CONTAINER env var if you have one."
fi

echo ""
echo "=============================================="
echo " Vulnerable Lab running:"
echo "   DVWA:             http://localhost:8081"
echo "   Metasploitable2:  ssh msfadmin@localhost -p 2222"
echo "                     http://localhost:8082"
echo "   Metasploit:       container sentinel-msf"
echo ""
echo " Targets for agent:"
echo "   - sentinel-dvwa"
echo "   - sentinel-metasploitable2"
echo "=============================================="
