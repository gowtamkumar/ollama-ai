import os
import re
import time
import json
import hashlib
import uuid
import logging
import threading
from fnmatch import fnmatch

import requests
from qdrant_client import QdrantClient
from qdrant_client import models as qmodels
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("indexer")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434").rstrip("/")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333").rstrip("/")
EMBED_MODEL = os.environ.get("EMBED_MODEL", os.environ.get("EMBEDDING_MODEL", "nomic-embed-text"))
COLLECTION_NAME = os.environ.get("COLLECTION_NAME", "codebase")
PROJECTS_DIR = os.environ.get("PROJECTS_DIR", "/projects")
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "60"))
HYBRID_ENABLED = os.environ.get("HYBRID_ENABLED", "true").lower() == "true"
SPARSE_MODEL = os.environ.get("SPARSE_MODEL", "Qdrant/bm25")
FASTEMBED_CACHE = os.environ.get("FASTEMBED_CACHE", "/app/cache")
VECTOR_SIZE = int(os.environ.get("VECTOR_SIZE", "768"))
DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "bm25"

STATE_FILE_PATH = os.path.join(PROJECTS_DIR, ".indexer_state.json")
RAGIGNORE_PATH = os.path.join(PROJECTS_DIR, ".ragignore")

SUPPORTED_EXTENSIONS = {
    ".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".json", ".md",
    ".txt", ".html", ".css", ".scss", ".rs", ".java", ".cpp", ".c", ".h",
    ".hpp", ".cs", ".rb", ".php", ".vue", ".svelte", ".sh", ".yml", ".yaml",
    ".sql", ".kt", ".swift",
}

LANGUAGE_BY_EXT = {
    ".ts": "typescript", ".tsx": "typescript", ".js": "javascript", ".jsx": "javascript",
    ".py": "python", ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin",
    ".cpp": "cpp", ".hpp": "cpp", ".c": "c", ".h": "c", ".cs": "csharp", ".rb": "ruby",
    ".php": "php", ".vue": "vue", ".svelte": "svelte", ".swift": "swift", ".sql": "sql",
    ".html": "html", ".css": "css", ".scss": "scss", ".json": "json", ".md": "markdown",
    ".yml": "yaml", ".yaml": "yaml", ".sh": "shell", ".txt": "text",
}

IGNORE_DIRS = {
    "node_modules", ".git", "dist", "build", "venv", ".venv",
    "__pycache__", ".docker", "target", "out", ".idea", ".vscode", ".next",
    "coverage", ".turbo", ".cache",
}

BOUNDARY_RES = [
    re.compile(r"^\s*(export\s+)?(default\s+)?(async\s+)?function\b"),
    re.compile(r"^\s*(export\s+)?(abstract\s+)?class\b"),
    re.compile(r"^\s*(export\s+)?(interface|type|enum|namespace|module)\b"),
    re.compile(r"^\s*(public|private|protected|static|async)\s+[\w<>\[\],\s]+\("),
    re.compile(r"^\s*def\s+\w+"),
    re.compile(r"^\s*class\s+\w+"),
    re.compile(r"^\s*func\s+"),
    re.compile(r"^\s*(pub\s+)?(fn|struct|impl|trait|mod)\b"),
    re.compile(r"^\s*[\w$]+\s*[=:]\s*(async\s+)?\([^)]*\)\s*=>"),
    re.compile(r"^\s*@[\w.]+\s*\(?"),
]

# Optional sparse (BM25) embedding for hybrid search.
sparse_model = None
if HYBRID_ENABLED:
    try:
        from fastembed import SparseTextEmbedding

        logger.info(f"Loading sparse model '{SPARSE_MODEL}' for hybrid indexing...")
        sparse_model = SparseTextEmbedding(model_name=SPARSE_MODEL, cache_dir=FASTEMBED_CACHE)
        logger.info("Sparse model loaded.")
    except Exception as e:
        logger.warning(f"Could not load sparse model ({e}). Falling back to dense-only indexing.")
        sparse_model = None

HYBRID_ACTIVE = sparse_model is not None


