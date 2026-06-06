import os
import time
import json
import hashlib
import uuid
import logging
import requests
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("indexer")

# Configuration from environment
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434").rstrip("/")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333").rstrip("/")
EMBED_MODEL = os.environ.get("EMBED_MODEL", os.environ.get("EMBEDDING_MODEL", "nomic-embed-text"))
COLLECTION_NAME = os.environ.get("COLLECTION_NAME", "codebase")
PROJECTS_DIR = os.environ.get("PROJECTS_DIR", "/projects")
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "30"))
STATE_FILE_PATH = os.path.join(PROJECTS_DIR, ".indexer_state.json")

# File extensions to index
SUPPORTED_EXTENSIONS = {
    ".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".json", ".md",
    ".txt", ".html", ".css", ".rs", ".java", ".cpp", ".c", ".h",
    ".sh", ".yml", ".yaml"
}

# Directories to ignore
IGNORE_DIRS = {
    "node_modules", ".git", "dist", "build", "venv", ".venv",
    "__pycache__", ".docker", "target", "out", ".idea", ".vscode"
}

def wait_for_services():
    """Waits for Ollama and Qdrant to be ready and responsive."""
    qdrant_ready = False
    ollama_ready = False

    while not (qdrant_ready and ollama_ready):
        if not qdrant_ready:
            try:
                # Simple ping/health check for Qdrant
                resp = requests.get(f"{QDRANT_URL}/healthz", timeout=2)
                if resp.status_code == 200:
                    logger.info("Qdrant service is ready!")
                    qdrant_ready = True
            except Exception:
                logger.info("Waiting for Qdrant to be ready...")

        if not ollama_ready:
            try:
                # Ollama root endpoint or /api/tags
                resp = requests.get(f"{OLLAMA_URL}/", timeout=2)
                if resp.status_code == 200 or resp.status_code == 404:  # Ollama returns 200 or 404 for root depending on version
                    logger.info("Ollama service is ready!")
                    ollama_ready = True
            except Exception:
                logger.info("Waiting for Ollama to be ready...")

        if not (qdrant_ready and ollama_ready):
            time.sleep(3)

def ensure_ollama_model():
    """Verifies if the embedding model is loaded, pulls it if missing."""
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        if resp.status_code == 200:
            models = resp.json().get("models", [])
            model_names = [m["name"] for m in models]
            
            # Match both name and name:latest
            target_names = {EMBED_MODEL, f"{EMBED_MODEL}:latest"}
            if any(name in target_names for name in model_names):
                logger.info(f"Ollama embedding model '{EMBED_MODEL}' is already pulled.")
                return

        # Pull model if not found
        logger.info(f"Ollama model '{EMBED_MODEL}' not found. Pulling it now (this may take a while)...")
        pull_resp = requests.post(
            f"{OLLAMA_URL}/api/pull",
            json={"name": EMBED_MODEL, "stream": False},
            timeout=600  # High timeout for model download
        )
        if pull_resp.status_code == 200:
            logger.info(f"Successfully pulled model '{EMBED_MODEL}'!")
        else:
            logger.error(f"Failed to pull model '{EMBED_MODEL}': {pull_resp.text}")
    except Exception as e:
        logger.error(f"Error checking/pulling Ollama model: {e}")

def get_embeddings(texts, ollama_url, model_name):
    """Generates embeddings for a list of texts using Ollama."""
    if not texts:
        return []

    # Try newer /api/embed batch API first
    try:
        resp = requests.post(f"{ollama_url}/api/embed", json={
            "model": model_name,
            "input": texts
        }, timeout=30)
        if resp.status_code == 200:
            return resp.json().get("embeddings", [])
    except Exception as e:
        logger.warning(f"Error using batch embed API: {e}. Falling back to single embedding API.")

    # Fallback to single-item /api/embeddings or /api/embed
    embeddings = []
    for text in texts:
        success = False
        for endpoint in ["/api/embed", "/api/embeddings"]:
            try:
                payload = {
                    "model": model_name,
                    "input" if endpoint == "/api/embed" else "prompt": text
                }
                resp = requests.post(f"{ollama_url}{endpoint}", json=payload, timeout=15)
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
            # Append zero vector of size 768 to avoid breaking the batch alignment
            embeddings.append([0.0] * 768)

    return embeddings

def chunk_file(content, max_chars=1500, overlap_lines=5):
    """Splits file content into line-based chunks with line tracking."""
    lines = content.splitlines()
    chunks = []
    current_chunk = []
    current_len = 0
    start_line = 1

    for idx, line in enumerate(lines):
        line_len = len(line) + 1  # +1 for newline
        if current_len + line_len > max_chars and current_chunk:
            end_line = idx
            chunk_text = "\n".join(current_chunk)
            chunks.append({
                "text": chunk_text,
                "start_line": start_line,
                "end_line": end_line
            })
            # Overlap by taking last N lines
            overlap = current_chunk[-overlap_lines:] if len(current_chunk) >= overlap_lines else current_chunk
            current_chunk = list(overlap)
            current_len = sum(len(l) + 1 for l in current_chunk)
            start_line = idx - len(overlap) + 1

        current_chunk.append(line)
        current_len += line_len

    if current_chunk:
        end_line = len(lines)
        chunk_text = "\n".join(current_chunk)
        chunks.append({
            "text": chunk_text,
            "start_line": start_line,
            "end_line": end_line
        })

    return chunks

