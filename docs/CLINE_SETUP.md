# Cline Configuration Guide (Local AI Coding)

এই গাইডটা ধাপে ধাপে দেখায় কীভাবে Cline-কে তোমার local stack-এর সাথে configure করতে হয়:

- Ollama model (chat / coding)
- MCP RAG server (codebase auto-search — Cursor-like)
- (Optional) Continue শুধু tab autocomplete-এর জন্য

> Prerequisite: তোমার Docker stack চালু থাকতে হবে। চেক করতে: `docker compose ps`।
> RAG details বুঝতে চাইলে দেখো [`RAG_API.md`](RAG_API.md)।

---

## 0. একনজরে পুরো ছবি

```text
VS Code
  ├── Cline ──► Ollama (chat model: qwen2.5-coder:7b)
  │      └──► MCP server ──► rag-api ──► ollama(embed) + qdrant   (codebase search)
  └── Continue (optional) ──► Ollama (autocomplete: qwen2.5-coder:1.5b-base)
```

- **Cline** = main agent (multi-file edit, terminal, MCP RAG)
- **Continue** = শুধু inline tab autocomplete (চাইলে)

---

## 1. আগে stack চালু করো

```bash
cd /home/gowtamkumar/projects/ollama-ai

# main services
docker compose up -d

# MCP image build (একবার)
docker compose --profile mcp build mcp-rag

# সব ঠিক আছে কিনা
docker compose ps
docker exec -it ollama ollama list      # qwen2.5-coder + nomic-embed-text আছে কিনা
curl http://localhost:8011/health       # {"status":"ok"}
```

⚠️ তোমার আসল project অবশ্যই `projects/` folder-এ থাকতে হবে, না হলে RAG search খালি ফেরত দেবে।

```bash
cp -r /path/to/AssureOps-V3 projects/
docker compose logs -f indexer          # "Scan complete. Total files in index: N"
```

---

## 2. Cline Install

1. VS Code → Extensions (`Ctrl+Shift+X`)
2. সার্চ করো **Cline**
3. Install
4. বাম sidebar-এ Cline icon আসবে

---

## 3. Cline-এ Ollama Configure

1. Cline panel খোলো → উপরের **⚙️ Settings** icon
2. নিচের মান বসাও:

| Field        | Value                    |
| ------------ | ------------------------ |
| API Provider | `Ollama`                 |
| Base URL     | `http://localhost:11434` |
| Model        | `qwen2.5-coder:7b`       |

3. Save করো।
4. টেস্ট: Cline-এ লেখো `Write a Python function to reverse a string` — উত্তর এলে connection ঠিক।

> Autocomplete দরকার হলে Continue-তে `qwen2.5-coder:1.5b-base` ব্যবহার করো।

---

## 4. Cline-এ MCP RAG Configure (Cursor-like auto search)

এটাই Cline-কে নিজে থেকে codebase খোঁজার ক্ষমতা দেয়।

### 4.1 — MCP settings খোলো

Cline panel → **MCP Servers** → **Configure MCP Servers**
(এটা `cline_mcp_settings.json` ফাইল খোলে)

### 4.2 — এই JSON যোগ করো

```json
{
  "mcpServers": {
    "local-codebase-rag": {
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "--network",
        "ollama-ai_ai-net",
        "-e",
        "RAG_API_URL=http://ai-rag-api:8011",
        "ollama-ai-mcp-rag:latest"
      ],
      "disabled": false,
      "autoApprove": ["search_codebase", "get_context"]
    }
  }
}
```

> এই block `mcp-server/cline-mcp-settings.example.json`-এও আছে — কপি করতে পারো।

### 4.3 — Save করে reload

Save করার পর Cline নিজে server connect করবে। MCP Servers list-এ **local-codebase-rag** সবুজ/active দেখা উচিত, আর দুটো tool আসবে:

- `search_codebase` — matching code chunk
- `get_context` — prompt-ready context

### 4.4 — Network নাম যাচাই (যদি connect না হয়)

```bash
docker network ls | grep ai-net
```

সাধারণত `ollama-ai_ai-net`। আলাদা নাম হলে JSON-এর `--network` মান বদলাও।

---

## 5. টেস্ট করো (সব মিলে কাজ করছে কিনা)

Cline-এ প্রশ্ন করো:

```text
Search my codebase for where authentication is implemented, then explain the flow.
```

ঠিক থাকলে Cline `search_codebase` tool call করবে → relevant ফাইল দেখাবে → ব্যাখ্যা দেবে।

আরেকটা:

```text
Find the invoice approval logic and refactor it to add an audit log.
```

Cline নিজে search করে ফাইল খুঁজে edit করবে।

---