def wait_for_services():
    qdrant_ready = False
    ollama_ready = False
    while not (qdrant_ready and ollama_ready):
        if not qdrant_ready:
            try:
                resp = requests.get(f"{QDRANT_URL}/healthz", timeout=2)
                if resp.status_code == 200:
                    logger.info("Qdrant service is ready!")
                    qdrant_ready = True
            except Exception:
                logger.info("Waiting for Qdrant to be ready...")
        if not ollama_ready:
            try:
                resp = requests.get(f"{OLLAMA_URL}/", timeout=2)
                if resp.status_code in (200, 404):
                    logger.info("Ollama service is ready!")
                    ollama_ready = True
            except Exception:
                logger.info("Waiting for Ollama to be ready...")
        if not (qdrant_ready and ollama_ready):
            time.sleep(3)


def ensure_ollama_model():
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        if resp.status_code == 200:
            models = resp.json().get("models", [])
            model_names = [m["name"] for m in models]
            target_names = {EMBED_MODEL, f"{EMBED_MODEL}:latest"}
            if any(name in target_names for name in model_names):
                logger.info(f"Ollama embedding model '{EMBED_MODEL}' is already pulled.")
                return
        logger.info(f"Ollama model '{EMBED_MODEL}' not found. Pulling it now...")
        pull_resp = requests.post(
            f"{OLLAMA_URL}/api/pull",
            json={"name": EMBED_MODEL, "stream": False},
            timeout=600,
        )
        if pull_resp.status_code == 200:
            logger.info(f"Successfully pulled model '{EMBED_MODEL}'!")
        else:
            logger.error(f"Failed to pull model '{EMBED_MODEL}': {pull_resp.text}")
    except Exception as e:
        logger.error(f"Error checking/pulling Ollama model: {e}")


