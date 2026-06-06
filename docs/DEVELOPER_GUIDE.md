# Developer Guide — Local AI Coding Stack

এই ডকুমেন্টটা পুরো system-এর **single source of truth**। প্রতিটা component, configuration option, data flow, command, আর failure mode এখানে deep detail-এ আছে।

পড়ার ক্রম:
- নতুন হলে → Section 1 → 2 → 4 → 5
- শুধু configure করতে → Section 6
- কিছু ভাঙলে → Section 11
- নিজে extend করতে → Section 12

> Related docs: [`RAG_API.md`](RAG_API.md) (RAG internals line-by-line), [`CLINE_SETUP.md`](CLINE_SETUP.md) (Cline step-by-step), [`../README.md`](../README.md) (quick start)।

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture & Data Flow](#2-architecture--data-flow)
3. [Repository Layout](#3-repository-layout)
4. [Prerequisites](#4-prerequisites)
5. [Quick Start](#5-quick-start)
6. [Configuration Reference](#6-configuration-reference)
7. [Service Deep Dive](#7-service-deep-dive)
8. [Indexer Internals](#8-indexer-internals)
9. [RAG API Internals](#9-rag-api-internals)
10. [MCP Server & Cline Integration](#10-mcp-server--cline-integration)
11. [Operations & Troubleshooting](#11-operations--troubleshooting)
12. [Extending the System](#12-extending-the-system)
13. [Security & Limitations](#13-security--limitations)
14. [FAQ](#14-faq)
15. [Command Cheat Sheet](#15-command-cheat-sheet)

---

## 1. System Overview

এটা একটা **fully local, Docker-based AI coding backend**। লক্ষ্য: Cursor-এর মতো "project-aware AI" — কিন্তু তোমার নিজের মেশিনে, কোনো API key বা cloud ছাড়া।

কী কী দেয়:

| Capability | কোন component দেয় |
|------------|-------------------|
| Local LLM (chat/coding) | Ollama |
| Embeddings (text → vector) | Ollama + `nomic-embed-text` |
| Vector memory (semantic search) | Qdrant |
| Repo scan + incremental index | Indexer (Python daemon) |
| Search/Context HTTP API | RAG API (FastAPI) |
| Cline auto-search tool | MCP server |
| Browser chat UI | Open WebUI |
| Filesystem MCP (optional) | mcp-filesystem |

কী **নয়**: এটা Cursor-এর ১০০% replacement না। Cursor-এর proprietary realtime ranking আর IDE-deep integration নেই। বাস্তবে এটা ~80-85% Cursor-like local experience।

---

## 2. Architecture & Data Flow

### Component map

```text
┌──────────────────────── Docker network: ollama-ai_ai-net ────────────────────────┐
│                                                                                   │
│   ollama (:11434)         qdrant (:6333)        open-webui (:8801)                │
│   - chat models           - vector DB           - browser chat                    │
│   - embed model           - "codebase" coll.                                      │
│        ▲   ▲                   ▲   ▲                                               │
│        │   │                   │   │                                               │
│   embed│   │embed         write│   │search                                        │
│        │   │                   │   │                                               │
│   ┌────┴───┴───┐          ┌────┴───┴────┐        ┌──────────────┐                 │
│   │  indexer   │──write──▶│   (qdrant)  │◀─read──│   rag-api    │ (:8011)         │
│   │  (daemon)  │          └─────────────┘        │  /search     │                 │
│   └─────▲──────┘                                 │  /context    │                 │
│         │ scan                                   │  /health     │                 │
│   ./projects (bind mount)                        └──────▲───────┘                 │
│                                                         │ HTTP                     │
│                                                  ┌──────┴───────┐                  │
│                                                  │  mcp-rag     │ (stdio, on-demand)│
│                                                  │  tools:      │                  │
│                                                  │  search_*    │                  │
│                                                  └──────▲───────┘                  │
└─────────────────────────────────────────────────────────┼────────────────────────┘
                                                            │ docker run -i (launched by Cline)
                                                     ┌──────┴───────┐
                                                     │ VS Code +    │
                                                     │ Cline        │
                                                     └──────────────┘
```

### দুইটা প্রধান flow

**A) Write flow (indexing) — background, continuous:**

```text
file in ./projects
  → indexer reads + hashes
  → unchanged? skip
  → changed? chunk → Ollama embed → upsert into Qdrant (with payload)
  → save state to projects/.indexer_state.json
  → sleep SCAN_INTERVAL, repeat
```

**B) Read flow (query) — on demand:**

```text
query (curl / Cline tool / Open WebUI)
  → rag-api embeds query via Ollama
  → Qdrant nearest-neighbour search
  → return chunks (file_path, lines, content, score)
  → /context also packs them into a size-limited block
```

দুই flow-কে যুক্ত রাখে: একই `COLLECTION_NAME` + একই `EMBED_MODEL`।

---

## 3. Repository Layout

```text
ollama-ai/
├── docker-compose.yml              # সব service-এর definition
├── .env.example                    # configurable defaults (copy → .env)
├── README.md                       # quick start
├── docs/
│   ├── DEVELOPER_GUIDE.md          # এই ফাইল
│   ├── RAG_API.md                  # RAG/API internals (line-by-line)
│   └── CLINE_SETUP.md              # Cline configuration walkthrough
├── scripts/
│   └── pull-models.sh              # model bootstrap (model-init চালায়)
├── indexer/                        # codebase scanner daemon
│   ├── app.py
│   ├── Dockerfile
│   └── requirements.txt
├── rag-api/                        # FastAPI search/context service
│   ├── app.py
│   ├── Dockerfile
│   └── requirements.txt
├── mcp-server/                     # stdio MCP server for Cline
│   ├── server.py
│   ├── Dockerfile
│   ├── requirements.txt
│   └── cline-mcp-settings.example.json
└── projects/                       # ← তোমার codebase এখানে রাখো
    ├── .gitignore                  # সব ignore করে, শুধু .gitignore track করে
    └── .indexer_state.json         # auto-generated index state (hand দিও না)
```

> `projects/.gitignore` ইচ্ছাকৃতভাবে সব ignore করে — যাতে তোমার আসল source code ভুলে git-এ commit না হয়।

---

## 4. Prerequisites

| Tool | Version | কেন |
|------|---------|-----|
| Docker | 24+ | container runtime |
| Docker Compose | v2+ | multi-service orchestration |
| Disk | ~10-20 GB free | coding models + vector/cache data |
| RAM | 16 GB+ recommended | 30 GB হলে আরাম |
| GPU | optional | CPU চলে, কিন্তু slow |

হার্ডওয়্যার নোট (তোমার Ryzen 7 5700G + 30 GB, no dGPU):
- coding/chat → `qwen2.5-coder:7b`
- autocomplete → `qwen2.5-coder:1.5b-base`
- embedding (`nomic-embed-text`) হালকা, CPU-তে দ্রুত।

যাচাই:

```bash
docker --version
docker compose version
df -h .
```

---

## 5. Quick Start

```bash
cd ollama-ai

# 1) (optional) custom config
cp .env.example .env

# 2) তোমার project রাখো
cp -r /path/to/AssureOps-V3 projects/

# 3) main stack চালু (প্রথমবার model download হবে — ধৈর্য ধরো)
docker compose up -d --build

# 4) MCP image build (Cline-এর জন্য, একবার)
docker compose --profile mcp build mcp-rag

# 5) progress দেখো
docker compose logs -f model-init      # model pull complete?
docker compose logs -f indexer         # "Scan complete. Total files in index: N"

# 6) verify
curl http://localhost:8011/health      # {"status":"ok"}
```

Endpoints:

| Service | URL |
|---------|-----|
| Ollama API | http://localhost:11434 |
| RAG API | http://localhost:8011 |
| RAG Swagger UI | http://localhost:8011/docs |
| Qdrant dashboard | http://localhost:6333/dashboard |
| Open WebUI | http://localhost:8801 |

---

## 6. Configuration Reference

### 6.1 `.env` variables

এগুলো `docker-compose.yml`-এ `${VAR:-default}` দিয়ে পড়া হয়। `.env` না থাকলে default ব্যবহার হয়।

| Variable | Default | কে ব্যবহার করে | মানে |
|----------|---------|----------------|------|
| `CHAT_MODELS` | *(empty if no `.env`)* | model-init | কোন chat model গুলো pull হবে (space-separated) |
| `REASONING_MODELS` | *(empty)* | model-init | extra reasoning model (যেমন `deepseek-r1:14b`) |
| `AUTOCOMPLETE_MODELS` | *(empty)* | model-init | Continue/FIM autocomplete model |
| `EMBED_MODEL` | `nomic-embed-text` | model-init, indexer, rag-api | embedding model |
| `COLLECTION_NAME` | `codebase` | indexer, rag-api | Qdrant collection নাম |
| `SCAN_INTERVAL` | `30` | indexer | কত সেকেন্ড পরপর rescan |

> ⚠️ Golden rule: `EMBED_MODEL` আর `COLLECTION_NAME` indexer ও rag-api-তে **সবসময় একই** হতে হবে। `.env` দিয়ে সেট করলে দুটোই একসাথে পায়, তাই mismatch হয় না।

বর্তমান `.env.example`:

```bash
CHAT_MODELS=qwen2.5-coder:7b
AUTOCOMPLETE_MODELS=qwen2.5-coder:1.5b-base
# REASONING_MODELS=deepseek-r1:14b
EMBED_MODEL=nomic-embed-text
COLLECTION_NAME=codebase
SCAN_INTERVAL=60
```

Coding-only setup-এ recommended two models:

```bash
CHAT_MODELS=qwen2.5-coder:7b
AUTOCOMPLETE_MODELS=qwen2.5-coder:1.5b-base
```

পরিবর্তনের পর:

```bash
docker compose up -d            # model-init আবার চলবে, নতুন model pull করবে
```

### 6.2 Per-service environment (compose-এ hardcoded)

| Service | Env | Value |
|---------|-----|-------|
| indexer | `OLLAMA_URL` | `http://ollama:11434` |
| indexer | `QDRANT_URL` | `http://qdrant:6333` |
| indexer | `PROJECTS_DIR` | `/projects` |
| rag-api | `OLLAMA_URL` | `http://ollama:11434` |
| rag-api | `QDRANT_URL` | `http://qdrant:6333` |
| mcp-rag | `RAG_API_URL` | `http://ai-rag-api:8011` |

> এগুলো **container-to-container** URL (service নাম দিয়ে)। Host থেকে access করতে `localhost:<port>` ব্যবহার করো।

### 6.3 Indexer code-level config (`indexer/app.py`)

| Setting | Default | পরিবর্তন কোথায় |
|---------|---------|----------------|
| Supported extensions | `.ts .tsx .js .jsx .py .go .json .md .txt .html .css .rs .java .cpp .c .h .sh .yml .yaml` | `SUPPORTED_EXTENSIONS` |
| Ignored dirs | `node_modules .git dist build venv .venv __pycache__ target out .idea .vscode` + dot-dirs | `IGNORE_DIRS` |
| Chunk size | ~1500 chars | `chunk_file(max_chars=1500)` |
| Chunk overlap | 5 lines | `chunk_file(overlap_lines=5)` |
| Vector size | 768 (nomic) | `VectorParams(size=768)` |

### 6.4 RAG API request params

| Endpoint | Param | Range | Default |
|----------|-------|-------|---------|
| `/search`, `/context` | `query` | non-empty | required |
| `/search`, `/context` | `limit` | 1–50 | 8 |
| `/search`, `/context` | `score_threshold` | 0.0–1.0 | none |
| `/context` only | `max_chars` | 1000–30000 | 9000 |

### 6.5 Cline MCP config

ফাইল: `mcp-server/cline-mcp-settings.example.json` → Cline-এর `cline_mcp_settings.json`-এ paste।

```json
{
  "mcpServers": {
    "local-codebase-rag": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "--network", "ollama-ai_ai-net",
        "-e", "RAG_API_URL=http://ai-rag-api:8011",
        "ollama-ai-mcp-rag:latest"
      ],
      "disabled": false,
      "autoApprove": ["search_codebase", "get_context"]
    }
  }
}
```

---

## 7. Service Deep Dive

### 7.1 ollama

```yaml
ollama:
  image: ollama/ollama
  ports: ["11434:11434"]
  volumes: [ollama_data:/root/.ollama]
  healthcheck: ollama list (10s interval, 12 retries)
```

- LLM runtime। chat ও embedding দুটোই serve করে।
- Models `ollama_data` named volume-এ থাকে → restart-এ re-download হয় না।
- Healthcheck গুরুত্বপূর্ণ: model-init/indexer/rag-api এর `service_healthy`-এর জন্য অপেক্ষা করে।

### 7.2 model-init

```yaml
model-init:
  image: ollama/ollama
  depends_on: { ollama: service_healthy }
  entrypoint: ["/bin/sh", "/scripts/pull-models.sh"]
  restart: "no"
```

- এক-শট bootstrap container। `scripts/pull-models.sh` চালিয়ে সব model pull করে, তারপর exit করে।
- `restart: "no"` — কাজ শেষে আর চালু থাকে না। indexer এর `service_completed_successfully`-এর জন্য অপেক্ষা করে।
- Script env-safe (missing model list-এ crash করে না)।

### 7.3 qdrant

```yaml
qdrant:
  image: qdrant/qdrant
  ports: ["6333:6333"]
  volumes: [qdrant_data:/qdrant/storage]
```

- Vector database। `codebase` collection-এ সব embedding থাকে।
- Dashboard: http://localhost:6333/dashboard
- Data `qdrant_data` volume-এ persist।

### 7.4 open-webui

```yaml
open-webui:
  image: ghcr.io/open-webui/open-webui:main
  ports: ["8801:8080"]
  environment: [OLLAMA_BASE_URL=http://ollama:11434]
```

- ব্রাউজার chat UI। প্রথম visit-এ local admin account বানাতে হয়।
- RAG ব্যবহার করে না — শুধু সরাসরি Ollama chat। (RAG চাইলে `/context` output paste করো।)

### 7.5 indexer

Section 8 দেখো।

### 7.6 rag-api

Section 9 দেখো।

### 7.7 mcp-rag (profile: mcp)

Section 10 দেখো।

### 7.8 mcp-filesystem (profile: mcp, optional)

```yaml
mcp-filesystem:
  image: node:22-alpine
  command: npx -y @modelcontextprotocol/server-filesystem /projects
```

- Optional stdio MCP filesystem server। সাধারণত দরকার নেই — Cline/Roo নিজেই workspace file পড়ে।
- চালাতে: `docker compose --profile mcp up -d mcp-filesystem`

---

## 8. Indexer Internals

ফাইল: `indexer/app.py`। এটা একটা infinite-loop daemon।

### Lifecycle

```text
main()
 ├─ wait_for_services()        # Qdrant /healthz + Ollama / পর্যন্ত অপেক্ষা
 ├─ ensure_ollama_model()      # EMBED_MODEL না থাকলে pull
 ├─ create collection (768, COSINE) if missing
 ├─ load_state()               # projects/.indexer_state.json
 └─ loop forever:
      ├─ os.walk(/projects), IGNORE_DIRS বাদ
      ├─ প্রতিটা supported file:
      │    ├─ mtime+size একই? → skip
      │    ├─ md5 hash একই? → state update, skip
      │    └─ changed/new? → chunk → embed → delete old points → upsert
      ├─ deleted files → Qdrant থেকে points মুছে দাও
      ├─ save_state()
      └─ sleep(SCAN_INTERVAL)
```

### Chunking (`chunk_file`)

- Line-based, ~1500 char/chunk, শেষ 5 line পরের chunk-এ overlap (context ধরে রাখতে)।
- প্রতিটা chunk-এ `start_line`, `end_line` track হয় → search result-এ line range পাওয়া যায়।

### Embedding (`get_embeddings`)

- আগে batch `/api/embed` try করে, fallback single `/api/embeddings`।
- ব্যর্থ হলে 768-dim zero vector বসায় (batch alignment ভাঙে না)।

### Incremental strategy

তিন স্তর change detection (দ্রুত → নিশ্চিত):
1. `mtime` + `size` মিললে skip (ফাইল খোলে না)।
2. না মিললে md5 hash চেক — content একই হলে skip।
3. সত্যিই বদলালে re-index।

State: `projects/.indexer_state.json` — প্রতি ফাইলের `{mtime, size, hash}`।

### Qdrant point structure

```python
PointStruct(
    id=uuid5(NAMESPACE_DNS, f"{rel_path}_{idx}_{start_line}"),  # deterministic
    vector=embedding,
    payload={"file_path", "start_line", "end_line", "content"},
)
```

Deterministic id মানে একই chunk re-index করলে নতুন duplicate না বানিয়ে overwrite হয়।

---

## 9. RAG API Internals

ফাইল: `rag-api/app.py` (FastAPI)। বিস্তারিত line-by-line আছে [`RAG_API.md`](RAG_API.md)-এ। এখানে সারাংশ।

### Endpoints

| Method | Path | Body | Returns |
|--------|------|------|---------|
| GET | `/health` | – | `{"status":"ok"}` |
| POST | `/search` | `{query, limit, score_threshold}` | `{query, results[]}` |
| POST | `/context` | `{query, limit, score_threshold, max_chars}` | `{query, context, results[]}` |
| GET | `/docs` | – | Swagger UI |

### `embed()` — version-resilient

Ollama-র নতুন (`/api/embed`) আর পুরোনো (`/api/embeddings`) দুই API handle করে। ব্যর্থ হলে `502`।

### `search_points()` — client-resilient

```python
if hasattr(qdrant, "search"):
    points = qdrant.search(...)          # পুরোনো qdrant-client
else:
    points = qdrant.query_points(...).points   # নতুন qdrant-client (1.27+)
```

> এই compatibility layer-ই আগের `'QdrantClient' object has no attribute 'search'` bug ঠিক করেছিল।

### Response shape

```json
{
  "query": "...",
  "results": [
    {"score": 0.61, "file_path": "...", "start_line": 36, "end_line": 74, "content": "..."}
  ]
}
```

`/context` অতিরিক্ত একটা `context` string দেয় — header সহ block, `max_chars` budget মেনে।

---

## 10. MCP Server & Cline Integration

ফাইল: `mcp-server/server.py` (FastMCP, stdio transport)।

### কীভাবে কাজ করে

- Cline `docker run -i --rm ... ollama-ai-mcp-rag:latest` দিয়ে server টা **প্রতি session-এ নতুন করে** launch করে (stdio)।
- Server `ollama-ai_ai-net` network-এ থাকে, তাই `http://ai-rag-api:8011`-এ পৌঁছায়।
- দুটো tool expose করে:

| Tool | Args | কাজ |
|------|------|-----|
| `search_codebase` | `query`, `limit=8` | matching chunk গুলো (file:line + content) |
| `get_context` | `query`, `limit=8`, `max_chars=9000` | একটা packed context block |

### Build

```bash
docker compose --profile mcp build mcp-rag
```

> এটা শুধু image বানায় (`ollama-ai-mcp-rag:latest`)। long-running service না — Cline নিজে launch করে।

### Verify (protocol level)

```bash
printf '%s\n' \
'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"1.0"}}}' \
'{"jsonrpc":"2.0","method":"notifications/initialized"}' \
'{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
| docker run --rm -i --network ollama-ai_ai-net ollama-ai-mcp-rag:latest 2>/dev/null
```

`search_codebase` ও `get_context` list-এ এলে server ঠিক আছে।

### Cline-এ যুক্ত করা

পূর্ণ ধাপ [`CLINE_SETUP.md`](CLINE_SETUP.md)-এ। সংক্ষেপে: MCP settings-এ JSON paste → save → reload → MCP Servers-এ 🟢 দেখো → prompt দাও।

### গুরুত্বপূর্ণ mental model

```text
Cline edits  → VS Code-এ খোলা folder
RAG searches → ./projects-এ index করা copy
```

সেরা ফল: একই project দুই জায়গায় রাখো, অথবা সরাসরি `ollama-ai/projects/<project>` VS Code-এ খোলো।

---

## 11. Operations & Troubleshooting

### Daily commands

```bash
docker compose ps                        # status
docker compose up -d                     # start
docker compose down                      # stop (data রাখে)
docker compose restart rag-api           # একটা service restart
docker compose up -d --build rag-api     # code বদলালে rebuild
docker compose logs -f indexer           # live logs
```

### Health checklist

```bash
docker compose ps                                  # সব Up?
curl http://localhost:8011/health                  # {"status":"ok"}
curl http://localhost:11434/api/tags               # models আছে?
docker exec -it ollama ollama list                 # একই জিনিস
curl http://localhost:8011/search -H 'Content-Type: application/json' \
  -d '{"query":"test","limit":1}'                  # search কাজ করে?
```

### Common issues

| লক্ষণ | কারণ | সমাধান |
|------|------|--------|
| `model-init` exit 2, "parameter not set" | env var missing + `set -u` | (fixed) script এখন env-safe; pull করো latest |
| `/search` → `'QdrantClient' has no attribute 'search'` | নতুন qdrant-client | (fixed) `query_points` fallback আছে; `docker compose up -d --build rag-api` |
| `/search` খালি results | indexing হয়নি / `./projects` খালি | project কপি করো; `docker compose logs -f indexer` |
| `502 Ollama embedding failed` | embed model নেই | `docker exec -it ollama ollama pull nomic-embed-text` |
| `502 Qdrant search failed` | collection নেই / নাম mismatch | `EMBED_MODEL`+`COLLECTION_NAME` দুই service-এ এক কিনা |
| Cline MCP red/disconnected | stack বন্ধ / network নাম ভুল | `docker compose up -d`; `--network ollama-ai_ai-net` মিলছে? |
| Cline "thinking" forever | MCP tool error/timeout | backend verify করো (Section 11 checklist); Cline reload |
| `docker: command not found` (Cline) | VS Code shell-এ docker নেই | terminal-এ `docker ps` চলে কিনা; PATH ঠিক করো |
| উত্তর slow | CPU + বড় model | একসাথে অনেক model load করো না; Cline-এ `qwen2.5-coder:7b` রাখো |
| ভুল/পুরোনো result | embed model বদলেছো, re-index করোনি | `docker compose down -v && docker compose up -d --build` |

### Logs

```bash
docker compose logs -f rag-api
docker compose logs -f indexer
docker compose logs -f ollama
docker compose logs --since=10m rag-api indexer
```

### Reset levels

```bash
# 1) restart only (data intact)
docker compose restart

# 2) rebuild after code change
docker compose up -d --build

# 3) FULL reset (models + vectors মুছে যাবে!)
docker compose down -v
docker compose up -d --build
```

> Level 3 দরকার মূলত embedding model বদলালে (vector dimension বদলায়, পুরোনো collection invalid হয়)।

---

## 12. Extending the System

### নতুন file type index করতে

`indexer/app.py` → `SUPPORTED_EXTENSIONS`-এ extension যোগ করো:

```python
SUPPORTED_EXTENSIONS = { ..., ".vue", ".php", ".rb" }
```

তারপর: `docker compose up -d --build indexer`

### Chunk size/overlap বদলাতে

`indexer/app.py` → `chunk_file(content, max_chars=1500, overlap_lines=5)`। বড় chunk = কম, বড় context; ছোট chunk = বেশি precise কিন্তু বেশি vector।

### নতুন RAG endpoint

`rag-api/app.py`-এ:

```python
@app.post("/files")
def files(request: SearchRequest) -> dict[str, Any]:
    results = search_points(request)
    unique = sorted({r["file_path"] for r in results})
    return {"query": request.query, "files": unique}
```

`docker compose up -d --build rag-api`

### নতুন MCP tool

`mcp-server/server.py`-এ:

```python
@mcp.tool()
def list_relevant_files(query: str, limit: int = 10) -> str:
    """Return just the unique file paths relevant to a query."""
    r = requests.post(f"{RAG_API_URL}/search", json={"query": query, "limit": limit}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    files = sorted({x["file_path"] for x in r.json().get("results", [])})
    return "\n".join(files) or "No files found."
```

তারপর: `docker compose --profile mcp build mcp-rag` (Cline নতুন tool নিজে দেখবে)।

### Embedding model বদলাতে

```bash
# .env-এ
EMBED_MODEL=mxbai-embed-large    # উদাহরণ (vector size আলাদা!)
```

⚠️ vector dimension বদলালে indexer-এর `VectorParams(size=768)`-ও বদলাতে হবে, আর collection নতুন করে বানাতে হবে:

```bash
docker compose down -v && docker compose up -d --build
```

---

## 13. Security & Limitations

### Security

- পুরো stack **localhost-only** ধরে বানানো — কোনো auth নেই।
- Ports (`11434`, `6333`, `8011`, `8801`) 0.0.0.0-তে bind — মানে LAN থেকেও access হতে পারে। Public/shared network-এ থাকলে firewall দিয়ে বন্ধ করো বা bind `127.0.0.1`-এ করো।
- MCP server host-এ `docker run` চালায় — মানে Cline-এর docker access আছে। বিশ্বাসযোগ্য পরিবেশেই ব্যবহার করো।
- `projects/.gitignore` source code git-এ যাওয়া আটকায়, কিন্তু embedding Qdrant volume-এ থাকে।

### Limitations

- ১০০% Cursor না: realtime re-rank, proprietary ranking, deep IDE hooks নেই।
- Indexing eventual: change-এর পর ≤ `SCAN_INTERVAL` সেকেন্ড লাগে index update হতে।
- Embedding quality model-নির্ভর: `nomic-embed-text` ভালো কিন্তু সেরা না।
- CPU inference slow: বড় model-এ latency বেশি।
- Chunking naive (line-based) — function/class-aware না।

---

## 14. FAQ

**Q: project কোথায় রাখব?**
`ollama-ai/projects/` ফোল্ডারে। indexer শুধু এটাই scan করে।

**Q: Cline নিজে কি Cursor-এর মতো অটো index করে?**
না। Cline workspace file পড়ে on-demand। RAG/MCP যোগ করার পর সে semantic search tool হিসেবে পায় — তখন ~Cursor-like হয়।

**Q: Continue না Cline?**
Cline = agentic + MCP RAG (recommended)। Continue = শুধু tab autocomplete। চাইলে দুটো একসাথে। বিস্তারিত [`CLINE_SETUP.md`](CLINE_SETUP.md) section 6।

**Q: project বদলালে কি কিছু করতে হবে?**
না, indexer প্রতি `SCAN_INTERVAL` সেকেন্ডে নিজে rescan করে।

**Q: কোন model কখন?**
`qwen2.5-coder:7b` Cline coding agent; `qwen2.5-coder:1.5b-base` Continue autocomplete; `nomic-embed-text` embedding (বদলিও না যদি না re-index করো)।

**Q: GPU ছাড়া চলবে?**
হ্যাঁ, কিন্তু slow। embedding দ্রুত, chat slow।

**Q: একাধিক project একসাথে?**
হ্যাঁ। `projects/`-এ একাধিক folder রাখো; সব এক collection-এ index হবে, `file_path` দিয়ে আলাদা করা যায়।

---

## 15. Command Cheat Sheet

```bash
# ── Lifecycle ──
docker compose up -d --build              # build + start main stack
docker compose --profile mcp build mcp-rag# build MCP image (Cline)
docker compose ps                         # status
docker compose down                       # stop (keep data)
docker compose down -v                    # stop + wipe models/vectors

# ── Logs ──
docker compose logs -f model-init         # model download progress
docker compose logs -f indexer            # indexing progress
docker compose logs -f rag-api            # API errors

# ── Health ──
curl http://localhost:8011/health
curl http://localhost:11434/api/tags
docker exec -it ollama ollama list

# ── RAG ──
curl http://localhost:8011/search  -H 'Content-Type: application/json' \
  -d '{"query":"auth logic","limit":5}'
curl http://localhost:8011/context -H 'Content-Type: application/json' \
  -d '{"query":"auth logic","limit":5,"max_chars":6000}'

# ── Models ──
docker exec -it ollama ollama pull qwen2.5-coder:7b
docker exec -it ollama ollama run  qwen2.5-coder:7b
docker exec -it ollama ollama rm   deepseek-r1:14b

# ── MCP probe ──
printf '%s\n' \
'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"1.0"}}}' \
'{"jsonrpc":"2.0","method":"notifications/initialized"}' \
'{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
| docker run --rm -i --network ollama-ai_ai-net ollama-ai-mcp-rag:latest 2>/dev/null

# ── Network / images ──
docker network ls | grep ai-net
docker image ls | grep ollama-ai
```

---

## 16. Advanced Retrieval Features (v2)

v2-তে retrieval quality আর developer experience বাড়াতে কয়েকটা feature যোগ হয়েছে। সবগুলো graceful — কোনোটা load না হলে system dense-only mode-এ চলবে।

### 16.1 Code-specialized models + FIM autocomplete

`.env`-এ default model:

```bash
CHAT_MODELS=qwen2.5-coder:7b                # code-tuned chat/coding model
AUTOCOMPLETE_MODELS=qwen2.5-coder:1.5b-base # FIM (fill-in-the-middle)
```

- `qwen2.5-coder:7b` — coding chat/agent work-এর জন্য।
- `qwen2.5-coder:1.5b-base` — Continue-এর inline tab autocomplete-এর জন্য হালকা FIM model।

Continue config: `clients/continue-config.example.json` (autocomplete + chat + RAG context provider সহ)। Cline-এ autocomplete নেই, তাই autocomplete চাইলে Continue পাশে রাখো।

### 16.2 Realtime file-watch indexing

Indexer এখন `watchdog` দিয়ে file change detect করে — সাথে সাথে re-index করে (আগের ৩০s polling-এর বদলে)।
`SCAN_INTERVAL` (default 60s) এখন শুধু safety net (deleted file sync ইত্যাদির জন্য)।

watchdog load না হলে আগের periodic scan-এ fall back করে।

### 16.3 Smart (boundary-aware) chunking

`indexer/app.py` → `smart_chunk_file()` function/class/def boundary ধরে chunk ভাগ করে (naive line-split-এর বদলে)। ফলে প্রতিটা chunk বেশি meaningful — retrieval ভালো হয়।

### 16.4 Hybrid search (dense + sparse BM25)

- Indexer প্রতিটা chunk-এ dense vector (Ollama) **এবং** sparse BM25 vector (fastembed `Qdrant/bm25`) দুটোই বানায়।
- Collection এখন **named vectors**: `dense` (768, COSINE) + `bm25` (sparse, IDF)।
- RAG API search-এ দুটো একসাথে চালিয়ে **RRF fusion** করে → semantic + exact keyword/symbol দুটোর সুবিধা।

Toggle: `HYBRID_ENABLED=true|false` (`.env`)।
fastembed/sparse model না থাকলে dense-only mode।

### 16.5 Cross-encoder reranking

- Search candidate (default 40) fetch করে fastembed cross-encoder (`Xenova/ms-marco-MiniLM-L-6-v2`) দিয়ে reorder করে, তারপর top-`limit` ফেরত দেয়।
- Top result-এর ordering অনেক ভালো হয়।

Toggle: `RERANK_ENABLED=true|false`, বা per-request `{"rerank": false}`।
Tune: `RERANK_CANDIDATES` (default 40), `RERANK_MODEL`।

### 16.6 Metadata filtering + `.ragignore`

`/search` ও `/context`-এ নতুন optional param:

```json
{"query":"login flow","language":"typescript","path_prefix":"server/src/auth"}
```

- `language` — শুধু ওই ভাষার chunk (payload-এ `language` থাকে)।
- `path_prefix` — file path-এ text match (folder/module narrow করতে)।

`.ragignore` (`projects/.ragignore`) — gitignore-style; কোন file/folder index হবে না তা ঠিক করতে:

```text
# উদাহরণ
*.test.ts
**/migrations/*
legacy-app
```

### 16.7 fastembed model cache

fastembed model (BM25 + reranker) `fastembed_cache` named volume-এ থাকে (`/app/cache`), তাই container restart-এ re-download হয় না। প্রথম request-এ একবার download (ছোট, কয়েক MB)।

### 16.8 Enabling v2 (one-time re-index)

Hybrid + smart chunking + language metadata সব নতুন collection schema চায়। তাই একবার full re-index দরকার:

```bash
docker compose down -v        # পুরোনো collection + vectors মুছে যাবে
docker compose up -d --build  # নতুন schema-তে re-index হবে
docker compose logs -f indexer
```

> Indexer schema mismatch নিজে detect করে collection auto-recreate করে, তাই down -v না করলেও প্রথমবার চালালে এটি re-index করবে। তবে পরিষ্কার শুরুর জন্য `down -v` recommended।

Verify:

```bash
curl http://localhost:8011/health
# {"status":"ok","hybrid":true,"rerank":true,"embed_model":"nomic-embed-text","collection":"codebase"}
```

---

## Quick Reference Table

| জিনিস | মান |
|------|-----|
| Ollama Base URL (host) | `http://localhost:11434` |
| Ollama URL (container) | `http://ollama:11434` |
| RAG API (host) | `http://localhost:8011` |
| RAG API (container) | `http://ai-rag-api:8011` |
| Qdrant (host) | `http://localhost:6333` |
| Open WebUI | `http://localhost:8801` |
| Docker network | `ollama-ai_ai-net` |
| MCP image | `ollama-ai-mcp-rag:latest` |
| Collection | `codebase` |
| Embed model | `nomic-embed-text` (768-dim) |
| Chat model | `qwen2.5-coder:7b` |
| MCP tools | `search_codebase`, `get_context` |
| Project folder | `ollama-ai/projects/` |