def get_file_hash(content_bytes):
    """Computes MD5 hash of file content."""
    return hashlib.md5(content_bytes).hexdigest()

def load_state():
    """Loads existing indexer state from disk."""
    if os.path.exists(STATE_FILE_PATH):
        try:
            with open(STATE_FILE_PATH, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading state file: {e}. Starting fresh.")
    return {}

def save_state(state):
    """Saves current indexer state to disk."""
    try:
        with open(STATE_FILE_PATH, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving state file: {e}")

def main():
    logger.info("Starting local codebase indexer daemon...")
    wait_for_services()
    ensure_ollama_model()

    # Initialize Qdrant Client
    qdrant_client = QdrantClient(url=QDRANT_URL)
    
    # Ensure Qdrant collection exists
    try:
        collections = qdrant_client.get_collections().collections
        if not any(c.name == COLLECTION_NAME for c in collections):
            logger.info(f"Creating collection '{COLLECTION_NAME}' in Qdrant...")
            qdrant_client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(size=768, distance=Distance.COSINE)
            )
            logger.info(f"Collection '{COLLECTION_NAME}' created.")
    except Exception as e:
        logger.critical(f"Failed to verify/create Qdrant collection: {e}")
        return

    # Load file index state
    state = load_state()

    while True:
        logger.info("Scanning codebase for changes...")
        current_files = {}

        # Recursively walk directories
        for root, dirs, files in os.walk(PROJECTS_DIR):
            # Ignore specified directories in-place to avoid walking them
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]

            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext not in SUPPORTED_EXTENSIONS:
                    continue

                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, PROJECTS_DIR)
                
                # Skip the state file itself
                if full_path == STATE_FILE_PATH:
                    continue

                try:
                    mtime = os.path.getmtime(full_path)
                    size = os.path.getsize(full_path)

                    # Simple quick checks first
                    existing = state.get(rel_path)
                    if existing and existing.get("mtime") == mtime and existing.get("size") == size:
                        # File hasn't changed on disk, keep existing state
                        current_files[rel_path] = existing
                        continue

                    # Read file and calculate hash for precise verification
                    with open(full_path, "rb") as f:
                        content_bytes = f.read()
                    
                    file_hash = get_file_hash(content_bytes)

                    if existing and existing.get("hash") == file_hash:
                        # Content hash is same, just update size/mtime in state
                        existing["mtime"] = mtime
                        existing["size"] = size
                        current_files[rel_path] = existing
                        continue

                    # File is new or changed! Index it.
                    logger.info(f"Indexing file: {rel_path}")
                    try:
                        content = content_bytes.decode("utf-8", errors="ignore")
                    except Exception:
                        content = content_bytes.decode("latin-1", errors="ignore")

                    chunks = chunk_file(content)
                    chunk_texts = [c["text"] for c in chunks]
                    
                    # Call Ollama for embeddings
                    embeddings = get_embeddings(chunk_texts, OLLAMA_URL, EMBED_MODEL)

                    # Prepare Qdrant points
                    points = []
                    for idx, chunk in enumerate(chunks):
                        if idx < len(embeddings):
                            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{rel_path}_{idx}_{chunk['start_line']}"))
                            points.append(PointStruct(
                                id=point_id,
                                vector=embeddings[idx],
                                payload={
                                    "file_path": rel_path,
                                    "start_line": chunk["start_line"],
                                    "end_line": chunk["end_line"],
                                    "content": chunk["text"]
                                }
                            ))

                    # Remove old points for this file
                    qdrant_client.delete(
                        collection_name=COLLECTION_NAME,
                        points_selector=Filter(
                            must=[
                                FieldCondition(
                                    key="file_path",
                                    match=MatchValue(value=rel_path)
                                )
                            ]
                        )
                    )

                    # Upsert new points
                    if points:
                        qdrant_client.upsert(
                            collection_name=COLLECTION_NAME,
                            points=points
                        )

                    current_files[rel_path] = {
                        "mtime": mtime,
                        "size": size,
                        "hash": file_hash
                    }
                    logger.info(f"Indexed {len(points)} chunks for {rel_path}")

                except Exception as fe:
                    logger.error(f"Failed to process file {rel_path}: {fe}")
                    # Keep existing state for safety if read failed temporarily
                    if rel_path in state:
                        current_files[rel_path] = state[rel_path]

        # Detect deleted files (files in state that were not found in current scan)
        deleted_files = set(state.keys()) - set(current_files.keys())
        for rel_path in deleted_files:
            logger.info(f"Deleting indexes for removed file: {rel_path}")
            try:
                qdrant_client.delete(
                    collection_name=COLLECTION_NAME,
                    points_selector=Filter(
                        must=[
                            FieldCondition(
                                key="file_path",
                                match=MatchValue(value=rel_path)
                            )
                        ]
                    )
                )
            except Exception as de:
                logger.error(f"Failed to delete index for {rel_path}: {de}")

        # Update state and save
        state = current_files
        save_state(state)

        logger.info(f"Scan complete. Total files in index: {len(state)}")
        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    main()