def get_embeddings(texts, ollama_url, model_name):
    if not texts:
        return []
    try:
        resp = requests.post(
            f"{ollama_url}/api/embed",
            json={"model": model_name, "input": texts},
            timeout=60,
        )
        if resp.status_code == 200:
            embeddings = resp.json().get("embeddings", [])
            if embeddings:
                return embeddings
    except Exception as e:
        logger.warning(f"Batch embed API failed: {e}. Falling back to single embeddings.")

    embeddings = []
    for text in texts:
        success = False
        for endpoint in ["/api/embed", "/api/embeddings"]:
            try:
                payload = {
                    "model": model_name,
                    "input" if endpoint == "/api/embed" else "prompt": text,
                }
                resp = requests.post(f"{ollama_url}{endpoint}", json=payload, timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    if endpoint == "/api/embed":
                        embedding = data.get("embeddings", [data.get("embedding")])[0]
                    else:
                        embedding = data.get("embedding")
                    if embedding:
                        embeddings.append(embedding)
                        success = True
                        break
            except Exception as ex:
                logger.debug(f"Endpoint {endpoint} failed: {ex}")
                continue
        if not success:
            logger.error(f"Failed to generate embedding for chunk: {text[:50]}...")
            embeddings.append([0.0] * VECTOR_SIZE)
    return embeddings


def get_sparse_embeddings(texts):
    """Returns list of (indices, values) tuples, or None if sparse disabled."""
    if not HYBRID_ACTIVE or not texts:
        return None
    try:
        results = []
        for emb in sparse_model.embed(texts):
            results.append((emb.indices.tolist(), emb.values.tolist()))
        return results
    except Exception as e:
        logger.warning(f"Sparse embedding failed: {e}")
        return None


def smart_chunk_file(content, max_chars=1800, min_chars=400, overlap_lines=3):
    """Boundary-aware chunking: prefers to split at function/class boundaries."""
    lines = content.splitlines()
    chunks = []
    current = []
    current_len = 0
    start_line = 1

    def flush(end_line):
        if current:
            chunks.append({
                "text": "\n".join(current),
                "start_line": start_line,
                "end_line": end_line,
            })

    for idx, line in enumerate(lines):
        line_len = len(line) + 1
        is_boundary = idx > 0 and any(r.search(line) for r in BOUNDARY_RES)
        too_big = current_len + line_len > max_chars
        if current and ((is_boundary and current_len >= min_chars) or too_big):
            flush(idx)
            overlap = current[-overlap_lines:] if len(current) >= overlap_lines else list(current)
            current = list(overlap)
            current_len = sum(len(l) + 1 for l in current)
            start_line = idx - len(current) + 1
        current.append(line)
        current_len += line_len

    if current:
        flush(len(lines))
    return chunks


def get_file_hash(content_bytes):
    return hashlib.md5(content_bytes).hexdigest()


def load_state():
    if os.path.exists(STATE_FILE_PATH):
        try:
            with open(STATE_FILE_PATH, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading state file: {e}. Starting fresh.")
    return {}


def save_state(state):
    try:
        with open(STATE_FILE_PATH, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving state file: {e}")


def load_ragignore():
    patterns = []
    if os.path.exists(RAGIGNORE_PATH):
        try:
            with open(RAGIGNORE_PATH, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        patterns.append(line.rstrip("/"))
            logger.info(f"Loaded {len(patterns)} pattern(s) from .ragignore")
        except Exception as e:
            logger.error(f"Error reading .ragignore: {e}")
    return patterns


def path_is_ignored(rel_path, patterns):
    if not patterns:
        return False
    parts = rel_path.split(os.sep)
    for pat in patterns:
        if fnmatch(rel_path, pat) or fnmatch(rel_path, pat + "/*"):
            return True
        if any(fnmatch(part, pat) for part in parts):
            return True
    return False


def desired_collection_ok(qdrant_client):
    """Returns True if existing collection matches the desired (named-vector) schema."""
    try:
        info = qdrant_client.get_collection(COLLECTION_NAME)
    except Exception:
        return False
    try:
        vectors = info.config.params.vectors
        has_named_dense = isinstance(vectors, dict) and DENSE_VECTOR_NAME in vectors
        if not has_named_dense:
            return False
        if HYBRID_ACTIVE:
            sparse = getattr(info.config.params, "sparse_vectors", None)
            if not sparse or SPARSE_VECTOR_NAME not in sparse:
                return False
        return True
    except Exception:
        return False


def ensure_collection(qdrant_client):
    """Creates the collection with the right schema. Returns True if it was (re)created."""
    if desired_collection_ok(qdrant_client):
        return False

    try:
        qdrant_client.delete_collection(COLLECTION_NAME)
        logger.info(f"Removed incompatible collection '{COLLECTION_NAME}' for schema upgrade.")
    except Exception:
        pass

    vectors_config = {DENSE_VECTOR_NAME: VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE)}
    sparse_config = None
    if HYBRID_ACTIVE:
        sparse_config = {SPARSE_VECTOR_NAME: qmodels.SparseVectorParams(modifier=qmodels.Modifier.IDF)}

    qdrant_client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=vectors_config,
        sparse_vectors_config=sparse_config,
    )
    logger.info(
        f"Created collection '{COLLECTION_NAME}' "
        f"(hybrid={'on' if HYBRID_ACTIVE else 'off'})."
    )
    return True


def build_points(rel_path, language, chunks, dense_embeddings, sparse_embeddings):
    points = []
    for idx, chunk in enumerate(chunks):
        if idx >= len(dense_embeddings):
            break
        point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{rel_path}_{idx}_{chunk['start_line']}"))
        vector = {DENSE_VECTOR_NAME: dense_embeddings[idx]}
        if sparse_embeddings and idx < len(sparse_embeddings):
            indices, values = sparse_embeddings[idx]
            vector[SPARSE_VECTOR_NAME] = qmodels.SparseVector(indices=indices, values=values)
        points.append(PointStruct(
            id=point_id,
            vector=vector,
            payload={
                "file_path": rel_path,
                "language": language,
                "start_line": chunk["start_line"],
                "end_line": chunk["end_line"],
                "content": chunk["text"],
            },
        ))
    return points


def delete_file_points(qdrant_client, rel_path):
    qdrant_client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(
            must=[FieldCondition(key="file_path", match=MatchValue(value=rel_path))]
        ),
    )


def scan_once(qdrant_client, state, ignore_patterns):
    current_files = {}
    for root, dirs, files in os.walk(PROJECTS_DIR):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            if ext not in SUPPORTED_EXTENSIONS:
                continue
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, PROJECTS_DIR)
            if full_path == STATE_FILE_PATH or path_is_ignored(rel_path, ignore_patterns):
                continue
            try:
                mtime = os.path.getmtime(full_path)
                size = os.path.getsize(full_path)
                existing = state.get(rel_path)
                if existing and existing.get("mtime") == mtime and existing.get("size") == size:
                    current_files[rel_path] = existing
                    continue
                with open(full_path, "rb") as f:
                    content_bytes = f.read()
                file_hash = get_file_hash(content_bytes)
                if existing and existing.get("hash") == file_hash:
                    existing["mtime"] = mtime
                    existing["size"] = size
                    current_files[rel_path] = existing
                    continue

                logger.info(f"Indexing file: {rel_path}")
                content = content_bytes.decode("utf-8", errors="ignore")
                language = LANGUAGE_BY_EXT.get(ext, "text")
                chunks = smart_chunk_file(content)
                chunk_texts = [c["text"] for c in chunks]
                dense_embeddings = get_embeddings(chunk_texts, OLLAMA_URL, EMBED_MODEL)
                sparse_embeddings = get_sparse_embeddings(chunk_texts)
                points = build_points(rel_path, language, chunks, dense_embeddings, sparse_embeddings)

                delete_file_points(qdrant_client, rel_path)
                if points:
                    qdrant_client.upsert(collection_name=COLLECTION_NAME, points=points)

                current_files[rel_path] = {"mtime": mtime, "size": size, "hash": file_hash}
                logger.info(f"Indexed {len(points)} chunks for {rel_path}")
            except Exception as fe:
                logger.error(f"Failed to process file {rel_path}: {fe}")
                if rel_path in state:
                    current_files[rel_path] = state[rel_path]

    deleted_files = set(state.keys()) - set(current_files.keys())
    for rel_path in deleted_files:
        logger.info(f"Deleting indexes for removed file: {rel_path}")
        try:
            delete_file_points(qdrant_client, rel_path)
        except Exception as de:
            logger.error(f"Failed to delete index for {rel_path}: {de}")

    return current_files


