# RAG API — Deep Step-by-Step Guide

এই ডকুমেন্টটা তোমার local AI stack-এর **brain layer** (`rag-api`) কীভাবে কাজ করে, লাইন ধরে ধরে ব্যাখ্যা করে।
লক্ষ্য: তুমি যেন পুরো flow বুঝে নিজে modify/debug করতে পারো।

---

## 1. Big Picture — পুরো System কীভাবে যুক্ত

```text
                ┌─────────────────────────────────────────────┐
                │   ./projects  (তোমার সব codebase এখানে)        │
                └───────────────┬─────────────────────────────┘
                                │ scan (every SCAN_INTERVAL sec)
                                ▼
                    ┌───────────────────────┐
                    │   indexer (daemon)     │   indexer/app.py
                    │   - file walk          │
                    │   - chunk করা          │
                    │   - embedding বানানো    │
                    │   - Qdrant-এ লেখা       │
                    └───────┬───────┬────────┘
                            │       │
                  embeddings│       │ upsert vectors
                            ▼       ▼
            ┌───────────────────┐   ┌───────────────────────┐
            │ ollama            │   │ qdrant                 │
            │ nomic-embed-text  │   │ collection: codebase   │
            │ :11434            │   │ :6333                  │
            └─────────▲─────────┘   └───────────▲───────────┘
                      │                         │
            embed(query)                 vector search
                      │                         │
                ┌─────┴─────────────────────────┴─────┐
                │   rag-api (FastAPI)                  │   rag-api/app.py
                │   /health  /search  /context        │
                │   :8011                              │
                └──────────────────▲──────────────────┘
                                   │ HTTP (curl / extension / script)
                                   │
                    ┌──────────────┴───────────────┐
                    │ তুমি / VS Code / Open WebUI    │
                    └───────────────────────────────┘
```

**মূল কথা:**
- `indexer` = লেখক (codebase → vector memory)।
- `rag-api` = পাঠক (প্রশ্ন → relevant code খুঁজে দেয়)।
- `ollama` = embedding বানায় (text → number list)।
- `qdrant` = vector database (similarity search)।

---

## 2. RAG আসলে কী (Concept)

RAG = **Retrieval-Augmented Generation**।

সমস্যা: LLM তোমার পুরো codebase জানে না, আর পুরো repo prompt-এ পাঠানোও যায় না (context limit)।

সমাধান (৩ ধাপ):
1. **Index একবার:** প্রতিটা ফাইল ছোট ছোট chunk-এ ভাগ করো → প্রতিটা chunk-কে একটা **vector** (সংখ্যার list) বানাও → Qdrant-এ রাখো।
2. **Retrieve প্রতি প্রশ্নে:** প্রশ্নকেও vector বানাও → Qdrant-এ সবচেয়ে কাছের chunk গুলো খুঁজে আনো।
3. **Augment:** ওই chunk গুলো LLM-এর prompt-এ যোগ করে দাও, তারপর উত্তর চাও।

> Vector "কাছাকাছি" মানে **অর্থে কাছাকাছি (semantic)**, শুধু শব্দ মিল না।
> তাই "where is login handled" লিখলে `authenticateUser()` ফাংশনও খুঁজে পায়।

---

## 3. ফাইল ও Endpoint Overview

API কোড: `rag-api/app.py`

| Endpoint | Method | কাজ |
|----------|--------|------|
| `/health` | GET | সার্ভিস বেঁচে আছে কিনা চেক |
| `/search` | POST | প্রশ্নের সাথে মিলে যাওয়া raw code chunk list |
| `/context` | POST | prompt-এ paste করার মতো পরিষ্কার, size-limited context block |

Base URL (host থেকে): `http://localhost:8011`
Base URL (অন্য container থেকে): `http://ai-rag-api:8011`

---

## 4. Configuration (Environment Variables)

```python
OLLAMA_URL     = os.environ.get("OLLAMA_URL", "http://ollama:11434").rstrip("/")
QDRANT_URL     = os.environ.get("QDRANT_URL", "http://qdrant:6333").rstrip("/")
EMBED_MODEL    = os.environ.get("EMBED_MODEL", "nomic-embed-text")
COLLECTION_NAME= os.environ.get("COLLECTION_NAME", "codebase")
```

| Variable | Default | মানে |
|----------|---------|------|
| `OLLAMA_URL` | `http://ollama:11434` | embedding কোথা থেকে বানাবে |
| `QDRANT_URL` | `http://qdrant:6333` | কোন vector DB-তে খুঁজবে |
| `EMBED_MODEL` | `nomic-embed-text` | কোন embedding model |
| `COLLECTION_NAME` | `codebase` | Qdrant-এর কোন collection |

