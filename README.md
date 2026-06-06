# Ollama Local AI Coding Stack

This repo packages a local, Docker-based AI coding backend:

- Ollama for local chat and coding models
- Qdrant for vector search memory
- `nomic-embed-text` embeddings through Ollama
- Python indexer daemon for repo scanning and incremental re-indexing
- RAG API for semantic code search/context retrieval
- Open WebUI for browser chat
- Optional MCP filesystem server profile

It is not a full Cursor clone, but it gives you the local backend pieces needed for Roo Code, Cline, VS Code Chat, Open WebUI, or custom tooling.

## Documentation

- [`docs/DEVELOPER_GUIDE.md`](docs/DEVELOPER_GUIDE.md) — complete developer manual (every component, config, flow, troubleshooting). See section 16 for the v2 retrieval features.
- [`docs/RAG_API.md`](docs/RAG_API.md) — RAG and API internals, line-by-line
- [`docs/CLINE_SETUP.md`](docs/CLINE_SETUP.md) — Cline configuration walkthrough
- [`docs/MODELS.md`](docs/MODELS.md) — adding/managing models, custom Cline-optimized models
- [`clients/continue-config.example.json`](clients/continue-config.example.json) — Continue config (FIM autocomplete + RAG context)

## v2 Retrieval Features

- Code-specialized models (`qwen2.5-coder`) + FIM autocomplete model
- Realtime file-watch indexing (no more fixed 30s polling)
- Boundary-aware (function/class) chunking
- Hybrid search: dense + sparse BM25 with RRF fusion
- Cross-encoder reranking
- Metadata filtering (`language`, `path_prefix`) and `.ragignore`

Enabling v2 needs a one-time re-index:

```bash
docker compose down -v
docker compose up -d --build
```

## Architecture

```text
VS Code extension / Open WebUI / custom script
        |
        v
Ollama coding model: qwen2.5-coder:7b
        |
        v
RAG API -> Ollama embedding model -> Qdrant codebase collection
        ^
        |
Python indexer daemon scans ./projects
```

## Requirements

- Docker 24+
- Docker Compose v2+
- Enough disk space for models
- CPU works, but large models will be slow without GPU

For coding-only use, use `qwen2.5-coder:7b` in Cline and `qwen2.5-coder:1.5b-base` for Continue autocomplete.

## Project Layout

```text
ollama-ai/
├── docker-compose.yml
├── .env.example
├── indexer/
│   ├── app.py
│   ├── Dockerfile
│   └── requirements.txt
├── rag-api/
│   ├── app.py
│   ├── Dockerfile
│   └── requirements.txt
└── projects/
    └── put-your-codebases-here
```

## Quick Start

Copy the env template if you want to customize models:

```bash
cp .env.example .env
```

Put the projects you want indexed inside `./projects`:

```bash
mkdir -p projects
# Example:
# cp -r /path/to/your/project projects/your-project
```

Start everything:

```bash
docker compose up -d --build
```

The first run pulls these models from `.env`:

- `nomic-embed-text`
- `qwen2.5-coder:7b`
- `qwen2.5-coder:1.5b-base`

You can change that in `.env` before starting.

## Services

- Ollama API: `http://localhost:11434`
- Open WebUI: `http://localhost:8801`
- Qdrant API/dashboard: `http://localhost:6333/dashboard`
- RAG API: `http://localhost:8011`

Check containers:

```bash
docker compose ps
```

Watch indexer logs:

```bash
docker compose logs -f indexer
```

## RAG API Usage

Search indexed code:

```bash
curl http://localhost:8011/search \
  -H "Content-Type: application/json" \
  -d '{"query":"where is authentication handled?","limit":8}'
```

Get compact context for a chat prompt:

```bash
curl http://localhost:8011/context \
  -H "Content-Type: application/json" \
  -d '{"query":"explain the invoice approval flow","limit":8,"max_chars":9000}'
```

The `/context` output can be pasted into Roo Code, Cline, VS Code Chat, or Open WebUI when you want stronger project awareness.

For a deep, line-by-line explanation of how the RAG API and indexing flow work, see [`docs/RAG_API.md`](docs/RAG_API.md).

## VS Code Extension Setup

Use any extension that can talk to Ollama. Recommended order:

1. Roo Code
2. Cline
3. VS Code built-in chat with Ollama support
4. Continue as fallback

Ollama settings:

```json
{
  "baseUrl": "http://localhost:11434",
  "model": "qwen2.5-coder:7b"
}
```

For inline autocomplete, use `qwen2.5-coder:1.5b-base` in Continue.

Important: most VS Code extensions will not automatically use this Qdrant RAG API unless they support custom context providers or MCP/tool wiring. Two workflows:

Manual workflow (works everywhere):

1. Let the extension read workspace files directly.
2. Use `http://localhost:8011/context` when you need semantic codebase memory.
3. Paste the returned context into the chat before asking for a code change.

Automatic workflow with Cline (most Cursor-like):

Cline supports MCP, so it can call the RAG API by itself. Build the MCP server image and register it in Cline:

```bash
docker compose --profile mcp build mcp-rag
```

Then add `mcp-server/cline-mcp-settings.example.json` to your Cline MCP settings. Full step-by-step is in [`docs/RAG_API.md`](docs/RAG_API.md) section 14.

For a complete Cline configuration walkthrough (Ollama model + MCP RAG + optional Continue autocomplete), see [`docs/CLINE_SETUP.md`](docs/CLINE_SETUP.md).

## Optional MCP Filesystem

The compose file includes an optional MCP filesystem server profile:

```bash
docker compose --profile mcp up -d mcp-filesystem
```

MCP filesystem servers are normally stdio-based, so a VS Code extension must be configured to launch or attach to the MCP command it expects. Treat this as optional. Roo Code/Cline workspace file access is usually enough for normal coding.

## Useful Commands

List pulled models:

```bash
docker exec -it ollama ollama list
```

Run a model in the terminal:

```bash
docker exec -it ollama ollama run qwen2.5-coder:7b
```

Pull another model:

```bash
docker exec -it ollama ollama pull qwen2.5-coder:1.5b-base
```

Rebuild only the indexer:

```bash
docker compose up -d --build indexer
```

Stop without deleting model/vector data:

```bash
docker compose down
```

Delete all persisted data:

```bash
docker compose down -v
```

## Indexing Behavior

The indexer scans `./projects` every `SCAN_INTERVAL` seconds.

It indexes common source and config files:

```text
.ts .tsx .js .jsx .py .go .json .md .txt .html .css .rs .java .cpp .c .h .sh .yml .yaml
```

It ignores heavy/generated folders such as:

```text
node_modules .git dist build venv .venv __pycache__ target out .idea .vscode
```

State is stored in `projects/.indexer_state.json`, so unchanged files are skipped on the next scan.

## Troubleshooting

If the first run is slow, model downloads are still running:

```bash
docker compose logs -f model-init
```

If search returns no results, wait for indexing to finish:

```bash
docker compose logs -f indexer
```

If Ollama is slow on CPU, keep only one coding model loaded at a time and use `qwen2.5-coder:7b` for Cline.

If the RAG API fails, check:

```bash
docker compose logs -f rag-api
```
