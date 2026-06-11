#!/usr/bin/env bash
# =============================================================================
# SentinelForge — Start All Services
# 1. Start vulnerable lab containers (optional)
# 2. Verify vLLM is running (or warn if not)
# 3. Start FastAPI backend
# 4. Start Streamlit frontend
# Run:  bash infra/start_all.sh [--with-lab]
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="${SCRIPT_DIR}/.."
WITH_LAB=false

if [[ "${1:-}" == "--with-lab" ]]; then
    WITH_LAB=true
fi

echo "=============================================="
echo "  SentinelForge — Launching All Services"
echo "=============================================="

# --- Lab containers ---
if $WITH_LAB; then
    echo "[0/3] Starting vulnerable lab..."
    bash "${SCRIPT_DIR}/setup_lab.sh"
fi

# --- Check vLLM ---
echo "[1/3] Checking vLLM..."
if curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/health 2>/dev/null | grep -q 200; then
    echo "  [OK] vLLM is running on port 8080"
else
    echo "  [WARN] vLLM not detected. Using API from .env (DeepSeek or other)."
    echo "  To start vLLM: bash infra/setup_vllm.sh"
fi

# --- Start FastAPI ---
echo "[2/3] Starting FastAPI backend..."
cd "${ROOT_DIR}"
# Use uvicorn directly for proper signal handling
# Use uvicorn with package path (backend.server:app) from project root
python -m uvicorn backend.server:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!
echo "  FastAPI started (PID: ${BACKEND_PID})"

# Wait for backend to be ready
echo "  Waiting for backend..."
for i in {1..15}; do
    if curl -s http://localhost:8000/health >/dev/null 2>&1; then
        echo "  [OK] Backend is ready"
        break
    fi
    sleep 1
done

# --- Start Streamlit ---
echo "[3/3] Starting Streamlit frontend..."
streamlit run frontend/app.py --server.port 8501 --server.address 0.0.0.0 &
FRONTEND_PID=$!
echo "  Streamlit started (PID: ${FRONTEND_PID})"

echo ""
echo "=============================================="
echo "  SentinelForge is running!"
echo "  Frontend:  http://localhost:8501"
echo "  Backend:   http://localhost:8000"
echo "  API Docs:  http://localhost:8000/docs"
echo ""
echo "  Press Ctrl+C to stop all services."
echo "=============================================="

# Trap Ctrl+C to clean up
cleanup() {
    echo ""
    echo "Shutting down..."
    kill ${BACKEND_PID} 2>/dev/null || true
    kill ${FRONTEND_PID} 2>/dev/null || true
    wait ${BACKEND_PID} 2>/dev/null || true
    wait ${FRONTEND_PID} 2>/dev/null || true
    echo "Done."
}
trap cleanup INT TERM

# Wait for background processes
wait
