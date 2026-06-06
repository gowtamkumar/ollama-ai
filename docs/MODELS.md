# Model Management Guide

এই ডকুমেন্টে আছে কীভাবে নতুন model **add**, **custom (Cline-optimized)** model বানানো, **Cline/Continue-এ wire করা**, আর **remove/manage** করতে হয়।

> সব model Ollama container-এ থাকে আর `ollama_data` named volume-এ persist হয় — container restart-এ re-download হয় না।

---

## 1. দ্রুত mental model

তোমার stack-এ model তিন ভূমিকায়:

| ভূমিকা                        | কে ব্যবহার করে    | উদাহরণ                    |
| ----------------------------- | ----------------- | ------------------------- |
| Chat/agent (coding)           | Cline             | `qwen2.5-coder:7b`        |
| Tab autocomplete (FIM)        | Continue          | `qwen2.5-coder:1.5b-base` |
| Embedding (RAG)               | indexer + rag-api | `nomic-embed-text`        |
| Reasoning/planning (optional) | Cline (switch)    | `deepseek-r1:14b`         |

> ⚠️ **base model ≠ chat model।** `*-base` (যেমন `qwen2.5-coder:1.5b-base`) শুধু autocomplete-এ চলে — Cline chat/agent-এ দিলে ভাঙবে।

---

## 2. নতুন model add করার ৩টা উপায়

### উপায় A — সরাসরি pull (সবচেয়ে সহজ)

```bash
docker exec -it ollama ollama pull <model-name>
```

উদাহরণ:

```bash
docker exec -it ollama ollama pull qwen2.5-coder:14b
docker exec -it ollama ollama pull deepseek-r1:14b
docker exec -it ollama ollama pull llama3.1:8b
```

pull হয়ে গেলে সাথে সাথে ব্যবহারযোগ্য — কোনো restart লাগে না।

> বড় model (9GB+) pull করার সময় terminal খোলা রাখো। background container (`model-init`)-এ বড় pull আগে exit 137-এ crash করেছিল, তাই বড় model **manually** pull করাই নিরাপদ।

### উপায় B — `.env` দিয়ে auto-pull (startup-এ)

`.env`-এ model যোগ করো:

```bash
CHAT_MODELS=qwen2.5-coder:7b
AUTOCOMPLETE_MODELS=qwen2.5-coder:1.5b-base
REASONING_MODELS=deepseek-r1:14b
```

তারপর:

```bash
docker compose up -d        # model-init নতুন model গুলো pull করবে
docker compose logs -f model-init
```

space দিয়ে একাধিক দেওয়া যায়:

```bash
CHAT_MODELS=qwen2.5-coder:7b llama3.1:8b
```

> মনে রাখো: `model-init` fail হলেও (একটা model না পেলে) বাকি stack চালু থাকে — script resilient।

### উপায় C — custom Modelfile (নিজের tuned model)

বড় context window বা নির্দিষ্ট parameter দরকার হলে (যেমন Cline-এর জন্য) — section 3 দেখো।

---

## 3. (Optional) বড় context custom model বানানো

> Default setup-এ দরকার নেই — সরাসরি `qwen2.5-coder:7b` ব্যবহার করো।
> শুধু যদি Cline-এ **ভাঙা tool call** (`ask_followup_question without value...`) বারবার হয়,
> তখন বড় `num_ctx`-সহ একটা custom model বানাতে পারো।

Ollama-র default context window ছোট হতে পারে, ফলে Cline-এর বড় system prompt truncate হয়ে tool format ভাঙে। সমাধান: বড় context-সহ derived model।

### ধাপ ১ — একটা Modelfile লেখো

`clients/Modelfile.cline` নামে একটা ফাইল বানাও:

```text
FROM qwen2.5-coder:7b

PARAMETER num_ctx 16384
PARAMETER temperature 0.2
PARAMETER top_p 0.9
```

| Parameter     | কাজ                                            |
| ------------- | ---------------------------------------------- |
| `FROM`        | কোন base model-এর উপর তৈরি                     |
| `num_ctx`     | context window (token)। Cline-এ 16k+ ভালো      |
| `temperature` | কম = বেশি deterministic/structured tool output |
| `top_p`       | sampling diversity                             |

### ধাপ ২ — build করো

```bash
docker cp clients/Modelfile.cline ollama:/tmp/Modelfile.cline
docker exec ollama ollama create qwen2.5-coder:7b-cline -f /tmp/Modelfile.cline
docker exec ollama ollama list | grep cline
```

ফল: `qwen2.5-coder:7b-cline` — তখন এটাই Cline-এ দাও।

> RAM বেশি থাকলে `num_ctx 32768` দিতে পারো (বেশি RAM + একটু slow)।
> `qwen2.5-coder:14b`-এর জন্য `FROM qwen2.5-coder:14b` দিয়ে একই কৌশল।