> ⚠️ **গুরুত্বপূর্ণ:** `rag-api` আর `indexer` দুটোতেই **একই `EMBED_MODEL` আর `COLLECTION_NAME`** থাকতে হবে।
> না হলে query vector আর stored vector আলাদা "space"-এ থাকবে, search ভুল হবে।

`.rstrip("/")` কেন? — শেষের `/` থাকলে `http://ollama:11434//api/embed` হয়ে যেত, তাই কেটে দেওয়া হয়।

App boot হওয়ার সময় একবারই client তৈরি হয় (প্রতি request-এ নতুন connection বানায় না):

```python
app = FastAPI(title="Local Code RAG API", version="1.0.0")
qdrant = QdrantClient(url=QDRANT_URL)
```

---

## 5. Request Models (Validation Layer)

Pydantic দিয়ে input আগেই validate হয়। ভুল হলে FastAPI নিজে `422` error দেয়, তোমার code পর্যন্ত পৌঁছায় না।

```python
class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = Field(default=8, ge=1, le=50)
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)

class ContextRequest(SearchRequest):
    max_chars: int = Field(default=9000, ge=1000, le=30000)
```

| Field | নিয়ম | মানে |
|-------|------|------|
| `query` | required, খালি হওয়া যাবে না | তোমার প্রশ্ন |
| `limit` | 1–50, default 8 | কতগুলো chunk ফেরত দেবে |
| `score_threshold` | 0.0–1.0, optional | এর নিচের মিল বাদ (noise filter) |
| `max_chars` | 1000–30000, default 9000 | `/context`-এর output size cap |

`ContextRequest(SearchRequest)` — মানে `ContextRequest` সব ফিল্ড উত্তরাধিকার পায়, শুধু `max_chars` extra।

---

## 6. Step-by-Step: একটা Request-এর Lifecycle

ধরো তুমি call করলে:

```bash
curl http://localhost:8011/context \
  -H "Content-Type: application/json" \
  -d '{"query":"how is user login validated?","limit":5,"max_chars":6000}'
```

ভেতরে যা ঘটে:

### Step 1 — Validation
FastAPI JSON-টা `ContextRequest`-এ রূপান্তর করে। `query` খালি বা `limit > 50` হলে এখানেই `422`।

### Step 2 — `embed(query)` → Ollama
প্রশ্নটাকে vector বানানো হয়।

```python
def embed(text: str) -> list[float]:
    # 2a) নতুন API আগে try
    try:
        response = requests.post(
            f"{OLLAMA_URL}/api/embed",
            json={"model": EMBED_MODEL, "input": text},
            timeout=30,
        )
        if response.status_code == 200:
            data = response.json()
            embeddings = data.get("embeddings")
            if embeddings:
                return embeddings[0]          # নতুন format: {"embeddings": [[...]]}
            embedding = data.get("embedding")
            if embedding:
                return embedding              # কিছু version: {"embedding": [...]}
    except requests.RequestException:
        pass                                  # network/timeout হলে fallback-এ যাও

    # 2b) পুরোনো API fallback
    response = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=30,
    )
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Ollama embedding failed: {response.text}")

    embedding = response.json().get("embedding")
    if not embedding:
        raise HTTPException(status_code=502, detail="Ollama returned no embedding")
    return embedding
```

**কেন দুইবার try?** Ollama version ভেদে embedding endpoint আলাদা:
- নতুন: `POST /api/embed` → `{"embeddings": [[...]]}`
- পুরোনো: `POST /api/embeddings` → `{"embedding": [...]}`

এই কোড দুটোই handle করে, তাই Ollama upgrade করলেও ভাঙবে না।
ব্যর্থ হলে `502 Bad Gateway` (মানে: upstream সার্ভিস দোষী, তোমার request না)।

ফলাফল: একটা `list[float]`, যেমন `nomic-embed-text`-এ ৭৬৮টা সংখ্যা।

### Step 3 — `search_points()` → Qdrant
ওই vector দিয়ে Qdrant-এ nearest neighbour খোঁজা হয়।

