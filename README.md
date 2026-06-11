# 🛡️ SentinelForge — Autonomous Pentest Agent on AMD ROCm

> **Hackathon project** · AMD Developer Hackathon: ACT II  
> Fully local, LLM-driven penetration testing agent with real-time attack graph visualization.  
> Built for AMD Instinct MI300X + ROCm.

---

## 🎯 Concept

SentinelForge is an autonomous security audit agent that:

1. Takes a **target IP** from the user
2. Runs **nmap reconnaissance** to discover open services
3. Maps discovered services to **known Metasploit exploits**
4. Executes exploitation modules via **Metasploit Framework**
5. Verifies results and generates a **final pentest report**

Everything runs **100% locally** — LLM inference, tool execution, and visualization.
No data leaves the machine.

---

## 🏗️ Architecture

```
[Streamlit UI] ← HTTP/WS → [FastAPI Server] → [Agent Core]
                                            ↳ [LLM Client] → vLLM / DeepSeek API
                                            ↳ [Tool Executor] → Nmap, Metasploit, Hydra
                                            ↳ [Attack Memory] → ChromaDB / Dict fallback
                                            ↳ [Attack Graph] → NetworkX → Cytoscape.js
```

| Layer | Technology |
|-------|-----------|
| Frontend | Streamlit + Cytoscape.js + WebSocket |
| Backend | FastAPI + asyncio |
| LLM | vLLM (WhiteRabbitNeo-33B-AWQ) or DeepSeek API |
| Tools | Nmap, Metasploit, Hydra (real + Docker fallback) |
| GPU | AMD ROCm (MI300X) |
| Memory | ChromaDB with SentenceTransformer embeddings |

---

## 📁 Project Structure

```
SentinelForge/
├── backend/
│   ├── server.py           # FastAPI + WebSocket server
│   ├── agent_core.py       # Autonomous agent loop (kill-chain)
│   ├── llm_client.py       # Provider-agnostic OpenAI-compatible client
│   ├── parser.py           # Robust LLM response parser (JSON recovery)
│   ├── tool_executor.py    # Nmap, Metasploit, Hydra executor
│   ├── attack_graph.py     # NetworkX attack graph builder
│   ├── memory.py           # ChromaDB-backed attack memory
│   └── utils.py            # Config loader, logging
├── frontend/
│   ├── app.py              # Streamlit UI (thought stream, graph, GPU monitor)
│   └── components/
│       ├── graph_view.py   # Cytoscape.js graph renderer
│       └── gpu_monitor.py  # ROCm GPU metrics display
├── infra/
│   ├── install_rocm.sh     # AMD ROCm installer
│   ├── setup_vllm.sh       # vLLM Docker container launcher
│   ├── setup_lab.sh        # Vulnerable lab (DVWA + Metasploitable2 + MSF)
│   └── start_all.sh        # One-command launch
├── vuln_lab/
│   ├── docker-compose.yml  # DVWA, Metasploitable2, MSF, Hydra containers
│   └── targets.json        # Target definitions
├── tests/
│   ├── test_parser.py      # 11 parser scenarios
│   ├── test_modules.py     # 18 unit/integration tests
│   └── test_e2e.py         # 5 end-to-end agent tests
├── requirements.txt        # Pinned dependencies
├── .env.example            # Configuration template
└── README.md
```

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Docker (for lab targets and Metasploit)
- AMD ROCm (for GPU-accelerated LLM inference) — optional, works with API too

### 1. Clone & Install

```bash
git clone https://github.com/ambartsumov/SentinelForge.git
cd SentinelForge
cp .env.example .env
# Edit .env with your API keys
pip install -r requirements.txt
```

### 2. (Optional) Start Vulnerable Lab

```bash
bash infra/setup_lab.sh
```

This launches DVWA (port 8081), Metasploitable2 (ports 21, 22, 80, 1524),
Metasploit Framework, and Hydra containers.

### 3. (Optional) Start Local vLLM

```bash
bash infra/setup_vllm.sh
# Then update .env:
# LLM_BASE_URL=http://localhost:8080/v1
# LLM_API_KEY=not-needed
# LLM_MODEL=TheBloke/WhiteRabbitNeo-33B-AWQ
```

### 4. Launch Everything

```bash
bash infra/start_all.sh --with-lab
```

- **Frontend**: http://localhost:8501
- **Backend API**: http://localhost:8000/docs
- **WebSocket**: ws://localhost:8000/ws

### 5. Run a Pentest

1. Open http://localhost:8501
2. Enter target IP (e.g., `sentinel-metasploitable2`)
3. Click **🚀 Launch Attack**
4. Watch the agent think, scan, exploit, and report — in real time.

---

## 🧠 How the Agent Works

### Kill-Chain Steps

```
Step 1: nmap_scan      → Discover open ports & services
Step 2: Service ID     → Map versions to known exploits
Step 3: msf_module     → Execute Metasploit exploit
Step 4: Verification   → Confirm exploitation success
Step 5: FINISH         → Generate report
```

### LLM Response Format

```
Thought: <tactical reasoning — what was found, why this module>
Action: <tool_name>
Action Input: <valid JSON parameters>
```

The parser handles malformed JSON, single quotes, missing commas,
markdown fences, and extraneous conversational text.

### Tool Result Integrity

Every tool returns a `ToolResult(output, real)` — the agent distinguishes
**real** tool execution from **simulated** results. The final report includes
an integrity warning if any tools were mocked.

---

## 🧪 Tests

```bash
pytest tests/ -v
```

```
34 passed
├── test_parser.py      — 11 tests (format parsing, JSON recovery, edge cases)
├── test_modules.py     — 18 tests (unit + integration smoke tests)
└── test_e2e.py         — 5 tests (full kill-chain with mock LLM)
```

---

## ⚙️ Configuration (`.env`)

| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_BASE_URL` | OpenAI-compatible endpoint | `https://api.deepseek.com/v1` |
| `LLM_API_KEY` | API key | — |
| `LLM_MODEL` | Model name | `deepseek-chat` |
| `AGENT_MAX_STEPS` | Max agent loop iterations | `12` |
| `AGENT_TOOL_TIMEOUT` | Tool execution timeout (seconds) | `30` |
| `MSF_CONTAINER` | Docker container name for Metasploit | `sentinel-msf` |
| `HYDRA_CONTAINER` | Docker container name for Hydra | `sentinel-hydra` |
| `HOST` | Server bind address | `0.0.0.0` |
| `PORT` | Server port | `8000` |

---

## 🔒 Security Notes

- **Use only on systems you own or are authorized to test.**
- The project is designed for **authorized security audits**.
- CORS is open for development — restrict in production.
- Rotate API keys regularly.

---

## 📄 License

MIT — see [LICENSE](LICENSE) file.

---

## 👤 Author

**ambartsumov** — AMD Developer Hackathon: ACT II

---

## 🏆 Hackathon Pitch

| Criteria | How SentinelForge Delivers |
|----------|---------------------------|
| **Innovation** | First fully local, LLM-driven autonomous pentest agent on AMD ROCm |
| **Technical** | Streaming architecture, real-time attack graph, tool integrity verification |
| **Business** | On-premise security auditing for air-gapped environments; no data leakage |
| **Scalability** | Modular design — swap LLM providers, add tools, extend kill-chain |