> মনে রাখো: এই derived model গুলো optional/experimental — মুছে ফেলতে চাইলে
> `docker exec ollama ollama rm qwen2.5-coder:7b-cline`।

---

## 4. নতুন model Cline-এ wire করা

Cline panel → **⚙️ Settings**:

| Field        | Value                                    |
| ------------ | ---------------------------------------- |
| API Provider | `Ollama`                                 |
| Base URL     | `http://localhost:11434`                 |
| Model        | `qwen2.5-coder:7b` (বা তোমার নতুন model) |

Save → আবার প্রশ্ন করো।

> Cline-এ সবসময় **chat/instruct বা custom (`-cline`) model** দাও, কখনো `-base` না।

---

## 5. নতুন model Continue-এ wire করা (autocomplete/chat)

`clients/continue-config.example.json` কপি করে `~/.continue/config.json`-এ বসাও, বা edit করো:

```json
{
  "models": [
    {
      "title": "Coder 7B",
      "provider": "ollama",
      "model": "qwen2.5-coder:7b",
      "apiBase": "http://localhost:11434"
    }
  ],
  "tabAutocompleteModel": {
    "title": "Coder 1.5B FIM",
    "provider": "ollama",
    "model": "qwen2.5-coder:1.5b-base",
    "apiBase": "http://localhost:11434"
  }
}
```

> autocomplete-এ অবশ্যই একটা `*-base` (FIM) model দাও — chat model autocomplete-এ ভালো না।

---

## 6. নতুন embedding model (সাবধান!)

Embedding model বদলানো RAG-এর জন্য sensitive:

```bash
# .env
EMBED_MODEL=mxbai-embed-large
```

⚠️ embedding model বদলালে **vector dimension** বদলায় (যেমন nomic=768)। তখন:

1. `indexer/app.py`-এ `VECTOR_SIZE` মিলিয়ে দাও।
2. পুরো collection নতুন করে বানাতে হবে:

```bash
docker compose down -v
docker compose up -d --build
```

> শুধু coding চাইলে embedding model বদলানোর দরকার নেই — `nomic-embed-text` ভালো।

---

## 7. Model manage / remove

```bash
# সব model list
docker exec -it ollama ollama list

# একটা model মুছে ফেলো (disk বাঁচাতে)
docker exec -it ollama ollama rm qwen2.5-coder:7b

# terminal-এ test করো
docker exec -it ollama ollama run qwen2.5-coder:7b "write a bubble sort in python"

# একটা model-এর তথ্য দেখো
docker exec -it ollama ollama show qwen2.5-coder:7b
```

---

## 8. কোন model কখন (তোমার machine: Ryzen 7 5700G, 30GB, no GPU)

| দরকার                 | recommended model         | নোট                        |
| --------------------- | ------------------------- | -------------------------- |
| Cline daily coding    | `qwen2.5-coder:7b`        | best balance               |
| বেশি reliable agentic | `qwen2.5-coder:14b`       | slower, smarter            |
| Autocomplete          | `qwen2.5-coder:1.5b-base` | হালকা, দ্রুত               |
| Planning/reasoning    | `deepseek-r1:14b`         | thinking-tuned, switch করে |
| Embedding             | `nomic-embed-text`        | বদলিও না                   |

> CPU-only বলে একসাথে অনেক বড় model load রেখো না — দরকার মতো switch করো।

---

## 9. Troubleshooting

| সমস্যা                      | কারণ                             | সমাধান                                                        |
| --------------------------- | -------------------------------- | ------------------------------------------------------------- |
| ভাঙা tool call (Cline)      | context ছোট / base model         | `*-cline` (বড় num_ctx) model ব্যবহার করো; base model বাদ দাও |
| model pull crash (exit 137) | বড় download background-এ killed | `docker exec -it ollama ollama pull <model>` manually         |
| "model not found" Cline-এ   | নাম ভুল / pull হয়নি             | `docker exec ollama ollama list` দিয়ে নাম মিলাও              |
| autocomplete কাজ করে না     | chat model দেওয়া হয়েছে         | `*-base` FIM model দাও                                        |
| খুব slow                    | বড় model + CPU                  | ছোট model / কম `num_ctx`                                      |

দরকারি command:

```bash
docker exec -it ollama ollama list
docker compose logs -f model-init
docker compose logs -f ollama
```

---

## 10. Quick Reference

| কাজ          | command                                                                                                             |
| ------------ | ------------------------------------------------------------------------------------------------------------------- |
| pull         | `docker exec -it ollama ollama pull <model>`                                                                        |
| list         | `docker exec -it ollama ollama list`                                                                                |
| remove       | `docker exec -it ollama ollama rm <model>`                                                                          |
| custom build | `docker cp clients/Modelfile.cline ollama:/tmp/ && docker exec ollama ollama create <name> -f /tmp/Modelfile.cline` |
| test         | `docker exec -it ollama ollama run <model> "..."`                                                                   |
| info         | `docker exec -it ollama ollama show <model>`                                                                        |