```python
def search_points(request: SearchRequest) -> list[dict[str, Any]]:
    vector = embed(request.query)
    try:
        points = qdrant.search(
            collection_name=COLLECTION_NAME,
            query_vector=vector,
            limit=request.limit,
            score_threshold=request.score_threshold,
            with_payload=True,          # শুধু vector না, সাথে metadata-ও আনো
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Qdrant search failed: {exc}") from exc

    results = []
    for point in points:
        payload = point.payload or {}
        results.append({
            "score": point.score,           # 0..1, যত বড় তত বেশি মিল (cosine)
            "file_path": payload.get("file_path"),
            "start_line": payload.get("start_line"),
            "end_line": payload.get("end_line"),
            "content": payload.get("content", ""),
        })
    return results
```

`payload` ফিল্ডগুলো indexer লেখার সময় বসিয়েছিল (দেখো section 9)। তাই প্রতিটা match-এর সাথে **কোন ফাইল, কোন line range** তা পাওয়া যায়।

### Step 4 — Response বানানো
- `/search` হলে এখানেই raw list ফেরত যায়।
- `/context` হলে আরও এক ধাপ: chunk গুলোকে একটা text block-এ জোড়া দেয় (নিচে)।

---

## 7. `/search` Endpoint

```python
@app.post("/search")
def search(request: SearchRequest) -> dict[str, Any]:
    return {"query": request.query, "results": search_points(request)}
```

**Request:**

```bash
curl http://localhost:8011/search \
  -H "Content-Type: application/json" \
  -d '{"query":"where is authentication handled?","limit":3}'
```

**Response (উদাহরণ):**

```json
{
  "query": "where is authentication handled?",
  "results": [
    {
      "score": 0.83,
      "file_path": "your-project/src/auth/login.ts",
      "start_line": 12,
      "end_line": 41,
      "content": "export async function authenticateUser(...) { ... }"
    },
    {
      "score": 0.79,
      "file_path": "your-project/src/middleware/auth.ts",
      "start_line": 1,
      "end_line": 28,
      "content": "..."
    }
  ]
}
```

কখন ব্যবহার করবে: যখন **structured data** চাও (কোন ফাইল, কোন line, কত score) — যেমন নিজের tool/script বানানোর সময়।

---

## 8. `/context` Endpoint

```python
@app.post("/context")
def context(request: ContextRequest) -> dict[str, Any]:
    results = search_points(request)
    blocks: list[str] = []
    used_chars = 0

    for result in results:
        header = f"{result['file_path']}:{result['start_line']}-{result['end_line']}"
        block = f"---\n{header}\n{result['content']}\n"
        if used_chars + len(block) > request.max_chars:
            break                       # budget শেষ → থেমে যাও
        blocks.append(block)
        used_chars += len(block)

    return {
        "query": request.query,
        "context": "\n".join(blocks),
        "results": results[: len(blocks)],   # context-এ যতগুলো ঢুকেছে শুধু সেগুলো
    }
```

**পার্থক্য `/search` থেকে:**
- প্রতিটা chunk-কে একটা header (`file:line-line`) সহ block বানায়।
- `max_chars` budget মেনে চলে — LLM-এর context window ভরে না ফেলার জন্য।
- output-এর `context` ফিল্ডটা সরাসরি chat-এ paste করার উপযুক্ত।

**Request:**

```bash
curl http://localhost:8011/context \
  -H "Content-Type: application/json" \
  -d '{"query":"explain the invoice approval flow","limit":8,"max_chars":9000}'
```

**Response (উদাহরণ):**

```json
{
  "query": "explain the invoice approval flow",
  "context": "---\nyour-project/src/invoice/approve.ts:5-60\n<code>\n\n---\nyour-project/src/invoice/state.ts:1-30\n<code>\n",
  "results": [ { "score": 0.81, "file_path": "...", "...": "..." } ]
}
```

কখন ব্যবহার করবে: VS Code chat / Open WebUI-তে প্রশ্নের আগে এই `context` paste করতে।

---

## 9. Indexer কীভাবে এই data বানায় (কেন response-এ ওই field গুলো আছে)

`rag-api` শুধু **পড়ে**। লেখে `indexer/app.py`। সংক্ষেপে indexer যা করে:

1. `./projects` recursively scan করে (`node_modules`, `.git` ইত্যাদি বাদ)।
2. supported extension (`.ts .py .js ...`) ফাইল নেয়।
3. প্রতিটা ফাইল **line-based chunk**-এ ভাগ করে (≈1500 char, 5 line overlap)।
4. প্রতিটা chunk Ollama দিয়ে embed করে।
5. Qdrant-এ `PointStruct` হিসেবে লেখে, payload সহ:

```python
payload = {
    "file_path": rel_path,
    "start_line": chunk["start_line"],
    "end_line": chunk["end_line"],
    "content": chunk["text"],
}
```

