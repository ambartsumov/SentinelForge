#!/usr/bin/env bash
# =============================================================================
# SentinelForge — vLLM Setup Script
# Pulls and starts the vLLM Docker container on port 8080.
# Model: TheBloke/WhiteRabbitNeo-33B-AWQ (security-tuned LLM).
# Prerequisites: ROCm installed, Docker with GPU access.
# Run:  bash infra/setup_vllm.sh [model] [port]
# =============================================================================
set -euo pipefail

MODEL="${1:-TheBloke/WhiteRabbitNeo-33B-AWQ}"
PORT="${2:-8080}"
VLLM_IMAGE="rocm/vllm:rocm6.2"

echo "=== SentinelForge: vLLM Setup ==="

# Check Docker
if ! command -v docker &>/dev/null; then
    echo "[ERROR] Docker not found. Install Docker first."
    exit 1
fi

# Check ROCm
if ! command -v rocm-smi &>/dev/null; then
    echo "[WARN] rocm-smi not found. GPU acceleration may not work."
    echo "      Install ROCm: sudo bash infra/install_rocm.sh"
fi

# Pull vLLM ROCm image
echo "[INFO] Pulling vLLM ROCm Docker image: ${VLLM_IMAGE}..."
if ! docker pull "${VLLM_IMAGE}" 2>/dev/null; then
    echo "[WARN] Failed to pull ${VLLM_IMAGE}. Trying rocm/vllm:rocm6.1..."
    VLLM_IMAGE="rocm/vllm:rocm6.1"
    if ! docker pull "${VLLM_IMAGE}" 2>/dev/null; then
        echo "[WARN] Cannot pull ROCm vLLM image. Trying vllm/vllm-openai:latest..."
        VLLM_IMAGE="vllm/vllm-openai:latest"
        docker pull "${VLLM_IMAGE}" || {
            echo "[ERROR] Cannot pull any vLLM image. Check Docker Hub connectivity."
            exit 1
        }
    fi
fi

# Stop existing container if any
if docker ps -a --format '{{.Names}}' | grep -q '^sentinel-vllm$'; then
    echo "[INFO] Stopping existing sentinel-vllm container..."
    docker stop sentinel-vllm 2>/dev/null || true
    docker rm sentinel-vllm 2>/dev/null || true
fi

echo "[INFO] Starting vLLM server on port ${PORT}..."
docker run -d \
    --name sentinel-vllm \
    --device /dev/kfd \
    --device /dev/dri \
    --group-add video \
    --security-opt seccomp=unconfined \
    -p "${PORT}:8000" \
    "${VLLM_IMAGE}" \
    --model "${MODEL}" \
    --host 0.0.0.0 \
    --port 8000 \
    --max-model-len 4096 \
    --dtype auto

echo ""
echo "[INFO] Waiting for vLLM to load the model (may take 2-5 minutes)..."
echo "       Check progress: docker logs -f sentinel-vllm"

# Wait up to 5 minutes for vLLM health check
MAX_WAIT=300
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${PORT}/health" 2>/dev/null || echo "000")
    if [ "$STATUS" = "200" ]; then
        echo ""
        echo "=============================================="
        echo " [OK] vLLM is ready on http://localhost:${PORT}"
        echo " Model: ${MODEL}"
        echo " Health: curl http://localhost:${PORT}/health"
        echo "=============================================="
        exit 0
    fi
    sleep 10
    ELAPSED=$((ELAPSED + 10))
    echo "  Waiting... (${ELAPSED}s, status=${STATUS})"
done

echo ""
echo "[WARN] vLLM did not become healthy within 5 minutes."
echo "       Check logs: docker logs sentinel-vllm"
echo "       Container may still be loading the model."
