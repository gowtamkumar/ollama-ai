# 🧠 Ollama AI — Local LLM Server with Docker

Run powerful open-source AI coding models **locally** using [Ollama](https://ollama.com/) + [Open WebUI](https://github.com/open-webui/open-webui), fully containerized with Docker. Connect to [Cline](https://github.com/cline/cline) in VS Code for AI-assisted coding — **100% offline, no API keys needed**.

---

## ✨ Features

- 🚀 Run LLMs locally — no cloud, no API keys
- 🧑‍💻 Pre-configured with `deepseek-coder-v2` for coding tasks
- 💬 Chat UI via Open WebUI at `http://localhost:8801`
- 🔌 Direct API at `http://localhost:11434`
- 🔁 Auto-restarts on crash or reboot
- 💾 Persistent model storage (won't re-download on restart)
- 🔗 Ready to connect with **Cline** (VS Code AI coding extension)

---

## 📦 Services

| Service        | Image                              | Port (Host → Container) | Description                     |
| -------------- | ---------------------------------- | ------------------------ | ------------------------------- |
| **ollama**     | `ollama/ollama`                    | `11434 → 11434`          | LLM backend & model manager     |
| **open-webui** | `ghcr.io/open-webui/open-webui`    | `8801 → 8080`            | Web-based chat UI for Ollama    |

---

## 🛠️ Requirements

| Tool | Version | Install |
|------|---------|---------|
| Docker | 24+ | [docs.docker.com](https://docs.docker.com/get-docker/) |
| Docker Compose | v2+ | Included with Docker Desktop |

> **GPU (Optional):** For faster inference, ensure your Docker has NVIDIA GPU support. CPU mode works but is slower.

---

## 🚀 Getting Started

### 1. Clone the repository

```bash
git clone <your-repo-url>
cd ollama-ai
```

### 2. Start the stack

```bash
docker compose up -d
```

On first start, it will automatically:
1. Start the **Ollama** server
2. Pull the `deepseek-coder-v2` model (~9 GB, one-time download)
3. Run a warm-up test prompt
4. Start **Open WebUI**

### 3. Check running containers

```bash
docker compose ps
```

Expected output:
```
NAME          STATUS    PORTS
ollama        Running   0.0.0.0:11434->11434/tcp
open-webui    Running   0.0.0.0:8801->8080/tcp
```

### 4. Watch logs

```bash
# All services
docker compose logs -f

# Ollama only (watch model download progress)
docker compose logs -f ollama

# Open WebUI only
docker compose logs -f open-webui
```

---

## 🌐 Using Open WebUI

1. Open your browser → **http://localhost:8801**
2. On first visit, **create an admin account** (local only)
3. Select **`deepseek-coder-v2`** from the model dropdown
4. Start chatting!

---

## 🖥️ Using the Terminal (CLI)

Run a model interactively inside the container:

```bash
docker exec -it ollama ollama run deepseek-coder-v2
```

Run with a one-shot prompt:

```bash
docker exec -it ollama ollama run deepseek-coder-v2 "Explain recursion in Python"
```

### Manage Models

```bash
# List downloaded models
docker exec -it ollama ollama list

# Pull a new model
docker exec -it ollama ollama pull codellama

# Pull other coding models
docker exec -it ollama ollama pull codegemma
docker exec -it ollama ollama pull llama3
docker exec -it ollama ollama pull codellama:7b
docker exec -it ollama ollama pull deepseek-coder-v2

# Remove a model
docker exec -it ollama ollama rm codellama
```

---

## 🔌 Connect with Cline (VS Code)

[Cline](https://marketplace.visualstudio.com/items?itemName=saoudrizwan.claude-dev) is a VS Code extension that gives you an AI coding assistant. Follow these steps to connect it to your local Ollama server.

### Step 1 — Install Cline Extension

1. Open **VS Code**
2. Go to **Extensions** (`Ctrl+Shift+X`)
3. Search for **`Cline`**
4. Click **Install**

### Step 2 — Configure Cline to use Ollama

1. Open Cline from the sidebar (robot icon)
2. Click the **⚙️ Settings** icon in Cline panel
3. Set the **API Provider** to **`Ollama`**
4. Set the following values:

| Field | Value |
|-------|-------|
| **API Provider** | `Ollama` |
| **Base URL** | `http://localhost:11434` |
| **Model** | `deepseek-coder-v2` |

> ✅ No API key needed — it's running locally!

### Step 3 — Test the connection

In Cline, type a message like:

```
Write a Python function to reverse a linked list
```

Cline will send the request to your local Ollama container and stream the response back.

---

## 🧩 Using Ollama API Directly

The Ollama REST API is available at `http://localhost:11434`.

### Generate a completion

```bash
curl http://localhost:11434/api/generate \
  -d '{
    "model": "deepseek-coder-v2",
    "prompt": "Write a hello world in Go",
    "stream": false
  }'
```

### Chat completion (OpenAI-compatible)

```bash
curl http://localhost:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-coder-v2",
    "messages": [
      {"role": "user", "content": "Explain Docker networking"}
    ]
  }'
```

### List available models

```bash
curl http://localhost:11434/api/tags
```

---

## 🤖 Recommended Coding Models

| Model | Size | Best For |
|-------|------|----------|
| `deepseek-coder-v2` | ~9 GB | General coding, multi-language |
| `codellama:7b` | ~4 GB | Lightweight coding tasks |
| `codegemma:7b` | ~5 GB | Google's coding model |
| `llama3` | ~5 GB | General purpose + reasoning |

---

## 🛑 Stop & Cleanup

```bash
# Stop containers (keep data)
docker compose down

# Stop and remove ALL data (models will re-download!)
docker compose down -v
```

---

## 🔧 Troubleshooting

| Issue | Solution |
|-------|----------|
| Open WebUI not loading | Wait 30s for it to fully start, then refresh |
| Model download stuck | Check logs: `docker compose logs -f ollama` |
| Cline not connecting | Ensure Base URL is `http://localhost:11434` (not https) |
| Out of disk space | Models are large. Use `docker exec -it ollama ollama rm <model>` to free space |
| Slow responses | Normal on CPU. For GPU, add NVIDIA runtime to docker-compose.yml |

---

## 📁 Project Structure

```
ollama-ai/
├── docker-compose.yml   # Service definitions
└── README.md            # This file
```

---

## 📄 License

MIT — feel free to use and modify.