এই payload-ই পরে `rag-api`-এর response-এ ফিরে আসে।

**Incremental:** indexer প্রতিটা ফাইলের `mtime + size + md5 hash` `projects/.indexer_state.json`-এ রাখে।
ফাইল না বদলালে আবার embed করে না — তাই বড় repo-তেও দ্রুত।

**Delete sync:** ফাইল মুছে ফেললে পরের scan-এ Qdrant থেকেও ওই vector মুছে যায়।

> দুই layer এক `COLLECTION_NAME` (`codebase`) আর এক `EMBED_MODEL` শেয়ার করে — এটাই দুটোকে যুক্ত রাখে।

---

## 10. End-to-End Test (নিজে যাচাই করো)

```bash
# 1) পুরো stack চালু করো
docker compose up -d --build

# 2) model আর indexing শেষ হওয়া পর্যন্ত দেখো
docker compose logs -f model-init     # সব model pull complete
docker compose logs -f indexer        # "Scan complete. Total files in index: N"

# 3) API বেঁচে আছে?
curl http://localhost:8011/health
# -> {"status":"ok"}

# 4) একটা search করো
curl http://localhost:8011/search \
  -H "Content-Type: application/json" \
  -d '{"query":"database connection setup","limit":5}'

# 5) chat-ready context নাও
curl http://localhost:8011/context \
  -H "Content-Type: application/json" \
  -d '{"query":"database connection setup","limit":5,"max_chars":6000}'
```

> Interactive docs: ব্রাউজারে `http://localhost:8011/docs` খুললে FastAPI-এর auto Swagger UI পাবে — সেখান থেকেই endpoint টেস্ট করা যায়।

---

## 11. VS Code-এ কীভাবে কাজে লাগাবে

⚠️ বেশিরভাগ extension (Roo Code/Cline/Continue) এই `rag-api` নিজে থেকে call করবে না। দুটো বাস্তব উপায়:

**Option A — Manual context (এখনই কাজ করে):**
1. টার্মিনালে `/context` call করো।
2. response-এর `context` কপি করো।
3. VS Code chat-এ প্রশ্নের আগে paste করো:
   ```
   এই code context ব্যবহার করো:
   <pasted context>

   প্রশ্ন: invoice approval flow refactor করে দাও।
   ```

**Option B — Workspace files + RAG combo:**
- ছোট/পরিচিত ফাইলের জন্য extension-এর নিজের workspace access যথেষ্ট।
- বড় repo-তে "কোথায় আছে" খুঁজতে `rag-api` ব্যবহার করো, তারপর সেই ফাইল extension-এ খোলো।

**Option C — MCP server (auto, most Cursor-like) ✅ recommended:**

Cline MCP support করে। `mcp-server/` একটা stdio MCP server, যেটা `rag-api`-কে দুটো tool হিসেবে Cline-এ দেয়:
- `search_codebase(query, limit)` — matching code chunk
- `get_context(query, limit, max_chars)` — prompt-ready context

এতে Cline **নিজে থেকেই** দরকার মতো codebase search করবে — তোমাকে manually paste করতে হবে না।

দেখো section 14 — সম্পূর্ণ MCP setup।

---

## 12. Troubleshooting

| সমস্যা | কারণ | সমাধান |
|--------|------|--------|
| `/search` খালি `results` | indexing শেষ হয়নি বা `./projects` খালি | `docker compose logs -f indexer` দেখো; project কপি করেছো কিনা চেক করো |
| `502 Ollama embedding failed` | embed model pull হয়নি | `docker exec -it ollama ollama list` → `nomic-embed-text` আছে কিনা |
| `502 Qdrant search failed` | collection নেই / নাম mismatch | দুই সার্ভিসে `COLLECTION_NAME` এক কিনা দেখো |
| সব score কম | প্রশ্ন আর code আলাদা ভাষায়/অস্পষ্ট | প্রশ্ন আরও নির্দিষ্ট করো, `score_threshold` সরাও |
| উল্টাপাল্টা result | embed model বদলেছো কিন্তু re-index করোনি | `docker compose down -v` করে আবার build করো (collection নতুন করে তৈরি হবে) |

লগ দেখার commands:

```bash
docker compose logs -f rag-api
docker compose logs -f indexer
docker compose logs -f ollama
```

---

## 13. কীভাবে নিজে Extend করবে