## 6. Continue — Chat + Autocomplete + RAG (CPU-তে recommended)

CPU-only machine-এ Cline-এর agentic tool-call 7B model দিয়ে অনির্ভরযোগ্য। তাই Continue + RAG বেশি practical: chat, edit, tab autocomplete, আর `@codebase`/`@Local RAG` context।

1. Extensions → **Continue** install
2. Continue config খোলো: `~/.continue/config.yaml` (নতুন version YAML ব্যবহার করে)
3. পুরো config এই দিয়ে replace করো (`clients/continue-config.example.yaml`-এও আছে):

```yaml
name: Local Coding Config
version: 1.0.0
schema: v1

models:
  - name: Qwen2.5 Coder 7B
    provider: ollama
    model: qwen2.5-coder:7b
    apiBase: http://localhost:11434
    roles: [chat, edit, apply]

  - name: Qwen2.5 Coder 1.5B (FIM)
    provider: ollama
    model: qwen2.5-coder:1.5b-base
    apiBase: http://localhost:11434
    roles: [autocomplete]

  - name: Nomic Embed
    provider: ollama
    model: nomic-embed-text
    apiBase: http://localhost:11434
    roles: [embed]

context:
  - provider: code
  - provider: diff
  - provider: terminal
  - provider: codebase          # Continue-এর নিজের index
  - provider: http              # এই stack-এর hybrid + rerank RAG
    params:
      url: http://localhost:8011/continue/context
      title: Local RAG
```

4. Save করো। Continue নিজে থেকে reload করবে।

### ব্যবহার

| চাও | কীভাবে |
|-----|--------|
| Chat / প্রশ্ন | Continue panel-এ লেখো |
| Codebase-aware উত্তর | প্রশ্নে `@codebase` বা `@Local RAG` যোগ করো |
| Inline suggestion | কোড টাইপ করলে autocomplete নিজেই আসবে |
| File edit | কোড select → `Ctrl+I` |

> দুটো RAG পথ আছে: `@codebase` = Continue-এর নিজস্ব index; `@Local RAG` = এই stack-এর
> hybrid + rerank API (`/continue/context`), যা সাধারণত বেশি নির্ভুল।

---

## 7. দৈনন্দিন Workflow

1. VS Code-এ তোমার project folder খোলো (edit এখানেই হয়)।
2. একই project `projects/`-এ আছে নিশ্চিত করো (RAG এখান থেকে index করে)।
3. Cline-এ স্বাভাবিক ভাষায় কাজ দাও — সে নিজে search + edit করবে।
4. টাইপ করার সময় suggestion চাইলে Continue autocomplete ব্যবহার করো।

> মনে রাখো: Cline edit করে **VS Code workspace**-এ; RAG search চালায় **`projects/`**-এ index করা কোডে। তাই একই project দুই জায়গায় রাখা জরুরি (অথবা VS Code-এ সরাসরি `ollama-ai/projects/your-project` খোলো)।

---

## 8. Troubleshooting

| সমস্যা                                   | সমাধান                                                                             |
| ---------------------------------------- | ---------------------------------------------------------------------------------- |
| Cline Ollama-তে connect হয় না           | Base URL `http://localhost:11434` (https না); `docker compose ps`-এ ollama চলছে?   |
| MCP server active হয় না                 | image build হয়েছে? `docker compose --profile mcp build mcp-rag`; JSON syntax ঠিক? |
| `RAG search failed: connection`          | main stack চালু? network নাম `ollama-ai_ai-net` মিলছে?                             |
| search খালি ফেরত                         | project `projects/`-এ আছে? `docker compose logs -f indexer`                        |
| `docker: command not found` (Cline থেকে) | VS Code যে shell-এ চলে সেখানে `docker` PATH-এ আছে কিনা                             |
| উত্তর খুব slow                           | `qwen2.5-coder:7b` keep করো; একসাথে অনেক model load করো না                         |

দরকারি লগ:

```bash
docker compose logs -f rag-api
docker compose logs -f indexer
docker compose logs -f ollama
```

---

## 9. দ্রুত Reference

| জিনিস            | মান                               |
| ---------------- | --------------------------------- |
| Ollama Base URL  | `http://localhost:11434`          |
| Chat model       | `qwen2.5-coder:7b`                |
| Embed model      | `nomic-embed-text`                |
| RAG API          | `http://localhost:8011`           |
| Qdrant dashboard | `http://localhost:6333/dashboard` |
| Open WebUI       | `http://localhost:8801`           |
| Docker network   | `ollama-ai_ai-net`                |
| MCP image        | `ollama-ai-mcp-rag:latest`        |
| MCP tools        | `search_codebase`, `get_context`  |