def start_watcher(change_event):
    """Starts a watchdog observer that sets change_event on relevant FS changes."""
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except Exception as e:
        logger.warning(f"watchdog unavailable ({e}); using periodic scan only.")
        return None

    class Handler(FileSystemEventHandler):
        def on_any_event(self, event):
            if event.is_directory:
                return
            path = getattr(event, "dest_path", None) or event.src_path
            if path and path.endswith(".indexer_state.json"):
                return
            ext = os.path.splitext(path)[1].lower()
            if ext in SUPPORTED_EXTENSIONS:
                change_event.set()

    observer = Observer()
    observer.schedule(Handler(), PROJECTS_DIR, recursive=True)
    observer.daemon = True
    observer.start()
    logger.info("File watcher started (realtime indexing enabled).")
    return observer


def main():
    logger.info("Starting local codebase indexer daemon...")
    logger.info(f"Hybrid search: {'ENABLED' if HYBRID_ACTIVE else 'disabled'}")
    wait_for_services()
    ensure_ollama_model()

    qdrant_client = QdrantClient(url=QDRANT_URL)
    try:
        recreated = ensure_collection(qdrant_client)
    except Exception as e:
        logger.critical(f"Failed to verify/create Qdrant collection: {e}")
        return

    state = {} if recreated else load_state()
    if recreated:
        logger.info("Collection schema changed; performing a full re-index.")
        save_state(state)

    change_event = threading.Event()
    start_watcher(change_event)

    while True:
        ignore_patterns = load_ragignore()
        logger.info("Scanning codebase for changes...")
        state = scan_once(qdrant_client, state, ignore_patterns)
        save_state(state)
        logger.info(f"Scan complete. Total files in index: {len(state)}")

        # Wait for a file change (realtime) or fall back to periodic safety scan.
        triggered = change_event.wait(timeout=SCAN_INTERVAL)
        if triggered:
            change_event.clear()
            time.sleep(2)  # debounce burst of edits


if __name__ == "__main__":
    main()