- **নতুন endpoint:** `app.py`-তে আরেকটা `@app.post(...)` যোগ করো, ভেতরে `search_points()` reuse করো।
- **Re-rank:** `search_points()`-এর পরে নিজের scoring/filtering যোগ করতে পারো।
- **বেশি file type index:** `indexer/app.py`-এর `SUPPORTED_EXTENSIONS` set-এ extension যোগ করো।
- **Chunk size বদলানো:** `indexer/app.py`-এর `chunk_file(max_chars=..., overlap_lines=...)`।
- **অন্য embed model:** `.env`-এ `EMBED_MODEL` বদলাও, তারপর `docker compose down -v && docker compose up -d --build` (vector dimension বদলালে re-index বাধ্যতামূলক)।

> মনে রেখো: embedding model বদলালে পুরোনো vector আর কাজে লাগে না — collection নতুন করে বানাতেই হবে।

---

## 14. Cline + MCP Setup (Cursor-like auto search)

এই অংশটা Cline-কে নিজে থেকে codebase search করার ক্ষমতা দেয়, যাতে behavior Cursor-এর কাছাকাছি হয়।

### কীভাবে কাজ করে

```text
Cline (VS Code)
   │  নিজে সিদ্ধান্ত নেয়: "এর জন্য codebase search দরকার"
   ▼
docker run -i  mcp-server (stdio)   ←── Cline launch করে
   │  HTTP call
   ▼
rag-api :8011  →  ollama (embed)  →  qdrant (search)
```

MCP server stdio দিয়ে কথা বলে, তাই Cline নিজেই process-টা launch করে। সে container network (`ollama-ai_ai-net`) দিয়ে `ai-rag-api:8011`-এ পৌঁছায়।

### Step 1 — MCP image build করো

```bash
docker compose --profile mcp build mcp-rag
```

> এটা শুধু image বানায় (`ollama-ai-mcp-rag:latest`)। service হিসেবে চালু থাকে না — Cline প্রতিবার `docker run` দিয়ে নতুন container চালাবে।

### Step 2 — main stack চালু আছে নিশ্চিত করো

```bash
docker compose up -d
docker compose ps        # rag-api, ollama, qdrant, indexer চলছে কিনা
```

### Step 3 — Cline-এ MCP server যোগ করো

VS Code-এ Cline panel → **MCP Servers** → **Configure MCP Servers** (এটা `cline_mcp_settings.json` খোলে)।
নিচের block যোগ করো (`mcp-server/cline-mcp-settings.example.json`-এও আছে):

```json
{
  "mcpServers": {
    "local-codebase-rag": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "--network", "ollama-ai_ai-net",
        "ollama-ai-mcp-rag:latest"
      ],
      "disabled": false,
      "autoApprove": ["search_codebase", "get_context"]
    }
  }
}
```

Save করলে Cline নিজে server connect করে নেবে।

> Network নাম যাচাই: `docker network ls | grep ai-net`। সাধারণত `ollama-ai_ai-net`। আলাদা হলে `--network` মান বদলাও।

### Step 4 — ব্যবহার

Cline-এ স্বাভাবিকভাবে প্রশ্ন করো, যেমন:

```text
Find where invoice approval is implemented and refactor it.
```

Cline নিজে `search_codebase` / `get_context` tool call করবে, relevant ফাইল খুঁজে নেবে, তারপর কাজ করবে।

### দুটো জিনিস মনে রাখো

1. **Cline workspace ≠ ./projects।** Cline VS Code-এ খোলা folder-এর ফাইল edit করে, কিন্তু MCP search চালায় `./projects`-এ index করা codebase-এ। সবচেয়ে ভালো ফল পেতে **একই project** দুই জায়গায় রাখো:
   - VS Code-এ project folder খোলো (edit করার জন্য)
   - সেই project `./projects`-এ কপি/রাখো (index হওয়ার জন্য)
2. **Index আপডেট থাকতে হবে।** indexer প্রতি `SCAN_INTERVAL` সেকেন্ডে `./projects` rescan করে, তাই MCP search সবসময় latest indexed কোড দেখে।

### Troubleshooting (MCP)

| সমস্যা | সমাধান |
|--------|--------|
| Cline-এ tool দেখা যায় না | `cline_mcp_settings.json` ঠিক আছে কিনা, image build হয়েছে কিনা চেক করো |
| `RAG search failed: ... connection` | main stack চালু আছে? `docker compose ps`; network নাম মিলছে? |
| খালি result | `docker compose logs -f indexer` — indexing শেষ হয়েছে কিনা |
| `docker: command not found` (Cline থেকে) | VS Code যে shell-এ চলছে সেখানে `docker` PATH-এ আছে কিনা দেখো |
